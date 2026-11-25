"""สรุปผลประจำวัน: what the call staff did on one day, from the calls recorded in this system.

Two texts the staff used to type by hand every day:
- the daily summary for the commanders (จำนวน Case ID, ไม่ประสงค์ / ประสงค์ / ติดต่อไม่ได้)
- the ลงบันทึกประจำวัน text listing the victims who said they do not want to proceed

Each case called that day counts once, by the result of its last call that day.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
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
    qs = (_of_day(day).select_related("case__complainant", "case__investigator_alias__investigator", "created_by")
          .order_by("called_at", "number", "pk"))
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

    return (FileContact.objects.filter(day=day)
            .select_related("case__complainant", "case__investigator_alias__investigator").order_by("time", "pk"))


# --------------------------------------------------------------------------- texts
def short_day(day: dt.date) -> str:
    """'9 พ.ย.69' (as staff write it in the diary)."""
    return f"{day.day} {THAI_MONTHS_ABBR[day.month - 1]}{ce_to_be(day) % 100:02d}"


def unit_text() -> str:
    return settings.OWN_UNIT.replace(" ", "")  # "กก.1บก.สอท.4"


def period_text(d: Day) -> str:
    """'ประจำวันที่ 9 พฤศจิกายน 2569'."""
    return f"ประจำวันที่ {thai_date_full(d.day, thai_digits=False)}"


# The two texts on the page - the report for the commanders and ลงบันทึกประจำวัน - are written from a wording
# the staff can change there (and then lock), so a new wording needs no new version of the system.
# {…} are filled in for the day; a line holding only placeholders that come out empty disappears.
DEFAULT_SUMMARY_TEMPLATE = "\n".join([
    "เรียนผู้บังคับบัญชา",
    "",
    "ขอรายงานสรุปผลการดำเนินการ ของ {ผู้โทร} สะสาง Case ID ประจำวันที่ {วันที่}",
    "จำนวน {จำนวนทั้งหมด} Case ID",
    f"1. {GROUP_LABEL[NOT_WANT]} จำนวน {{ไม่ประสงค์ดำเนินคดี}} Case",
    f"2. {GROUP_LABEL[WANT]} จำนวน {{ประสงค์ดำเนินคดีต่อ}} Case",
    f"3. {GROUP_LABEL[UNREACHED]} จำนวน {{ติดต่อไม่ได้}} Case",
    "{บรรทัดยังไม่ได้ข้อยุติ}",
    "",
    "จึงเรียนมาเพื่อโปรดทราบ",
])
DEFAULT_DIARY_TEMPLATE = "\n".join([
    "วันนี้( {วันที่สั้น}) พนักงานสอบสวน {หน่วย}  ได้ทำการโทรศัพท์ติดต่อผู้เสียหายที่แจ้งความออนไลน์ "
    "ผ่านระบบ Thaipoliceonline.com/สายด่วน 1441 เพื่อมาให้ถ้อยคำเพิ่มเติมพร้อมนำพยานหลักฐานที่เกี่ยวข้องกับคดี"
    "มามอบให้กับพนักงานสอบสวน",
    "พบว่ามีผู้เสียหายไม่ประสงค์พบพนักงานสอบสวนและไม่ประสงค์ดำเนินคดี ดังนี้",
    "",
    "{รายชื่อ}",
    "",
    "จึงลงบันทึกไว้เป็นหลักฐาน เพื่อที่จะดำเนินการในส่วนที่เกี่ยวข้องต่อไป",
])
SUMMARY_FIELDS = {  # placeholder -> what it becomes (shown next to the editor)
    "วันที่": "วันที่ เช่น 25 พฤศจิกายน 2569",
    "ผู้โทร": "เจ้าหน้าที่ Outsource (มีชื่อเมื่อเลือกผู้โทร)",
    "หน่วย": "ชื่อหน่วย เช่น กก.1บก.สอท.4",
    "จำนวนทั้งหมด": "จำนวน Case ที่โทรทั้งหมด",
    "ไม่ประสงค์ดำเนินคดี": "จำนวน ไม่ประสงค์ดำเนินคดี",
    "ประสงค์ดำเนินคดีต่อ": "จำนวน ประสงค์ดำเนินคดีต่อ",
    "ติดต่อไม่ได้": "จำนวน ไม่สามารถติดต่อได้",
    "ยังไม่ได้ข้อยุติ": "จำนวน ติดต่อได้แต่ยังไม่ได้ข้อยุติ",
    "บรรทัดยังไม่ได้ข้อยุติ": "ข้อ 4 ทั้งบรรทัด (หายไปเองเมื่อเป็น 0)",
}
DIARY_FIELDS = {
    "วันที่สั้น": "วันที่แบบย่อ เช่น 9 พ.ย.69",
    "วันที่": "วันที่ เช่น 9 พฤศจิกายน 2569",
    "หน่วย": "ชื่อหน่วย เช่น กก.1บก.สอท.4",
    "จำนวน": "จำนวนผู้ที่ไม่ประสงค์ดำเนินคดี",
    "รายชื่อ": "รายชื่อทีละบรรทัด: 1. ผู้เสียหาย: … เลขรับแจ้ง: … เบอร์โทรศัพท์ผู้เสียหาย: …",
    "รายชื่อแยกพนักงานสอบสวน": "รายชื่อแยกตามพนักงานสอบสวน พร้อมจำนวนของแต่ละคน",
}
_FIELD = re.compile(r"\{([^{}\n]*)\}")
MAX_TEMPLATE = 3000


def summary_values(d: Day) -> dict[str, str]:
    c = d.counts
    who = "เจ้าหน้าที่ Outsource"
    if d.caller is not None:
        who += f" ({d.caller.display_name()})"
    return {
        "วันที่": thai_date_full(d.day, thai_digits=False), "ผู้โทร": who, "หน่วย": unit_text(),
        "จำนวนทั้งหมด": f"{c['total']:,}", "ไม่ประสงค์ดำเนินคดี": f"{c[NOT_WANT]:,}",
        "ประสงค์ดำเนินคดีต่อ": f"{c[WANT]:,}", "ติดต่อไม่ได้": f"{c[UNREACHED]:,}", "ยังไม่ได้ข้อยุติ": f"{c[OTHER]:,}",
        # so the numbered lines always add up to the total
        "บรรทัดยังไม่ได้ข้อยุติ": f"4. {GROUP_LABEL[OTHER]} จำนวน {c[OTHER]:,} Case" if c[OTHER] else "",
    }


def _victim_line(r: Row) -> str:
    return f"ผู้เสียหาย: {r.name} เลขรับแจ้ง: {r.case.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: {r.phone}"


def diary_values(d: Day) -> dict[str, str]:
    rows = d.not_want
    return {
        "วันที่สั้น": short_day(d.day), "วันที่": thai_date_full(d.day, thai_digits=False), "หน่วย": unit_text(),
        "จำนวน": f"{len(rows):,}",
        "รายชื่อ": "\n".join(f"{i}. {_victim_line(r)}" for i, r in enumerate(rows, start=1)),
        "รายชื่อแยกพนักงานสอบสวน": "\n\n".join(
            "\n".join([f"{name} จำนวน {len(group):,} Case"] + [_victim_line(r) for r in group])
            for name, group in by_investigator(rows)),
    }


@dataclass(frozen=True)
class Wording:
    """One text whose wording can be changed on the page: where it is kept and what it can hold."""
    kind: str
    title: str
    default: str
    fields: dict
    values: object  # Day -> dict
    prefix: str  # SystemSetting fields <prefix>_template, _locked, _updated_by, _updated_at


WORDINGS = {
    "summary": Wording("summary", "ข้อความรายงานผู้บังคับบัญชา", DEFAULT_SUMMARY_TEMPLATE, SUMMARY_FIELDS,
                       summary_values, "report"),
    "diary": Wording("diary", "ข้อความลงบันทึกประจำวัน", DEFAULT_DIARY_TEMPLATE, DIARY_FIELDS, diary_values, "diary"),
}


def template_problems(text: str, kind: str = "summary") -> list[str]:
    """What is wrong with a wording the staff typed (empty list = fine). Unknown {…} are refused, so nothing
    like {…} can end up in a report."""
    fields = WORDINGS[kind].fields
    problems = []
    if not text.strip():
        problems.append("ข้อความว่างเปล่า")
    if len(text) > MAX_TEMPLATE:
        problems.append(f"ยาวเกิน {MAX_TEMPLATE:,} ตัวอักษร")
    unknown = sorted({m for m in _FIELD.findall(text) if m not in fields})
    if unknown:
        problems.append("ไม่รู้จัก " + ", ".join("{" + u + "}" for u in unknown))
    if _FIELD.sub("", text).count("{") or _FIELD.sub("", text).count("}"):
        problems.append("มีวงเล็บปีกกา { } ไม่ครบคู่")
    return problems


def wording_state(kind: str) -> dict:
    """The saved wording of one text and its lock, as stored in SystemSetting."""
    from apps.core.models import SystemSetting

    s, w = SystemSetting.get(), WORDINGS[kind]
    saved = getattr(s, f"{w.prefix}_template")
    text = saved if saved.strip() and not template_problems(saved, kind) else w.default
    return {"kind": kind, "title": w.title, "text": text, "custom": text != w.default, "standard": w.default,
            "locked": getattr(s, f"{w.prefix}_locked"), "by": getattr(s, f"{w.prefix}_updated_by"),
            "at": getattr(s, f"{w.prefix}_updated_at"), "fields": w.fields}


def summary_template() -> str:
    """The wording in use: the one saved on the page, else the standard one."""
    return wording_state("summary")["text"]


def render(template: str, values: dict) -> str:
    lines = []
    for line in template.replace("\r\n", "\n").split("\n"):
        out = _FIELD.sub(lambda m: values.get(m.group(1), ""), line)
        if not out.strip() and _FIELD.search(line) and not _FIELD.sub("", line).strip():
            continue  # a line of placeholders only, all empty (e.g. no ข้อ 4 today)
        lines.append(out.rstrip())
    return "\n".join(lines).strip("\n")


def render_summary(template: str, d: Day) -> str:
    return render(template, summary_values(d))


def summary_text(d: Day, template: str | None = None) -> str:
    """The daily report for the commanders (counts only: no names or phone numbers)."""
    return render(template if template is not None else summary_template(), summary_values(d))


def diary_text(d: Day, template: str | None = None) -> str:
    """ลงบันทึกประจำวัน: the victims called that day who said they do not want to proceed."""
    return render(template if template is not None else wording_state("diary")["text"], diary_values(d))


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
NO_INVESTIGATOR = "(ไม่ระบุพนักงานสอบสวน)"


def investigator_of(r: Row) -> str:
    """'พ.ต.ต.สมชาย ใจดี' (rank short + name of the linked signer), else the register's name."""
    from .status_report import investigator_label

    label = investigator_label(r.case)
    return NO_INVESTIGATOR if label in ("", "-") else label


