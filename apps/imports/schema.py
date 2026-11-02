"""Expected columns for each import file kind (field name, header, required)."""
import re

from .models import ImportKind

COMPLAINANT_COLUMNS = [
    ("receipt_no", "เลขรับแจ้ง", True),
    ("prefix", "คำนำหน้าชื่อ", False),
    ("first_name", "ชื่อ", True),
    ("last_name", "นามสกุล", True),
    ("age", "อายุ", False),
    ("national_id", "เลขประจำตัวประชาชน", False),
    ("phone", "หมายเลขโทรศัพท์", False),
    ("house_no", "บ้านเลขที่", True),
    ("moo", "หมู่", False),
    ("building", "อาคาร/ตึก", False),
    ("soi", "ซอย", False),
    ("road", "ถนน", False),
    ("subdistrict", "ตำบล", True),
    ("district", "อำเภอ", True),
    ("province", "จังหวัด", True),
    ("postcode", "รหัสไปรษณีย์", False),
    ("residence", "ที่อยู่อาศัย", False),
    ("marital_status", "สถานภาพ", False),
    ("education", "ระดับการศึกษา", False),
    ("third_party", "บุคคลที่สาม", False),
    ("occupation", "อาชีพ", False),
    ("workplace", "สถานที่ทำงาน", False),
    ("income", "รายได้", False),
    ("father_name", "ชื่อบิดา", False),
    ("mother_name", "ชื่อมารดา", False),
    ("province_at_report", "จังหวัดที่อยู่ขณะที่แจ้ง", False),
    ("preferred_unit", "สะดวกที่หน่วยใด", False),
    ("case_type", "ประเภทคดี", False),
]

REGISTER_COLUMNS = [
    ("seq", "ลำดับ", False),
    ("receipt_no", "เลขรับแจ้ง", True),
    ("criminal_case_no", "เลขคดีอาญา", False),
    ("report_day", "วันที่รับแจ้ง", True),
    ("report_month", "เดือนที่รับแจ้ง", True),
    ("report_year", "ปีที่รับแจ้ง", True),
    ("victim_name", "ผู้เสียหาย", True),
    ("gender", "เพศ", False),
    ("age", "อายุ", False),
    ("case_type", "ประเภทคดี", False),
    ("case_type_l1", "ประเภทคดีระดับ 1", False),
    ("case_type_l2", "ประเภทคดีระดับ 2", False),
    ("case_type_l3", "ประเภทคดีระดับ 3", False),
    ("case_group", "ประเภทกลุ่มคดี", False),
    ("detail", "รายละเอียด", False),
    ("damage_amount", "มูลค่าความเสียหาย", False),
    ("bureau", "บช.", False),
    ("division", "บก./ภ.จว.", False),
    ("unit_raw", "หน่วยที่รับผิดชอบ", True),
    ("investigator_raw", "พนักงานสอบสวน", True),
    ("detective", "พนักงานสืบสวน", False),
    ("status_name", "สถานะ", True),
    ("victim_phone", "เบอร์โทรศัพท์ผู้เสียหาย", False),
    ("bank_ref", "เลขอ้างอิงธนาคาร", False),
    ("contact_log", "บันทึกการติดต่อ", False),
    ("daily_log", "ประจำวัน", False),
    ("remark", "หมายเหตุ", False),
]

COLUMNS = {
    ImportKind.COMPLAINANTS: COMPLAINANT_COLUMNS,
    ImportKind.REGISTER: REGISTER_COLUMNS,
}

DEFAULT_SHEET = {ImportKind.COMPLAINANTS: "Grid"}


def header_key(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def field_labels(kind) -> dict:
    return {f: h for f, h, _ in COLUMNS[kind]}


def auto_map(kind, headers, remembered: dict | None = None) -> dict:
    """Return {field: source_header} using standard names, then remembered choices."""
    remembered = remembered or {}
    by_key = {}
    for h in headers:
        by_key.setdefault(header_key(h), h)
    mapping = {}
    used = set()
    for field, header, _ in COLUMNS[kind]:
        src = by_key.get(header_key(header))
        if src is not None and src not in used:
            mapping[field] = src
            used.add(src)
    for src, field in remembered.items():
        if field not in mapping and src in headers and src not in used:
            mapping[field] = src
            used.add(src)
    return mapping


def missing_required(kind, mapping: dict) -> list:
    return [header for field, header, req in COLUMNS[kind] if req and not mapping.get(field)]
