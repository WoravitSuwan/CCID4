from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from apps.documents import docx_engine


def _split_runs(paragraph, pieces):
    for piece in pieces:
        paragraph.add_run(piece)


def _textbox_paragraph(text_runs):
    runs = "".join(f"<w:r><w:t xml:space='preserve'>{t}</w:t></w:r>" for t in text_runs)
    xml = (
        f"<w:p {nsdecls('w')} xmlns:v='urn:schemas-microsoft-com:vml'><w:r><w:pict><v:shape><v:textbox><w:txbxContent>"
        f"<w:p>{runs}</w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>"
    )
    return parse_xml(xml)


def build_template(path):
    doc = Document()
    p = doc.add_paragraph()
    _split_runs(p, ["เรียน ", "{", "ชื่อ", "ผู้แจ้ง", "}", " ที่อยู่ {ที่", "อยู่}"])
    p.runs[1].bold = True
    table = doc.add_table(rows=1, cols=2)
    _split_runs(table.cell(0, 0).paragraphs[0], ["{วัน", "ปัจจุบัน}"])
    nested = table.cell(0, 1).add_table(rows=1, cols=1)
    _split_runs(nested.cell(0, 0).paragraphs[0], ["ซ้อน {เดือน", "ปัจจุบัน} จบ"])
    doc.element.body.insert(0, _textbox_paragraph(["กล่อง {ปี", "ปัจจุบัน", "}"]))
    section = doc.sections[0]
    _split_runs(section.header.paragraphs[0], ["หัว {เลขรับ", "แจ้ง}"])
    _split_runs(section.footer.paragraphs[0], ["ท้าย {ครั้ง", "ที่}"])
    doc.save(path)


VALUES = {
    "ชื่อผู้แจ้ง": "นายสมมติ ทดสอบ", "ที่อยู่": "บ้านเลขที่ ๑", "วันปัจจุบัน": "๙",
    "เดือนปัจจุบัน": "กุมภาพันธ์", "ปีปัจจุบัน": "๖๙", "เลขรับแจ้ง": "L1", "ครั้งที่": "๑",
}


def test_replaces_everywhere(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    build_template(src)
    info = docx_engine.scan_placeholders(src)
    assert set(info["placeholders"]) == set(VALUES)
    report = docx_engine.render(src, VALUES, out)
    assert report.ok, report.problems()
    text = docx_engine.document_text(out)
    assert "{" not in text and "}" not in text
    for expected in ["เรียน นายสมมติ ทดสอบ ที่อยู่ บ้านเลขที่ ๑", "๙", "ซ้อน กุมภาพันธ์ จบ", "กล่อง ๖๙",
                     "หัว L1", "ท้าย ๑"]:
        assert expected in text


def test_keeps_first_run_format(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    p = doc.add_paragraph()
    r1 = p.add_run("{ชื่อ")
    r1.bold = True
    p.add_run("ผู้แจ้ง}")
    doc.save(src)
    docx_engine.render(src, VALUES, out)
    runs = Document(out).paragraphs[0].runs
    assert runs[0].text == "นายสมมติ ทดสอบ" and runs[0].bold


def test_unknown_and_missing_reported(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    doc.add_paragraph("{ไม่รู้จัก} {ที่อยู่}")
    doc.save(src)
    report = docx_engine.render(src, {"ที่อยู่": ""}, out)
    assert not report.ok
    assert report.unknown == {"ไม่รู้จัก"}
    assert report.missing == {"ที่อยู่"}
    assert report.leftovers


def test_legacy_plus14_row(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    t = doc.add_table(rows=1, cols=6)
    for i, text in enumerate(["ในวันที่", "+14", "เดือน", "", "พ.ศ. ๒๕", ""]):
        t.cell(0, i).paragraphs[0].text = text
    doc.save(src)
    assert docx_engine.scan_placeholders(src)["legacy_appointment"]
    report = docx_engine.render(src, {"วันนัด": "๒๓", "เดือนนัด": "กุมภาพันธ์", "ปีนัด": "๖๙"}, out)
    assert report.ok, report.problems()
    cells = [c.text for c in Document(out).tables[0].rows[0].cells]
    assert cells == ["ในวันที่", "๒๓", "เดือน", "กุมภาพันธ์", "พ.ศ. ๒๕", "๖๙"]


def test_show_hidden_text(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    doc.add_paragraph().add_run("ป้าย").font.hidden = True
    doc.save(src)
    docx_engine.render(src, {}, out, show_hidden=True)
    assert not Document(out).paragraphs[0].runs[0].font.hidden


def test_bundled_templates_scan():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "templates_docx"
    v2 = docx_engine.scan_placeholders(root / "summons_template_v2.docx")
    assert {"วันนัด", "เดือนนัด", "ปีนัด", "เวลานัด", "สถานที่นัด", "เหตุที่เรียก",
            "ยศผู้ออกหมาย", "ชื่อผู้ออกหมาย", "ตำแหน่งผู้ออกหมาย", "โทรศัพท์ผู้ออกหมาย"} <= set(v2["placeholders"])
    v1 = docx_engine.scan_placeholders(root / "summons_template_sanitized.docx")
    assert v1["legacy_appointment"]
