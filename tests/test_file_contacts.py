"""Calls written in the Excel register (บันทึกการติดต่อ) count in the daily summary and the statistics on the day
written in the file, once the file is loaded."""
import datetime as dt

import pytest
from django.utils import timezone
from openpyxl import Workbook

from apps.calls import daily_report as report
from apps.calls.file_log import parse_line, rebuild
from apps.calls.models import CallOutcome as O
from apps.calls.models import FileContact
from apps.imports.models import ImportKind
from apps.registry.models import Case

pytestmark = pytest.mark.django_db
DAY = dt.date(2026, 11, 20)


@pytest.mark.parametrize("line, day, time, outcome, number", [
    ("ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 09.27 น.", DAY, dt.time(9, 27), "not_proceed", None),
    ("ครั้งที่ 2 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 20 พ.ย. 2569 เวลา 15.54 น.", DAY, dt.time(15, 54), "no_answer", 2),
    ("ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง) 9 ตค 68 เวลา 10.30 น.", dt.date(2025, 10, 9), dt.time(10, 30), "off", 1),
    ("ประสงค์ดำเนินคดี ๒๐ พ.ย. ๖๙", DAY, None, "appointed", None),
    ("ครั้งที่ 1 ติดต่อแล้ว ขอตัดสินใจ 3 วัน วันที่ 20 พ.ย. 2569", DAY, None, "reached_other", 1),
])
def test_dated_lines_of_the_contact_log(line, day, time, outcome, number):
    p = parse_line(line)
    assert (p["day"], p["time"], p["outcome"], p["number"]) == (day, time, outcome, number)


def test_lines_without_a_date_are_left_out():
    assert parse_line("ไม่ประสงค์ดำเนินคดี") is None and parse_line("โทรแล้ว") is None


def register_file(tmp_path, rows):
    """The unit's own register with the contact log column, loaded as "อัปเดตจากระบบใหญ่"."""
    wb = Workbook()
    ws = wb.active
    ws.title = "ปี 2569 (2026)"
    head = ["เลขรับแจ้ง", "ผู้เสียหาย", "หน่วยที่รับผิดชอบ", "สถานะ", "บันทึกการติดต่อ"]
    ws.append(head)
    for r in rows:
        ws.append([r.get(h) for h in head])
    path = tmp_path / "register.xlsx"
    wb.save(path)
    return path


def load(tmp_path, rows):
    from apps.imports.services import commit
    from tests.conftest import make_batch

    batch = make_batch(ImportKind.UPDATE, register_file(tmp_path, rows), sheets=["ปี 2569 (2026)"])
    commit(batch)
    batch.refresh_from_db()
    return batch


@pytest.fixture
def four(imported):
    Case.objects.update(contact_log="")
    rebuild()
    return list(Case.objects.not_final().order_by("receipt_no")[:4])


def test_staff_file_counts_in_the_daily_summary_and_the_bot(tmp_path, four, client, admin_user):
    a, b, c, d = four
    rows = [
        {"เลขรับแจ้ง": a.receipt_no, "สถานะ": a.status.name,
         "บันทึกการติดต่อ": "ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 19 พ.ย. 2569 เวลา 10.00 น.\n"
                            "ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 09.27 น."},
        {"เลขรับแจ้ง": b.receipt_no, "สถานะ": b.status.name,
         "บันทึกการติดต่อ": "ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 11.00 น."},
        {"เลขรับแจ้ง": c.receipt_no, "สถานะ": c.status.name,
         "บันทึกการติดต่อ": "ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง) วันที่ 20 พ.ย. 2569 เวลา 13.10 น."},
        {"เลขรับแจ้ง": d.receipt_no, "สถานะ": d.status.name, "บันทึกการติดต่อ": None},  # blank: kept
    ]
    batch = load(tmp_path, rows)
    a.refresh_from_db()
    assert "ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569" in a.contact_log
    assert batch.summary["file_contacts"] == 4 and FileContact.objects.filter(day=DAY).count() == 3
    day = report.load(DAY)
    assert day.counts == {"not_want": 1, "want": 1, "unreached": 1, "other": 0, "total": 3}
    assert all(r.from_file for r in day.rows)
    assert report.load(DAY - dt.timedelta(days=1)).counts["unreached"] == 1  # the 19th line, on the 19th
    assert "จำนวน 3 Case ID" in report.summary_text(day)
    assert a.receipt_no in report.diary_text(day)
    text = report.summary_file_text(day)
    assert "- บันทึกการติดต่อ: ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 09.27 น." in text
    client.force_login(admin_user)
    html = client.get("/reports/daily/", {"date": "2026-11-20"}).content.decode()
    assert "จากไฟล์ Excel" in html and "รวมผลการโทรที่บันทึกในไฟล์ Excel" in html


