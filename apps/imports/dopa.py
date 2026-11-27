"""PDFs of the civil registration (ทะเบียนราษฎร / DOPA report): read the person and the house address.

Text PDFs are read with poppler's ``pdftotext -layout`` (or pypdf when poppler is missing), so the
columns of the page stay apart. A scanned or image-only page has no text: it is rendered and read
with Tesseract OCR (Thai) when that is installed on the server; otherwise the staff are told so and
can type the values in on the review page. Nothing is sent outside the machine.

Values are found by their labels (เลขประจำตัวประชาชน, ชื่อ-ชื่อสกุล, ที่อยู่, ตำบล/แขวง ...): each value runs
until the next label. Addresses in Bangkok (แขวง/เขต, or with no label at all) are recognised with
the system's own table of ตำบล/อำเภอ/จังหวัด (the postcode table), which also fills the postcode.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from apps.registry.address import BANGKOK_NAMES, Address, clean_text, is_blank_value

logger = logging.getLogger(__name__)

MAX_PAGES_OCR = 10
OCR_DPI = 300

# --------------------------------------------------------------------------- text
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def normalize(text: str) -> str:
    """Undo what PDF text extraction does to Thai: Thai digits, ำ split into ํ + า, แ written as เเ,
    zero-width and non-breaking spaces, ':' of all widths."""
    text = (text or "").translate(_THAI_DIGITS)
    text = text.replace("ํา", "ำ").replace("เเ", "แ")
    text = re.sub(r"[​‌‍﻿]", "", text).replace("\xa0", " ").replace("：", ":")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    # a space put before a mark or vowel that never starts a Thai word ('ป่ า' -> 'ป่า'), within a line
    text = re.sub(r"[ \t]+(?=[\u0e30\u0e32\u0e33\u0e45\u0e31\u0e34-\u0e3a\u0e47-\u0e4e])", "", text)
    return text


def _run(cmd, data: bytes | None = None, timeout: int = 90) -> bytes:
    return subprocess.run(cmd, input=data, capture_output=True, timeout=timeout, check=True).stdout


_BBOX_LINE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
_BBOX_WORD = re.compile(r"<word [^>]*>([^<]*)</word>")


def bbox_layout(data: bytes) -> tuple[str, int]:
    """(the page rebuilt from the position of each line of text, how many lines ran up the page). Reports like
    the DOPA printout (JasperReports) carry a watermark written upwards across the page ("พิมพ์วันที่ ...",
    "ผู้พิมพ์รายงาน ..."): those lines are dropped, the others put back in rows (top to bottom) and columns
    (left to right) so a label and its value stay on one line."""
    import html

    out_pages, rotated = [], 0
    xml = _run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", "-", "-"], data).decode("utf-8", "replace")
    for page in re.split(r"<page ", xml)[1:]:
        lines = []
        for m in _BBOX_LINE.finditer(page):
            x0, y0, x1, y1 = (float(v) for v in m.groups()[:4])
            if y1 - y0 > x1 - x0 and y1 - y0 > 30:  # taller than wide: written upwards
                rotated += 1
                continue
            text = " ".join(html.unescape(w) for w in _BBOX_WORD.findall(m.group(5))).strip()
            if text:
                lines.append((y0, x0, text))
        out_pages.append(_rows_to_text(lines))
    return "\n\f".join(out_pages), rotated


def _rows_to_text(pieces) -> str:
    """[(top, left, text)] of one page -> lines: the same row of the page on one line, in columns."""
    rows: list[list] = []
    for top, left, text in sorted(pieces):
        if rows and abs(rows[-1][0] - top) <= 4:  # the same row of the page
            rows[-1][1].append((left, text))
        else:
            rows.append([top, [(left, text)]])
    out = []
    for _top, cells in rows:
        line = ""
        for left, text in sorted(cells):
            col = int(left / 4)  # keep the columns: a value lines up under/after its label
            line += " " * max(3 if line else 0, col - len(line)) + text
        out.append(line)
    return "\n".join(out)


def pypdf_layout(data: bytes) -> tuple[str, int]:
    """The same as bbox_layout without poppler: each piece of text with its position from pypdf; pieces turned
    on their side (the watermark) are dropped, the rest put back in rows and columns."""
    from pypdf import PdfReader

    pages, rotated = [], 0
    for page in PdfReader(io.BytesIO(data)).pages:
        height = float(page.mediabox.height)
        pieces = []

        def visit(text, cm, tm, _font, _size):
            nonlocal rotated
            if not text.strip():
                return
            a = tm[0] * cm[0] + tm[1] * cm[2]
            b = tm[0] * cm[1] + tm[1] * cm[3]
            if abs(b) > abs(a):  # written upwards (or downwards)
                rotated += 1
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            pieces.append((round(height - y, 1), round(x, 1), text.strip()))

        page.extract_text(visitor_text=visit)
        pages.append(_rows_to_text(pieces))
    return "\n\f".join(pages), rotated


def text_layer(data: bytes) -> tuple[str, str]:
    """(the PDF's own text laid out as on the page, which reader): '' for a scanned page. pdftotext (poppler)
    reads Thai correctly; pypdf is only the stand-by - with some fonts its Thai comes out garbled."""
    if shutil.which("pdftotext"):
        try:
            text, rotated = bbox_layout(data)
            if rotated:  # a watermark running up the page breaks -layout's reading order
                return text, "poppler"
            return _run(["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"], data).decode("utf-8", "replace"), "poppler"
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pdftotext failed: %s", exc)
    try:
        from pypdf import PdfReader

        text, rotated = pypdf_layout(data)
        if rotated:  # the DOPA printout's watermark: rebuilt from positions, as with poppler
            return text, "pypdf"
        reader = PdfReader(io.BytesIO(data))
        return "\n\f".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages), "pypdf"
    except Exception as exc:  # a broken file: the caller reports it
        logger.warning("pypdf could not read the PDF: %s", exc)
        return "", "pypdf"


@lru_cache(maxsize=1)
def ocr_status() -> str:
    """'ok' (tesseract with Thai), 'no-thai' (tesseract without the Thai language), 'missing'."""
    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        return "missing"
    try:
        langs = _run(["tesseract", "--list-langs"], timeout=20).decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return "missing"
    return "ok" if re.search(r"^tha$", langs, re.M) else "no-thai"


OCR_HELP = {
    "missing": "เครื่องนี้ยังไม่มีโปรแกรมอ่านภาพ (OCR) ให้ผู้ดูแลเซิร์ฟเวอร์ติดตั้ง tesseract-ocr และ tesseract-ocr-tha "
               "(Docker ของระบบติดตั้งให้แล้ว) หรือกรอกข้อมูลเองในหน้าตรวจสอบ",
    "no-thai": "โปรแกรม OCR ยังไม่มีภาษาไทย ให้ผู้ดูแลเซิร์ฟเวอร์ติดตั้ง tesseract-ocr-tha หรือกรอกข้อมูลเองในหน้าตรวจสอบ",
}


def ocr_text(data: bytes) -> str:
    """Render each page and read it with Tesseract (Thai + English). --psm 4 keeps each row of the page on
    one line (a label stays next to its value)."""
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "in.pdf"
        pdf.write_bytes(data)
        _run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", "-l", str(MAX_PAGES_OCR), str(pdf),
              str(Path(tmp) / "p")], timeout=180)
        pages = []
        for img in sorted(Path(tmp).glob("p*.png")):
            pages.append(_run(["tesseract", str(img), "stdout", "-l", "tha+eng", "--psm", "4"],
                              timeout=180).decode("utf-8", "replace"))
        return "\n\f".join(pages)


def readable(text: str) -> bool:
    """Enough of a registration report to parse: a label we know, or an ID number, and some Thai."""
    t = normalize(text)
    thai = len(re.findall(r"[ก-๛]", t))
    return thai >= 20 and (bool(_LABEL_ANY.search(t)) or bool(find_ids(t)))


def extract(data: bytes) -> tuple[str, str, list[str]]:
    """(text, how it was read: 'text' | 'ocr' | 'none', notes for the staff)."""
    if not data.startswith(b"%PDF"):
        return "", "none", ["ไม่ใช่ไฟล์ PDF"]
    text, reader = text_layer(data)
    if readable(text):
        notes = [] if reader == "poppler" else [
            "เครื่องนี้ไม่มี poppler-utils (pdftotext) จึงอ่านด้วยตัวสำรอง ตัวอักษรไทยอาจเพี้ยน ตรวจทุกช่องกับเอกสาร "
            "(Docker ของระบบติดตั้ง poppler-utils ให้แล้ว)"]
        return normalize(text), "text", notes
    status = ocr_status()
    if status != "ok":
        notes = ["ไฟล์นี้เป็นภาพสแกน/มีลายน้ำ อ่านข้อความโดยตรงไม่ได้", OCR_HELP[status]]
        return normalize(text), "none", notes
    try:
        ocr = ocr_text(data)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("OCR failed: %s", exc)
        return normalize(text), "none", ["อ่านภาพ (OCR) ไม่สำเร็จ กรอกข้อมูลเองในหน้าตรวจสอบ"]
    return normalize(ocr), "ocr", ["อ่านจากภาพ (OCR): ตรวจทุกช่องให้ตรงกับเอกสารก่อนบันทึก"]


# --------------------------------------------------------------------------- labels
# field -> labels (most specific first). A value runs until the next label of any field.
LABELS: dict[str, list[str]] = {
    "national_id": ["เลขประจำตัวประชาชน", "เลขบัตรประจำตัวประชาชน", "เลขบัตรประชาชน", "เลขประชาชน"],
    "house_code": ["เลขรหัสประจำบ้าน", "รหัสประจำบ้าน", "เลขประจำบ้าน"],
    "father": ["ชื่อบิดา", "บิดา"],
    "mother": ["ชื่อมารดา", "มารดา"],
    "owner": ["ชื่อเจ้าบ้าน"],
    "full_name": ["ชื่อ-ชื่อสกุล", "ชื่อ - ชื่อสกุล", "ชื่อ-นามสกุล", "ชื่อ - นามสกุล", "ชื่อและชื่อสกุล",
                  "ชื่อตัว-ชื่อสกุล", "ชื่อ-สกุล", "ชื่อ - สกุล", "ชื่อสกุล-ชื่อ"],
    "prefix": ["คำนำหน้าชื่อ", "คำนำหน้านาม", "คำนำหน้า"],
    "first_name": ["ชื่อตัว"],
    "last_name": ["ชื่อสกุล", "นามสกุล"],
    "name": ["ชื่อ"],
    "gender": ["เพศ"],
    "birth_date": ["วันเดือนปีเกิด", "วัน/เดือน/ปีเกิด", "วัน เดือน ปีเกิด", "วัน เดือน ปี เกิด", "วันเกิด", "เกิดวันที่"],
    "age": ["อายุ"],
    "nationality": ["สัญชาติ"],
    "owner_status": ["สถานภาพเจ้าบ้าน", "สถานะภาพเจ้าบ้าน", "สถานะเจ้าบ้าน", "สถานภาพในบ้าน", "สถานะในบ้าน",
                     "สถานะภาพในบ้าน",
                     "ความเกี่ยวพันกับเจ้าบ้าน"],
    "person_status": ["สถานภาพบุคคล", "สถานะบุคคล", "สถานะภาพบุคคล", "สถานภาพ"],
    "moved_in": ["วันที่ย้ายเข้า", "วันย้ายเข้า", "ย้ายเข้าวันที่", "ย้ายเข้าเมื่อ"],
    "address": ["ที่อยู่ตามทะเบียนบ้าน", "ที่อยู่ตามทะเบียนราษฎร", "ที่อยู่ปัจจุบัน", "ที่อยู่",
                "ทีอยู่"],  # OCR often drops the tone mark
    # address parts written as their own labelled fields
    "house_no": ["บ้านเลขที่"],
    "moo": ["หมู่ที่"],
    "building": ["อาคาร", "หมู่บ้าน"],
    "alley": ["ตรอก"],
    "soi": ["ซอย"],
    "road": ["ถนน"],
    "subdistrict": ["ตำบล/แขวง", "แขวง/ตำบล", "ตำบล", "แขวง"],
    "district": ["อำเภอ/เขต", "เขต/อำเภอ", "อำเภอ", "เขต"],
    "province": ["จังหวัด"],
    "postcode": ["รหัสไปรษณีย์"],
}
ADDRESS_PARTS = ["house_no", "moo", "building", "alley", "soi", "road", "subdistrict", "district", "province",
                 "postcode"]


def _alt(labels) -> str:
    return "|".join(re.escape(x).replace(r"\ ", r"\s*") for x in sorted(labels, key=len, reverse=True))


_ALL_LABELS = [(f, label) for f, labels in LABELS.items() for label in labels]
_LABEL_ANY = re.compile(_alt([label for _, label in _ALL_LABELS if len(label) > 3]))
# a label starts a value when it opens a cell, or follows a space/':' - never in the middle of a word;
# "ชื่อ" alone must not be the start of ชื่อบิดา etc. (longest labels win)
_LABEL_AT = re.compile(r"(?:(?<=^)|(?<=[\s:|]))(" + _alt([label for _, label in _ALL_LABELS]) + r")(?!บ้าน)")
_FIELD_OF = {re.sub(r"\s+", "", label): f for f, label in _ALL_LABELS}


def _label_field(label: str) -> str:
    return _FIELD_OF[re.sub(r"\s+", "", label)]


@dataclass
class Cell:
    start: int  # column on the laid-out page
    text: str


def _cells(text: str) -> list[list[Cell]]:
    """Each line split into the cells of the page layout (3+ spaces, a tab or '|' apart), with their columns."""
    rows = []
    for line in text.split("\n"):
        cells = [Cell(m.start(), m.group(0).strip()) for m in re.finditer(r"[^\s|]+(?:[ ]{1,2}[^\s|]+)*", line)]
        if cells:
            rows.append(cells)
    return rows


def _noise(text: str) -> bool:
    """A speck read by OCR (a letter or two, no digits)."""
    return len(text) <= 2 and not re.search(r"\d", text)


def _meaningful(value: str) -> bool:
    return bool(re.search(r"[\u0e01-\u0e2e\d]", value or ""))


def _below(rows, r: int, col: int, limit: int) -> tuple[str, int, int]:
    """The value written under a label (OCR often puts it on the next line): in the next two lines, the cell
    nearest the label's column, before the next label's column. ('', -1, -1) when a label is found first."""
    for rr in range(r + 1, min(r + 3, len(rows))):
        near = [x for x in rows[rr] if col - 25 <= x.start < limit - 3 and not _noise(x.text)]
        if not near:
            continue
        best = min(near, key=lambda x: abs(x.start - col))
        if _LABEL_AT.match(best.text):
            return "", -1, -1
        return best.text, rr, best.start
    return "", -1, -1


def pairs(text: str) -> list[tuple[str, str, int, int]]:
    """(field, value, row, column of the value) for every label on the page: the value is the rest of the cell
    up to the next label; when nothing (or only an OCR speck) follows the label, the next cell on the line, or
    the cell under the label on the next line."""
    rows = _cells(text)
    out = []
    for r, cells in enumerate(rows):
        for c, cell in enumerate(cells):
            hits = list(_LABEL_AT.finditer(cell.text))
            for i, m in enumerate(hits):
                end = hits[i + 1].start() if i + 1 < len(hits) else len(cell.text)
                value = cell.text[m.end():end].strip(" :-–.\t")
                raw = cell.text[m.end():end].strip(" :\t")
                vr, vcol = r, cell.start + m.end()
                if raw in ("-", "--", "–"):  # written as empty
                    value = ""
                elif not _meaningful(value) and i + 1 == len(hits):
                    value = ""
                    nxt = next((x for x in cells[c + 1:] if not _noise(x.text)), None)
                    if nxt is not None and not _LABEL_AT.match(nxt.text) and _meaningful(nxt.text):
                        value, vcol = nxt.text.strip(" :-–."), nxt.start
                    else:
                        limit = nxt.start if nxt is not None else 10_000
                        value, vr, vcol = _below(rows, r, cell.start + m.start(), limit)
                        value = value.strip(" :-–.")
                out.append((_label_field(m.group(1)), clean_text(value), vr, vcol))
    return out


# --------------------------------------------------------------------------- national ID / names / dates
def id_checksum_ok(digits: str) -> bool:
    if not re.fullmatch(r"\d{13}", digits):
        return False
    total = sum(int(digits[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(digits[12])


_ID = re.compile(r"(?<!\d)(\d[\s-]?\d{4}[\s-]?\d{5}[\s-]?\d{2}[\s-]?\d)(?!\d)")


def find_ids(text: str) -> list[str]:
    """13-digit numbers that pass the check digit (with or without dashes/spaces)."""
    found = []
    for m in _ID.finditer(normalize(text)):
        digits = re.sub(r"\D", "", m.group(1))
        if id_checksum_ok(digits) and digits not in found:
            found.append(digits)
    return found


def format_id(digits: str) -> str:
    return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}" if len(digits) == 13 else digits


PREFIXES = ["เด็กชาย", "เด็กหญิง", "นางสาว", "ด.ช.", "ด.ญ.", "น.ส.", "นาย", "นาง",
            "Mr.", "Mrs.", "Miss", "Ms."]
_RANK = re.compile(r"^((?:[ก-ฮ]{1,3}\.){2,5})")  # พ.ต.ต. / ร.ต.อ. / จ.ส.ต. written before the name


def split_name(value: str) -> tuple[str, str, str]:
    """'นาย สมชาย ใจดี' / 'นายสมชาย ใจดี' / 'สมชาย ใจดี' -> (prefix, first, last). No prefix: none is guessed."""
    text = clean_text(value)
    prefix = ""
    for p in PREFIXES:
        if text.startswith(p):
            prefix, text = p, text[len(p):].strip()
            break
    else:
        m = _RANK.match(text)
        if m:
            prefix, text = m.group(1), text[m.end():].strip()
    parts = text.split(" ", 1)
    return prefix, parts[0] if parts else "", parts[1].strip() if len(parts) > 1 else ""


_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}


def parse_thai_date(value: str) -> dt.date | None:
    """'12 มีนาคม 2530', '12 มี.ค. 30', '12/03/2530' (พ.ศ.) -> a date; None when unreadable or only partly known."""
    text = clean_text(normalize(value))
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(\d{1,2})\s*(" + "|".join(re.escape(k) for k in sorted(_MONTHS, key=len, reverse=True))
                      + r")\s*(?:พ\.ศ\.\s*)?(\d{2,4})", text)
        if not m:
            return None
        day, month, year = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    if year < 100:
        year += 2500
    if year > 2400:
        year -= 543
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------- places (from the postcode table)
@dataclass
class Places:
    provinces: dict = field(default_factory=dict)  # key -> display name
    districts: dict = field(default_factory=dict)  # province -> {key: display}
    subdistricts: dict = field(default_factory=dict)  # (province, district) -> {key: display}


def _key(text: str) -> str:
    return re.sub(r"[\s.]", "", text or "")


@lru_cache(maxsize=1)
def places() -> Places:
    from .postcode import DATA_FILE

    out = Places()
    if not DATA_FILE.exists():
        return out
    with open(DATA_FILE, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            p, d, s = row["province"], row["district"], row["subdistrict"]
            out.provinces[_key(p)] = p
            out.districts.setdefault(p, {})[_key(d)] = d
            out.subdistricts.setdefault((p, d), {})[_key(s)] = s
    return out


def province_name(value: str) -> str:
    """'กทม.' / 'กรุงเทพฯ' / 'จ.ลำพูน' / 'ลำพูน' -> the name as in the table ('' when unknown)."""
    key = _key(re.sub(r"^(จังหวัด|จ\.)", "", clean_text(value)))
    if key in {_key(n) for n in BANGKOK_NAMES} or key.startswith("กรุงเทพ"):
        return "กรุงเทพมหานคร"
    return places().provinces.get(key, "")


def _district_in(province: str, value: str) -> str:
    table = places().districts.get(province, {})
    key = _key(value)
    if key in table:
        return table[key]
    if key.startswith("เมือง") and key == "เมือง" + _key(province):
        return table.get(key, value)
    return ""


def _subdistrict_in(province: str, district: str, value: str) -> str:
    key = _key(value)
    if district:
        return places().subdistricts.get((province, district), {}).get(key, "")
    for (p, _d), subs in places().subdistricts.items():
        if p == province and key in subs:
            return subs[key]
    return ""


# --------------------------------------------------------------------------- addresses
_PART_LABELS = {
    "house_no": ["บ้านเลขที่", "เลขที่"],
    "moo": ["หมู่ที่", "หมู่", "ม."],
    "building": ["อาคาร", "ตึก", "หมู่บ้าน"],
    "alley": ["ตรอก", "ตร."],
    "soi": ["ซอย", "ซ."],
    "road": ["ถนน", "ถ."],
    "subdistrict": ["ตำบล", "แขวง", "ต."],
    "district": ["อำเภอ", "เขต", "อ."],
    "province": ["จังหวัด", "จ."],
    "postcode": ["รหัสไปรษณีย์"],
}
_PART_FIELD = {label: f for f, labels in _PART_LABELS.items() for label in labels}
# labels may be glued to the word before ("แขวงลาดยาวเขตจตุจักร"); abbreviations only after a space.
# หมู่บ้าน (a village name) is a building, not หมู่.
_PART_AT = re.compile(
    r"(บ้านเลขที่|รหัสไปรษณีย์|หมู่บ้าน|หมู่ที่|หมู่(?!บ้าน)|อาคาร|ตรอก|ซอย|ถนน|ตำบล|แขวง|อำเภอ|จังหวัด"
    r"|เขต|(?<![ก-๙])ตึก|(?:(?<=^)|(?<=\s))(?:เลขที่|ม\.|ตร\.|ซ\.|ถ\.|ต\.|อ\.|จ\.))")


def parse_address(text: str) -> Address:
    """One address string, in any of the usual ways it is written, -> its parts.

    '12/3 หมู่ที่ 4 ซอยวัด ถนนสุขุมวิท ตำบลบ้านกลาง อำเภอเมืองลำพูน จังหวัดลำพูน 51000',
    '99/1 แขวงลาดยาวเขตจตุจักร กรุงเทพมหานคร', '45 ซ.ลาดพร้าว 101 ลาดพร้าว จตุจักร กทม.' ...
    """
    s = clean_text(normalize(text)).strip(" ,")
    parts: dict[str, str] = {}
    pc = re.search(r"(?<!\d)(\d{5})\s*$", s)
    if pc:
        parts["postcode"], s = pc.group(1), s[:pc.start()].strip(" ,")
    hits = list(_PART_AT.finditer(s))
    head = s[:hits[0].start()] if hits else s
    for i, m in enumerate(hits):
        f = _PART_FIELD[m.group(1)]
        value = s[m.end():hits[i + 1].start() if i + 1 < len(hits) else len(s)].strip(" ,:")
        if f == "postcode":
            value = re.sub(r"\D", "", value)[:5]
        if f == "building" and m.group(1) == "หมู่บ้าน":
            value = "หมู่บ้าน" + value  # keep the kind of place: หมู่บ้านพฤกษา, not อาคารพฤกษา
        if value and not parts.get(f):
            parts[f] = value
    head = head.strip(" ,")
    if head and not parts.get("house_no"):
        m = re.match(r"^(\d[\d/\-]*(?:\s*[ก-ฮ](?=\s|$))?)\s*(.*)$", head)
        if m:
            parts["house_no"], head = m.group(1).strip(), m.group(2).strip()
    # names written with no label: the province at the end, then เขต/อำเภอ and แขวง/ตำบล before it
    _place_names(parts, head)
    _tidy(parts)
    return Address(**{k: parts.get(k, "") for k in ADDRESS_PARTS})


def _place_names(parts: dict, head: str) -> None:
    """Fill จังหวัด / อำเภอ(เขต) / ตำบล(แขวง) written without labels, using the table of places: from the end of a
    labelled value that ran on ('จตุจักร กรุงเทพมหานคร'), or from loose words before the first label."""
    # a province written after the last labelled value
    for f in ("district", "subdistrict", "road", "soi", "moo", "building", "alley", "house_no"):
        v = parts.get(f, "")
        words = v.split()
        if not parts.get("province") and len(words) > 1:
            for n in (2, 1):
                tail = " ".join(words[-n:])
                if len(words) > n and province_name(tail):
                    parts["province"], parts[f] = province_name(tail), " ".join(words[:-n])
                    break
        if parts.get("province"):
            break
    if parts.get("province"):
        parts["province"] = province_name(parts["province"]) or parts["province"]
    # loose words: "... ลาดยาว จตุจักร กรุงเทพมหานคร"
    words = head.split()
    if words and not parts.get("province"):
        for n in (2, 1):
            if len(words) >= n and province_name(" ".join(words[-n:])):
                parts["province"] = province_name(" ".join(words[-n:]))
                words = words[:-n]
                break
    province = parts.get("province", "")
    if not province:
        return
    for f in ("road", "soi", "building", "alley", "moo"):  # a district/subdistrict that ran on after a label
        words_f = parts.get(f, "").split()
        while len(words_f) > 1 and (not parts.get("district") or not parts.get("subdistrict")):
            last = words_f[-1]
            if not parts.get("district") and _district_in(province, last):
                parts["district"] = _district_in(province, last)
            elif not parts.get("subdistrict") and _subdistrict_in(province, parts.get("district", ""), last):
                parts["subdistrict"] = _subdistrict_in(province, parts.get("district", ""), last)
            else:
                break
            words_f.pop()
        if parts.get(f):
            parts[f] = " ".join(words_f)
    while words and (not parts.get("district") or not parts.get("subdistrict")):
        last = words[-1]
        if not parts.get("district") and _district_in(province, last):
            parts["district"] = _district_in(province, last)
        elif not parts.get("subdistrict") and _subdistrict_in(province, parts.get("district", ""), last):
            parts["subdistrict"] = _subdistrict_in(province, parts.get("district", ""), last)
        else:
            break
        words.pop()


def _tidy(parts: dict) -> None:
    for f in list(parts):
        v = clean_text(parts[f]).strip(" ,:-")
        if f == "moo":
            v = re.sub(r"^(ที่)\s*", "", v)
        if is_blank_value(v):
            v = ""
        parts[f] = v
    province = parts.get("province", "")
    if province and province_name(province):
        parts["province"] = province_name(province)


# --------------------------------------------------------------------------- one person
PERSON_FIELDS = ["national_id", "prefix", "first_name", "last_name", "gender", "birth_date", "age", "nationality",
                 "house_code", "owner_status", "person_status", "moved_in"]
FIELD_LABELS = {
    "national_id": "เลขประจำตัวประชาชน", "prefix": "คำนำหน้า", "first_name": "ชื่อ", "last_name": "ชื่อสกุล",
    "gender": "เพศ", "birth_date": "วันเดือนปีเกิด", "age": "อายุ", "nationality": "สัญชาติ",
    "house_code": "เลขรหัสประจำบ้าน", "owner_status": "สถานภาพเจ้าบ้าน", "person_status": "สถานภาพบุคคล",
    "moved_in": "วันที่ย้ายเข้า",
}


@dataclass
class Person:
    national_id: str = ""
    prefix: str = ""
    first_name: str = ""
    last_name: str = ""
    gender: str = ""
    birth_date: str = ""  # as written in the document
    age: str = ""
    nationality: str = ""
    house_code: str = ""
    owner_status: str = ""
    person_status: str = ""
    moved_in: str = ""
    address: Address = field(default_factory=Address)
    postcode_from: str = ""  # 'pdf' | 'table' | ''
    warnings: list = field(default_factory=list)

    @property
    def full_name(self) -> str:
        name = " ".join(x for x in (self.first_name, self.last_name) if x)
        return f"{self.prefix}{name}" if self.prefix and not re.match(r"[A-Za-z]", self.prefix) else \
            " ".join(x for x in (self.prefix, name) if x)

    @property
    def birth(self) -> dt.date | None:
        return parse_thai_date(self.birth_date)

    def as_dict(self) -> dict:
        out = {f: getattr(self, f) for f in PERSON_FIELDS}
        out["address"] = self.address.as_dict()
        out["postcode_from"], out["warnings"] = self.postcode_from, list(self.warnings)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        p = cls(**{f: data.get(f, "") for f in PERSON_FIELDS})
        p.address = Address(**{k: (data.get("address") or {}).get(k, "") for k in ADDRESS_PARTS})
        p.postcode_from, p.warnings = data.get("postcode_from", ""), list(data.get("warnings") or [])
        return p


def _first(found: list[tuple[str, str, int, int]], f: str) -> str:
    return next((v for k, v, _r, _c in found if k == f and v), "")


def _address_text(text: str, found) -> str:
    """The value of ที่อยู่ plus the lines under it in the same column (a wrapped address), until another label
    or the province."""
    rows = _cells(text)
    for k, v, r, col in found:
        if k != "address" or not v:
            continue
        out = [v]
        for nxt in rows[r + 1:r + 4]:
            if province_name((out[-1].split() or [""])[-1]):
                break
            cell = next((x for x in nxt if x.start >= col - 12 and not _noise(x.text)), None)
            if cell is None:  # OCR may shift a wrapped line left: take it when it starts with อำเภอ/จังหวัด...
                cell = next((x for x in nxt if not _noise(x.text) and _PART_AT.match(x.text)), None)
            if cell is None:
                continue
            label = _LABEL_AT.match(cell.text)
            if label and _label_field(label.group(1)) not in ADDRESS_PARTS:
                break
            out.append(cell.text)
        return " ".join(out)
    return ""


_PREFIXED_NAME = re.compile(r"(?<![ก-๙])(นางสาว|นาย|นาง|เด็กชาย|เด็กหญิง|ด\.ช\.|ด\.ญ\.|น\.ส\.)\s?[ก-๙]+\s+[ก-๙]+")


def _name_without_label(text: str) -> str:
    """The first 'นาย/นาง/นางสาว + ชื่อ + สกุล' on the page that is not a parent's name (when the label was not
    read, e.g. by OCR)."""
    for line in text.split("\n"):
        for m in _PREFIXED_NAME.finditer(line):
            before = line[:m.start()]
            if not re.search(r"(บิดา|มารดา|เจ้าบ้าน)\s*:?\s*$", before):
                return m.group(0)
    return ""


def parse_person(text: str) -> Person:
    text = normalize(text)
    found = pairs(text)
    p = Person()
    # national ID: the labelled one, else the only valid 13-digit number on the page
    raw_id = re.sub(r"\D", "", _first(found, "national_id"))[:13]
    ids = find_ids(text)
    if len(raw_id) == 13:
        p.national_id = raw_id
        if not id_checksum_ok(raw_id):
            p.warnings.append("เลขประจำตัวประชาชนไม่ผ่านการตรวจหลักสุดท้าย (อาจอ่านผิด) ตรวจกับเอกสาร")
    elif len(ids) == 1:
        p.national_id = ids[0]
    elif len(ids) > 1:
        p.national_id = ids[0]
        p.warnings.append("พบเลขประจำตัวประชาชนหลายเลขในหน้าเดียว ตรวจว่าเป็นของบุคคลนี้")
    # name
    name = _first(found, "full_name") or _first(found, "name")
    if re.search(r"ชื่อ|สกุล", name):  # a misread label (OCR), not a name
        name = ""
    if not name and not (_first(found, "first_name") and _first(found, "last_name")):
        name = _name_without_label(text)
        if name:
            p.warnings.append("ไม่พบหัวข้อชื่อ-ชื่อสกุล ใช้ชื่อแรกที่พบในเอกสาร ตรวจว่าเป็นบุคคลนี้")
    if name:
        p.prefix, p.first_name, p.last_name = split_name(name)
    p.prefix = _first(found, "prefix") or p.prefix
    p.first_name = _first(found, "first_name") or p.first_name
    p.last_name = _first(found, "last_name") or p.last_name
    if p.prefix and p.first_name.startswith(p.prefix):
        p.first_name = p.first_name[len(p.prefix):].strip()
    # the rest
    g = _first(found, "gender")
    p.gender = "ชาย" if g.startswith("ชาย") else "หญิง" if g.startswith("หญิง") else g.split(" ")[0] if g else ""
    p.birth_date = _first(found, "birth_date")
    age = re.search(r"\d{1,3}", _first(found, "age"))
    p.age = age.group(0) if age and int(age.group(0)) <= 120 else ""
    if not p.age and p.birth:
        today = dt.date.today()
        p.age = str(today.year - p.birth.year - ((today.month, today.day) < (p.birth.month, p.birth.day)))
    p.nationality = _first(found, "nationality").split(" ")[0]
    code = re.sub(r"\D", "", _first(found, "house_code"))
    p.house_code = f"{code[:4]}-{code[4:10]}-{code[10:]}" if len(code) == 11 else _first(found, "house_code")
    p.owner_status = _first(found, "owner_status").split(" ")[0]
    p.person_status = _first(found, "person_status").split(" ")[0]
    p.moved_in = _first(found, "moved_in")
    # address: the ที่อยู่ line, with parts given as their own labelled fields filling what it lacks
    addr = parse_address(_address_text(text, found))
    for f in ADDRESS_PARTS:
        labelled = _first(found, f)
        if labelled and not getattr(addr, f):
            sub = parse_address(labelled) if f in ("subdistrict", "district", "province") else None
            value = getattr(sub, f) if sub is not None and getattr(sub, f) else labelled
            setattr(addr, f, value)
    _settle_area(addr)
    p.address = addr
    _postcode(p)
    _check(p)
    return p


def _settle_area(addr: Address) -> None:
    """Strip the labels, and in Bangkok keep แขวง/เขต names as they are in the table."""
    from apps.registry.address import strip_prefix

    for f in ("subdistrict", "district", "province"):
        setattr(addr, f, strip_prefix(f, getattr(addr, f)).strip(" ,"))
    province = province_name(addr.province)
    if province:
        addr.province = province
        d = _district_in(province, addr.district)
        if d:
            addr.district = d
        s = _subdistrict_in(province, addr.district, addr.subdistrict)
        if s:
            addr.subdistrict = s
    for f in ("house_no", "moo", "building", "alley", "soi", "road"):
        setattr(addr, f, clean_text(getattr(addr, f)).strip(" ,"))
    addr.moo = re.sub(r"^ที่\s*", "", addr.moo)


def _postcode(p: Person) -> None:
    from .postcode import lookup_postcode

    a = p.address
    if re.fullmatch(r"\d{5}", a.postcode or ""):
        p.postcode_from = "pdf"
        return
    code = lookup_postcode(a.subdistrict, a.district, a.province)
    if code:
        a.postcode, p.postcode_from = code, "table"


def _check(p: Person) -> None:
    if not p.national_id:
        p.warnings.append("ไม่พบเลขประจำตัวประชาชน")
    if not (p.first_name and p.last_name):
        p.warnings.append("อ่านชื่อ-ชื่อสกุลไม่ครบ")
    missing = p.address.missing_labels()
    if missing:
        p.warnings.append("ที่อยู่ยังขาด: " + ", ".join(missing))
    elif not province_name(p.address.province):
        p.warnings.append(f"ไม่รู้จักจังหวัด \"{p.address.province}\" ตรวจการสะกด")
    if p.address.province and not p.address.postcode:
        p.warnings.append("หารหัสไปรษณีย์จากตำบล/อำเภอ/จังหวัดไม่ได้ ตรวจการสะกด หรือกรอกเอง")
    if p.person_status and not re.search(r"มีชีวิต|ปกติ|ภูมิลำเนาอยู่ในบ้านนี้|-", p.person_status):
        p.warnings.append(f"สถานภาพบุคคล: {p.person_status}")


_PERSON_START = re.compile(_alt(LABELS["national_id"]))


def parse(text: str) -> list[Person]:
    """Every person in the text: a report may hold several (each starts at its เลขประจำตัวประชาชน)."""
    text = normalize(text)
    starts = [m.start() for m in _PERSON_START.finditer(text)]
    if len(starts) <= 1:
        return [parse_person(text)] if text.strip() else []
    # each person: from just after the previous one ended (labels like ชื่อ may come before the ID line)
    line_starts = [text.rfind("\n", 0, s) + 1 for s in starts]
    chunks = []
    for i, s in enumerate(line_starts):
        begin = 0 if i == 0 else s
        end = line_starts[i + 1] if i + 1 < len(line_starts) else len(text)
        chunks.append(text[begin:end])
    people = [parse_person(c) for c in chunks]
    return [p for p in people if p.national_id or p.first_name]


def read_pdf(data: bytes) -> tuple[list[Person], str, list[str], str]:
    """(people, how it was read, notes, the text) for one uploaded PDF."""
    text, how, notes = extract(data)
    people = parse(text) if how != "none" or readable(text) else []
    return people, how, notes, text
