"""Thai number/date formatting helpers used by documents and the UI."""
from __future__ import annotations

import datetime as dt

THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
_TO_THAI = str.maketrans("0123456789", THAI_DIGITS)
_TO_ARABIC = str.maketrans(THAI_DIGITS, "0123456789")

THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]
THAI_MONTHS_ABBR = [
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]
BE_OFFSET = 543


def to_thai_digits(value) -> str:
    """Convert Arabic digits in ``value`` to Thai digits. ``None`` -> ''."""
    if value is None:
        return ""
    return str(value).translate(_TO_THAI)


def to_arabic_digits(value) -> str:
    if value is None:
        return ""
    return str(value).translate(_TO_ARABIC)


def thai_month_name(month: int) -> str:
    if not 1 <= int(month) <= 12:
        raise ValueError(f"invalid month: {month}")
    return THAI_MONTHS[int(month) - 1]


def thai_month_abbr(month: int) -> str:
    if not 1 <= int(month) <= 12:
        raise ValueError(f"invalid month: {month}")
    return THAI_MONTHS_ABBR[int(month) - 1]


def _year_of(value) -> int:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.year
    return int(value)


def ce_to_be(value) -> int:
    """Gregorian (ค.ศ.) year or date -> Buddhist Era (พ.ศ.) year."""
    return _year_of(value) + BE_OFFSET


def be_to_ce(year: int) -> int:
    return int(year) - BE_OFFSET


def normalize_year_to_ce(year: int) -> int:
    """Accept either a ค.ศ. or พ.ศ. year and return ค.ศ. (years > 2400 are พ.ศ.)."""
    year = int(year)
    return be_to_ce(year) if year > 2400 else year


def be_year_2(value, thai_digits: bool = True) -> str:
    """Last two digits of the พ.ศ. year, e.g. 2026 -> '๖๙'."""
    text = f"{ce_to_be(value) % 100:02d}"
    return to_thai_digits(text) if thai_digits else text


def thai_day(value: dt.date, thai_digits: bool = True) -> str:
    text = str(value.day)
    return to_thai_digits(text) if thai_digits else text


def thai_date_full(value: dt.date, thai_digits: bool = True) -> str:
    """2026-02-09 -> '๙ กุมภาพันธ์ ๒๕๖๙'."""
    text = f"{value.day} {thai_month_name(value.month)} {ce_to_be(value)}"
    return to_thai_digits(text) if thai_digits else text


def thai_date_short(value: dt.date, thai_digits: bool = True) -> str:
    """2026-02-09 -> '๙ ก.พ. ๖๙'."""
    text = f"{value.day} {thai_month_abbr(value.month)} {ce_to_be(value) % 100:02d}"
    return to_thai_digits(text) if thai_digits else text


def thai_time(value: dt.time, thai_digits: bool = True) -> str:
    """09:30 -> '๐๙.๓๐'."""
    text = f"{value.hour:02d}.{value.minute:02d}"
    return to_thai_digits(text) if thai_digits else text


def thai_datetime_full(value: dt.datetime, thai_digits: bool = True) -> str:
    return f"{thai_date_full(value, thai_digits)} เวลา {thai_time(value, thai_digits)} น."
