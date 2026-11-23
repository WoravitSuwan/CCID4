"""สรุปผลประจำวัน: what the call staff did on one day, from the calls recorded in this system.

Two texts the staff used to type by hand every day:
- the daily summary for the commanders (จำนวน Case ID, ไม่ประสงค์ / ประสงค์ / ติดต่อไม่ได้)
- the ลงบันทึกประจำวัน text listing the victims who said they do not want to proceed

Each case called that day counts once, by the result of its last call that day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.core.thai_format import THAI_MONTHS_ABBR, ce_to_be, thai_date_full

from .models import CallAttempt, CallOutcome

NOT_WANT, WANT, UNREACHED, OTHER = "not_want", "want", "unreached", "other"
GROUP_OF = {
    CallOutcome.NOT_PROCEED: NOT_WANT,
    CallOutcome.APPOINTED: WANT, CallOutcome.INFO: WANT,
    CallOutcome.OFF: UNREACHED, CallOutcome.NO_ANSWER: UNREACHED, CallOutcome.BUSY: UNREACHED,
    CallOutcome.VOICEMAIL: UNREACHED, CallOutcome.UNKNOWN_PERSON: UNREACHED, CallOutcome.WRONG: UNREACHED,
    CallOutcome.CALLBACK: OTHER, CallOutcome.REACHED_OTHER: OTHER,
}
GROUP_LABEL = {
    NOT_WANT: "ไม่ประสงค์ดำเนินคดี",
    WANT: "ประสงค์ดำเนินคดีต่อ",
    UNREACHED: "ไม่สามารถติดต่อได้ (รวมถึง ไม่รับสาย, ปิดเครื่อง, ไม่มีหมายเลข, ปลายสายไม่รู้จัก ฯลฯ)",
    OTHER: "ติดต่อได้ แต่ยังไม่ได้ข้อยุติ (ขอให้โทรกลับ, อื่นๆ)",
}
GROUP_SHORT = {NOT_WANT: "ไม่ประสงค์ดำเนินคดี", WANT: "ประสงค์ดำเนินคดีต่อ", UNREACHED: "ไม่สามารถติดต่อได้",
               OTHER: "ยังไม่ได้ข้อยุติ"}


@dataclass
class Row:
    attempt: CallAttempt  # the last call of the day for this case
    group: str
    calls: int  # calls made to this case that day

    @property
    def case(self):
        return self.attempt.case

    @property
    def from_file(self) -> bool:
        """Written in the Excel register (บันทึกการติดต่อ), not recorded in this system."""
        return getattr(self.attempt, "from_file", False)

    @property
    def name(self) -> str:
        case = self.case
        return case.victim_name or (case.complainant.full_name if case.complainant_id else "") or "-"

    @property
    def phone(self) -> str:
        return self.attempt.phone or self.case.victim_phone or "-"


@dataclass
class Day:
    day: dt.date
    rows: list[Row] = field(default_factory=list)
    caller: object = None  # one staff member, or None for everyone

    def of(self, group: str) -> list[Row]:
        return [r for r in self.rows if r.group == group]

    @property
    def counts(self) -> dict:
        c = {g: 0 for g in GROUP_LABEL}
        for r in self.rows:
            c[r.group] += 1
        c["total"] = len(self.rows)
        return c

    @property
    def not_want(self) -> list[Row]:
        return self.of(NOT_WANT)


def _of_day(day: dt.date):
    tz = timezone.get_current_timezone()
    first = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
    after = timezone.make_aware(dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min), tz)
    return CallAttempt.objects.filter(called_at__gte=first, called_at__lt=after)


def caller_ids(day: dt.date) -> set[int]:
    return set(_of_day(day).exclude(created_by=None).values_list("created_by", flat=True))


def load(day: dt.date, caller=None) -> Day:
    """Cases called on ``day``, each once, by its last call of that day."""
    qs = _of_day(day).select_related("case__complainant", "created_by").order_by("called_at", "number", "pk")
    if caller is not None:
        qs = qs.filter(created_by=caller)
    first_at, last, calls = {}, {}, {}
    for a in qs:
        first_at.setdefault(a.case_id, a.called_at)
        last[a.case_id] = a
        calls[a.case_id] = calls.get(a.case_id, 0) + 1
    if caller is None:  # calls written in the Excel register for that day (the system's own calls come first)
        for fc in _file_contacts(day).exclude(case_id__in=list(last)):
            a = CallAttempt(case=fc.case, number=fc.number or 0, called_at=fc.called_at, outcome=fc.outcome,
                            note=fc.line, phone="")
            a.from_file = True
            first_at.setdefault(fc.case_id, a.called_at)
            last[fc.case_id] = a
            calls[fc.case_id] = calls.get(fc.case_id, 0) + 1
    rows = [Row(a, _group(a), calls[cid]) for cid, a in last.items()]
    rows.sort(key=lambda r: (first_at[r.case.pk], r.case.receipt_no))
    return Day(day, rows, caller)


def _group(a) -> str:
    if getattr(a, "from_file", False) and not a.outcome:
        return UNREACHED  # a date with no result written: counted as not reached, as on the call list
    return GROUP_OF.get(a.outcome, OTHER)


def _file_contacts(day: dt.date):
    from .models import FileContact

    return FileContact.objects.filter(day=day).select_related("case__complainant").order_by("time", "pk")


# --------------------------------------------------------------------------- texts
def short_day(day: dt.date) -> str:
    """'9 พ.ย.69' (as staff write it in the diary)."""
    return f"{day.day} {THAI_MONTHS_ABBR[day.month - 1]}{ce_to_be(day) % 100:02d}"


def unit_text() -> str:
    return settings.OWN_UNIT.replace(" ", "")  # "กก.1บก.สอท.4"


def diary_text(d: Day) -> str:
    """ลงบันทึกประจำวัน: the victims called that day who said they do not want to proceed."""
    lines = [
        f"วันนี้( {short_day(d.day)}) พนักงานสอบสวน {unit_text()}  ได้ทำการโทรศัพท์ติดต่อผู้เสียหายที่แจ้งความออนไลน์ "
        "ผ่านระบบ Thaipoliceonline.com/สายด่วน 1441 เพื่อมาให้ถ้อยคำเพิ่มเติมพร้อมนำพยานหลักฐานที่เกี่ยวข้องกับคดี"
        "มามอบให้กับพนักงานสอบสวน",
        "พบว่ามีผู้เสียหายไม่ประสงค์พบพนักงานสอบสวนและไม่ประสงค์ดำเนินคดี ดังนี้",
        "",
    ]
    for i, r in enumerate(d.not_want, start=1):
        lines.append(f"{i}. ผู้เสียหาย: {r.name} เลขรับแจ้ง: {r.case.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: {r.phone}")
    lines += ["", "จึงลงบันทึกไว้เป็นหลักฐาน เพื่อที่จะดำเนินการในส่วนที่เกี่ยวข้องต่อไป"]
    return "\n".join(lines)


def period_text(d: Day) -> str:
    """'ประจำวันที่ 9 พฤศจิกายน 2569'."""
    return f"ประจำวันที่ {thai_date_full(d.day, thai_digits=False)}"


def summary_text(d: Day) -> str:
    """The daily report for the commanders (counts only: no names or phone numbers)."""
    c = d.counts
    who = "เจ้าหน้าที่ Outsource"
    if d.caller is not None:
        who += f" ({d.caller.display_name()})"
    lines = [
        "เรียนผู้บังคับบัญชา",
        "",
        f"ขอรายงานสรุปผลการดำเนินการ ของ {who} สะสาง Case ID {period_text(d)}",
        f"จำนวน {c['total']:,} Case ID",
        f"1. {GROUP_LABEL[NOT_WANT]} จำนวน {c[NOT_WANT]:,} Case",
        f"2. {GROUP_LABEL[WANT]} จำนวน {c[WANT]:,} Case",
        f"3. {GROUP_LABEL[UNREACHED]} จำนวน {c[UNREACHED]:,} Case",
    ]
    if c[OTHER]:  # so the lines always add up to the total
        lines.append(f"4. {GROUP_LABEL[OTHER]} จำนวน {c[OTHER]:,} Case")
    lines += ["", "จึงเรียนมาเพื่อโปรดทราบ"]
    return "\n".join(lines)


def stamp(day: dt.date, caller=None) -> str:
    """Changes whenever a call of that day is recorded, edited away or deleted (the page then reloads its numbers)."""
    from django.db.models import Count, Max

    qs = _of_day(day)
    if caller is not None:
        qs = qs.filter(created_by=caller)
    agg = qs.aggregate(n=Count("pk"), last=Max("pk"), at=Max("created_at"))
    files = _file_contacts(day).aggregate(n=Count("pk"), last=Max("pk")) if caller is None else {"n": 0, "last": 0}
    return (f"{agg['n']}-{agg['last'] or 0}-{agg['at'].timestamp() if agg['at'] else 0}"
            f"-{files['n']}-{files['last'] or 0}")


def file_day(day: dt.date) -> str:
    """'9.11.2569' as in the staff's file names."""
    return f"{day.day}.{day.month}.{ce_to_be(day)}"


