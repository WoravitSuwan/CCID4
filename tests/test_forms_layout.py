"""Envelope / summons details matching the agency's printed forms."""
import zipfile

import pytest
from docx import Document

from apps.documents import docx_engine
from apps.documents.models import DocTemplate
from apps.documents.placeholders import check_section_53
from apps.registry.address import Address

pytestmark = pytest.mark.django_db


def test_address_lines_and_house_label():
    a = Address(house_no="12/3", moo="4", road="ท่าหลวง", subdistrict="ต.วัดใหม่", district="อ.เมืองจันทบุรี",
                province="จ.จันทบุรี", postcode="22000")
    assert a.lines() == ["12/3 หมู่ 4 ถนนท่าหลวง", "ตำบลวัดใหม่ อำเภอเมืองจันทบุรี", "จังหวัดจันทบุรี"]
    assert a.format(house_label=False).startswith("12/3 หมู่ 4")
    assert a.format().startswith("บ้านเลขที่ 12/3")
    bkk = Address(house_no="1", subdistrict="ลาดยาว", district="จตุจักร", province="กทม.")
    assert bkk.lines() == ["1", "แขวงลาดยาว เขตจตุจักร", "กรุงเทพมหานคร"]


def test_newline_values_become_line_breaks(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    doc.add_paragraph("{ที่อยู่หลายบรรทัด}")
    doc.save(src)
    report = docx_engine.render(src, {"ที่อยู่หลายบรรทัด": "บรรทัด1\nบรรทัด2"}, out)
    assert report.ok
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert "<w:br/>" in xml and "บรรทัด1" in xml and "บรรทัด2" in xml


def test_optional_placeholders(tmp_path):
    src, out = tmp_path / "t.docx", tmp_path / "o.docx"
    doc = Document()
    doc.add_paragraph("{รหัสไปรษณีย์}")
    doc.save(src)
    assert not docx_engine.render(src, {"รหัสไปรษณีย์": ""}, out).ok
    assert docx_engine.render(src, {"รหัสไปรษณีย์": ""}, out, optional={"รหัสไปรษณีย์"}).ok


def test_section53_accepts_address_variants():
    values = {"ที่อยู่หลังบ้านเลขที่": "12 หมู่ 3"}
    info = {"placeholders": ["ที่อยู่หลังบ้านเลขที่"]}
    assert not any("ที่อยู่" in p for p in check_section_53(values, info, "สถานที่ออกหมาย"))


def test_bundled_templates_registered_and_labels_shown(setup_signers):
    active_summons = DocTemplate.active("summons")
    assert active_summons.version == 2 and active_summons.show_hidden_text
    env = DocTemplate.active("envelope")
    assert env.version == 3  # government envelope on A4 portrait (v2 landscape stays selectable)
    assert DocTemplate.objects.filter(kind="envelope", version=2).exists()
    assert {"ชื่อหน่วย", "ที่อยู่หน่วย", "ใบอนุญาตฝากส่ง", "ชื่อผู้แจ้ง", "ที่อยู่หลายบรรทัด",
            "รหัสไปรษณีย์"} <= set(env.placeholders)


def test_envelope_uses_arabic_digits_and_permit(setup_signers, admin_user, tmp_path):
    import datetime as dt

    from apps.documents.services import IssueOptions, issue, plan_issue, render_envelope
    from apps.registry.models import Case

    case = next(c for c in Case.objects.not_final() if not c.issue_blockers())
    opts = IssueOptions(issue_date=dt.date(2026, 2, 9), appointment_date=dt.date(2026, 2, 23))
    [summons] = issue(admin_user, plan_issue(admin_user, [case], opts), opts)
    out = render_envelope(summons, tmp_path / "env.docx")
    text = docx_engine.document_text(out)
    assert "(ใช้ในราชการสำนักงานตำรวจแห่งชาติ)" in text and "กรุณาส่ง" in text
    # like the printed envelope: Arabic digits everywhere except the postage-permit box (๑๙/๒๕๖๘)
    assert "ใบอนุญาตที่ ๑๙/๒๕๖๘" in text
    rest = text.replace("ใบอนุญาตที่ ๑๙/๒๕๖๘", "")
    assert not any(ch in rest for ch in "๐๑๒๓๔๕๖๗๘๙") and "เลขที่ 370" in text
    assert "{" not in text
    # the summons itself keeps Thai digits and shows the address after the 'บ้านเลขที่' label
    stext = docx_engine.document_text(summons.docx_file.path)
    assert "บ้านเลขที่" in stext and "บ้านเลขที่ บ้านเลขที่" not in stext
