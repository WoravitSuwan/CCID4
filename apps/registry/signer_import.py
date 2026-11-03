"""Import signer data (rank / name / position / phone) from an Excel list.

Accepts the template downloaded from the system or the unit's own list:
headers are matched by common names, and when rank and name share one cell
("พ.ต.ท.สมชาย ใจดี", "พันตำรวจโทหญิง สุดา ดี") the rank is split off and
abbreviations are expanded to the full rank used on the summons.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import openpyxl

from .address import clean_text
from .defaults import POLICE_RANKS
from .models import Investigator, InvestigatorAlias, Unit, person_core_key, signer_name_from_register

HEADER_ALIASES = {
    "name": ["ชื่อ-สกุล(ตามทะเบียน)", "ชื่อ-สกุล", "ชื่อสกุล", "ชื่อ-นามสกุล", "ชื่อนามสกุล", "ชื่อ", "ชื่อที่ใช้ลงนาม",
             "ชื่อในทะเบียน", "พนักงานสอบสวน", "ยศชื่อสกุล", "ยศ-ชื่อ-สกุล", "ยศชื่อ-สกุล", "ผู้ลงนาม"],
    "surname": ["นามสกุล", "สกุล"],  # files that split first name / surname into two columns
    "rank": ["ยศ(เต็ม)", "ยศเต็ม", "ยศ", "คำนำหน้า", "คำนำหน้าชื่อ", "ยศ/คำนำหน้า"],
    "position": ["ตำแหน่ง", "ตำแหน่ง(เต็ม)", "ตำแหน่งเต็ม", "ตำแหน่งงาน"],
    "phone": ["โทรศัพท์", "เบอร์โทร", "เบอร์โทรศัพท์", "หมายเลขโทรศัพท์", "โทร", "มือถือ"],
    "unit": ["หน่วย", "สังกัด"],
}
TEMPLATE_HEADERS = ["ชื่อ-สกุล (ตามทะเบียน)", "ยศ (เต็ม)", "ตำแหน่ง", "โทรศัพท์", "หน่วย"]

RANK_ABBREVIATIONS = {
    "พล.ต.อ.": "พลตำรวจเอก", "พล.ต.ท.": "พลตำรวจโท", "พล.ต.ต.": "พลตำรวจตรี",
    "พ.ต.อ.(พิเศษ)": "พันตำรวจเอก (พิเศษ)", "พ.ต.อ.": "พันตำรวจเอก", "พ.ต.ท.": "พันตำรวจโท", "พ.ต.ต.": "พันตำรวจตรี",
    "ร.ต.อ.": "ร้อยตำรวจเอก", "ร.ต.ท.": "ร้อยตำรวจโท", "ร.ต.ต.": "ร้อยตำรวจตรี",
    "ด.ต.": "ดาบตำรวจ", "จ.ส.ต.": "จ่าสิบตำรวจ", "ส.ต.อ.": "สิบตำรวจเอก", "ส.ต.ท.": "สิบตำรวจโท",
    "ส.ต.ต.": "สิบตำรวจตรี",
}
_FULL_RANKS = sorted({r for r in POLICE_RANKS if not r.endswith("หญิง")}, key=len, reverse=True)
_ABBRS = sorted(RANK_ABBREVIATIONS, key=len, reverse=True)


def _key(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _abbr_pattern(abbr: str) -> re.Pattern:
    """'พ.ต.ท.' also written 'พ.ต.ท', 'พ. ต. ท.' or 'พตท.' - dots and spaces are optional."""
    letters = [part for part in re.split(r"[.\s]+", abbr.replace("(พิเศษ)", "")) if part]
    body = r"\.?\s*".join(re.escape(x) for x in letters) + r"\.?"
    if "(พิเศษ)" in abbr:
        body += r"\s*\(\s*พิเศษ\s*\)"
    return re.compile(body)


_ABBR_PATTERNS = [(abbr, _abbr_pattern(abbr)) for abbr in _ABBRS]


def _match_abbr(text: str):
    """(abbreviation, matched length) for a rank abbreviation at the start of text, else (None, 0).

    At least one dot is required so a name that happens to start with the same letters is never cut.
    """
    for abbr, pattern in _ABBR_PATTERNS:
        m = pattern.match(text)
        if m and "." in m.group(0):
            return abbr, m.end()
    return None, 0


ACTING = "ว่าที่"  # 'ว่าที่ พ.ต.ต.' = acting rank, kept in front of the full rank: 'ว่าที่พันตำรวจตรี'


def _split_acting(raw: str) -> tuple[str, str]:
    if raw.startswith(ACTING):
        return ACTING, raw[len(ACTING):].lstrip()
    return "", raw


def expand_rank(text: str) -> str:
    """'พ.ต.ท.' -> 'พันตำรวจโท', 'พ.ต.ต.หญิง' -> 'พันตำรวจตรีหญิง'; full ranks are kept."""
    acting, raw = _split_acting(clean_text(text))
    if acting:
        rank = expand_rank(raw)
        return ACTING + rank if rank else ""
    abbr, end = _match_abbr(raw)
    if abbr:
        rest = _key(raw[end:])
        if rest in ("", "หญิง"):
            return RANK_ABBREVIATIONS[abbr] + rest
    return raw


def split_rank(text: str) -> tuple[str, str]:
    """'พ.ต.ท.สมชาย ใจดี' -> ('พันตำรวจโท', 'สมชาย ใจดี'); no rank -> ('', text)."""
    acting, raw = _split_acting(clean_text(text))
    if acting:
        rank, rest = split_rank(raw)
        return (ACTING + rank, rest) if rank else ("", clean_text(text))
    raw = clean_text(text)
    abbr, end = _match_abbr(raw)
    if abbr:
        rest = raw[end:].lstrip()
        female = rest.startswith("หญิง")
        if female:
            rest = rest[len("หญิง"):]
        return RANK_ABBREVIATIONS[abbr] + ("หญิง" if female else ""), rest.strip()
    for rank in _FULL_RANKS:
        if raw.startswith(rank):
            rest = raw[len(rank):]
            female = rest.startswith("หญิง")
            if female:
                rest = rest[len("หญิง"):]
            return rank + ("หญิง" if female else ""), rest.strip()
    return "", raw


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return clean_text(value)


def fix_phone(text: str) -> str:
    """Excel stores 0812345678 typed as a number as 812345678: put the leading 0 back."""
    digits = re.sub(r"\D", "", text)
    if digits == text and len(digits) == 9 and digits[0] in "689":
        return "0" + digits
    return text


@dataclass
class SignerRow:
    row_number: int
    name: str
    rank: str = ""
    position: str = ""
    phone: str = ""
    unit: str = ""
    action: str = ""  # new / update / same / skip
    investigator_id: int | None = None
    changes: list = field(default_factory=list)
    problem: str = ""

    def as_dict(self):
        return self.__dict__.copy()


def _find_header(ws):
    """(header row number, {field: column index}) - header may sit below a title row."""
    for row_number, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), start=1):
        keys = [_key(c) for c in row]
        mapping = {}
        for field_name, names in HEADER_ALIASES.items():
            for idx, k in enumerate(keys):
                if k and k in names and idx not in mapping.values():
                    mapping[field_name] = idx
                    break
        if "name" in mapping:
            return row_number, mapping
    return None, {}


def parse(file) -> tuple[list[SignerRow], str]:
    """Read the first sheet that has a name column. Returns (rows, error)."""
    try:
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:  # not an xlsx
        return [], f"อ่านไฟล์ไม่ได้: {exc}"
    try:
        for ws in wb.worksheets:
            header_row, cols = _find_header(ws)
            if header_row is None:
                continue
            rows = []
            for row_number, values in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True),
                                                start=header_row + 1):
                get = lambda f: cell(values[cols[f]]) if f in cols and cols[f] < len(values) else ""  # noqa: E731
                name, rank = get("name"), expand_rank(get("rank"))
                surname = get("surname")
                if surname and _key(surname) not in _key(name):
                    name = f"{name} {surname}".strip()
                if not name:
                    continue
                embedded_rank, name_only = split_rank(name)
                if embedded_rank:
                    name, rank = name_only, rank or embedded_rank
                rows.append(SignerRow(row_number, signer_name_from_register(name) or name, rank,
                                      get("position"), fix_phone(get("phone")), get("unit")))
            if rows:
                return rows, ""
        return [], "ไม่พบคอลัมน์ชื่อ (เช่น 'ชื่อ-สกุล' หรือ 'พนักงานสอบสวน') ในไฟล์"
    finally:
        wb.close()


def _surname_key(name: str) -> str:
    parts = signer_name_from_register(name).split()
    return _key(" ".join(parts[1:])) if len(parts) >= 2 else ""


def plan(rows: list[SignerRow]) -> list[SignerRow]:
    """Match each row to an existing signer (by name, ignoring spacing and a leading 'หญิง')."""
    by_core = {}
    for inv in Investigator.objects.prefetch_related("aliases"):
        by_core.setdefault(person_core_key(inv.sign_name), inv)
        for alias in inv.aliases.all():
            by_core.setdefault(person_core_key(alias.raw_name), inv)
    by_surname = {}
    for inv in Investigator.objects.all():
        key = _surname_key(inv.sign_name)
        if key:
            by_surname.setdefault(key, []).append(inv)
    seen = set()
    for row in rows:
        core = person_core_key(row.name)
        if core in seen:
            row.action, row.problem = "skip", "ชื่อซ้ำในไฟล์ (ใช้แถวแรก)"
            continue
        seen.add(core)
        inv = by_core.get(core)
        if inv is None:
            row.action = "new"
            surname = _surname_key(row.name)
            similar = [i for i in by_surname.get(surname, []) if person_core_key(i.sign_name) != core]
            if similar:
                row.problem = ("นามสกุลตรงกับ " + ", ".join(i.sign_name for i in similar)
                               + " ในระบบ แต่ชื่อสะกดต่างกัน — ถ้าเป็นคนเดียวกัน แก้ชื่อในไฟล์ให้ตรงทะเบียนแล้วนำเข้าใหม่")
            continue
        row.investigator_id = inv.pk
        for label, attr, value in (("ยศ", "rank_full", row.rank), ("ตำแหน่ง", "position", row.position),
                                   ("โทรศัพท์", "phone", row.phone)):
            if value and value != getattr(inv, attr):
                row.changes.append(f"{label}: {getattr(inv, attr) or '-'} → {value}")
        if row.unit and (inv.unit is None or inv.unit.name != row.unit):
            row.changes.append(f"หน่วย: {inv.unit or '-'} → {row.unit}")
        row.action = "update" if row.changes else "same"
    return rows


def apply(rows: list[dict]) -> dict:
    """Save planned rows. Empty cells never erase existing values."""
    counts = {"new": 0, "update": 0, "same": 0, "skip": 0}
    units = {u.name: u for u in Unit.objects.all()}
    for data in rows:
        row = SignerRow(**data)
        if row.action in ("same", "skip"):
            counts[row.action] += 1
            continue
        inv = Investigator.objects.filter(pk=row.investigator_id).first() if row.investigator_id else None
        if inv is None:
            inv = Investigator(sign_name=row.name)
            counts["new"] += 1
        else:
            counts["update"] += 1
        for attr, value in (("rank_full", row.rank), ("position", row.position), ("phone", row.phone)):
            if value:
                setattr(inv, attr, value)
        if row.unit and row.unit in units:
            inv.unit = units[row.unit]
        inv.save()
        # names in the register that belong to this person but are not linked yet
        core = person_core_key(inv.sign_name)
        for alias in InvestigatorAlias.objects.filter(investigator__isnull=True):
            if person_core_key(alias.raw_name) == core:
                alias.investigator = inv
                alias.save(update_fields=["investigator"])
    return counts


def template_workbook() -> bytes:
    """Excel list of current signers to fill in (rank column has a drop-down of full ranks)."""
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ผู้ลงนาม"
    ws.append(TEMPLATE_HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E0E7FF")
    for inv in Investigator.objects.select_related("unit").order_by("sign_name"):
        ws.append([inv.sign_name, inv.rank_full, inv.position, inv.phone, inv.unit.name if inv.unit else ""])
    lists = wb.create_sheet("รายการยศ")
    for i, rank in enumerate(POLICE_RANKS, start=1):
        lists.cell(row=i, column=1, value=rank)
    for i, unit in enumerate(Unit.objects.values_list("name", flat=True), start=1):
        lists.cell(row=i, column=2, value=unit)
    rank_dv = DataValidation(type="list", formula1=f"='รายการยศ'!$A$1:$A${len(POLICE_RANKS)}", allow_blank=True,
                             showErrorMessage=False)
    ws.add_data_validation(rank_dv)
    rank_dv.add("B2:B500")
    unit_count = Unit.objects.count()
    if unit_count:
        unit_dv = DataValidation(type="list", formula1=f"='รายการยศ'!$B$1:$B${unit_count}", allow_blank=True)
        ws.add_data_validation(unit_dv)
        unit_dv.add("E2:E500")
    for col, width in zip("ABCDE", (30, 22, 45, 18, 18)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=4):
        row[0].number_format = "@"  # keep leading zeros of phone numbers
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
