"""ตรวจสอบการอัปเดตสถานะสิ้นสุด: cases whose victim said they do not want to proceed (a call in this system, or
the register's contact log) while their status - as last imported from the big system - is not final yet.
The list goes to the big system's staff, who change those cases to สิ้นสุด; the next weekly import then shows
them as final and they drop off the list by themselves.

Same layout as the unit's own sheet: ลำดับ, พนักงานสอบสวน, เลขรับแจ้ง, ผู้เสียหาย, มูลค่าความเสียหาย (บาท),
สถานะในระบบปัจจุบัน, วันที่ผู้เสียหายแจ้งไม่ประสงค์ฯ, ต้องแก้เป็น - grouped by investigator, oldest first.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from django.utils import timezone

from apps.core.thai_format import THAI_MONTHS_ABBR, normalize_year_to_ce, thai_date_short, to_arabic_digits
from apps.registry.models import NOT_WANT_CASE, STATUS_OPEN_CASE, Case

TITLE = "ตรวจสอบการอัปเดตสถานะสิ้นสุด"
TARGET = "สิ้นสุด"
HEADERS = ["ลำดับ", "พนักงานสอบสวน", "เลขรับแจ้ง", "ผู้เสียหาย", "มูลค่าความเสียหาย (บาท)", "สถานะในระบบปัจจุบัน",
           "วันที่ผู้เสียหายแจ้งไม่ประสงค์ฯ", "ต้องแก้เป็น"]

_SHORT_RANK = {
    "พลตำรวจเอก": "พล.ต.อ.", "พลตำรวจโท": "พล.ต.ท.", "พลตำรวจตรี": "พล.ต.ต.", "พันตำรวจเอก (พิเศษ)": "พ.ต.อ.(พิเศษ)",
    "พันตำรวจเอก": "พ.ต.อ.", "พันตำรวจโท": "พ.ต.ท.", "พันตำรวจตรี": "พ.ต.ต.", "ร้อยตำรวจเอก": "ร.ต.อ.",
    "ร้อยตำรวจโท": "ร.ต.ท.", "ร้อยตำรวจตรี": "ร.ต.ต.", "ดาบตำรวจ": "ด.ต.", "จ่าสิบตำรวจ": "จ.ส.ต.",
    "สิบตำรวจเอก": "ส.ต.อ.", "สิบตำรวจโท": "ส.ต.ท.", "สิบตำรวจตรี": "ส.ต.ต.",
}
# "28 ต.ค. 2569", "15 ตค 69", "9 ต.ค.69" (months with or without dots)
_MONTHS = [m.replace(".", "") for m in THAI_MONTHS_ABBR]
_DATE_RE = re.compile(r"(\d{1,2})\s*(" + "|".join(r"\.?".join(m) + r"\.?" for m in _MONTHS) + r")\s*(\d{2,4})")


def short_rank(rank_full: str) -> str:
    """'พันตำรวจโทหญิง' -> 'พ.ต.ท.หญิง', 'ว่าที่พันตำรวจตรี' -> 'ว่าที่ พ.ต.ต.'."""
    rank = (rank_full or "").strip()
    lead = ""
    if rank.startswith("ว่าที่"):
        lead, rank = "ว่าที่ ", rank[len("ว่าที่"):].strip()
    female = rank.endswith("หญิง")
    rank = rank[:-len("หญิง")].strip() if female else rank
    return lead + _SHORT_RANK.get(rank, rank) + ("หญิง" if female else "")


def investigator_label(case: Case) -> str:
    inv = case.investigator
    if inv and inv.sign_name:
        return f"{short_rank(inv.rank_full)}{inv.sign_name}".strip()
    return case.investigator_raw or "-"


def date_in_log(text: str) -> dt.date | None:
    """The date written after 'ไม่ประสงค์...' in the register's contact log, if any."""
    text = to_arabic_digits(text or "")
    at = text.rfind("ไม่ประสง")
    for part in (text[at:], text) if at >= 0 else (text,):
        m = _DATE_RE.search(part)
        if m:
            day, month = int(m.group(1)), _MONTHS.index(m.group(2).replace(".", "")) + 1
            year = int(m.group(3))
            year = year + 2500 if year < 100 else year  # 69 -> 2569
            try:
                return dt.date(normalize_year_to_ce(year), month, day)
            except ValueError:
                return None
    return None


def not_want_date(case: Case) -> dt.date | None:
    a = getattr(case, "call_assignment", None)
    if a is not None and a.attempt_count:
        return timezone.localdate(a.last_call_at) if a.last_call_at else None
    return date_in_log(case.contact_log)


def pending_cases():
    return (Case.objects.filter(STATUS_OPEN_CASE).filter(NOT_WANT_CASE)
            .select_related("status", "call_assignment", "complainant", "investigator_alias__investigator"))


def rows(skip_statuses=()) -> list[dict]:
    """skip_statuses: current statuses to leave out (e.g. ส่งสำนวนอัยการ, if those are not changed to สิ้นสุด)."""
    out = []
    for case in pending_cases():
        if (case.status.name if case.status_id else "-") in skip_statuses:
            continue
        out.append({"case": case, "investigator": investigator_label(case), "receipt": case.receipt_no,
                    "name": case.victim_name or (case.complainant.full_name if case.complainant_id else ""),
                    "amount": case.damage_amount, "status": case.status.name if case.status_id else "-",
                    "date": not_want_date(case), "target": TARGET})
    far = dt.date.max
    out.sort(key=lambda r: (r["investigator"], r["date"] or far, r["receipt"]))
    for i, r in enumerate(out, start=1):
        r["no"] = i
        r["date_text"] = thai_date_short(r["date"], thai_digits=False) if r["date"] else ""
    return out


def write_xlsx(data: list[dict], path: Path, as_of: dt.date) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "สถานะสิ้นสุด"
    font, bold = Font(name="TH Sarabun New", size=14), Font(name="TH Sarabun New", size=14, bold=True)
    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append(HEADERS)
    for r in data:
        ws.append([r["no"], r["investigator"], r["receipt"], r["name"],
                   float(r["amount"]) if r["amount"] is not None else None, r["status"], r["date_text"], r["target"]])
    widths = [7, 30, 15, 28, 15, 20, 17, 11]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    centre = {1, 3, 7, 8}
    for row in ws.iter_rows():
        for cell in row:
            cell.border = box
            cell.font = bold if cell.row == 1 else font
            if cell.row == 1:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            elif cell.column in centre:
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif cell.column == 5:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="top", indent=1)
            else:
                cell.alignment = Alignment(vertical="top", wrap_text=True, indent=1)
    ws.row_dimensions[1].height = 40
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"  # the header row on every printed page
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top, ws.page_margins.bottom = 0.9, 0.7
    ws.page_margins.header, ws.page_margins.footer = 0.35, 0.3
    stamp = thai_date_short(as_of, thai_digits=False)
    ws.oddHeader.center.text = f"{TITLE} ข้อมูล ณ {stamp} ({len(data):,} คดี)"
    ws.oddFooter.right.text = "หน้า &P / &N"
    wb.properties.title = f"{TITLE}_{stamp.replace(' ', '')}"
    path = Path(path)
    wb.save(path)
    return path


def file_stem(as_of: dt.date) -> str:
    return f"{TITLE}_{thai_date_short(as_of, thai_digits=False).replace(' ', '')}"
