"""Calls written in the Excel register's "บันทึกการติดต่อ" (outside this system), one FileContact per dated entry:
'ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 12 พ.ย. 2569 เวลา 15.54 น.' -> 12 Nov 2026 15:54, no_answer.
The register keeps every call in one cell, often on one line, so entries are split at each date (and the time
after it), not at line breaks. Entries without a date cannot be put on a day and are skipped."""
from __future__ import annotations

import datetime as dt
import re

from apps.core.thai_format import normalize_year_to_ce, to_arabic_digits

from .models import CallOutcome

_TIME_RE = re.compile(r"เวลา\s*(\d{1,2})\s*[.:]\s*(\d{2})")
_NUMBER_RE = re.compile(r"ครั้งที่\s*(\d{1,3})")
_REACHED_WORDS = re.compile(r"ติดต่อ\s*(ได้|แล้ว)")


# '28/10/2026 10:15' (a date/time cell written by Excel; พ.ศ. or ค.ศ.)
_NUM_DATE = r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])"
_NUM_DATE_RE = re.compile(_NUM_DATE)
_CLOCK_RE = re.compile(r"(?<![\d.:])(\d{1,2})\s*[.:]\s*(\d{2})(?![\d.:/])")


def _date(line: str) -> dt.date | None:
    from .status_report import _DATE_RE, _MONTHS

    m = _DATE_RE.search(line)
    if m:
        day, month, year = int(m.group(1)), _MONTHS.index(m.group(2).replace(".", "")) + 1, int(m.group(3))
    else:
        m = _NUM_DATE_RE.search(line)
        if not m:
            return None
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = year + 2500 if year < 100 else year
    try:
        return dt.date(normalize_year_to_ce(year), month, day)
    except ValueError:
        return None


def parse_line(line: str) -> dict | None:
    """{'day', 'time', 'number', 'outcome', 'line'} for a dated line, else None."""
    from .services import outcome_from_log

    text = to_arabic_digits(line.strip())
    day = _date(text)
    if day is None:
        return None
    t = _TIME_RE.search(text)
    if not t:  # '28/10/2026 10:15'
        after = _NUM_DATE_RE.search(text)
        t = _CLOCK_RE.search(text, after.end()) if after else None
    time = None
    if t and int(t.group(1)) < 24 and int(t.group(2)) < 60:
        time = dt.time(int(t.group(1)), int(t.group(2)))
    n = _NUMBER_RE.search(text)
    outcome = outcome_from_log(text) or ""
    if not outcome and _REACHED_WORDS.search(text) and "ไม่สามารถ" not in text:
        outcome = CallOutcome.REACHED_OTHER  # reached, result written in words
    return {"day": day, "time": time, "number": int(n.group(1)) if n else None, "outcome": outcome,
            "line": line.strip()[:2000]}


def _segment_end():
    from .status_report import _DATE_RE

    return re.compile(f"(?:{_DATE_RE.pattern}|{_NUM_DATE})" + r"(?:\s*(?:เวลา)?\s*\d{1,2}\s*[.:]\s*\d{2}\s*(?:น\.?)?)?")


def segments(text: str) -> list[str]:
    """One entry per call: everything up to and including its date (and time); a last entry without a date
    stays as it is. 'ครั้งที่ 1 ปิดเครื่อง 9 ตค 68 ครั้งที่ 2 ไม่รับสาย 10 ตค 68' -> two entries."""
    text = to_arabic_digits(text or "")
    out, pos = [], 0
    for m in _segment_end().finditer(text):
        part = text[pos:m.end()].strip(" ,;/\n\t")
        if part:
            out.append(part)
        pos = m.end()
    rest = text[pos:].strip(" ,;/\n\t")
    if rest:
        out.append(rest)
    return out


def parse_log(text: str) -> list[dict]:
    return [p for p in (parse_line(part) for part in segments(text)) if p]


def rebuild(case_model=None, contact_model=None, case_ids=None) -> int:
    """Rebuild FileContact from the cases' contact logs (all cases, or case_ids). Returns rows written.
    The models can be passed in (a data migration gives its own)."""
    from django.db import transaction

    if case_model is None:
        from apps.registry.models import Case as case_model  # noqa: N813
    if contact_model is None:
        from .models import FileContact as contact_model  # noqa: N813
    cases = case_model.objects.exclude(contact_log="")
    old = contact_model.objects.all()
    if case_ids is not None:
        cases, old = cases.filter(pk__in=case_ids), old.filter(case_id__in=case_ids)
    rows = []
    for pk, log in cases.values_list("pk", "contact_log").iterator(chunk_size=2000):
        rows += [contact_model(case_id=pk, **p) for p in parse_log(log)]
    with transaction.atomic():
        old.delete()
        contact_model.objects.bulk_create(rows, batch_size=2000)
    return len(rows)
