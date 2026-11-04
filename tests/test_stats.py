"""สถิติ: totals now plus a day-by-day history for a chosen period."""
import datetime as dt
from io import BytesIO

import pytest
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import Role, User
from apps.calls.models import CallOutcome
from apps.calls.services import record_call
from apps.registry.models import Case

pytestmark = pytest.mark.django_db


@pytest.fixture
def calls(imported):
    caller = User.objects.create_user("statcaller", password="x" * 10, role=Role.OUTSOURCE, first_name="สถิติ")
    c1, c2, c3 = Case.objects.not_final().order_by("receipt_no")[:3]
    ten_days_ago = timezone.now() - dt.timedelta(days=10)
    record_call(c1, caller, outcome=CallOutcome.OFF, called_at=ten_days_ago)
    record_call(c1, caller, outcome=CallOutcome.APPOINTED, appointment_at=timezone.now())
    record_call(c2, caller, outcome=CallOutcome.NO_ANSWER)
    record_call(c3, caller, outcome=CallOutcome.INFO, called_at=timezone.now() - dt.timedelta(days=60))
    return caller


def test_outsource_and_admin_see_stats(client, calls, admin_user):
    for user in (calls, admin_user):
        client.force_login(user)
        page = client.get("/stats/")
        assert page.status_code == 200 and "สถิติ" in page.content.decode()
    period = page.context["period"]  # last 30 days by default
    assert (period["calls"], period["reached"], period["not_reached"], period["appointed"]) == (3, 1, 2, 1)
    assert period["cases_called"] == 2
    assert page.context["overall"]["cases"] == Case.objects.count()
    assert [p["calls"] for p in page.context["people"]] == [3]
    assert len(page.context["calls_chart"]["hover"]) == 30 and len(page.context["calls_chart"]["lines"]) == 2
    week = client.get("/stats/?range=7").context["period"]
    assert week["calls"] == 2  # the call 10 days ago is outside
    custom = client.get(f"/stats/?from={(timezone.localdate() - dt.timedelta(days=70)).isoformat()}"
                        f"&to={timezone.localdate().isoformat()}")
    assert custom.context["period"]["calls"] == 4 and custom.context["preset"] == "custom"
    assert 'class="viz-day"' in custom.content.decode()


def test_stats_excel(client, calls):
    client.force_login(calls)
    resp = client.get("/stats/?range=30&format=xlsx")
    wb = load_workbook(BytesIO(resp.content))
    assert wb.sheetnames == ["รายวัน", "รายเจ้าหน้าที่"]
    rows = list(wb["รายวัน"].values)
    assert rows[-1][:4] == ("รวม", 3, 1, 2) and len(rows) == 1 + 30 + 1
    assert list(wb["รายเจ้าหน้าที่"].values)[1][:3] == ("สถิติ", 3, 1)