def by_investigator(rows: list[Row]) -> list[tuple[str, list[Row]]]:
    """Rows grouped by investigator: most cases first, then by name; the unnamed group last."""
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(investigator_of(r), []).append(r)
    return sorted(groups.items(), key=lambda g: (g[0] == NO_INVESTIGATOR, -len(g[1]), g[0]))


def diary_file_text(d: Day) -> str:
    """ไม่ประสงค์ดำเนินคดี: the total, then one block per investigator with their count and their victims, one
    line each:
        ไม่ประสงค์ดำเนินคดี จำนวน 9 Case ดังนี้

        พ.ต.ต.สมชาย ใจดี จำนวน 3 Case
        ผู้เสียหาย: … เลขรับแจ้ง: … เบอร์โทรศัพท์ผู้เสียหาย: …
    """
    rows = d.not_want
    parts = [f"ไม่ประสงค์ดำเนินคดี จำนวน {len(rows):,} Case" + (" ดังนี้" if rows else "")]
    for name, group in by_investigator(rows):
        parts.append("\n".join([f"{name} จำนวน {len(group):,} Case"] + [
            f"ผู้เสียหาย: {r.name} เลขรับแจ้ง: {r.case.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: {r.phone}" for r in group]))
    return "\n\n".join(parts)


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


