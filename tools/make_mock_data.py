"""Generate fictional sample files with the exact structure of the real exports.

    python tools/make_mock_data.py [output_dir]   (default: sample_data/)

All names, numbers and addresses are invented. The numbers are fixed so the
acceptance tests can rely on them:

* complainants: 50 rows, register: 40 cases (sheet 2568 = 15, sheet 2569 = 25),
  all 40 match; 10 complainants are not in the register
* 32 cases are not final; among them 5 have no house number, 2 have no
  province, 1 has neither  -> 6 cases with an incomplete address
* over all 40 cases: 6 without house number, 3 without province
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.imports.postcode import lookup_postcode  # noqa: E402  (pure python, no Django needed)

COMPLAINANT_HEADERS = [
    "เลขรับแจ้ง", "คำนำหน้าชื่อ", "ชื่อ", "นามสกุล", "อายุ", "เลขประจำตัวประชาชน", "หมายเลขโทรศัพท์",
    "บ้านเลขที่", "หมู่", "อาคาร/ตึก", "ซอย", "ถนน", "ตำบล", "อำเภอ", "จังหวัด", "รหัสไปรษณีย์",
    "ที่อยู่อาศัย", "สถานภาพ", "ระดับการศึกษา", "บุคคลที่สาม", "อาชีพ", "สถานที่ทำงาน", "รายได้",
    "ชื่อบิดา", "ชื่อมารดา", "จังหวัดที่อยู่ขณะที่แจ้ง", "สะดวกที่หน่วยใด", "ประเภทคดี",
]
REGISTER_HEADERS = [
    "ลำดับ", "เลขรับแจ้ง", "เลขคดีอาญา", "วันที่รับแจ้ง", "เดือนที่รับแจ้ง", "ปีที่รับแจ้ง", "ผู้เสียหาย",
    "เพศ", "อายุ", "ประเภทคดี", "ประเภทคดีระดับ 1", "ประเภทคดีระดับ 2", "ประเภทคดีระดับ 3",
    "ประเภทกลุ่มคดี", "รายละเอียด", "มูลค่าความเสียหาย", "บช.", "บก./ภ.จว.", "หน่วยที่รับผิดชอบ",
    "พนักงานสอบสวน", "พนักงานสืบสวน", "สถานะ", "เบอร์โทรศัพท์ผู้เสียหาย", "เลขอ้างอิงธนาคาร",
    "บันทึกการติดต่อ", "ประจำวัน", "หมายเหตุ",
]

FIRST_M = ["สมชาย", "ประเสริฐ", "วิชัย", "สุรชัย", "อนุชา", "ธีรวัฒน์", "ณัฐพล", "กิตติพงษ์", "อภิชาติ", "ชาญวิทย์"]
FIRST_F = ["สมศรี", "วรรณา", "สุภาพร", "กนกวรรณ", "พิมพ์ชนก", "ศิริพร", "อรอุมา", "จันทร์เพ็ญ", "นภัสสร", "ปิยะดา"]
LAST = ["ทดสอบดี", "สมมติกุล", "ตัวอย่างงาม", "ใจสมมติ", "แสงทดลอง", "มั่นทดสอบ", "บุญตัวอย่าง",
        "ศรีสมมติ", "รุ่งทดลอง", "วงศ์ตัวอย่าง", "ทองสมมติ", "แก้วทดสอบ"]
# Real ตำบล/อำเภอ/จังหวัด so the postcode auto-fill can be tested.
AREAS = [
    ("ดอนแก้ว", "แม่ริม", "เชียงใหม่"),
    ("ริมใต้", "แม่ริม", "เชียงใหม่"),
    ("สุเทพ", "เมืองเชียงใหม่", "เชียงใหม่"),
    ("ช้างเผือก", "เมืองเชียงใหม่", "เชียงใหม่"),
    ("หนองหอย", "เมืองเชียงใหม่", "เชียงใหม่"),
    ("สันทรายหลวง", "สันทราย", "เชียงใหม่"),
    ("ต.ท่าศาลา", "อ.เมืองเชียงใหม่", "จ.เชียงใหม่"),
    ("ตำบลเวียง", "อำเภอเมืองเชียงราย", "จังหวัดเชียงราย"),
    ("สบตุ๋ย", "เมืองลำปาง", "ลำปาง"),
    ("ในเมือง", "เมืองลำพูน", "ลำพูน"),
    ("ปากน้ำโพ", "เมืองนครสวรรค์", "นครสวรรค์"),
    ("ลาดยาว", "จตุจักร", "กรุงเทพมหานคร"),
]
CASE_TYPES = [
    ("หลอกลวงซื้อขายสินค้าหรือบริการ ", "ฉ้อโกง", "ซื้อขายออนไลน์", "ไม่ได้รับสินค้า", "คดีออนไลน์"),
    ("หลอกให้ลงทุน   ", "ฉ้อโกง", "การลงทุน", "แพลตฟอร์มปลอม", "คดีออนไลน์"),
    ("หลอกให้กู้เงิน ", "ฉ้อโกง", "เงินกู้", "ค่าธรรมเนียมล่วงหน้า", "คดีออนไลน์"),
    ("ข่มขู่ทางโทรศัพท์ (แก๊งคอลเซ็นเตอร์)  ", "ฉ้อโกง", "คอลเซ็นเตอร์", "แอบอ้างเจ้าหน้าที่", "คดีออนไลน์"),
]
NON_FINAL = ["อายัดบัญชี", "พบพนักงานสอบสวน", "ออกหมายเรียก", "ส่งสำนวนอัยการ", "ระหว่างดำเนินการ", "จับกุม",
             "ออกหมายจับ"]
INVESTIGATORS = ["ศักดิ์ชัย ใจดีสมมติ", "หญิง สุดา รักษ์ทดสอบ", "สุดา  รักษ์ทดสอบ", "ธนพล  มั่นคงสมมติ",
                 "ธนพล มั่นคงสมมติ"]
UNITS = ["กก.1 บก.สอท.4", "กก.2 บก.สอท.4"]


def build(out_dir: Path):
    rnd = random.Random(20260209)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefixes = ["L", "W", "T", "C"]
    people = []
    for i in range(50):
        female = i % 2 == 0
        receipt = f"{prefixes[i % 4]}{6800000000 + i * 7919:010d}"
        people.append({
            "receipt": receipt,
            "female": female,
            "first": (FIRST_F if female else FIRST_M)[i % 10],
            "last": LAST[(i * 5) % len(LAST)],
            "age": str(20 + (i * 7) % 50),
            "area": AREAS[i % len(AREAS)],
            "house": f"{(i * 37) % 400 + 1}" + ("/2" if i % 9 == 0 else ""),
            "moo": str(i % 12 + 1) if i % 3 else "",
            "phone": f"08{(i * 1234567) % 100000000:08d}",
        })
    rnd.shuffle(people)
    register_people = people[:40]  # people[40:] are not in the register

    # --- status and address-defect plan for the 40 register cases
    plan = []  # (status, missing_house, missing_province)
    for _ in range(8):
        plan.append(("สิ้นสุด", False, False))
    plan[0] = ("สิ้นสุด", True, False)
    plan[1] = ("สิ้นสุด", False, True)
    non_final = []
    for i in range(32):
        non_final.append((NON_FINAL[i % len(NON_FINAL)], False, False))
    for i in range(4):
        non_final[i] = (non_final[i][0], True, False)
    non_final[4] = (non_final[4][0], False, True)
    non_final[5] = (non_final[5][0], True, True)
    plan += non_final
    rnd.shuffle(plan)
    for person, (status, no_house, no_province) in zip(register_people, plan):
        person["status"] = status
        if no_house:
            person["house"] = ""
        if no_province:
            person["area"] = (person["area"][0], person["area"][1], "")

    # --- complainants file
    wb = Workbook()
    ws = wb.active
    ws.title = "Grid"
    ws.append(COMPLAINANT_HEADERS)
    for i, p in enumerate(people):
        prefix = ""
        if i % 10 in (0, 3, 7):  # ~30 %
            prefix = ("นางสาว" if i % 4 == 0 else "นาง") if p["female"] else "นาย"
        national_id = ""
        if i % 4 == 1:  # ~25 %
            national_id = f"3{(i * 98765432101) % 10**12:012d}"
        if i == 5:
            national_id = "310100012345"  # 12 digits -> flagged
        sub, dist, prov = p["area"]
        postcode = lookup_postcode(sub, dist, prov) if i in (2, 11) and prov else ""  # zip rarely present
        row = [
            p["receipt"], prefix, p["first"], p["last"] + "  ", p["age"], national_id, p["phone"],
            p["house"], p["moo"], "", "ซ.สมมติ " + str(i % 5) if i % 5 == 0 else "", "",
            sub, dist, prov, postcode, "", "", "", "", "รับจ้าง", "", "", "", "", "", "",
            CASE_TYPES[i % len(CASE_TYPES)][0] + "    ",
        ]
        ws.append(row)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"
    wb.save(out_dir / "mock_complainants.xlsx")

    # --- register file
    wb = Workbook()
    wb.remove(wb.active)
    sheets = [("ปี 2568 (2025)", 2025, register_people[:15]), ("ปี 2569 (2026)", 2026, register_people[15:])]
    for title, year, group in sheets:
        ws = wb.create_sheet(title)
        ws.append(REGISTER_HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for n, p in enumerate(group, start=1):
            ct = CASE_TYPES[n % len(CASE_TYPES)]
            month = (n % 12) + 1 if year == 2025 else (n % 10) + 1
            day = (n * 3) % 27 + 1
            receipt = p["receipt"]
            if n == 2:
                receipt = f" {receipt.lower()} "  # tests key normalisation
            unit = UNITS[1] if n % 6 == 0 else UNITS[0]
            inv = INVESTIGATORS[n % len(INVESTIGATORS)]
            prefix_name = ("นางสาว" if p["female"] else "นาย")
            ws.append([
                n, receipt, f"{100 + n}/{year + 543}" if n % 5 == 0 else "", day, month, year,
                f"{prefix_name}{p['first']} {p['last']}", "หญิง" if p["female"] else "ชาย", int(p["age"]),
                ct[0], ct[1], ct[2], ct[3], ct[4], "รายละเอียดสมมติสำหรับทดสอบระบบ",
                (n * 1537) % 90000 + 500, "บช.สอท.", "บก.สอท.4", unit, inv,
                "ด.ต.สมมติ ทดลองงาน", p["status"], p["phone"], f"REF{n:06d}" if n % 3 == 0 else "",
                f"ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง) {day} ต.ค. 68 เวลา 10.30 น." if n % 4 == 0 else "",
                f"{day:02d}/{month:02d}/{year} 1{n % 10}:30" if n % 2 == 0 else "",
                "",
            ])
        # trailing junk rows like the real export
        for k in range(30):
            ws.append([None] * len(REGISTER_HEADERS))
        ws.append([len(group) + 1] + [None] * (len(REGISTER_HEADERS) - 1))
        for row in ws.iter_rows(min_row=2, min_col=23, max_col=23):
            for cell in row:
                cell.number_format = "@"
    wb.save(out_dir / "mock_case_register.xlsx")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample_data"
    build(target)
    print(f"wrote {target}/mock_complainants.xlsx and mock_case_register.xlsx")
