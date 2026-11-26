"""Make the made-up civil registration (DOPA) report PDFs in sample_data/dopa/ used by the tests.

Every person, number and address here is invented (the ID numbers only pass the check digit).
Needs LibreOffice (soffice) and poppler (pdftoppm) on the machine:

    python tools/make_dopa_samples.py

Four layouts, as such reports come:
  province.pdf   a two-column table, the address in one cell over two lines (ตำบล/อำเภอ/จังหวัด)
  bangkok.pdf    "label : value" lines, the address in separate fields (ตำบล/แขวง, อำเภอ/เขต) in Bangkok
  two_people.pdf two people in one file (one per page)
  scanned.pdf    the province page as a picture only (no text: needs OCR)
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sample_data" / "dopa"
FONT = "TH SarabunPSK"


def make_id(first12: str) -> str:
    total = sum(int(first12[i]) * (13 - i) for i in range(12))
    return first12 + str((11 - total % 11) % 10)


def dashed(d: str) -> str:
    return f"{d[0]}-{d[1:5]}-{d[5:10]}-{d[10:12]}-{d[12]}"


PEOPLE = {
    "province": {
        "id": make_id("110170012345"), "name": "นางสาวขวัญใจ ทดสอบดี", "gender": "หญิง",
        "birth": "12 มีนาคม 2535", "age": "34 ปี", "nationality": "ไทย", "house": "5101-012345-6",
        "owner": "ผู้อาศัย", "status": "มีชีวิต", "moved": "5 มกราคม 2560",
        "father": "นายสมหมาย ทดสอบดี", "mother": "นางสมศรี ทดสอบดี",
        "address": ["38/2 หมู่ที่ 4 ตำบลบ้านกลาง", "อำเภอเมืองลำพูน จังหวัดลำพูน"],
    },
    "bangkok": {
        "id": make_id("310060123456"), "prefix": "นาย", "first": "ประเสริฐ", "last": "มั่นคงทดสอบ",
        "gender": "ชาย", "birth": "1 ม.ค. 2520", "nationality": "ไทย", "house": "1030-004567-8",
        "owner": "เจ้าบ้าน", "status": "มีชีวิต", "moved": "20 ส.ค. 2555",
        "house_no": "99/1", "moo": "-", "alley": "-", "soi": "ลาดพร้าว 101", "road": "ลาดพร้าว",
        "sub": "ลาดยาว", "dist": "จตุจักร", "prov": "กรุงเทพมหานคร",
    },
    "second": {
        "id": make_id("150990054321"), "name": "นายสมศักดิ์ รักทดสอบ", "gender": "ชาย",
        "birth": "3 กรกฎาคม 2530", "age": "39 ปี", "nationality": "ไทย", "house": "5003-011111-2",
        "owner": "เจ้าบ้าน", "status": "มีชีวิต", "moved": "1 มิถุนายน 2558",
        "father": "นายมา รักทดสอบ", "mother": "นางมี รักทดสอบ",
        "address": ["7 หมู่ที่ 2 ซอยวัดป่า", "ถนนโชตนา ตำบลดอนแก้ว", "อำเภอแม่ริม จังหวัดเชียงใหม่"],
    },
}


def _doc():
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name, style.font.size = FONT, Pt(16)
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(attr), FONT)
    return doc


def _title(doc, text):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run(text).bold = True


def table_page(doc, person):
    """Two columns of label / value pairs, as the DOPA linkage printout."""
    _title(doc, "รายการข้อมูลทะเบียนราษฎร (ข้อมูลบุคคล)")
    rows = [
        ("เลขประจำตัวประชาชน", dashed(person["id"]), "เลขรหัสประจำบ้าน", person["house"]),
        ("ชื่อ-ชื่อสกุล", person["name"], "เพศ", person["gender"]),
        ("วันเดือนปีเกิด", person["birth"], "อายุ", person["age"]),
        ("สัญชาติ", person["nationality"], "สถานภาพบุคคล", person["status"]),
        ("สถานภาพเจ้าบ้าน", person["owner"], "วันที่ย้ายเข้า", person["moved"]),
        ("ชื่อบิดา", person["father"], "ชื่อมารดา", person["mother"]),
        ("ที่อยู่", "\n".join(person["address"]), "", ""),
    ]
    table = doc.add_table(rows=len(rows), cols=4)
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            table.cell(r, c).text = value


def lines_page(doc, person):
    """'label : value' lines, the address as separate labelled fields."""
    _title(doc, "ข้อมูลทะเบียนราษฎร")
    p = person
    for line in [
        f"เลขประจำตัวประชาชน : {dashed(p['id'])}",
        f"คำนำหน้าชื่อ : {p['prefix']}          ชื่อตัว : {p['first']}          ชื่อสกุล : {p['last']}",
        f"เพศ : {p['gender']}          วันเดือนปีเกิด : {p['birth']}          สัญชาติ : {p['nationality']}",
        f"เลขรหัสประจำบ้าน : {p['house']}          สถานภาพเจ้าบ้าน : {p['owner']}",
        f"สถานภาพบุคคล : {p['status']}          วันที่ย้ายเข้า : {p['moved']}",
        f"บ้านเลขที่ : {p['house_no']}          หมู่ที่ : {p['moo']}          ตรอก : {p['alley']}",
        f"ซอย : {p['soi']}          ถนน : {p['road']}",
        f"ตำบล/แขวง : {p['sub']}          อำเภอ/เขต : {p['dist']}          จังหวัด : {p['prov']}",
    ]:
        doc.add_paragraph(line)


def to_pdf(doc, name: str, tmp: Path) -> Path:
    src = tmp / f"{name}.docx"
    doc.save(src)
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(src)],
                   check=True, capture_output=True, timeout=180)
    return tmp / f"{name}.pdf"


def main() -> int:
    from docx.enum.section import WD_SECTION
    from docx.shared import Inches

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        doc = _doc()
        table_page(doc, PEOPLE["province"])
        (OUT / "province.pdf").write_bytes(to_pdf(doc, "province", tmp).read_bytes())
        doc = _doc()
        lines_page(doc, PEOPLE["bangkok"])
        (OUT / "bangkok.pdf").write_bytes(to_pdf(doc, "bangkok", tmp).read_bytes())
        doc = _doc()
        table_page(doc, PEOPLE["province"])
        doc.add_section(WD_SECTION.NEW_PAGE)
        table_page(doc, PEOPLE["second"])
        (OUT / "two_people.pdf").write_bytes(to_pdf(doc, "two", tmp).read_bytes())
        # the province page as a picture only
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-singlefile", str(OUT / "province.pdf"), str(tmp / "pic")],
                       check=True, timeout=60)
        doc = _doc()
        doc.add_picture(str(tmp / "pic.png"), width=Inches(6.3))
        (OUT / "scanned.pdf").write_bytes(to_pdf(doc, "scanned", tmp).read_bytes())
    for f in sorted(OUT.glob("*.pdf")):
        print(f.relative_to(ROOT), f.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
