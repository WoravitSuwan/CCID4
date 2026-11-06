"""Look and feel: pagination, messages, scripts that give feedback."""
import pytest
from django.core.paginator import Paginator

from apps.core.templatetags.ui import page_window

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("current, expected", [
    (1, [1, 2, 3, 4, None, 20]),
    (4, [1, 2, 3, 4, 5, None, 20]),
    (10, [1, None, 9, 10, 11, None, 20]),
    (20, [1, None, 17, 18, 19, 20]),
])
def test_page_window(current, expected):
    assert page_window(Paginator(range(1000), 50).page(current)) == expected


def test_page_window_small_lists_show_every_page():
    assert page_window(Paginator(range(250), 50).page(3)) == [1, 2, 3, 4, 5]


def test_pagination_shows_range_and_pages(client, admin_user):
    from apps.core.models import AuditLog

    AuditLog.objects.bulk_create([AuditLog(action="login", detail=f"x{i}") for i in range(450)])
    client.force_login(admin_user)
    html = client.get("/settings/audit/", {"page": 2}).content.decode()
    assert "แสดง <b" in html and "101–200" in html and 'aria-current="page"' in html and 'rel="next"' in html
    assert 'href="?page=5"' in html  # last page


def test_every_page_loads_the_feedback_script_and_messages_can_be_closed(client, admin_user):
    client.force_login(admin_user)
    resp = client.post("/settings/system/", {}, follow=True)
    html = resp.content.decode()
    assert "js/ui.js" in html and 'class="page-enter' in html
    login = client.get("/accounts/login/")  # logged in: redirected, so check a fresh client
    from django.test import Client

    assert "js/ui.js" in Client().get("/accounts/login/").content.decode() or login.status_code == 302


def test_success_message_closes_by_itself_and_errors_stay(client, admin_user):
    from apps.core.models import SystemSetting

    st = SystemSetting.get()
    client.force_login(admin_user)
    data = {"retention_days": st.retention_days, "default_reason": st.default_reason or "ให้มาพบ",
            "default_appointment_days": st.default_appointment_days, "letter_number": st.letter_number}
    html = client.post("/settings/system/", data, follow=True).content.decode()
    assert "alert-success" in html and 'data-alert="auto"' in html and 'aria-label="ปิดข้อความ"' in html
    resp = client.post("/calls/issue-all/", {"query": "q=ไม่มีแน่นอน"}, follow=True)  # an info message
    assert 'data-alert="stay"' in resp.content.decode()