def file_name(stem: str, d: Day) -> str:
    """'ลงบันทึกประจำวัน 9.11.2569.txt'."""
    return f"{stem} {file_day(d.day)}.txt"


def _when(value: dt.datetime) -> str:
    """'วันที่ 9 พ.ย. 2569 เวลา 09.27 น.' (as in the register's contact log)."""
    t = timezone.localtime(value)
    return f"วันที่ {t.day} {THAI_MONTHS_ABBR[t.month - 1]} {ce_to_be(t)} เวลา {t.hour:02d}.{t.minute:02d} น."


def log_line(a: CallAttempt) -> str:
    """บันทึกการติดต่อ of one call, in the register's words:
    'ไม่ประสงค์ดำเนินคดี วันที่ 9 พ.ย. 2569 เวลา 09.27 น.',
    'ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 12 พ.ย. 2569 เวลา 15.54 น.'"""
    if getattr(a, "from_file", False):
        return a.note  # the file's own words
    group = GROUP_OF.get(a.outcome, OTHER)
    label = a.get_outcome_display()
    if group == NOT_WANT:
        text = "ไม่ประสงค์ดำเนินคดี"
    elif group == WANT:
        text = "ประสงค์ดำเนินคดี"
    elif group == UNREACHED:
        reason = label[label.index("(") + 1:label.rindex(")")] if "(" in label else label
        text = f"ครั้งที่ {a.number} ไม่สามารถติดต่อได้ ({reason})"
    else:
        text = f"ติดต่อได้ ({label.split('–', 1)[-1].strip()})"
    text += f" {_when(a.called_at)}"
    if a.outcome == CallOutcome.APPOINTED and a.appointment_at:
        text += f" นัดเข้าพบพนักงานสอบสวน{_when(a.appointment_at)}"
    return text


