"""EMS / registered-mail number per summons, as proof that it was sent."""
import pytest

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.documents import ems
from apps.documents.models import Summons
from apps.documents.services import issue, plan_issue
from tests.test_summons import issuable_cases, opts

pytestmark = pytest.mark.django_db


def test_s10_check_digit():
    assert ems.validate("RR473124829GB") == ("RR473124829GB", "")  # UPU example
    assert ems.validate(" eb 1234 5678 5 th ") == ("EB123456785TH", "")
    assert "เลขตรวจสอบ" in ems.validate("EB123456789TH")[1]  # one wrong digit
    assert "รูปแบบ" in ems.validate("12345")[1]


@pytest.fixture
def issued(setup_signers, admin_user):
    return issue(admin_user, plan_issue(admin_user, issuable_cases()[:3], opts()), opts())


def test_caller_records_ems_inline(client, issued):
    caller = User.objects.create_user("call_ems", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(caller)
    s = issued[0]
    page = client.get("/summons/").content.decode()
    assert "เลข EMS" in page and f"/summons/{s.pk}/ems/" in page and "<form hx-post" not in page
    assert f'name="ems_{s.pk}"' in page
    others = {f"ems_{x.pk}": "" for x in issued if x.pk != s.pk}  # htmx also sends the other (empty) boxes
    bad = client.post(f"/summons/{s.pk}/ems/", {**others, f"ems_{s.pk}": "EB123456789TH"}, HTTP_HX_REQUEST="true")
    assert "เลขตรวจสอบ" in bad.content.decode() and not Summons.objects.get(pk=s.pk).ems_number
    ok = client.post(f"/summons/{s.pk}/ems/", {**others, f"ems_{s.pk}": "eb123456785th"}, HTTP_HX_REQUEST="true")
    s.refresh_from_db()
    assert s.ems_number == "EB123456785TH" and s.ems_by == caller and s.ems_at
    assert "track.thailandpost.co.th/?trackNumber=EB123456785TH" in ok.content.decode()
    assert AuditLog.objects.filter(action="edit", detail__contains="EB123456785TH").exists()
    # the same number cannot go on a second summons
    dup = client.post(f"/summons/{issued[1].pk}/ems/", {"ems": "EB123456785TH"}, HTTP_HX_REQUEST="true")
    assert "ใช้กับหมายเรียก" in dup.content.decode()
    # filters
    assert [x.pk for x in client.get("/summons/?ems=yes").context["page"]] == [s.pk]
    assert s.pk not in [x.pk for x in client.get("/summons/?ems=no").context["page"]]
    # shown on the call page too
    assert "EB123456785TH" in client.get(f"/calls/{s.case_id}/").content.decode()
    # remove
    client.post(f"/summons/{s.pk}/ems/", {"ems": ""}, HTTP_HX_REQUEST="true")
    assert not Summons.objects.get(pk=s.pk).ems_number


def test_bulk_paste(client, admin_user, issued):
    client.force_login(admin_user)
    lines = "\n".join([f"{issued[0].case.receipt_no}\tEB123456785TH",
                       f"{issued[1].case.receipt_no} RR473124829GB",
                       f"{issued[2].case.receipt_no} EB123456789TH",  # wrong check digit
                       "สวัสดี"])
    page = client.post("/summons/ems/", {"lines": lines})
    rows = page.context["rows"]
    assert [bool(r["error"]) for r in rows] == [False, False, True, True]
    assert Summons.objects.get(pk=issued[0].pk).ems_number == "EB123456785TH"
    assert Summons.objects.get(pk=issued[1].pk).ems_number == "RR473124829GB"
    assert not Summons.objects.get(pk=issued[2].pk).ems_number
    assert "EB123456785TH" in client.get(f"/cases/{issued[0].case_id}/").content.decode()


def test_scan_station_envelope_then_ems(client, issued):
    from apps.documents.barcodes import code128_png, summons_code

    caller = User.objects.create_user("scanner", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(caller)
    s = issued[0]
    assert client.get("/summons/scan/").status_code == 200
    code = summons_code(s)
    assert code == f"9{s.pk:07d}" and code128_png(code).startswith(b"\x89PNG")
    found = client.post("/summons/scan/lookup/", {"code": code}).json()
    assert found["ok"] and found["id"] == s.pk and found["receipt"] == s.case.receipt_no
    by_receipt = client.post("/summons/scan/lookup/", {"code": f"{s.case.receipt_no}-{s.number}"}).json()
    assert by_receipt["id"] == s.pk
    ems_first = client.post("/summons/scan/lookup/", {"code": "EB123456785TH"}).json()
    assert not ems_first["ok"] and "ยิงบาร์โค้ดบนซองก่อน" in ems_first["error"]
    wrong = client.post("/summons/scan/save/", {"id": s.pk, "ems": code}).json()
    assert not wrong["ok"] and "บาร์โค้ดซอง" in wrong["error"]
    saved = client.post("/summons/scan/save/", {"id": s.pk, "ems": "EB123456785TH"}).json()
    assert saved["ok"] and saved["ems"] == "EB123456785TH"
    s.refresh_from_db()
    assert s.ems_number == "EB123456785TH" and s.ems_by == caller


def test_envelope_has_letter_number_and_barcode(issued, tmp_path):
    import zipfile

    from apps.core.models import SystemSetting
    from apps.documents import docx_engine
    from apps.documents.services import render_envelope

    setting = SystemSetting.get()
    setting.letter_number = "ตช.0039.52/1548"
    setting.save()
    out = render_envelope(issued[0], tmp_path / "env.docx")
    text = docx_engine.document_text(out)
    assert "เลขที่หนังสือ: ตช.0039.52/1548 ลงวันที่: 9 กุมภาพันธ์ 2569" in text
    assert "[[BARCODE]]" not in text and f"{issued[0].case.receipt_no}-1" in text
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(out).namelist())  # barcode picture