def test_a_call_in_the_system_the_same_day_is_counted_once(tmp_path, four, caller_user):
    from apps.calls.services import ensure_call_pool, record_call

    a = four[0]
    ensure_call_pool()
    record_call(a, caller_user, outcome=O.BUSY, called_at=timezone.make_aware(dt.datetime(2026, 11, 20, 8, 0)))
    load(tmp_path, [{"เลขรับแจ้ง": a.receipt_no, "สถานะ": a.status.name,
                     "บันทึกการติดต่อ": "ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 09.27 น."}])
    day = report.load(DAY)
    assert day.counts["total"] == 1 and not day.rows[0].from_file  # the system's own call wins


def test_statistics_count_the_file_too(tmp_path, four, client, admin_user):
    a, b = four[:2]
    load(tmp_path, [
        {"เลขรับแจ้ง": a.receipt_no, "สถานะ": a.status.name,
         "บันทึกการติดต่อ": "ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569 เวลา 09.27 น."},
        {"เลขรับแจ้ง": b.receipt_no, "สถานะ": b.status.name,
         "บันทึกการติดต่อ": "ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 20 พ.ย. 2569 เวลา 10.00 น."},
    ])
    client.force_login(admin_user)
    ctx = client.get("/stats/", {"from": "2026-11-20", "to": "2026-11-20"}).context
    period = ctx["period"]
    assert (period["calls"], period["reached"], period["not_reached"], period["from_file"]) == (2, 1, 1, 2)
    row = next(d for d in ctx["daily"] if d["date"] == DAY)
    assert (row["calls"], row["reached"]) == (2, 1)
    assert any(p["name"].startswith("บันทึกในไฟล์ Excel") and p["calls"] == 2 for p in ctx["people"])


def test_the_big_systems_file_leaves_the_contact_log_alone(tmp_path, four):
    """Its Grid has no contact log column: nothing about calls changes."""
    from tests.test_weekly_update import row, run

    a = four[0]
    Case.objects.filter(pk=a.pk).update(contact_log="ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569")
    rebuild()
    run(tmp_path, [row(a, **{"สถานะ": "สิ้นสุด"})])
    a.refresh_from_db()
    assert a.contact_log == "ไม่ประสงค์ดำเนินคดี วันที่ 20 พ.ย. 2569" and FileContact.objects.filter(case=a).count() == 1


@pytest.fixture
def caller_user(db):
    from apps.accounts.models import Role, User

    return User.objects.create_user("caller20", password="pass12345", role=Role.OUTSOURCE)


def test_one_cell_on_one_line_holds_several_calls():
    """How the register really stores it (line breaks become spaces on import)."""
    from apps.calls.file_log import parse_log
    from apps.calls.services import outcome_from_log

    text = ("ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 19 พ.ย. 2569 เวลา 10.00 น. "
            "ครั้งที่ 2 ไม่สามารถติดต่อได้ (ปิดเครื่อง) วันที่ 20 พ.ย. 2569 เวลา 14.05 น. ไม่ประสงค์ดำเนินคดี 20 พ.ย. 69")
    entries = parse_log(text)
    assert [(e["day"].day, e["time"], e["outcome"]) for e in entries] == [
        (19, dt.time(10, 0), "no_answer"), (20, dt.time(14, 5), "off"), (20, None, "not_proceed")]
    assert outcome_from_log(text) == "not_proceed"  # the latest entry
    assert parse_log("9 พ.ย. 2569 เวลา 10.00 น.")[0]["outcome"] == ""  # a date only: no result written


@pytest.mark.parametrize("text, day, time", [
    ("28/10/2026 10:15", dt.date(2026, 10, 28), dt.time(10, 15)),
    ("28/10/2569 10:15", dt.date(2026, 10, 28), dt.time(10, 15)),
    ("28/10/2026", dt.date(2026, 10, 28), None),
])
def test_excel_date_cells(text, day, time):
    """A date/time cell becomes '28/10/2026 10:15': a call that day with no result written."""
    from apps.calls.file_log import parse_log

    [entry] = parse_log(text)
    assert (entry["day"], entry["time"], entry["outcome"]) == (day, time, "")
    d = report.Day(day)
    assert d.counts["total"] == 0  # (sanity: an empty day)
