from django.conf import settings
from django.db import models


class TemplateKind(models.TextChoices):
    SUMMONS = "summons", "หมายเรียก"
    ENVELOPE = "envelope", "ใบจ่าหน้าซอง"


class DocTemplate(models.Model):
    kind = models.CharField("ประเภท", max_length=20, choices=TemplateKind.choices)
    name = models.CharField("ชื่อแบบฟอร์ม", max_length=200)
    file = models.FileField("ไฟล์ .docx", upload_to="doc_templates/", max_length=500)
    version = models.PositiveIntegerField("เวอร์ชัน", default=1)
    is_active = models.BooleanField("ใช้งานอยู่", default=False)
    show_hidden_text = models.BooleanField(
        "แสดงข้อความที่ซ่อนไว้ในแบบฟอร์ม", default=False,
        help_text="แบบฟอร์มหมายเรียกเดิมซ่อนป้ายข้อความ (เช่น 'ออกหมายวันที่') เพื่อพิมพ์ลงกระดาษแบบพิมพ์ "
                  "ติ๊กช่องนี้ถ้าจะพิมพ์ลงกระดาษ A4 เปล่า",
    )
    note = models.CharField("หมายเหตุ", max_length=500, blank=True)
    placeholders = models.JSONField("ตัวแทนที่พบในไฟล์", default=list, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "-version"]
        unique_together = [("kind", "version")]
        verbose_name = "แบบฟอร์ม"
        verbose_name_plural = "แบบฟอร์ม"

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version} – {self.name}"

    @classmethod
    def active(cls, kind):
        return cls.objects.filter(kind=kind, is_active=True).order_by("-version").first()

    def activate(self):
        DocTemplate.objects.filter(kind=self.kind).exclude(pk=self.pk).update(is_active=False)
        self.is_active = True
        self.save(update_fields=["is_active"])


class SummonsResult(models.TextChoices):
    ISSUED = "issued", "ออกแล้ว"
    ATTENDED = "attended", "มาตามนัด"
    ABSENT = "absent", "ไม่มา"
    CANCELLED = "cancelled", "ยกเลิก"


class Summons(models.Model):
    case = models.ForeignKey("registry.Case", on_delete=models.PROTECT, related_name="summonses",
                             verbose_name="คดี")
    number = models.PositiveIntegerField("ครั้งที่")
    issue_date = models.DateField("วันออกหมาย")
    appointment_at = models.DateTimeField("วันเวลานัด")
    place = models.TextField("สถานที่นัด")
    reason = models.TextField("เหตุที่เรียก")
    issuer = models.ForeignKey("registry.Investigator", on_delete=models.PROTECT,
                               verbose_name="ผู้ออกหมาย (ผู้ลงนาม)")
    template = models.ForeignKey(DocTemplate, on_delete=models.PROTECT, verbose_name="แบบฟอร์ม")
    template_version = models.PositiveIntegerField("เวอร์ชันแบบฟอร์ม")
    thai_digits = models.BooleanField("แปลงตัวเลขเป็นเลขไทย", default=True)
    # Exact values rendered into the document, so files can be re-created
    # identically after the retention clean-up deletes them.
    context = models.JSONField(default=dict)
    recipient_name = models.TextField("ชื่อผู้ถูกเรียก")
    recipient_address = models.TextField("ที่อยู่ผู้ถูกเรียก")
    docx_file = models.FileField(upload_to="summons/", max_length=500, blank=True)
    pdf_file = models.FileField(upload_to="summons/", max_length=500, blank=True)
    result = models.CharField("ผล", max_length=20, choices=SummonsResult.choices,
                              default=SummonsResult.ISSUED, db_index=True)
    result_note = models.CharField("หมายเหตุผล", max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   verbose_name="ผู้กดออกหมาย", related_name="+")
    created_at = models.DateTimeField("เวลาสร้าง", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("case", "number")]
        verbose_name = "หมายเรียก"
        verbose_name_plural = "หมายเรียก"

    def __str__(self):
        return f"{self.case.receipt_no} ครั้งที่ {self.number}"

    def base_filename(self) -> str:
        safe_name = "".join(ch for ch in self.recipient_name if ch not in '\\/:*?"<>|').replace(" ", "")
        return f"{self.case.receipt_no}_{safe_name}_หมายเรียกครั้งที่{self.number}"

    def person_filename(self) -> str:
        """File name for the per-person export: เลขรับแจ้ง_ชื่อ (+ _ครั้งที่n from the 2nd summons on)."""
        name = " ".join("".join(ch for ch in self.recipient_name if ch not in '\\/:*?"<>|').split())
        stem = f"{self.case.receipt_no}_{name}" if name else self.case.receipt_no
        return stem if self.number <= 1 else f"{stem}_ครั้งที่{self.number}"


class ExportFormat(models.TextChoices):
    PDF = "pdf", "PDF"
    DOCX = "docx", "Word"
    BOTH = "both", "ทั้ง PDF และ Word"


class ExportMode(models.TextChoices):
    ZIP = "zip", "แยกไฟล์รายคน (ชื่อไฟล์ เลขรับแจ้ง_ชื่อ)"
    MERGED = "merged", "รวมทุกคนเป็นไฟล์เดียว"


class JobStatus(models.TextChoices):
    PENDING = "pending", "รอคิว"
    RUNNING = "running", "กำลังทำ"
    DONE = "done", "เสร็จแล้ว"
    FAILED = "failed", "ล้มเหลว"


class ExportJob(models.Model):
    summonses = models.ManyToManyField(Summons, related_name="export_jobs")
    format = models.CharField("รูปแบบ", max_length=10, choices=ExportFormat.choices,
                              default=ExportFormat.PDF)
    mode = models.CharField("การรวมไฟล์", max_length=10, choices=ExportMode.choices,
                            default=ExportMode.ZIP)
    include_summons = models.BooleanField("หมายเรียก", default=True)
    include_envelope = models.BooleanField("ใบจ่าหน้าซอง", default=False)
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.PENDING)
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    result_file = models.FileField(upload_to="exports/", max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "งานส่งออก"
        verbose_name_plural = "งานส่งออก"

    @property
    def percent(self) -> int:
        if not self.total:
            return 0 if self.status != JobStatus.DONE else 100
        return min(100, int(self.done * 100 / self.total))