SUMMARY_HEADERS = ["ลำดับ", "ผู้เสียหาย", "เลขรับแจ้ง", "มูลค่าความเสียหาย (บาท)", "พนักงานสอบสวน",
                   "เบอร์โทรศัพท์ผู้เสียหาย", "บันทึกการติดต่อ"]


def summary_title(d: Day) -> str:
    return f"สรุปข้อมูลคดีและบันทึกการติดต่อ {period_text(d)} ({len(d.rows):,} คดี)"


_THAI_MARKS = re.compile("[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]")  # vowels and tone marks above/below: no width


def _width(value) -> int:
    """About how many characters wide a cell's text is (Thai marks above and below the line take no room)."""
    return len(_THAI_MARKS.sub("", str(value if value is not None else "")))


def write_summary_xlsx(d: Day, path) -> Path:
    """สรุปข้อมูลคดี as a table (A4 landscape, the header row on every page): the source of the PDF."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "สรุปข้อมูลคดี"
    font, bold = Font(name="TH Sarabun New", size=14), Font(name="TH Sarabun New", size=14, bold=True)
    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append([summary_title(d)])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(SUMMARY_HEADERS))
    ws["A1"].font = Font(name="TH Sarabun New", size=18, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30
    ws.append(SUMMARY_HEADERS)
    for i, r in enumerate(d.rows, start=1):
        case = r.case
        ws.append([i, r.name, case.receipt_no,
                   float(case.damage_amount) if case.damage_amount is not None else None,
                   investigator_of(r), r.phone, log_line(r.attempt)])
    if not d.rows:
        ws.append(["", "ไม่มีคดีที่โทรในวันนี้"])
    widths = [7, 26, 15, 15, 26, 17, 54]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    for row in ws.iter_rows(min_row=3):  # LibreOffice does not grow rows for wrapped text: set the height
        lines = max(math.ceil(_width(c.value) / (widths[c.column - 1] * 1.3)) for c in row)
        ws.row_dimensions[row[0].row].height = 21 * max(lines, 1) + 3
    head_fill = PatternFill("solid", fgColor="E8EEF8")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = box
            cell.font = bold if cell.row == 2 else font
            if cell.row == 2:
                cell.fill = head_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            elif cell.column in (1, 3, 6):
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif cell.column == 4:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="top", indent=1)
            else:
                cell.alignment = Alignment(vertical="top", wrap_text=True, indent=1)
    ws.row_dimensions[2].height = 36
    ws.freeze_panes = "A3"
    ws.print_title_rows = "2:2"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top, ws.page_margins.bottom = 0.6, 0.75
    ws.page_margins.header, ws.page_margins.footer = 0.3, 0.3
    ws.oddFooter.left.text = f"{settings.OWN_UNIT} · พิมพ์ {timezone.localtime():%d/%m/%Y %H:%M}"
    ws.oddFooter.right.text = "หน้า &P / &N"
    wb.properties.title = summary_title(d)
    path = Path(path)
    wb.save(path)
    return path


def summary_pdf(d: Day) -> tuple[str, bytes]:
    """(file name, PDF) of สรุปข้อมูลคดี; raises apps.documents.pdf.PdfConversionError without LibreOffice."""
    import tempfile

    from apps.documents.pdf import convert_many

    stem = f"{FILE_STEM['summary']} {file_day(d.day)}"
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = write_summary_xlsx(d, Path(tmp) / f"{stem}.xlsx")
        pdf = convert_many([xlsx], tmp, export_filter="calc_pdf_Export")[0]
        return f"{stem}.pdf", pdf.read_bytes()


FILE_TEXT = {"diary": diary_file_text, "summary": summary_file_text}
FILE_STEM = {"diary": "ลงบันทึกประจำวัน", "summary": "สรุปข้อมูลคดี"}


def text_file(kind: str, d: Day) -> bytes:
    """UTF-8 with a BOM and Windows line endings: opens right in Notepad and pastes cleanly anywhere."""
    return ("\ufeff" + FILE_TEXT[kind](d).replace("\n", "\r\n") + "\r\n").encode("utf-8")