# --------------------------------------------------------------------------- text files (.txt)
def diary_file_text(d: Day) -> str:
    """ลงบันทึกประจำวัน: only the victims who do not want to proceed, one line each, a blank line between."""
    if not d.not_want:
        return f"ไม่มีผู้เสียหายที่แจ้งไม่ประสงค์ดำเนินคดี{period_text(d)}"
    return "\n\n".join(f"ผู้เสียหาย: {r.name} เลขรับแจ้ง: {r.case.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: {r.phone}"
                       for r in d.not_want)


def summary_file_text(d: Day) -> str:
    """สรุปข้อมูลคดีและบันทึกการติดต่อ: every case called that day, six lines each."""
    blocks = ["สรุปข้อมูลคดีและบันทึกการติดต่อ"]
    for r in d.rows:
        case = r.case
        amount = f"{case.damage_amount:,.2f} บาท" if case.damage_amount is not None else "-"
        blocks.append("\n".join([
            f"ผู้เสียหาย: {r.name}",
            f"- เลขรับแจ้ง: {case.receipt_no}",
            f"- มูลค่าความเสียหาย: {amount}",
            f"- พนักงานสอบสวน: {(case.investigator_raw or '').strip() or '-'}",
            f"- เบอร์โทรศัพท์ผู้เสียหาย: {r.phone}",
            f"- บันทึกการติดต่อ: {log_line(r.attempt)}",
        ]))
    return "\n\n".join(blocks)


FILE_TEXT = {"diary": diary_file_text, "summary": summary_file_text}
FILE_STEM = {"diary": "ลงบันทึกประจำวัน", "summary": "สรุปข้อมูลคดี"}


def text_file(kind: str, d: Day) -> bytes:
    """UTF-8 with a BOM and Windows line endings: opens right in Notepad and pastes cleanly anywhere."""
    return ("\ufeff" + FILE_TEXT[kind](d).replace("\n", "\r\n") + "\r\n").encode("utf-8")
