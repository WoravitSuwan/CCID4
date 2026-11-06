"""States that real data runs into: an empty system, a case with nothing linked, odd text in the register."""
from pathlib import Path

import pytest
from django.template import engines
from django.test import Client

from apps.accounts.models import Role, User
from tests.test_smoke import all_patterns

pytestmark = pytest.mark.django_db
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_every_template_compiles():
    engine = engines["django"]
    names = [str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.html")]
    assert len(names) > 40
    for name in names:
        engine.get_template(name)  # raises TemplateSyntaxError on a broken tag


def test_empty_system_every_list_page_opens(admin_user):
    """Before anything is imported: no cases, no summonses, no signers, no templates in use."""
    caller = User.objects.create_user("empty_call", password="x" * 12, role=Role.OUTSOURCE)
    for user in (admin_user, caller):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        bad = []
        for pattern, name in all_patterns():
            if "<" in pattern or name in ("logout", "healthz"):
                continue
            status = client.get("/" + pattern).status_code
            if status >= 500:
                bad.append(f"/{pattern} -> {status}")
        assert bad == [], user.username


def test_case_with_nothing_linked(imported, admin_user):
    """No complainant match, no status, no investigator, no unit, no name, no phone, no date."""
    from apps.calls.services import ensure_call_pool
    from apps.registry.models import Case

    case = Case.objects.order_by("receipt_no").first()
    Case.objects.filter(pk=case.pk).update(
        complainant=None, status=None, investigator_alias=None, unit=None, victim_name="", victim_phone="",
        report_date=None, damage_amount=None, contact_log="", case_type="", investigator_raw="")
    ensure_call_pool()
    caller = User.objects.create_user("bare_call", password="x" * 12, role=Role.OUTSOURCE)
    for user, urls in ((admin_user, [f"/cases/{case.pk}/", f"/cases/?q={case.receipt_no}", "/cases/",
                                     f"/calls/{case.pk}/", f"/calls/?q={case.receipt_no}", "/stats/", "/"]),
                       (caller, [f"/calls/{case.pk}/", f"/calls/?q={case.receipt_no}", "/calls/", "/stats/"])):
        client = Client(raise_request_exception=False)
        client.force_login(user)
        for url in urls:
            assert client.get(url).status_code == 200, (user.username, url)
    # asking for a summons explains what is missing instead of failing
    case.refresh_from_db()
    blockers = case.issue_blockers()
    assert any("จับคู่" in b for b in blockers) and any("พนักงานสอบสวน" in b for b in blockers)
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    resp = client.post("/summons/issue/start/", {"case_ids": [case.pk]}, follow=True)
    assert resp.status_code == 200


@pytest.mark.parametrize("log", [
    "", "   ", "02/03/2023 15:39", "ไม่ประสงค์ดำเนินคดี", "ประสงค์ดำเนินคดี", "ไม่สามารถติดดต่อได้ (พิมพ์ผิด)",
    "ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง)\nครั้งที่ 2 ติดต่อได้ ประสงค์ดำเนินคดี", "<script>alert(1)</script>",
    "x" * 5000,
])
def test_odd_contact_logs_are_shown_safely(imported, admin_user, log):
    from apps.calls import funnel
    from apps.calls.models import CallAssignment
    from apps.calls.services import ensure_call_pool
    from apps.registry.models import Case

    case = Case.objects.order_by("receipt_no").first()
    Case.objects.filter(pk=case.pk).update(contact_log=log)
    ensure_call_pool()
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    for url in ("/calls/", f"/calls/?q={case.receipt_no}", f"/calls/{case.pk}/", f"/cases/{case.pk}/"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert "<script>alert(1)</script>" not in resp.content.decode()  # always escaped
    n = funnel.counts(funnel.with_summons_flag(CallAssignment.objects.all()))
    assert funnel.adds_up(n)


def test_search_with_odd_input_does_not_crash(imported, admin_user):
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    for q in ("'", '"', "%", "_", "\\", "ก" * 300, "C66 0611 953", "1 OR 1=1", "<b>x</b>"):
        for url in ("/calls/", "/cases/", "/summons/"):
            assert client.get(url, {"q": q}).status_code == 200, (url, q)
    for params in ({"tab": "ไม่มี"}, {"page": "abc"}, {"page": "99999"}, {"result": "zzz"}, {"range": "-5"},
                   {"from": "2026-13-45"}, {"from": "2030-01-01", "to": "2020-01-01"}, {"to": "x"},
                   {"issuer": "abc"}, {"investigator": "abc"}, {"assignee": "abc"}, {"status": "abc"},
                   {"ems": "zzz"}, {"sort": "zzz"}, {"scope": "zzz"}, {"format": "zzz"}):
        for url in ("/calls/", "/stats/", "/cases/", "/summons/", "/calls/export.xlsx", "/exports/"):
            assert client.get(url, params).status_code == 200, (url, params)
    for params in ({"user": "x"}, {"action": "zzz"}, {"q": "'"}, {"page": "-1"}):
        assert client.get("/settings/audit/", params).status_code == 200, params


def _xlsx(rows, sheet="ปี 2569 (2026)"):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("name, content", [
    ("ทะเบียน.xlsx", b"this is not an excel file"),
    ("ว่าง.xlsx", b""),
    ("หัวตารางเท่านั้น.xlsx", None),  # header row only
    ("คอลัมน์ไม่รู้จัก.xlsx", "unknown"),
])
def test_bad_import_files_give_a_message(admin_user, name, content):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.imports.models import ImportBatch

    if content is None:
        content = _xlsx([["ลำดับ", "เลขรับแจ้ง", "สถานะ", "หน่วยที่รับผิดชอบ"]])
    elif content == "unknown":
        content = _xlsx([["ก", "ข", "ค"], [1, 2, 3]])
    client = Client(raise_request_exception=False)
    client.force_login(admin_user)
    resp = client.post("/imports/", {"kind": "register", "file": SimpleUploadedFile(name, content)}, follow=True)
    assert resp.status_code == 200
    batch = ImportBatch.objects.order_by("-pk").first()
    if batch is None:  # refused by the upload form
        return
    # walk through the wizard: every step answers, none crashes, and nothing is imported by mistake
    for url in (f"/imports/{batch.pk}/sheets/", f"/imports/{batch.pk}/mapping/", f"/imports/{batch.pk}/preview/"):
        assert client.get(url, follow=True).status_code == 200, url
    for url in (f"/imports/{batch.pk}/sheets/", f"/imports/{batch.pk}/mapping/"):
        assert client.post(url, {}, follow=True).status_code == 200, url
    assert client.post(f"/imports/{batch.pk}/confirm/", follow=True).status_code == 200
    from apps.registry.models import Case

    assert not Case.objects.exists()
