"""Initial reference data (seeded by migration, editable in the settings pages)."""

DEFAULT_STATUSES = [
    ("สิ้นสุด", True),
    ("อายัดบัญชี", False),
    ("พบพนักงานสอบสวน", False),
    ("ออกหมายเรียก", False),
    ("ส่งสำนวนอัยการ", False),
    ("ระหว่างดำเนินการ", False),
    ("จับกุม", False),
    ("ออกหมายจับ", False),
]
FINAL_BY_DEFAULT = {name for name, final in DEFAULT_STATUSES if final}
SUMMONS_STATUS_NAME = "ออกหมายเรียก"

POSTAGE_PERMIT = "ชำระฝากส่งเป็นรายเดือน\nใบอนุญาตที่ ๑๙/๒๕๖๘\nไปรษณีย์ ศาลากลาง ชม."

DEFAULT_UNITS = [
    {
        "name": "กก.1 บก.สอท.4",
        "full_name": "กองกำกับการ ๑ กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี ๔",
        "appointment_place": (
            "กองกำกับการ ๑ กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี ๔ "
            "เลขที่ ๓๗๐ หมู่ ๓ ตำบลดอนแก้ว อำเภอแม่ริม จังหวัดเชียงใหม่ ๕๐๑๘๐"
        ),
        "sender_address": "เลขที่ ๓๗๐ หมู่ ๓ ตำบลดอนแก้ว อำเภอแม่ริม จังหวัดเชียงใหม่ ๕๐๑๘๐",
        "postage_permit": POSTAGE_PERMIT,
    },
    {
        # Address of กก.2 is not in the provided form: administrators must fill it in
        # (summons for กก.2 cases are blocked until then).
        "name": "กก.2 บก.สอท.4",
        "full_name": "กองกำกับการ ๒ กองบังคับการตำรวจสืบสวนสอบสวนอาชญากรรมทางเทคโนโลยี ๔",
        "appointment_place": "",
        "sender_address": "",
    },
]


# Suggestions for the signer's rank / position fields (staff can still type anything;
# typed values are remembered - see RememberedValue).
POLICE_RANKS = [
    "พลตำรวจเอก", "พลตำรวจโท", "พลตำรวจตรี", "พันตำรวจเอก (พิเศษ)", "พันตำรวจเอก", "พันตำรวจโท",
    "พันตำรวจตรี", "ร้อยตำรวจเอก", "ร้อยตำรวจโท", "ร้อยตำรวจตรี", "ดาบตำรวจ", "จ่าสิบตำรวจ",
    "สิบตำรวจเอก", "สิบตำรวจโท", "สิบตำรวจตรี",
]
POLICE_RANKS = POLICE_RANKS + [f"{rank}หญิง" for rank in POLICE_RANKS]
COMMON_POSITIONS = [
    f"{title} กก.{unit} บก.สอท.๔ บช.สอท."
    for unit in ("๑", "๒")
    for title in ("ผกก.", "รอง ผกก.(สอบสวน)", "สว.(สอบสวน)", "รอง สว.(สอบสวน)")
]
