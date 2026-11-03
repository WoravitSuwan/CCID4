import datetime as dt
from io import BytesIO

import pytest
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import Role, User
from apps.calls.models import CallAssignment, CallAttempt, CallOutcome
from apps.calls.services import contact_log_with_calls, parse_receipt_list, record_call
from apps.core.models import AuditLog
from apps.registry.models import Case

pytestmark = pytest.mark.django_db


@pytest.fixture
def caller(db):
    return User.objects.create_user("caller", password="pass12345", role=Role.OUTSOURCE, first_name="สมใจ")


@pytest.fixture
def clerk(db):
    return User.objects.create_user("clerk1", password="pass12345", role=Role.ADMIN)


def test_parse_receipt_list():
    assert parse_receipt_list(" l6800000000\nW6800039595, w6800039595 ;T6800110866 ") == [
        "L6800000000", "W6800039595", "T6800110866"]


def test_assign_by_pasting_case_ids(client, imported, caller, clerk):
    client.force_login(clerk)
    keys = list(Case.objects.not_final().values_list("receipt_no", flat=True)[:5])
    resp = client.post("/calls/assign/", {"receipts": "\n".join(k.lower() for k in keys) + "\nX999",
                                          "assignee": caller.pk, "reassign": "on"})
    assert resp.status_code == 200
    assert resp.context["result"]["created"] == 5 and resp.context["result"]["not_found"] == ["X999"]
    assert CallAssignment.objects.filter(assignee=caller).count() == 5
    assert AuditLog.objects.filter(action="assign").exists()


def test_bulk_assign_from_register(client, imported, caller, clerk):
    client.force_login(clerk)
    ids = list(Case.objects.values_list("pk", flat=True)[:3])
    client.post("/calls/assign/bulk/", {"case_ids": ids, "assignee": caller.pk, "next": "/cases/"})
    assert CallAssignment.objects.filter(case_id__in=ids, assignee=caller).count() == 3


def test_outsource_sees_every_open_case_without_assignment(client, imported, caller, clerk):
    mine = Case.objects.not_final().first()
    CallAssignment.objects.create(case=mine, assignee=caller, assigned_by=clerk)
    client.force_login(caller)
    page = client.get("/calls/?tab=all")
    open_cases = Case.objects.not_final().count()
    assert page.context["page"].paginator.count == Case.objects.count()  # every case, no assigning needed
    assert page.context["counts"]["todo"] == open_cases
    final = Case.objects.final().first()
    assert final.pk not in [a.case_id for a in client.get("/calls/?tab=todo").context["page"].paginator.object_list]
    mine_only = client.get("/calls/?tab=all&assignee=me").context["page"]
    assert [a.case_id for a in mine_only] == [mine.pk]
    other = Case.objects.not_final().exclude(pk=mine.pk).first()
    assert client.get(f"/calls/{other.pk}/").status_code == 200
    # admin pages redirect back to the call list (summonses / exports are allowed: they issue too)
    for url in ["/", "/cases/", f"/cases/{mine.pk}/", "/imports/", "/settings/users/"]:
        resp = client.get(url)
        assert resp.status_code == 302 and resp["Location"] == "/calls/", url
    assert mine.complainant.national_id[-3:] not in client.get(f"/calls/{mine.pk}/").content.decode() or \
        not mine.complainant.national_id


def test_record_calls_flow(client, imported, caller, clerk):
    c1, c2 = Case.objects.not_final().order_by("receipt_no")[:2]
    for c in (c1, c2):
        CallAssignment.objects.create(case=c, assignee=caller, assigned_by=clerk)
    client.force_login(caller)
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    # 1st call: phone off -> stays in queue
    resp = client.post(f"/calls/{c1.pk}/", {"outcome": "off", "phone": "0812345678", "called_at": now,
                                            "note": "", "go": "stay"})
    assert resp.status_code == 302
    a = CallAssignment.objects.get(case=c1)
    assert a.attempt_count == 1 and not a.is_done and a.last_outcome == "off"
    # appointment requires a date
    resp = client.post(f"/calls/{c1.pk}/", {"outcome": "appointed", "called_at": now, "go": "stay"})
    assert resp.status_code == 200 and "appointment_at" in resp.context["form"].errors
    appt = (timezone.localtime() + dt.timedelta(days=3)).strftime("%Y-%m-%dT10:00")
    resp = client.post(f"/calls/{c1.pk}/", {"outcome": "appointed", "called_at": now, "appointment_at": appt,
                                            "note": "สะดวกมาวันพฤหัส", "go": "next"})
    assert resp["Location"] == f"/calls/{c2.pk}/"  # goes to the next case in the queue
    a.refresh_from_db()
    assert a.is_done and a.attempt_count == 2
    assert CallAttempt.objects.filter(case=c1).count() == 2
    assert AuditLog.objects.filter(action="call", target_id=str(c1.pk)).count() == 2
    # queue tabs
    page = client.get("/calls/?tab=done")
    assert [x.case_id for x in page.context["page"]] == [c1.pk]


def test_callback_hides_case_until_due(imported, caller):
    case = Case.objects.not_final().first()
    CallAssignment.objects.create(case=case, assignee=caller)
    record_call(case, caller, outcome=CallOutcome.CALLBACK, callback_at=timezone.now() + dt.timedelta(hours=2))
    a = CallAssignment.objects.get(case=case)
    assert not a.is_done and a.callback_at and not a.callback_due


