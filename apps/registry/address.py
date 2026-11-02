"""Address normalisation and formatting for Thai postal addresses."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ADDRESS_FIELDS = [
    "house_no", "moo", "building", "soi", "road",
    "subdistrict", "district", "province", "postcode",
]

BANGKOK_NAMES = {"กรุงเทพมหานคร", "กรุงเทพ", "กรุงเทพฯ", "กทม", "กทม.", "กรุงเทพมหานครฯ"}

_PREFIXES = {
    "house_no": ["บ้านเลขที่", "เลขที่", "บ้าน"],
    "moo": ["หมู่ที่", "หมู่", "ม."],
    "building": ["อาคาร", "ตึก"],
    "soi": ["ซอย", "ซ."],
    "road": ["ถนน", "ถ."],
    "subdistrict": ["ตำบล", "แขวง", "ต."],
    "district": ["อำเภอ", "เขต", "อ."],
    "province": ["จังหวัด", "จ."],
}


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("​", "").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_prefix(kind: str, value) -> str:
    """Remove a leading label such as 'ต.' / 'ตำบล' / 'อ.' / 'จ.' from a value."""
    text = clean_text(value)
    for prefix in _PREFIXES.get(kind, []):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def is_bangkok(province: str) -> bool:
    return strip_prefix("province", province).replace(" ", "") in BANGKOK_NAMES


def is_blank_value(value) -> bool:
    text = clean_text(value)
    return text in {"", "-", "--", ".", "0", "ไม่มี", "ไม่ระบุ", "n/a", "N/A"}


@dataclass
class Address:
    house_no: str = ""
    moo: str = ""
    building: str = ""
    soi: str = ""
    road: str = ""
    subdistrict: str = ""
    district: str = ""
    province: str = ""
    postcode: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_obj(cls, obj, postcode_attr: str = "effective_postcode") -> "Address":
        values = {name: clean_text(getattr(obj, name, "")) for name in ADDRESS_FIELDS if name != "postcode"}
        values["postcode"] = clean_text(getattr(obj, postcode_attr, "") or getattr(obj, "postcode", ""))
        return cls(**values)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in ADDRESS_FIELDS}

    @property
    def missing_house(self) -> bool:
        return is_blank_value(self.house_no)

    @property
    def missing_area(self) -> bool:
        return any(is_blank_value(getattr(self, n)) for n in ("subdistrict", "district", "province"))

    @property
    def missing_province(self) -> bool:
        return is_blank_value(self.province)

    @property
    def is_complete(self) -> bool:
        return not (self.missing_house or self.missing_area)

    def missing_labels(self) -> list[str]:
        labels = []
        if self.missing_house:
            labels.append("บ้านเลขที่")
        for name, label in (("subdistrict", "ตำบล"), ("district", "อำเภอ"), ("province", "จังหวัด")):
            if is_blank_value(getattr(self, name)):
                labels.append(label)
        return labels

    def format(self, include_postcode: bool = True) -> str:
        """Join the address parts with Thai labels, skipping blank parts."""
        bkk = is_bangkok(self.province)
        parts = []

        def add(kind, label, glue=" "):
            value = strip_prefix(kind, getattr(self, kind))
            if not is_blank_value(value):
                parts.append(f"{label}{glue}{value}" if label else value)

        add("house_no", "บ้านเลขที่")
        add("moo", "หมู่")
        add("building", "อาคาร")
        add("soi", "ซอย")
        add("road", "ถนน")
        add("subdistrict", "แขวง" if bkk else "ตำบล", "")
        add("district", "เขต" if bkk else "อำเภอ", "")
        province = strip_prefix("province", self.province)
        if not is_blank_value(province):
            parts.append("กรุงเทพมหานคร" if bkk else f"จังหวัด{province}")
        if include_postcode and not is_blank_value(self.postcode):
            parts.append(clean_text(self.postcode))
        return " ".join(parts)
