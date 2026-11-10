"""Weekly file from the big system (sheet "Grid") and the report sent back to it (ตรวจสอบการอัปเดตสถานะสิ้นสุด)."""
import datetime as dt
from io import BytesIO

import pytest
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from apps.calls import status_report as report
from apps.calls.models import CallOutcome
from apps.calls.services import ensure_call_pool, record_call
from apps.imports.models import ImportKind
from apps.registry.models import Case, Status

pytestmark = pytest.mark.django_db
HEAD = ["เลขรับแจ้ง", "เลขคดีอาญา", "วันที่รับแจ้ง", "ผู้เสียหาย", "เพศ", "อายุ", "ประเภทคดี", "ประเภทคดีระดับ 1",
        "ประเภทคดีระดับ 2", "ประเภทคดีระดับ 3", "ประเภทกลุ่มคดี", "รายละเอียด", "มูลค่าความเสียหาย", "บช.",
        "บก./ภ.จว.", "หน่วยที่รับผิดชอบ", "พนักงานสอบสวน", "พนักงานสืบสวน", "สถานะ", "เบอร์โทรศัพท์ผู้เสียหาย",
        "เลขอ้างอิงธนาคาร", "วันที่เริ่มโอน", "วันสุดท้ายที่โอน", "กลุ่มคดี"]
K1, K2 = "กก.1 บก.สอท.4 บช.สอท.", "กก.2 บก.สอท.4 บช.สอท."