def test_register_text_and_excel(client, imported, caller, clerk):
    case = Case.objects.not_final().exclude(contact_log="").first() or Case.objects.first()
    when = timezone.make_aware(dt.datetime(2026, 10, 9, 10, 30))
    attempt = record_call(case, caller, outcome=CallOutcome.OFF, called_at=when)
    assert attempt.register_text() == f"ครั้งที่ {attempt.number} ไม่สามารถติดต่อได้ (ปิดเครื่อง) 9 ตค 69 เวลา 10.30 น."
    combined = contact_log_with_calls(case)
    assert attempt.register_text() in combined
    if case.contact_log:
        assert combined.startswith(case.contact_log.strip())
    client.force_login(clerk)
    wb = load_workbook(BytesIO(client.get("/calls/export.xlsx").content))
    rows = list(wb.active.values)
    assert rows[0][0] == "เลขรับแจ้ง" and rows[1][0] == case.receipt_no
    # register export carries the call in 'บันทึกการติดต่อ'
    admin = User.objects.create_user("adm", password="pass12345", role=Role.ADMIN)
    client.force_login(admin)
    reg = load_workbook(BytesIO(client.get("/cases/export.xlsx").content))
    texts = [c.value for ws in reg.worksheets for c in ws["Y"] if c.value]
    assert any(attempt.register_text() in t for t in texts)


def test_outsource_cannot_manage(client, imported, caller):
    client.force_login(caller)
    assert client.get("/calls/assign/").status_code == 403
    assert client.get("/calls/export.xlsx").status_code == 403


def test_demo_has_call_staff(db, settings):
    from apps.core.demo import bootstrap_demo

    settings.DEMO_MODE = True
    bootstrap_demo()
    assert CallAssignment.objects.filter(assignee__username="call1").count() == 20
    assert CallAttempt.objects.count() == 4


def test_shared_list_first_caller_takes_the_case_and_busy_warning(client, imported, caller):
    other = User.objects.create_user("call2b", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(caller)
    first = client.get("/calls/").context["page"][0].case
    assert CallAssignment.objects.get(case=first).assignee is None  # nobody assigned it
    page = client.get(f"/calls/{first.pk}/")  # caller opens it
    nxt = page.context["next_pk"]
    # the second caller sees that it is open and is warned; "next" skips it
    client.force_login(other)
    queue = client.get("/calls/").content.decode()
    assert "กำลังเปิดอยู่" in queue
    warn = client.get(f"/calls/{first.pk}/")
    assert warn.context["busy_with"] == caller and "อาจกำลังโทรอยู่" in warn.content.decode()
    third = Case.objects.not_final().exclude(pk__in=[first.pk, nxt]).first()
    assert client.get(f"/calls/{third.pk}/").context["next_pk"] != first.pk
    # caller records the call: becomes responsible, and the case is released
    client.force_login(caller)
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    client.post(f"/calls/{first.pk}/", {"outcome": "off", "called_at": now, "go": "stay"})
    a = CallAssignment.objects.get(case=first)
    assert a.assignee == caller and a.opened_at is None
    client.force_login(other)
    assert client.get(f"/calls/{first.pk}/").context["busy_with"] is None
    # a later call by someone else does not take it away
    client.post(f"/calls/{first.pk}/", {"outcome": "info", "called_at": now, "go": "stay"})
    assert CallAssignment.objects.get(case=first).assignee == caller


def test_queue_tabs_and_status_filter(client, imported, caller):
    from apps.registry.models import Status

    client.force_login(caller)
    c1, c2 = Case.objects.not_final().order_by("receipt_no")[:2]
    record_call(c1, caller, outcome=CallOutcome.OFF)
    record_call(c2, caller, outcome=CallOutcome.INFO)
    page = client.get("/calls/")
    counts = page.context["counts"]
    total, open_cases = Case.objects.count(), Case.objects.not_final().count()
    assert counts["all"] == total and counts["called"] == 2 and counts["never"] == open_cases - 2
    assert counts["done"] == 1 and counts["todo"] == open_cases - 1
    html = page.content.decode()
    assert "โทรแล้ว" in html and "ยังไม่ได้โทร" in html and "สถานะคดี:" in html
    # status chips: each status with its count in the tab; picking one narrows every tab
    final = Status.objects.filter(is_final=True).first()
    all_tab = client.get("/calls/?tab=all")
    chips = {key: n for key, st, n in all_tab.context["status_chips"]}
    assert chips[str(final.pk)] == Case.objects.filter(status=final).count()
    assert sum(chips.values()) == total
    only_final = client.get(f"/calls/?tab=all&status={final.pk}")
    assert only_final.context["page"].paginator.count == Case.objects.filter(status=final).count()
    assert only_final.context["counts"]["todo"] == 0  # closed cases never need a call
    # latest result filter
    reached = client.get("/calls/?tab=called&result=reached").context["page"]
    assert [a.case_id for a in reached] == [c2.pk]
    off = client.get("/calls/?tab=called&result=off").context["page"]
    assert [a.case_id for a in off] == [c1.pk]
    # tab links keep the filters
    assert f"status={final.pk}" in client.get(f"/calls/?tab=all&status={final.pk}").content.decode()
