"""นำเข้า PDF ทะเบียนราษฎร: reading the PDFs (text and scanned), addresses (province and Bangkok แขวง/เขต),
postcodes, matching to the cases of the user (ใครคดีมัน), the review page and issuing the summons.
The PDFs in sample_data/dopa/ are made up (tools/make_dopa_samples.py)."""
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role, User
from apps.imports import dopa
from apps.imports.dopa_match import cases_for, match
from apps.imports.models import DopaRecord, DopaState
from apps.registry.address import Address, same_address
from apps.registry.models import AddressOverride, Case, Investigator

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data" / "dopa"
PROVINCE_ID, BANGKOK_ID, SECOND_ID = "1101700123456", "3100601234565", "1509900543215"


def pdf(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


# --------------------------------------------------------------------------- reading the PDFs
def test_text_pdf_in_a_province():
    people, how, notes, _ = dopa.read_pdf(pdf("province.pdf"))
    assert how == "text" and notes == [] and len(people) == 1
    p = people[0]
    assert (p.national_id, p.prefix, p.first_name, p.last_name) == (PROVINCE_ID, "นางสาว", "ขวัญใจ", "ทดสอบดี")
    assert (p.gender, p.birth_date, p.age, p.nationality) == ("หญิง", "12 มีนาคม 2535", "34", "ไทย")
    assert (p.house_code, p.owner_status, p.person_status, p.moved_in) == (
        "5101-012345-6", "ผู้อาศัย", "มีชีวิต", "5 มกราคม 2560")
    assert p.birth.isoformat() == "1992-03-12" and p.warnings == []
    a = p.address  # the address cell runs over two lines
    assert (a.house_no, a.moo, a.subdistrict, a.district, a.province) == ("38/2", "4", "บ้านกลาง", "เมืองลำพูน", "ลำพูน")
    assert a.postcode == "51000" and p.postcode_from == "table"  # not in the PDF: from the postcode table
    assert a.format() == "บ้านเลขที่ 38/2 หมู่ 4 ตำบลบ้านกลาง อำเภอเมืองลำพูน จังหวัดลำพูน 51000"


def test_text_pdf_in_bangkok_with_separate_fields():
    people, how, _, _ = dopa.read_pdf(pdf("bangkok.pdf"))
    p = people[0]
    assert how == "text" and (p.national_id, p.prefix, p.first_name, p.last_name) == (
        BANGKOK_ID, "นาย", "ประเสริฐ", "มั่นคงทดสอบ")
    a = p.address  # หมู่ที่ / ตรอก written as "-": left empty
    assert (a.house_no, a.moo, a.alley, a.soi, a.road) == ("99/1", "", "", "ลาดพร้าว 101", "ลาดพร้าว")
    assert (a.subdistrict, a.district, a.province, a.postcode) == ("ลาดยาว", "จตุจักร", "กรุงเทพมหานคร", "10900")
    assert a.format() == "บ้านเลขที่ 99/1 ซอยลาดพร้าว 101 ถนนลาดพร้าว แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900"
    assert a.lines() == ["99/1 ซอยลาดพร้าว 101 ถนนลาดพร้าว", "แขวงลาดยาว เขตจตุจักร", "กรุงเทพมหานคร"]


def test_the_dopa_printout_with_a_watermark_written_upwards():
    """Laid out like the real DOPA report (JasperReports): the watermark "พิมพ์วันที่ ... / ผู้พิมพ์รายงาน ..." runs up
    the page and scrambles the reading order of pdftotext -layout; the page is rebuilt from the positions instead."""
    data = pdf("dopa_report.pdf")
    text, rotated = dopa.bbox_layout(data)
    assert rotated == 18 and "พิมพ์วันที่" not in text  # the watermark is gone
    lines = [ln.split() for ln in text.split("\n")]
    assert ["ชื่อ-ชื่อสกุล", "นางสาวมะลิ", "ทดสอบใจ", "เพศ", "หญิง"] in lines  # a label and its value on one line
    people, how, notes, _ = dopa.read_pdf(data)
    p = people[0]
    assert how == "text" and notes == [] and p.warnings == []
    assert (p.national_id, p.prefix, p.first_name, p.last_name) == ("1709900112239", "นางสาว", "มะลิ", "ทดสอบใจ")
    assert (p.gender, p.birth_date, p.age, p.nationality) == ("หญิง", "3 ก.ย. 2541", "28", "ไทย")
    assert (p.house_code, p.owner_status, p.moved_in) == ("1030-012345-1", "ผู้อาศัย", "15 ม.ค. 2565")
    assert p.person_status == "บุคคลนี้มีภูมิลำเนาอยู่ในบ้านนี้"
    a = p.address  # Bangkok, under the labels ตำบล / อำเภอ; ตรอก and หมู่ที่ left blank in the report
    assert (a.house_no, a.moo, a.alley, a.soi, a.road) == ("77/5", "", "", "พหลโยธิน 32", "พหลโยธิน")
    assert a.format() == ("บ้านเลขที่ 77/5 ซอยพหลโยธิน 32 ถนนพหลโยธิน แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900")


def test_two_people_in_one_file():
    people, _, _, _ = dopa.read_pdf(pdf("two_people.pdf"))
    assert [p.national_id for p in people] == [PROVINCE_ID, SECOND_ID]
    second = people[1]  # address over three lines, with ซอย and ถนน
    assert second.full_name == "นายสมศักดิ์ รักทดสอบ"
    assert second.address.format() == ("บ้านเลขที่ 7 หมู่ 2 ซอยวัดป่า ถนนโชตนา ตำบลดอนแก้ว อำเภอแม่ริม "
                                       "จังหวัดเชียงใหม่ 50180")


def test_scanned_pdf_without_ocr_says_what_to_install(monkeypatch):
    monkeypatch.setattr(dopa, "ocr_status", lambda: "missing")
    people, how, notes, _ = dopa.read_pdf(pdf("scanned.pdf"))
    assert how == "none" and people == []
    assert "ภาพสแกน" in notes[0] and "tesseract-ocr-tha" in notes[1]


@pytest.mark.skipif(dopa.ocr_status() != "ok", reason="Tesseract with Thai is not installed here")
def test_scanned_pdf_read_with_ocr():
    people, how, notes, _ = dopa.read_pdf(pdf("scanned.pdf"))
    assert how == "ocr" and "OCR" in notes[0]
    p = people[0]
    assert p.national_id == PROVINCE_ID and p.full_name == "นางสาวขวัญใจ ทดสอบดี"
    assert p.address.format() == "บ้านเลขที่ 38/2 หมู่ 4 ตำบลบ้านกลาง อำเภอเมืองลำพูน จังหวัดลำพูน 51000"


def test_without_poppler_pypdf_reads_the_text(monkeypatch):
    real = dopa.shutil.which
    monkeypatch.setattr(dopa.shutil, "which", lambda name: None if name == "pdftotext" else real(name))
    people, how, notes, _ = dopa.read_pdf(pdf("bangkok.pdf"))
    assert how == "text" and people[0].national_id == BANGKOK_ID
    assert "poppler-utils" in notes[0]  # Thai may come out garbled: the staff are told to check every field


def test_the_dopa_printout_without_poppler(monkeypatch):
    """A computer with no poppler (e.g. run without Docker): pypdf gives each piece of text with its position, so
    the watermark written upwards is dropped there too - it used to end up in ถนน / อำเภอ / จังหวัด."""
    real = dopa.shutil.which
    monkeypatch.setattr(dopa.shutil, "which", lambda name: None if name == "pdftotext" else real(name))
    text, rotated = dopa.pypdf_layout(pdf("dopa_report.pdf"))
    assert rotated == 18 and "พิมพ์วันที่" not in text
    people, how, notes, _ = dopa.read_pdf(pdf("dopa_report.pdf"))
    p = people[0]
    assert how == "text" and "poppler-utils" in notes[0] and p.warnings == []
    assert p.national_id == "1709900112239" and p.full_name == "นางสาวมะลิ ทดสอบใจ"
    assert p.address.format() == ("บ้านเลขที่ 77/5 ซอยพหลโยธิน 32 ถนนพหลโยธิน แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900")


def test_not_a_pdf():
    assert dopa.read_pdf(b"hello") == ([], "none", ["ไม่ใช่ไฟล์ PDF"], "")


# --------------------------------------------------------------------------- addresses
@pytest.mark.parametrize("text, expected", [
    ("12/3 หมู่ที่ 4 ซอยวัดใหม่ ถนนสุขุมวิท ตำบลบ้านกลาง อำเภอเมืองลำพูน จังหวัดลำพูน 51000",
     "บ้านเลขที่ 12/3 หมู่ 4 ซอยวัดใหม่ ถนนสุขุมวิท ตำบลบ้านกลาง อำเภอเมืองลำพูน จังหวัดลำพูน 51000"),
    # Bangkok: แขวง/เขต glued together, กทม. / กรุงเทพฯ, and no labels at all
    ("99/1 แขวงลาดยาวเขตจตุจักร กรุงเทพมหานคร", "บ้านเลขที่ 99/1 แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900"),
    ("88 ลาดยาว จตุจักร กทม.", "บ้านเลขที่ 88 แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900"),
    ("45 ซอยลาดพร้าว 101 แขวงคลองเจ้าคุณสิงห์ เขตวังทองหลาง กรุงเทพฯ",
     "บ้านเลขที่ 45 ซอยลาดพร้าว 101 แขวงคลองเจ้าคุณสิงห์ เขตวังทองหลาง กรุงเทพมหานคร 10310"),
    # abbreviations, Thai digits
    ("7 ม.2 ต.ดอนแก้ว อ.แม่ริม จ.เชียงใหม่", "บ้านเลขที่ 7 หมู่ 2 ตำบลดอนแก้ว อำเภอแม่ริม จังหวัดเชียงใหม่ 50180"),
    ("๑๒ หมู่ ๓ ตำบลวัดใหม่ อำเภอเมืองจันทบุรี จังหวัดจันทบุรี",
     "บ้านเลขที่ 12 หมู่ 3 ตำบลวัดใหม่ อำเภอเมืองจันทบุรี จังหวัดจันทบุรี 22000"),
    # village, ตรอก, building with floor and room
    ("1/22 หมู่บ้านพฤกษา ตรอกจันทน์ ถนนจันทน์ แขวงทุ่งวัดดอน เขตสาทร กรุงเทพมหานคร 10120",
     "บ้านเลขที่ 1/22 หมู่บ้านพฤกษา ตรอกจันทน์ ถนนจันทน์ แขวงทุ่งวัดดอน เขตสาทร กรุงเทพมหานคร 10120"),
    ("5/9 อาคารเดอะเทรเชอร์ ชั้น 5 ห้อง 501 ซอยประชาอุทิศ 33 แขวงบางมด เขตทุ่งครุ กรุงเทพมหานคร",
     "บ้านเลขที่ 5/9 อาคารเดอะเทรเชอร์ ชั้น 5 ห้อง 501 ซอยประชาอุทิศ 33 แขวงบางมด เขตทุ่งครุ กรุงเทพมหานคร 10140"),
])
def test_addresses_written_in_different_ways(text, expected):
    from apps.imports.postcode import lookup_postcode

    a = dopa.parse_address(text)
    if not a.postcode:
        a.postcode = lookup_postcode(a.subdistrict, a.district, a.province)
    assert a.format() == expected and "  " not in a.format()


def test_address_with_only_some_parts_has_no_extra_spaces():
    a = Address(house_no="9", subdistrict="ศรีภูมิ", district="เมืองเชียงใหม่", province="เชียงใหม่", postcode="50200")
    assert a.format() == "บ้านเลขที่ 9 ตำบลศรีภูมิ อำเภอเมืองเชียงใหม่ จังหวัดเชียงใหม่ 50200"
    assert Address(house_no="9", alley="ร่วมใจ", province="กทม.").format() == "บ้านเลขที่ 9 ตรอกร่วมใจ กรุงเทพมหานคร"


def test_old_saved_addresses_are_not_a_conflict_after_the_new_alley_field():
    old = {"house_no": "1", "moo": "", "building": "", "soi": "", "road": "", "subdistrict": "a", "district": "b",
           "province": "c", "postcode": ""}
    assert same_address(old, {**old, "alley": ""}) and not same_address(old, {**old, "alley": "x"})


# --------------------------------------------------------------------------- IDs and names
def test_id_numbers_with_or_without_dashes():
    assert dopa.id_checksum_ok(PROVINCE_ID) and not dopa.id_checksum_ok("1101700123450")
    assert dopa.find_ids("เลข 1-1017-00123-45-6 และ 3 1006 01234 56 5 และ 1234567890123") == [PROVINCE_ID, BANGKOK_ID]
    assert dopa.format_id(PROVINCE_ID) == "1-1017-00123-45-6"


def test_names_and_prefixes_never_guessed():
    assert dopa.split_name("นายสมชาย ใจดี") == ("นาย", "สมชาย", "ใจดี")
    assert dopa.split_name("น.ส. สมหญิง ใจงาม") == ("น.ส.", "สมหญิง", "ใจงาม")
    assert dopa.split_name("พ.ต.ต.สมศักดิ์ รักดี") == ("พ.ต.ต.", "สมศักดิ์", "รักดี")
    assert dopa.split_name("สมใจ ทดสอบ") == ("", "สมใจ", "ทดสอบ")  # no prefix: none added


def test_thai_dates():
    assert dopa.parse_thai_date("12 มี.ค. 2535").isoformat() == "1992-03-12"
    assert dopa.parse_thai_date("๑ ม.ค. ๒๕๒๐").isoformat() == "1977-01-01"
    assert dopa.parse_thai_date("05/01/2560").isoformat() == "2017-01-05"
    assert dopa.parse_thai_date("ไม่ทราบ") is None


# --------------------------------------------------------------------------- matching (ใครคดีมัน)
@pytest.fixture
def caller(imported):
    """A พนักงานสอบสวน account (the only kind that may use the registration PDFs) owning every sample case."""
    from apps.registry.models import InvestigatorAlias

    inv = InvestigatorAlias.objects.filter(investigator__isnull=False).first().investigator
    InvestigatorAlias.objects.update(investigator=inv)
    return User.objects.create_user("inv_owner", password="pass12345", role=Role.INVESTIGATOR, first_name="สมใจ",
                                    investigator=inv)


def _case_with_id(national_id: str) -> Case:
    case = Case.objects.not_final().filter(complainant__isnull=False).first()
    case.complainant.national_id = dopa.format_id(national_id)
    case.complainant.save()
    return case


def test_match_by_id_receipt_in_file_name_or_name(imported, caller):
    person = dopa.read_pdf(pdf("province.pdf"))[0][0]
    case = _case_with_id(PROVINCE_ID)
    assert match(person, "x.pdf", caller)[:2] == (case, "เลขประจำตัวประชาชนตรงกับไฟล์ผู้แจ้ง")
    other = Case.objects.exclude(pk=case.pk).first()
    nobody = dopa.Person(first_name="ไม่มี", last_name="ในระบบ")
    assert match(nobody, f"{other.receipt_no}_ทะเบียนราษฎร.pdf", caller)[:2] == (other, "เลขรับแจ้งในชื่อไฟล์")
    assert match(nobody, "x.pdf", caller)[0] is None
    assert match(nobody, "x.pdf", caller, chosen=other)[:2] == (other, "เลือกจากหน้าคดี")


def test_a_linked_investigator_sees_only_their_own_cases(imported, caller):
    case = _case_with_id(PROVINCE_ID)
    mine = Investigator.objects.create(sign_name="ทดสอบ เจ้าของคดี")
    other = Investigator.objects.create(sign_name="คนอื่น ทดสอบ")
    case.investigator_alias.investigator = other
    case.investigator_alias.save()
    officer = User.objects.create_user("inv_d", password="pass12345", role=Role.INVESTIGATOR, investigator=mine)
    person = dopa.read_pdf(pdf("province.pdf"))[0][0]
    assert not cases_for(officer).filter(pk=case.pk).exists()
    assert match(person, "x.pdf", officer)[0] is None  # someone else's case: not matched, not offered
    case.investigator_alias.investigator = mine
    case.investigator_alias.save()
    assert match(person, "x.pdf", officer)[0] == case


# --------------------------------------------------------------------------- pages
def upload(client, name, **extra):
    return client.post("/imports/dopa/", {"files": SimpleUploadedFile(name, pdf(name), "application/pdf"), **extra})


def form_values(rec, **change):
    p = rec.person()
    values = {k: getattr(p.address, k) for k in ("house_no", "moo", "building", "alley", "soi", "road",
                                                   "subdistrict", "district", "province", "postcode")}
    values.update(receipt_no=rec.case.receipt_no if rec.case else "", recipient_name=p.full_name)
    values.update(change)
    return values


def test_upload_check_save_then_issue(client, setup_signers, caller):
    case = _case_with_id(BANGKOK_ID)
    client.force_login(caller)
    resp = upload(client, "bangkok.pdf")
    rec = DopaRecord.objects.get()
    assert resp["Location"] == f"/imports/dopa/{rec.pk}/" and rec.case == case and rec.read_by == "text"
    assert rec.national_id == BANGKOK_ID and rec.uploaded_by == caller
    page = client.get(f"/imports/dopa/{rec.pk}/").content.decode()
    assert "เลขประจำตัวประชาชนตรงกับไฟล์ผู้แจ้งของคดี" in page and "แขวงลาดยาว เขตจตุจักร" in page
    assert BANGKOK_ID not in page and "x-xxxx-xxxxx-56-5" in page  # the ID number stays hidden
    resp = client.post(f"/imports/dopa/{rec.pk}/", {**form_values(rec), "action": "save_issue"})
    assert resp["Location"] == f"/cases/{case.pk}/issue/"
    o = AddressOverride.objects.get(receipt_no=case.receipt_no)
    assert (o.subdistrict, o.district, o.province, o.postcode) == ("ลาดยาว", "จตุจักร", "กรุงเทพมหานคร", "10900")
    assert o.updated_by == caller and "ทะเบียนราษฎร" in o.note
    case = Case.objects.get(pk=case.pk)
    assert case.effective_address().format().endswith("แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร 10900")
    assert case.recipient_name() == "นายประเสริฐ มั่นคงทดสอบ" and not case.address_conflict()
    rec.refresh_from_db()
    assert rec.state == DopaState.APPLIED and rec.page_text == "" and rec.done_by == caller
    # the summons gets the Bangkok address (แขวง/เขต)
    import datetime as dt

    from apps.documents.placeholders import build_values
    values = build_values(case=case, issue_date=dt.date(2026, 11, 26), appointment_at=dt.datetime(2026, 12, 10, 9),
                          place="กก.1", reason="สอบปากคำ", issuer=case.investigator, number=1, thai_digits=False)
    assert "แขวงลาดยาว เขตจตุจักร กรุงเทพมหานคร" in values["ที่อยู่"]
    assert values["ที่อยู่หลายบรรทัด"].split("\n")[1] == "แขวงลาดยาว เขตจตุจักร"
    from apps.core.models import AuditLog
    assert AuditLog.objects.filter(detail__contains="ที่อยู่จาก PDF ทะเบียนราษฎร").exists()


def test_fix_a_field_choose_the_case_and_discard(client, imported, caller):
    client.force_login(caller)
    upload(client, "two_people.pdf")
    first, second = DopaRecord.objects.order_by("pk")
    assert first.case is None and second.case is None  # nobody in the sample cases has these ID numbers
    case = Case.objects.not_final().first()
    resp = client.post(f"/imports/dopa/{first.pk}/", {**form_values(first, receipt_no=case.receipt_no),
                                                         "action": "set_case"})
    first.refresh_from_db()
    assert resp.status_code == 302 and first.case == case and first.match_by == "เลือกเอง"
    # the staff fix the road read from the document, then save
    client.post(f"/imports/dopa/{first.pk}/", {**form_values(first, road="สายเอเชีย"), "action": "save"})
    o = AddressOverride.objects.get(receipt_no=case.receipt_no)
    assert o.road == "สายเอเชีย" and o.postcode == "51000"
    resp = client.post(f"/imports/dopa/{second.pk}/", {"action": "discard"})
    second.refresh_from_db()
    assert second.state == DopaState.DISCARDED and second.page_text == ""


def test_cannot_save_to_a_case_of_another_investigator(client, imported):
    case = Case.objects.not_final().first()
    mine = Investigator.objects.create(sign_name="ทดสอบ เจ้าของ")
    officer = User.objects.create_user("inv_e", password="pass12345", role=Role.INVESTIGATOR, investigator=mine)
    client.force_login(officer)
    upload(client, "province.pdf", case=case.pk)  # not theirs: the case from the link is not used
    rec = DopaRecord.objects.get()
    assert rec.case is None
    resp = client.post(f"/imports/dopa/{rec.pk}/", {**form_values(rec, receipt_no=case.receipt_no), "action": "save"})
    assert "ไม่พบเลขรับแจ้งนี้ในคดีที่คุณดูแล" in resp.content.decode()
    assert not AddressOverride.objects.filter(receipt_no=case.receipt_no).exists()


def test_other_users_records_are_private(client, imported, caller):
    client.force_login(caller)
    upload(client, "province.pdf")
    rec = DopaRecord.objects.get()
    other = User.objects.create_user("inv_x", password="pass12345", role=Role.INVESTIGATOR,
                                     investigator=Investigator.objects.create(sign_name="อีก คนหนึ่ง"))
    client.force_login(other)
    assert client.get(f"/imports/dopa/{rec.pk}/").status_code == 404
    assert "ขวัญใจ" not in client.get("/imports/dopa/").content.decode()


def test_upload_limits_and_wrong_files(client, imported, caller):
    client.force_login(caller)
    resp = client.post("/imports/dopa/", {"files": SimpleUploadedFile("a.pdf", b"not a pdf")}, follow=True)
    assert "ไม่ใช่ไฟล์ PDF" in resp.content.decode() and not DopaRecord.objects.exists()
    page = client.get("/imports/dopa/").content.decode()
    assert "นำเข้า PDF ทะเบียนราษฎร" in page and 'multiple' in page


def test_links_to_the_import_from_the_case_pages(client, imported, caller):
    case = Case.objects.not_final().first()
    client.force_login(caller)
    assert f'/imports/dopa/?case={case.pk}' in client.get(f"/calls/{case.pk}/").content.decode()
    assert 'href="/imports/dopa/"' in client.get("/calls/").content.decode()  # the menu
    page = client.get(f"/imports/dopa/?case={case.pk}").content.decode()
    assert f'name="case" value="{case.pk}"' in page


def test_only_investigator_accounts_have_the_registration_pdfs(client, admin_user, imported, caller):
    """ทะเบียนราษฎร: on the pages of พนักงานสอบสวน accounts only - not administrators, not call staff, and not an
    investigator account that is not linked yet."""
    client.force_login(caller)
    upload(client, "province.pdf")
    rec = DopaRecord.objects.get()
    staff = User.objects.create_user("call_z", password="pass12345", role=Role.OUTSOURCE)
    lone = User.objects.create_user("inv_lone", password="pass12345", role=Role.INVESTIGATOR)
    case = Case.objects.not_final().first()
    for user, pages in ((admin_user, ("/", f"/cases/{case.pk}/", f"/calls/{case.pk}/", "/calls/", "/imports/")),
                        (staff, (f"/calls/{case.pk}/", "/calls/", "/imports/")),
                        (lone, ("/calls/",))):
        client.force_login(user)
        for url in pages:
            html = client.get(url).content.decode()
            assert "/imports/dopa/" not in html and "ทะเบียนราษฎร" not in html, (user.username, url)
        for url in ("/imports/dopa/", f"/imports/dopa/{rec.pk}/", f"/imports/dopa/?case={case.pk}"):
            assert client.get(url).status_code == 403, (user.username, url)
        assert client.post("/imports/dopa/", {"files": SimpleUploadedFile("a.pdf", pdf("province.pdf"))}
                           ).status_code == 403
        assert client.post(f"/imports/dopa/{rec.pk}/", {"action": "discard"}).status_code == 403
    assert DopaRecord.objects.count() == 1 and DopaRecord.objects.get().state == DopaState.PENDING


def test_old_records_are_removed_with_the_old_files(imported, caller):
    import datetime as dt

    from django.core.management import call_command
    from django.utils import timezone

    old = DopaRecord.objects.create(file_name="a.pdf", file_hash="x", uploaded_by=caller)
    DopaRecord.objects.filter(pk=old.pk).update(uploaded_at=timezone.now() - dt.timedelta(days=400))
    new = DopaRecord.objects.create(file_name="b.pdf", file_hash="y", uploaded_by=caller)
    call_command("cleanup_media")
    assert list(DopaRecord.objects.values_list("pk", flat=True)) == [new.pk]
