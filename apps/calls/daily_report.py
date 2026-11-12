"""สรุปผลประจำวัน: what the call staff did on one day, from the calls recorded in this system.

Two texts the staff used to type by hand every day:
- the daily summary for the commanders (จำนวน Case ID, ไม่ประสงค์ / ประสงค์ / ติดต่อไม่ได้)
- the ลงบันทึกประจำวัน text listing the victims who said they do not want to proceed

Each case called that day counts once, by the result of its last call that day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

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
    rows = [Row(a, GROUP_OF.get(a.outcome, OTHER), calls[cid]) for cid, a in last.items()]
    rows.sort(key=lambda r: (first_at[r.case.pk], r.case.receipt_no))
    return Day(day, rows, caller)


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
        "เรียนผู้บังคับบัญชาเพื่อโปรดทราบ",
        f"ขอรายงานสรุปผลการดำเนินการ ของ {who} สะสาง Case ID {period_text(d)}",
        f"จำนวน {c['total']:,} Case ID",
        f"1. {GROUP_LABEL[NOT_WANT]} จำนวน {c[NOT_WANT]:,} Case",
        f"2. {GROUP_LABEL[WANT]} จำนวน {c[WANT]:,} Case",
        f"3. {GROUP_LABEL[UNREACHED]} จำนวน {c[UNREACHED]:,} Case",
    ]
    if c[OTHER]:  # so the lines always add up to the total
        lines.append(f"4. {GROUP_LABEL[OTHER]} จำนวน {c[OTHER]:,} Case")
    return "\n".join(lines)


def file_day(day: dt.date) -> str:
    """'9.11.2569' as in the staff's file names."""
    return f"{day.day}.{day.month}.{ce_to_be(day)}"


def file_name(stem: str, d: Day) -> str:
    """'ลงบันทึกประจำวัน 9.11.2569.docx'."""
    return f"{stem} {file_day(d.day)}.docx"


def _when(value: dt.datetime) -> str:
    """'วันที่ 9 พ.ย. 2569 เวลา 09.27 น.' (as in the register's contact log)."""
    t = timezone.localtime(value)
    return f"วันที่ {t.day} {THAI_MONTHS_ABBR[t.month - 1]} {ce_to_be(t)} เวลา {t.hour:02d}.{t.minute:02d} น."


def log_line(a: CallAttempt) -> str:
    """บันทึกการติดต่อ of one call, in the register's words:
    'ไม่ประสงค์ดำเนินคดี วันที่ 9 พ.ย. 2569 เวลา 09.27 น.',
    'ครั้งที่ 1 ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 12 พ.ย. 2569 เวลา 15.54 น.'"""
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


# --------------------------------------------------------------------------- Word files
FONT = "TH SarabunPSK"


def _document(margins_cm=(1.25, 1.0, 2.54, 2.54)):
    """A4, TH SarabunPSK 16 (Thai text needs the complex-script font and size set too)."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    left, right, top, bottom = margins_cm
    for section in doc.sections:
        section.page_height, section.page_width = Cm(29.7), Cm(21.0)
        section.left_margin, section.right_margin = Cm(left), Cm(right)
        section.top_margin, section.bottom_margin = Cm(top), Cm(bottom)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = FONT, Pt(16)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)
    size_cs = rpr.find(qn("w:szCs"))
    if size_cs is None:
        size_cs = rpr.makeelement(qn("w:szCs"), {})
        rpr.append(size_cs)
    size_cs.set(qn("w:val"), "32")
    style.paragraph_format.space_after = Pt(0)
    return doc


def _run(paragraph, text: str, bold: bool = False, size: int | None = None):
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run = paragraph.add_run(text)
    rpr = run._element.get_or_add_rPr()
    if bold:
        run.bold = True
        rpr.append(rpr.makeelement(qn("w:bCs"), {}))
    if size:
        run.font.size = Pt(size)
        rpr.append(rpr.makeelement(qn("w:szCs"), {qn("w:val"): str(size * 2)}))
    return run


def write_diary_docx(d: Day, path: Path) -> Path:
    """ลงบันทึกประจำวัน: one line per victim who does not want to proceed, a blank line between."""
    doc = _document()
    for r in d.not_want:
        p = doc.add_paragraph()
        _run(p, f"ผู้เสียหาย: {r.name} - เลขรับแจ้ง: ")
        _run(p, f"{r.case.receipt_no} - ")
        _run(p, "เบอร์โทรศัพท์: ")
        _run(p, f"{r.phone} ")
        doc.add_paragraph()
    doc.save(path)
    return Path(path)


def write_summary_docx(d: Day, path: Path) -> Path:
    """สรุปข้อมูลคดีและบันทึกการติดต่อ: every case called that day, six lines each."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    from apps.core.templatetags.thai import money

    doc = _document(margins_cm=(2.0, 1.34, 1.5, 1.5))
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(title, "สรุปข้อมูลคดีและบันทึกการติดต่อ", bold=True, size=20)
    doc.add_paragraph()
    for r in d.rows:
        case = r.case
        amount = f"{money(case.damage_amount)} บาท" if case.damage_amount is not None else "-"
        investigator = (case.investigator_raw or "").strip() or "-"
        _run(doc.add_paragraph(), f"ผู้เสียหาย: {r.name}", bold=True)
        for label, value in (("เลขรับแจ้ง", case.receipt_no), ("มูลค่าความเสียหาย", amount),
                             ("พนักงานสอบสวน", investigator), ("เบอร์โทรศัพท์ผู้เสียหาย", r.phone),
                             ("บันทึกการติดต่อ", log_line(r.attempt))):
            p = doc.add_paragraph()
            _run(p, f"- {label}: ", bold=True)
            _run(p, value)
        doc.add_paragraph()
    doc.save(path)
    return Path(path)
