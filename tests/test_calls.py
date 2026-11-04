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
    page = client.get("/calls/?tab=all&scope=all")
    open_cases = Case.objects.not_final().count()
    assert page.context["page"].paginator.count == Case.objects.count()  # every case, no assigning needed
    assert client.get("/calls/").context["counts"]["all"] == open_cases  # default: open cases
    final = Case.objects.final().first()
    never = client.get("/calls/?tab=never&scope=all").context["page"].paginator.object_list
    assert final.pk not in [a.case_id for a in never]  # closed cases count as called
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
    # queue tabs: appointed = reached and wants to proceed
    Case.objects.filter(pk=c1.pk).update(contact_log="")
    page = client.get("/calls/?tab=want")
    assert c1.pk in [x.case_id for x in page.context["page"].paginator.object_list]


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
    client.force_login(caller)
    Case.objects.update(contact_log="")  # control what the register's contact log says
    c1, c2, c3, c4, c5 = Case.objects.not_final().order_by("receipt_no")[:5]
    record_call(c1, caller, outcome=CallOutcome.OFF)
    record_call(c2, caller, outcome=CallOutcome.INFO)
    Case.objects.filter(pk=c3.pk).update(contact_log="ไม่ประสงค์ดำเนินคดี วันที่ 3 ก.ค. 68")  # reached before
    Case.objects.filter(pk=c4.pk).update(contact_log="ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง)")  # called, not reached
    Case.objects.filter(pk=c5.pk).update(contact_log="ประสงค์ดำเนินคดี")
    total, open_cases = Case.objects.count(), Case.objects.not_final().count()
    page = client.get("/calls/")  # default: open cases, tab "ทั้งหมด"
    counts, scopes = page.context["counts"], page.context["scope_counts"]
    assert scopes == {"open": open_cases, "final": total - open_cases, "all": total}  # same on every tab
    assert counts["all"] == open_cases and counts["never"] == open_cases - 5
    assert (counts["reached"], counts["not_reached"]) == (3, 2)  # c2 c3 c5 / c1 c4
    assert (counts["want"], counts["not_want"], counts["undecided"]) == (2, 1, 0)  # c2 (info given) c5 / c3
    assert (counts["to_issue"], counts["issued_nr"]) == (2, 0)
    assert [k for k, _ in page.context["tabs"]] == ["all", "reached", "want", "not_want", "not_reached", "to_issue",
                                                    "issued_nr"]
    sk = page.context["sankey"]
    nodes = {n["key"]: n for n in sk["nodes"]}
    assert nodes["want"]["n"] == 2 and nodes["never"]["n"] == open_cases - 5
    assert len(sk["links"]) == len(sk["nodes"]) - 1
    labels = sorted((n["ly"], n["x"]) for n in sk["nodes"])
    for col in {x for _, x in labels}:  # labels in one column never overlap
        ys = [y for y, x in labels if x == col]
        assert all(b - a >= 30 for a, b in zip(ys, ys[1:]))
    want_ids = {a.case_id for a in client.get("/calls/?tab=want").context["page"].paginator.object_list}
    assert want_ids == {c2.pk, c5.pk}
    unreached = client.get("/calls/?tab=not_reached")
    assert {a.case_id for a in unreached.context["page"]} == {c1.pk, c4.pk}
    assert "ออกหมายเรียกทุกคดีที่รอออกหมาย (2)" in unreached.content.decode()
    undecided = client.get("/calls/?tab=undecided").content.decode()  # a diagram-only stage
    assert "กำลังดู:" in undecided and "ติดต่อได้ ยังไม่ระบุ" in undecided
    html = page.content.decode()
    assert "ยังไม่สิ้นสุด" in html and "สิ้นสุดแล้ว" in html and 'name="assignee"' not in html
    all_page = client.get("/calls/?tab=all&scope=all")
    assert all_page.context["page"].paginator.count == total
    assert all_page.context["scope_counts"] == {"open": open_cases, "final": total - open_cases, "all": total}
    final_page = client.get("/calls/?tab=all&scope=final")
    assert final_page.context["page"].paginator.count == total - open_cases
    assert final_page.context["counts"]["never"] == 0  # closed cases count as called
    # latest result filter, kept across tabs
    reached = client.get("/calls/?tab=all&result=reached").context["page"]
    assert {a.case_id for a in reached} == {c2.pk, c3.pk, c5.pk}
    not_reached = client.get("/calls/?tab=all&result=not_reached").context["page"]
    assert {a.case_id for a in not_reached} == {c1.pk, c4.pk}
    html = client.get("/calls/?tab=all").content.decode()
    assert "ติดต่อได้ (ไฟล์)" in html and "โทรแล้ว (ไฟล์)" in html
    off = client.get("/calls/?tab=all&result=off").context["page"]
    assert [a.case_id for a in off] == [c1.pk]
    assert "result=off" in client.get("/calls/?tab=all&result=off").content.decode()


def test_issue_every_unreached_case(client, imported, caller):
    """"ติดต่อไม่ได้" → one button sends every case still waiting for a summons to the issue page."""
    client.force_login(caller)
    Case.objects.update(contact_log="")
    c1, c2, c3 = Case.objects.not_final().order_by("receipt_no")[:3]
    record_call(c1, caller, outcome=CallOutcome.OFF)
    record_call(c2, caller, outcome=CallOutcome.NO_ANSWER)
    record_call(c3, caller, outcome=CallOutcome.APPOINTED)  # reached: not included
    resp = client.post("/calls/issue-unreached/", {"query": "tab=to_issue&scope=open"})
    assert resp.status_code == 302 and resp["Location"] == "/summons/issue/"
    assert sorted(client.session["issue_case_ids"]) == sorted([c1.pk, c2.pk])
    # a search narrows it down
    client.post("/calls/issue-unreached/", {"query": f"tab=to_issue&q={c2.receipt_no}"})
    assert client.session["issue_case_ids"] == [c2.pk]
    # nothing left: back to the list with a message
    resp = client.post("/calls/issue-unreached/", {"query": "q=ไม่มีแน่นอน"})
    assert resp.status_code == 302 and resp["Location"].startswith("/calls/")
