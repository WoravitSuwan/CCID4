"""Thai Post tracking numbers (UPU S10): 2 letters + 8 digits + check digit + 2-letter country.

EMS numbers start with E (EB, EE, EF...), registered mail with R (RB, RR...). The check digit
catches most typing mistakes, so a wrong number is refused instead of saved.
"""
from __future__ import annotations

import re

S10 = re.compile(r"^([A-Z]{2})(\d{8})(\d)([A-Z]{2})$")
WEIGHTS = (8, 6, 4, 2, 3, 5, 9, 7)


def normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", (value or "")).upper()


def check_digit(serial: str) -> int:
    rest = 11 - sum(int(d) * w for d, w in zip(serial, WEIGHTS)) % 11
    return {10: 0, 11: 5}.get(rest, rest)


def validate(value: str) -> tuple[str, str]:
    """(normalized number, error message or '')."""
    number = normalize(value)
    m = S10.match(number)
    if not m:
        return number, "รูปแบบเลขไม่ถูกต้อง ต้องเป็นตัวอักษร 2 ตัว ตัวเลข 9 หลัก และ TH เช่น EB123456785TH"
    if check_digit(m.group(2)) != int(m.group(3)):
        return number, "เลขตรวจสอบ (หลักที่ 9) ไม่ตรง น่าจะพิมพ์ผิด ตรวจเลขจากใบรับฝากอีกครั้ง"
    return number, ""
