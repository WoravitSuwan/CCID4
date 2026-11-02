"""Build the Word templates used by the system (run once; outputs are committed).

    python tools/build_templates.py summons <original.docx>
    python tools/build_templates.py envelope

summons: from the agency's original form, writes
  templates_docx/summons_template_sanitized.docx  (v1: original layout, signer data
      replaced by {ยศผู้ออกหมาย} {ชื่อผู้ออกหมาย} {ตำแหน่งผู้ออกหมาย} {โทรศัพท์ผู้ออกหมาย},
      appointment day still the legacy '+14')
  templates_docx/summons_template_v2.docx         (v2: also {วันนัด} {เดือนนัด} {ปีนัด}
      {เวลานัด} {เหตุที่เรียก} {สถานที่นัด}, name/address on separate lines, typo fixes)
The original file is never modified. Signer data is located by pattern, so no
personal data appears in this script.

envelope: writes templates_docx/envelope_template.docx (A4 portrait, 1 recipient).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document  # noqa: E402
from docx.enum.section import WD_ORIENT  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt  # noqa: E402

from apps.documents.docx_engine import (  # noqa: E402
    _put_text_in_cell, iter_paragraphs, paragraph_text_nodes, replace_in_paragraph, text_parts,
    upgrade_legacy_appointment_row,
)

OUT = ROOT / "templates_docx"
FONT = "TH SarabunIT๙"
TYPO_FIXES = {"พยานบุคคคล": "พยานบุคคล", "สอบสอบสวน": "สอบสวน", "กกฎหมาย": "กฎหมาย", "กฏหมาย": "กฎหมาย"}


def ptext(p) -> str:
    return "".join(n.text or "" for n in paragraph_text_nodes(p))


def replace_span(p, start, end, text):
    replace_in_paragraph(p, lambda _full: [(start, end, text)])


def replace_from(p, pattern, text):
    """Replace from the first match of ``pattern`` to the end of the paragraph text."""
    full = ptext(p)
    m = re.search(pattern, full)
    if not m:
        return False
    end = len(full.rstrip())
    replace_span(p, m.start(), end, text)
    return True


def sanitize_signer(doc) -> list[str]:
    done = []
    for root in text_parts(doc):
        for p in iter_paragraphs(root):
            full = ptext(p)
            stripped = full.strip()
            if re.fullmatch(r"(พล|พัน|ร้อย|จ่าสิบ|สิบ|ดาบ)?ตำรวจ(เอก|โท|ตรี)(หญิง)?", stripped):
                replace_from(p, r"\S", "{ยศผู้ออกหมาย}")
                done.append("rank")
            elif re.fullmatch(r"\(\s*[^\s()][^()]*\)", stripped) and "ลงชื่อ" not in stripped:
                m = re.search(r"\(", full)
                n = full.rindex(")")
                replace_span(p, m.start(), n + 1, "(  {ชื่อผู้ออกหมาย}  )")
                done.append("name")
            elif stripped.startswith("สว.") or re.match(r"^(รอง\s*)?(ผกก|สว|รอง สว|พงส)\.?", stripped):
                replace_from(p, r"\S", "{ตำแหน่งผู้ออกหมาย}")
                done.append("position")
            elif stripped.startswith("โทรศัพท์หมายเลข"):
                m = re.search(r"โทรศัพท์หมายเลข\s*", full)
                replace_span(p, m.end(), len(full.rstrip()), "{โทรศัพท์ผู้ออกหมาย}")
                done.append("phone")
    return done


def clear_metadata(doc):
    cp = doc.core_properties
    cp.author = ""
    cp.last_modified_by = ""
    cp.comments = ""
    cp.title = "หมายเรียกผู้กล่าวหา/พยานบุคคล"


def build_summons(original: Path):
    # ---- v1: original layout, signer replaced by placeholders
    doc = Document(str(original))
    done = sanitize_signer(doc)
    missing = {"rank", "name", "position", "phone"} - set(done)
    if missing:
        raise SystemExit(f"could not find signer fields: {missing}")
    clear_metadata(doc)
    OUT.mkdir(exist_ok=True)
    v1 = OUT / "summons_template_sanitized.docx"
    doc.save(str(v1))

    # ---- v2: complete placeholders
    doc = Document(str(original))
    sanitize_signer(doc)
    for root in text_parts(doc):
        upgrade_legacy_appointment_row(root)
        rows = list(root.iter(qn("w:tr")))
        for p in iter_paragraphs(root):
            full = ptext(p)
            stripped = full.strip()
            for wrong, right in TYPO_FIXES.items():
                if wrong in full:
                    replace_in_paragraph(
                        p, lambda t, w=wrong, r=right: [(m.start(), m.end(), r) for m in re.finditer(w, t)])
                    full = ptext(p)
            if stripped == "+14":
                replace_from(p, r"\S", "{วันนัด}")
            elif re.fullmatch(r"[๐-๙0-9]{2}[.:][๐-๙0-9]{2}", stripped):
                replace_from(p, r"\S", "{เวลานัด}")
            elif stripped.startswith("ฉะนั้นให้"):
                replace_from(p, r"(?<=ท่าน)\s*\S", " {เหตุที่เรียก}")
            elif stripped.startswith("ไป ณ ที่"):
                m = re.search(r"ไป ณ ที่\s*", full)
                replace_span(p, m.end(), len(full.rstrip()), "{สถานที่นัด}")
            elif stripped == "{ชื่อผู้แจ้ง} {ที่อยู่}":
                replace_from(p, r"\s\{ที่อยู่\}", "")
        # name after 'หมายมายัง', address on the next row (after the hidden 'บ้านเลขที่' label,
        # which matches the pre-printed form)
        for idx, tr in enumerate(rows):
            cells = tr.findall(qn("w:tc"))
            texts = ["".join(t.text or "" for t in tc.iter(qn("w:t"))).strip() for tc in cells]
            if texts and texts[0] == "บ้านเลขที่" and len(cells) > 1 and not texts[1]:
                source = rows[idx - 1].findall(qn("w:tc"))[-1]
                _put_text_in_cell(cells[1], "{ที่อยู่}", source)
    clear_metadata(doc)
    v2 = OUT / "summons_template_v2.docx"
    doc.save(str(v2))
    print(f"wrote {v1}\nwrote {v2}")


# --------------------------------------------------------------------------- envelope
def _set_run_font(run, size_pt, bold=False):
    run.font.name = FONT
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)
    for tag in ("w:szCs",):
        el = rpr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            rpr.append(el)
        el.set(qn("w:val"), str(int(size_pt * 2)))
    if bold:
        b = rpr.find(qn("w:bCs"))
        if b is None:
            rpr.append(OxmlElement("w:bCs"))


def _para(doc, text, size, bold=False, indent_cm=0.0, space_before=0, space_after=0):
    p = doc.add_paragraph()
    fmt = p.paragraph_format
    fmt.left_indent = Cm(indent_cm)
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    fmt.line_spacing = 1.0
    _set_run_font(p.add_run(text), size, bold)
    return p


def build_envelope():
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    sec.left_margin = sec.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(16)
    rpr = normal.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), FONT)

    # sender: top-left, 16 pt
    _para(doc, "{ชื่อหน่วย}", 16, bold=True)
    _para(doc, "{ที่อยู่หน่วย}", 16)
    _para(doc, "ส่งในราชการ", 16, space_before=6)
    # recipient: lower half, right side, 20 pt (postcode larger)
    _para(doc, "กรุณาส่ง", 20, bold=True, indent_cm=7.0, space_before=300)
    _para(doc, "{ชื่อผู้แจ้ง}", 20, indent_cm=8.0, space_before=6)
    _para(doc, "{ที่อยู่ไม่รวมรหัส}", 20, indent_cm=8.0)
    _para(doc, "{รหัสไปรษณีย์}", 28, bold=True, indent_cm=8.0, space_before=6)
    doc.core_properties.title = "ใบจ่าหน้าซอง"
    doc.core_properties.author = ""
    OUT.mkdir(exist_ok=True)
    out = OUT / "envelope_template.docx"
    doc.save(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "summons":
        build_summons(Path(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "envelope":
        build_envelope()
    else:
        print(__doc__)
        raise SystemExit(2)
