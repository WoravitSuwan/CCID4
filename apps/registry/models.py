from __future__ import annotations

import re

from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Q

from .address import Address, clean_text


def normalize_receipt_no(value) -> str:
    """Matching key for เลขรับแจ้ง: no whitespace, upper case."""
    return re.sub(r"\s+", "", clean_text(value)).upper()


def normalize_person_key(value) -> str:
    """Matching key for investigator names in the register (whitespace removed)."""
    return re.sub(r"\s+", "", clean_text(value))


class Status(models.Model):
    name = models.CharField("ชื่อสถานะ", max_length=300, unique=True)
    is_final = models.BooleanField("ถือว่าสิ้นสุด", default=False)
    needs_review = models.BooleanField(
        "สถานะใหม่ รอผู้ดูแลตรวจสอบ", default=False,
        help_text="ระบบตั้งให้อัตโนมัติเมื่อพบสถานะใหม่ระหว่างนำเข้า",
    )
    sort_order = models.PositiveIntegerField("ลำดับ", default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "สถานะคดี"
        verbose_name_plural = "สถานะคดี"

    def __str__(self):
        return self.name


class Unit(models.Model):
    name = models.CharField("ชื่อหน่วยตามทะเบียน", max_length=500, unique=True,
                            help_text="ต้องตรงกับคอลัมน์ 'หน่วยที่รับผิดชอบ' เช่น กก.1 บก.สอท.4")
    full_name = models.CharField("ชื่อหน่วยเต็ม (ใช้บนซอง)", max_length=300, blank=True)
    appointment_place = models.TextField(
        "สถานที่นัด (ข้อความหลัง 'ไป ณ ที่')", blank=True,
        help_text="ใช้เป็น {สถานที่นัด} ในหมายเรียก",
    )
    sender_address = models.TextField("ที่อยู่ผู้ส่ง (บนซอง)", blank=True,
                                      help_text="ใช้เป็น {ที่อยู่หน่วย} บนใบจ่าหน้าซอง")
    postage_permit = models.TextField(
        "ข้อความกรอบชำระค่าฝากส่ง (บนซอง)", blank=True,
        help_text="ใช้เป็น {ใบอนุญาตฝากส่ง} เช่น ชำระฝากส่งเป็นรายเดือน / ใบอนุญาตที่ ๑๙/๒๕๖๘ / ไปรษณีย์ ศาลากลาง ชม. "
                  "(ขึ้นบรรทัดใหม่ได้)")

    class Meta:
        ordering = ["name"]
        verbose_name = "หน่วย"
        verbose_name_plural = "หน่วย"

    def __str__(self):
        return self.name

    @property
    def display_full_name(self) -> str:
        return self.full_name or self.name


class Investigator(models.Model):
    rank_full = models.CharField("ยศ (เต็ม)", max_length=100, blank=True, help_text="เช่น พันตำรวจโท")
    sign_name = models.CharField("ชื่อ-สกุลที่ใช้ลงนาม", max_length=200)
    position = models.CharField("ตำแหน่ง", max_length=300, blank=True,
                                help_text="เช่น สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.")
    phone = models.CharField("โทรศัพท์", max_length=50, blank=True)
    unit = models.ForeignKey(Unit, verbose_name="หน่วย", null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField("ใช้งานอยู่", default=True)

    class Meta:
        ordering = ["sign_name"]
        verbose_name = "พนักงานสอบสวน"
        verbose_name_plural = "พนักงานสอบสวน"

    def __str__(self):
        return f"{self.rank_full} {self.sign_name}".strip()

    def missing_signer_fields(self) -> list[str]:
        missing = []
        if not self.rank_full.strip():
            missing.append("ยศ")
        if not self.sign_name.strip():
            missing.append("ชื่อที่ใช้ลงนาม")
        if not self.position.strip():
            missing.append("ตำแหน่ง")
        if not self.phone.strip():
            missing.append("โทรศัพท์")
        return missing


class InvestigatorAlias(models.Model):
    """A spelling of an investigator name as it appears in the register."""

    raw_name = models.CharField("ชื่อตามทะเบียน", max_length=500)
    key = models.CharField("คีย์จับคู่ (ตัดช่องว่าง)", max_length=500, unique=True)
    investigator = models.ForeignKey(
        Investigator, verbose_name="พนักงานสอบสวน", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="aliases",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["raw_name"]
        verbose_name = "ชื่อพนักงานสอบสวนในทะเบียน"
        verbose_name_plural = "ชื่อพนักงานสอบสวนในทะเบียน"

    def __str__(self):
        return self.raw_name

    def save(self, *args, **kwargs):
        self.raw_name = clean_text(self.raw_name)
        self.key = normalize_person_key(self.raw_name)
        super().save(*args, **kwargs)


class AddressFields(models.Model):
    house_no = models.TextField("บ้านเลขที่", blank=True)
    moo = models.TextField("หมู่", blank=True)
    building = models.TextField("อาคาร/ตึก", blank=True)
    soi = models.TextField("ซอย", blank=True)
    road = models.TextField("ถนน", blank=True)
    subdistrict = models.TextField("ตำบล", blank=True)
    district = models.TextField("อำเภอ", blank=True)
    province = models.TextField("จังหวัด", blank=True)

    class Meta:
        abstract = True


class Complainant(AddressFields):
    receipt_no = models.CharField("เลขรับแจ้ง", max_length=100, unique=True)
    prefix = models.TextField("คำนำหน้าชื่อ", blank=True)
    first_name = models.TextField("ชื่อ", blank=True)
    last_name = models.TextField("นามสกุล", blank=True)
    age = models.TextField("อายุ", blank=True)
    national_id = models.TextField("เลขประจำตัวประชาชน", blank=True)
    phone = models.TextField("หมายเลขโทรศัพท์", blank=True)
    postcode = models.CharField("รหัสไปรษณีย์ (จากไฟล์)", max_length=100, blank=True)
    postcode_auto = models.CharField("รหัสไปรษณีย์ (ระบบเติม)", max_length=10, blank=True)
    residence = models.TextField("ที่อยู่อาศัย", blank=True)
    marital_status = models.TextField("สถานภาพ", blank=True)
    education = models.TextField("ระดับการศึกษา", blank=True)
    third_party = models.TextField("บุคคลที่สาม", blank=True)
    occupation = models.TextField("อาชีพ", blank=True)
    workplace = models.TextField("สถานที่ทำงาน", blank=True)
    income = models.TextField("รายได้", blank=True)
    father_name = models.TextField("ชื่อบิดา", blank=True)
    mother_name = models.TextField("ชื่อมารดา", blank=True)
    province_at_report = models.TextField("จังหวัดที่อยู่ขณะที่แจ้ง", blank=True)
    preferred_unit = models.TextField("สะดวกที่หน่วยใด", blank=True)
    case_type = models.TextField("ประเภทคดี", blank=True)

    flag_missing_house = models.BooleanField("ไม่มีบ้านเลขที่", default=False, db_index=True)
    flag_missing_area = models.BooleanField("ไม่มีตำบล/อำเภอ/จังหวัด", default=False, db_index=True)
    flag_missing_province = models.BooleanField("ไม่มีจังหวัด", default=False)
    flag_bad_id = models.BooleanField("เลขบัตรไม่ใช่ 13 หลัก", default=False)
    address_incomplete = models.BooleanField("ที่อยู่ไม่ครบ", default=False, db_index=True)

    last_batch = models.ForeignKey("imports.ImportBatch", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ผู้แจ้ง"
        verbose_name_plural = "ผู้แจ้ง"

    def __str__(self):
        return f"{self.receipt_no} {self.full_name}"

    @property
    def full_name(self) -> str:
        """คำนำหน้า + ชื่อ + นามสกุล. Missing prefix is left out, never guessed."""
        prefix = clean_text(self.prefix).lstrip("-").strip()
        name = " ".join(p for p in (clean_text(self.first_name), clean_text(self.last_name)) if p)
        return f"{prefix}{name}" if prefix else name

    @property
    def effective_postcode(self) -> str:
        return clean_text(self.postcode) or clean_text(self.postcode_auto)

    def address(self) -> Address:
        return Address.from_obj(self)

    def refresh_flags(self):
        addr = self.address()
        self.flag_missing_house = addr.missing_house
        self.flag_missing_area = addr.missing_area
        self.flag_missing_province = addr.missing_province
        digits = re.sub(r"\D", "", self.national_id or "")
        self.flag_bad_id = bool(self.national_id.strip()) and len(digits) != 13
        self.address_incomplete = not addr.is_complete


class AddressOverride(AddressFields):
    """Address typed in by staff. Never overwritten by imports.

    Keyed by เลขรับแจ้ง (not by Complainant) so it also works for cases that
    have no matching complainant row yet.
    """

    receipt_no = models.CharField("เลขรับแจ้ง", max_length=100, unique=True)
    recipient_name = models.CharField(
        "ชื่อผู้รับ (ถ้าต่างจากไฟล์ผู้แจ้ง)", max_length=300, blank=True,
        help_text="เว้นว่างเพื่อใช้ชื่อจากไฟล์ผู้แจ้ง (หรือชื่อผู้เสียหายในทะเบียนถ้าจับคู่ไม่ได้)",
    )
    postcode = models.CharField("รหัสไปรษณีย์", max_length=100, blank=True)
    # Complainant address at the moment this override was saved / confirmed.
    # When the imported address later differs from it, staff are asked to choose.
    source_snapshot = models.JSONField(default=dict, blank=True)
    note = models.TextField("หมายเหตุ", blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   verbose_name="ผู้แก้ไข")
    updated_at = models.DateTimeField("เวลาแก้ไข", auto_now=True)
    is_complete = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "ที่อยู่ที่แก้ไขเอง"
        verbose_name_plural = "ที่อยู่ที่แก้ไขเอง"

    def __str__(self):
        return self.receipt_no

    @property
    def effective_postcode(self) -> str:
        return clean_text(self.postcode)

    def address(self) -> Address:
        return Address.from_obj(self)

    def save(self, *args, **kwargs):
        if not clean_text(self.postcode):  # staff left it blank: fill from ตำบล/อำเภอ/จังหวัด
            from apps.imports.postcode import lookup_postcode

            self.postcode = lookup_postcode(self.subdistrict, self.district, self.province)
        self.is_complete = self.address().is_complete
        super().save(*args, **kwargs)


def complainant_address_snapshot(complainant: Complainant | None) -> dict:
    if complainant is None:
        return {}
    return complainant.address().as_dict()


class CaseQuerySet(models.QuerySet):
    def with_flags(self):
        override = AddressOverride.objects.filter(receipt_no=OuterRef("receipt_no"))
        return self.annotate(
            has_override=Exists(override),
            override_complete=Exists(override.filter(is_complete=True)),
        )

    def not_final(self):
        return self.filter(Q(status__isnull=True) | Q(status__is_final=False))

    def final(self):
        return self.filter(status__is_final=True)

    def unmatched(self):
        return self.filter(complainant__isnull=True)

    def address_incomplete(self):
        """Matched cases whose usable address is incomplete.

        An override takes precedence over the complainant address. Unmatched
        cases are a separate category and are not counted here, unless staff
        typed an incomplete address for them.
        """
        override = AddressOverride.objects.filter(receipt_no=OuterRef("receipt_no"))
        return self.filter(
            (Q(complainant__isnull=False) & ~Exists(override) & Q(complainant__address_incomplete=True))
            | Exists(override.filter(is_complete=False))
        )

    def never_summoned(self):
        from apps.documents.models import Summons, SummonsResult

        active = Summons.objects.filter(case=OuterRef("pk")).exclude(result=SummonsResult.CANCELLED)
        return self.filter(~Exists(active))

    def for_investigator(self, investigator):
        return self.filter(investigator_alias__investigator=investigator)


class Case(models.Model):
    receipt_no = models.CharField("เลขรับแจ้ง", max_length=100, unique=True)
    complainant = models.OneToOneField(
        Complainant, verbose_name="ผู้แจ้ง", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="case",
    )
    seq = models.TextField("ลำดับ", blank=True)
    criminal_case_no = models.TextField("เลขคดีอาญา", blank=True)
    report_date = models.DateField("วันที่รับแจ้ง", null=True, blank=True, db_index=True)
    report_year = models.PositiveIntegerField("ปีที่รับแจ้ง (ค.ศ.)", null=True, blank=True, db_index=True)
    victim_name = models.TextField("ผู้เสียหาย", blank=True)
    gender = models.TextField("เพศ", blank=True)
    age = models.TextField("อายุ", blank=True)
    case_type = models.TextField("ประเภทคดี", blank=True, db_index=True)
    case_type_l1 = models.TextField("ประเภทคดีระดับ 1", blank=True)
    case_type_l2 = models.TextField("ประเภทคดีระดับ 2", blank=True)
    case_type_l3 = models.TextField("ประเภทคดีระดับ 3", blank=True)
    case_group = models.TextField("ประเภทกลุ่มคดี", blank=True)
    detail = models.TextField("รายละเอียด", blank=True)
    damage_amount = models.DecimalField("มูลค่าความเสียหาย", max_digits=16, decimal_places=2,
                                        null=True, blank=True)
    bureau = models.TextField("บช.", blank=True)
    division = models.TextField("บก./ภ.จว.", blank=True)
    unit = models.ForeignKey(Unit, verbose_name="หน่วยที่รับผิดชอบ", null=True, blank=True,
                             on_delete=models.SET_NULL)
    unit_raw = models.TextField("หน่วยที่รับผิดชอบ (ตามทะเบียน)", blank=True)
    investigator_alias = models.ForeignKey(
        InvestigatorAlias, verbose_name="ชื่อพนักงานสอบสวนในทะเบียน", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="cases",
    )
    investigator_raw = models.TextField("พนักงานสอบสวน (ตามทะเบียน)", blank=True)
    detective = models.TextField("พนักงานสืบสวน", blank=True)
    status = models.ForeignKey(Status, verbose_name="สถานะ", null=True, blank=True,
                               on_delete=models.PROTECT)
    # Set when staff change the status in the system. Re-importing a register that
    # still says ``status_file_value`` keeps the manual status; a different value in
    # the file (newer information) replaces it.
    status_manual_at = models.DateTimeField("แก้สถานะเองเมื่อ", null=True, blank=True)
    status_manual_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="ผู้แก้สถานะ", null=True,
                                         blank=True, on_delete=models.SET_NULL, related_name="+")
    status_file_value = models.TextField("สถานะในไฟล์ทะเบียนตอนแก้", blank=True)
    victim_phone = models.TextField("เบอร์โทรศัพท์ผู้เสียหาย", blank=True)
    bank_ref = models.TextField("เลขอ้างอิงธนาคาร", blank=True)
    contact_log = models.TextField("บันทึกการติดต่อ", blank=True)
    daily_log = models.TextField("ประจำวัน", blank=True)
    daily_log_at = models.DateTimeField("ประจำวัน (วันเวลา)", null=True, blank=True)
    remark = models.TextField("หมายเหตุ", blank=True)
    source_sheet = models.TextField("ชีตต้นทาง", blank=True, db_index=True)

    last_batch = models.ForeignKey("imports.ImportBatch", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        ordering = ["-report_date", "receipt_no"]
        verbose_name = "คดี"
        verbose_name_plural = "คดี"

    def __str__(self):
        return self.receipt_no

    @property
    def investigator(self) -> Investigator | None:
        return self.investigator_alias.investigator if self.investigator_alias_id else None

    @property
    def is_final(self) -> bool:
        return bool(self.status and self.status.is_final)

    def get_override(self) -> AddressOverride | None:
        if not hasattr(self, "_override_cache"):
            self._override_cache = AddressOverride.objects.filter(receipt_no=self.receipt_no).first()
        return self._override_cache

    def effective_address(self) -> Address | None:
        override = self.get_override()
        if override:
            return override.address()
        if self.complainant:
            return self.complainant.address()
        return None

    def recipient_name(self) -> str:
        override = self.get_override()
        if override and clean_text(override.recipient_name):
            return clean_text(override.recipient_name)
        if self.complainant:
            return self.complainant.full_name
        return clean_text(self.victim_name)

    def address_conflict(self) -> bool:
        """True when the imported address changed after staff saved an override."""
        override = self.get_override()
        if not override or not self.complainant:
            return False
        return complainant_address_snapshot(self.complainant) != (override.source_snapshot or {})

    def set_status(self, status: Status, user=None, note: str = "") -> bool:
        """Change the status by hand (kept across re-imports of an unchanged register)."""
        from django.utils import timezone

        if self.status_id == (status.pk if status else None):
            return False
        if not self.status_manual_at:
            self.status_file_value = self.status.name if self.status_id else ""
        self.status = status
        self.status_manual_at = timezone.now()
        self.status_manual_by = user if getattr(user, "is_authenticated", False) else None
        self.save(update_fields=["status", "status_manual_at", "status_manual_by", "status_file_value",
                                 "updated_at"])
        return True

    def issue_blockers(self) -> list[str]:
        """Reasons this case cannot get a summons yet (empty list = OK)."""
        blockers = []
        override = self.get_override()
        if not self.complainant and not override:
            blockers.append("จับคู่กับไฟล์ผู้แจ้งไม่ได้ (กรอกที่อยู่เองในหน้ารายละเอียดคดีได้)")
        else:
            addr = self.effective_address()
            if addr and not addr.is_complete:
                blockers.append("ที่อยู่ไม่ครบ: ไม่มี " + ", ".join(addr.missing_labels()))
        if not self.recipient_name():
            blockers.append("ไม่มีชื่อผู้ถูกเรียก")
        inv = self.investigator
        if inv is None:
            if self.investigator_alias_id:
                blockers.append(f"ชื่อพนักงานสอบสวน '{self.investigator_raw}' ยังไม่ได้ผูกกับข้อมูลผู้ลงนาม")
            else:
                blockers.append("คดีนี้ไม่มีพนักงานสอบสวน")
        else:
            missing = inv.missing_signer_fields()
            if missing:
                blockers.append(f"ข้อมูลผู้ลงนาม ({inv.sign_name}) ไม่ครบ: " + ", ".join(missing))
        if not self.unit or not clean_text(self.unit.appointment_place):
            blockers.append(f"หน่วย '{self.unit_raw or '-'}' ยังไม่ได้ตั้งค่าสถานที่นัด")
        return blockers
