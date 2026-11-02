"""Supported placeholders and how their values are built."""
from __future__ import annotations

import datetime as dt

from django.utils import timezone

from apps.core.thai_format import be_year_2, thai_date_full, thai_day, thai_month_name, thai_time, to_thai_digits

# name -> description (shown in the template settings page)
SUMMONS_PLACEHOLDERS = {
    "วันปัจจุบัน": "วันที่ออกหมาย (เลขไทย เช่น ๙)",
    "เดือนปัจจุบัน": "เดือนที่ออกหมาย (ชื่อเต็ม เช่น กุมภาพันธ์)",
    "ปีปัจจุบัน": "ปี พ.ศ. ที่ออกหมาย 2 หลักท้าย (เช่น ๖๙)",
    "วันที่ออกหมาย": "วันที่ออกหมายแบบเต็ม (เช่น ๙ กุมภาพันธ์ ๒๕๖๙)",
    "ชื่อผู้แจ้ง": "คำนำหน้า + ชื่อ + นามสกุล ของผู้ถูกเรียก",
    "ที่อยู่": "ที่อยู่ผู้ถูกเรียก รวมรหัสไปรษณีย์",
    "ที่อยู่ไม่รวมรหัส": "ที่อยู่ผู้ถูกเรียก ไม่รวมรหัสไปรษณีย์",
    "รหัสไปรษณีย์": "รหัสไปรษณีย์ผู้ถูกเรียก",
    "เลขรับแจ้ง": "เลขรับแจ้งความออนไลน์",
    "เลขคดีอาญา": "เลขคดีอาญา (ถ้ามี)",
    "ครั้งที่": "หมายเรียกครั้งที่",
    "วันนัด": "วันที่นัด (เลขไทย)",
    "เดือนนัด": "เดือนที่นัด (ชื่อเต็ม)",
    "ปีนัด": "ปี พ.ศ. ที่นัด 2 หลักท้าย",
    "วันที่นัด": "วันที่นัดแบบเต็ม",
    "เวลานัด": "เวลานัด (เช่น ๐๙.๐๐)",
    "สถานที่นัด": "สถานที่ให้มาพบ (จากการตั้งค่าหน่วย)",
    "เหตุที่เรียก": "เหตุที่เรียก",
    "ยศผู้ออกหมาย": "ยศเต็มของพนักงานสอบสวนเจ้าของคดี",
    "ชื่อผู้ออกหมาย": "ชื่อ-สกุลที่ใช้ลงนาม",
    "ตำแหน่งผู้ออกหมาย": "ตำแหน่งผู้ออกหมาย",
    "โทรศัพท์ผู้ออกหมาย": "โทรศัพท์ผู้ออกหมาย",
    "ชื่อหน่วย": "ชื่อหน่วยเต็ม (ผู้ส่ง)",
    "ที่อยู่หน่วย": "ที่อยู่ผู้ส่ง (บนซอง)",
}
ENVELOPE_PLACEHOLDERS = SUMMONS_PLACEHOLDERS


def next_weekday(day: dt.date) -> dt.date:
    """Saturday/Sunday -> following Monday."""
    if day.weekday() == 5:
        return day + dt.timedelta(days=2)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


def default_appointment_date(issue_date: dt.date, days: int = 14) -> dt.date:
    return next_weekday(issue_date + dt.timedelta(days=days))