def grid_file(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Grid"
    ws.append(HEAD)
    for r in rows:
        line = dict.fromkeys(HEAD)
        line.update(r)
        ws.append([line[h] for h in HEAD])
    path = tmp_path / "grid.xlsx"
    wb.save(path)
    return path


def row(case=None, **kw):
    base = {"เลขรับแจ้ง": case.receipt_no if case else kw.pop("receipt"), "วันที่รับแจ้ง": "05/10/2026",
            "ผู้เสียหาย": case.victim_name if case else "ผู้เสียหายใหม่", "สถานะ": "อายัดบัญชี",
            "หน่วยที่รับผิดชอบ": None, "พนักงานสอบสวน": None}
    base.update(kw)
    return base


@pytest.fixture
def cases(imported):
    Case.objects.update(contact_log="")
    a, b, c, d = Case.objects.not_final().order_by("receipt_no")[:4]
    Case.objects.filter(pk=a.pk).update(contact_log="ไม่ประสงค์ดำเนินคดี วันที่ 28 ต.ค. 2569 เวลา 10.00")
    return a, b, c, d


def run(tmp_path, rows, kind=ImportKind.UPDATE):
    from apps.imports.services import commit
    from tests.conftest import make_batch

    batch = make_batch(kind, grid_file(tmp_path, rows))
    commit(batch)
    batch.refresh_from_db()
    return batch


def test_weekly_file_updates_adds_our_new_cases_and_deletes_nothing(tmp_path, cases):
    a, b, c, d = cases
    total = Case.objects.count()
    phone_b = Case.objects.get(pk=b.pk).victim_phone
    Case.objects.filter(pk=b.pk).update(investigator_raw="สมชาย ใจดี")
    rows = [
        row(a, **{"สถานะ": "สิ้นสุด"}),  # blank unit: still ours, because it is already here
        row(b, **{"หน่วยที่รับผิดชอบ": K2, "พนักงานสอบสวน": "พ.ต.ท.สมชาย ใจดี", "อายุ": "41",
                  "เบอร์โทรศัพท์ผู้เสียหาย": None}),  # listed under another unit: updated, never removed
        row(receipt="L69100000001", **{"หน่วยที่รับผิดชอบ": K1, "มูลค่าความเสียหาย": "12500.50",
                                        "พนักงานสอบสวน": "พ.ต.ต.หญิงสุดา รักษ์ดี"}),  # new case of ours
        row(receipt="L69100000002", **{"หน่วยที่รับผิดชอบ": None}),  # not ours, no unit: skipped
        row(receipt="L69100000003", **{"หน่วยที่รับผิดชอบ": K2}),  # another unit: skipped
    ]
    batch = run(tmp_path, rows)
    s = batch.summary
    assert (s["matched"], s["new"], s["other_unit_rows"]) == (2, 1, 2)
    assert s["other_units"] == {"(ไม่ระบุหน่วย)": 1, K2: 1}
    assert Case.objects.count() == total + 1
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.status.name == "สิ้นสุด" and "28 ต.ค. 2569" in a.contact_log  # the contact log is never touched
    assert b.unit.name.startswith("กก.1") and b.age == "41" and b.victim_phone == phone_b  # blank keeps the old
    assert b.investigator_raw == "สมชาย ใจดี"  # only the rank differs: same person, nothing changed
    new = Case.objects.get(receipt_no="L69100000001")
    assert new.unit.name == "กก.1 บก.สอท.4" and new.report_date == dt.date(2026, 10, 5)
    assert new.source_sheet == "ปี 2569 (2026)" and new.investigator_raw == "หญิงสุดา รักษ์ดี"
    assert str(new.damage_amount) == "12500.50"
    assert any("อายัดบัญชี → สิ้นสุด" in c for c in s["status_changes"])
    assert not Case.objects.filter(receipt_no__in=["L69100000002", "L69100000003"]).exists()


def test_status_changed_by_staff_is_kept_while_the_big_system_still_says_the_old_one(tmp_path, cases, admin_user,
                                                                                    client):
    a = cases[0]
    final = Status.objects.get(name="สิ้นสุด")
    client.force_login(admin_user)
    client.post(f"/cases/{a.pk}/status/", {"status": final.pk})
    a.refresh_from_db()
    assert a.status_manual_at is not None
    run(tmp_path, [row(a, **{"สถานะ": a.status_file_value})])
    a.refresh_from_db()
    assert a.status == final  # kept
    run(tmp_path, [row(a, **{"สถานะ": "ส่งสำนวนอัยการ"})])  # newer information from the big system
    a.refresh_from_db()
    assert a.status.name == "ส่งสำนวนอัยการ" and a.status_manual_at is None


def test_big_system_file_picked_as_register_is_refused(tmp_path, cases, admin_user, client):
    from apps.imports.services import analyze, summary_dict
    from tests.conftest import make_batch

    rows = [row(c) for c in cases] + [row(receipt=f"L6910000010{i}") for i in range(5)]
    batch = make_batch(ImportKind.REGISTER, grid_file(tmp_path, rows))
    s = summary_dict(analyze(batch))
    assert s["looks_like_update"]
    batch.summary, batch.status = s, "analyzed"
    batch.save()
    client.force_login(admin_user)
    resp = client.post(f"/imports/{batch.pk}/confirm/", follow=True)
    assert "อัปเดตจากระบบใหญ่" in resp.content.decode()
    batch.refresh_from_db()
    assert batch.status == "analyzed"  # not imported


def test_report_lists_not_want_cases_until_the_big_system_says_final(tmp_path, cases, admin_user, client):
    a, b, c, d = cases
    ensure_call_pool()
    record_call(b, admin_user, outcome=CallOutcome.NOT_PROCEED,
                called_at=timezone.make_aware(dt.datetime(2026, 11, 2, 10, 0)))
    record_call(c, admin_user, outcome=CallOutcome.APPOINTED)  # wants to proceed: not listed
    rows = {r["receipt"]: r for r in report.rows()}
    assert a.receipt_no in rows and b.receipt_no in rows and c.receipt_no not in rows
    assert rows[a.receipt_no]["date"] == dt.date(2026, 10, 28) and rows[a.receipt_no]["date_text"] == "28 ต.ค. 69"
    assert rows[b.receipt_no]["date"] == dt.date(2026, 11, 2) and rows[b.receipt_no]["target"] == "สิ้นสุด"
    client.force_login(admin_user)
    page = client.get("/reports/status-final/")
    assert page.status_code == 200 and a.receipt_no in page.content.decode()
    xlsx = client.get("/reports/status-final/", {"format": "xlsx"})
    sheet = load_workbook(BytesIO(xlsx.content)).active
    assert [c.value for c in sheet[1]] == report.HEADERS and sheet.max_row == 1 + len(rows)
    assert "attachment" in xlsx["Content-Disposition"]
    # a status left out of the report
    status_b = Case.objects.get(pk=b.pk).status.name
    left = client.get("/reports/status-final/", {"skip": status_b}).context["rows"]
    assert all(r["status"] != status_b for r in left)
    # the big system changes them to สิ้นสุด: they drop off
    run(tmp_path, [row(a, **{"สถานะ": "สิ้นสุด"}), row(b, **{"สถานะ": "สิ้นสุด"})])
    assert not {a.receipt_no, b.receipt_no} & {r["receipt"] for r in report.rows()}


def test_report_pdf(cases, admin_user, client):
    from apps.documents.pdf import soffice_available

    if not soffice_available():
        pytest.skip("LibreOffice is not installed")
    client.force_login(admin_user)
    resp = client.get("/reports/status-final/", {"format": "pdf"})
    assert resp["Content-Type"] == "application/pdf" and resp.content.startswith(b"%PDF")


@pytest.mark.parametrize("text, expected", [
    ("ไม่ประสงค์ดำเนินคดี วันที่ 28 ต.ค. 2569 เวลา 10.00", dt.date(2026, 10, 28)),
    ("ครั้งที่ 1 ไม่สามารถติดต่อได้ เมื่อ 15 ตค 69\nไม่ประสงค์ดำเนินคดี 19 ต.ค.69", dt.date(2026, 10, 19)),
    ("ไม่ประสงค์ดำเนินคดี วันที่ ๙ ต.ค. ๒๕๖๙", dt.date(2026, 10, 9)),
    ("ไม่ประสงค์ดำเนินคดี", None),
])
def test_date_in_contact_log(text, expected):
    assert report.date_in_log(text) == expected


def test_short_rank():
    assert report.short_rank("พันตำรวจตรี") == "พ.ต.ต."
    assert report.short_rank("พันตำรวจโทหญิง") == "พ.ต.ท.หญิง"
    assert report.short_rank("ว่าที่พันตำรวจตรี") == "ว่าที่ พ.ต.ต."
    assert report.short_rank("") == ""