def build_values(*, case, issue_date: dt.date, appointment_at: dt.datetime, place: str, reason: str,
                 issuer, number: int, thai_digits: bool = True) -> dict:
    """All placeholder values for one summons. Dates are always Thai digits."""
    data = (lambda s: to_thai_digits(s)) if thai_digits else (lambda s: s or "")
    addr = case.effective_address()
    full_address = addr.format(include_postcode=True) if addr else ""
    short_address = addr.format(include_postcode=False) if addr else ""
    postcode = addr.postcode if addr else ""
    unit = case.unit
    local_appt = timezone.localtime(appointment_at) if timezone.is_aware(appointment_at) else appointment_at
    return {
        "วันปัจจุบัน": thai_day(issue_date),
        "เดือนปัจจุบัน": thai_month_name(issue_date.month),
        "ปีปัจจุบัน": be_year_2(issue_date),
        "วันที่ออกหมาย": thai_date_full(issue_date),
        "ชื่อผู้แจ้ง": data(case.recipient_name()),
        "ที่อยู่": data(full_address),
        "ที่อยู่ไม่รวมรหัส": data(short_address),
        "รหัสไปรษณีย์": data(postcode),
        "เลขรับแจ้ง": data(case.receipt_no),
        "เลขคดีอาญา": data(case.criminal_case_no) or "-",
        "ครั้งที่": to_thai_digits(number),
        "วันนัด": thai_day(local_appt.date()),
        "เดือนนัด": thai_month_name(local_appt.month),
        "ปีนัด": be_year_2(local_appt.date()),
        "วันที่นัด": thai_date_full(local_appt.date()),
        "เวลานัด": thai_time(local_appt.time()),
        "สถานที่นัด": data(place),
        "เหตุที่เรียก": data(reason),
        "ยศผู้ออกหมาย": issuer.rank_full if issuer else "",
        "ชื่อผู้ออกหมาย": data(issuer.sign_name) if issuer else "",
        "ตำแหน่งผู้ออกหมาย": data(issuer.position) if issuer else "",
        "โทรศัพท์ผู้ออกหมาย": data(issuer.phone) if issuer else "",
        "ชื่อหน่วย": data(unit.display_full_name) if unit else "",
        "ที่อยู่หน่วย": data(unit.sender_address) if unit else "",
    }


# ป.วิ.อ. มาตรา 53: what a summons must contain -> placeholders that carry it
SECTION_53 = [
    ("วันที่ออกหมาย", ["วันปัจจุบัน", "เดือนปัจจุบัน", "ปีปัจจุบัน"]),
    ("ชื่อผู้ถูกเรียก", ["ชื่อผู้แจ้ง"]),
    ("ที่อยู่ผู้ถูกเรียก", ["ที่อยู่"]),
    ("เหตุที่เรียก", ["เหตุที่เรียก"]),
    ("สถานที่ที่ให้มา", ["สถานที่นัด"]),
    ("วันเวลาที่ให้มา", ["วันนัด", "เดือนนัด", "ปีนัด", "เวลานัด"]),
    ("ลายมือชื่อและตำแหน่งผู้ออกหมาย", ["ยศผู้ออกหมาย", "ชื่อผู้ออกหมาย", "ตำแหน่งผู้ออกหมาย"]),
]
# Texts the template itself must contain (fixed wording).
SECTION_53_TEMPLATE_TEXT = [
    ("สถานที่ออกหมาย", "สถานที่ออกหมาย"),
]
# Fixed wording that can replace a placeholder in an older template version
FIXED_EQUIVALENTS = {
    "เหตุที่เรียก": ["ฉะนั้นให้ท่าน"],
    "สถานที่นัด": ["ไป ณ ที่"],
    "เวลานัด": ["นาฬิกา"],
}


def check_section_53(values: dict, template_info: dict, template_text: str) -> list[str]:
    """Return missing items required by ป.วิ.อ. ม.53 (empty list = complete)."""
    used = set(template_info.get("placeholders", []))
    problems = []
    for label, names in SECTION_53:
        for name in names:
            in_template = name in used
            if not in_template:
                fixed = FIXED_EQUIVALENTS.get(name, [])
                if fixed and all(f in template_text for f in fixed):
                    continue  # the older form prints this part as fixed text
                problems.append(f"แบบฟอร์มไม่มีช่อง {label} ({{{name}}})")
            elif not str(values.get(name, "")).strip():
                problems.append(f"ไม่มีข้อมูล {label} ({{{name}}})")
    for label, text in SECTION_53_TEMPLATE_TEXT:
        if text not in template_text:
            problems.append(f"แบบฟอร์มไม่มี {label}")
    return problems
