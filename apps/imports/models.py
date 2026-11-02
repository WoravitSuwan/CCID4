from django.conf import settings
from django.db import models


class ImportKind(models.TextChoices):
    COMPLAINANTS = "complainants", "ข้อมูลผู้แจ้ง"
    REGISTER = "register", "ทะเบียนคดี"


class BatchStatus(models.TextChoices):
    UPLOADED = "uploaded", "อัปโหลดแล้ว"
    ANALYZING = "analyzing", "กำลังตรวจสอบ"
    ANALYZED = "analyzed", "รอยืนยัน"
    COMMITTING = "committing", "กำลังบันทึก"
    DONE = "done", "นำเข้าสำเร็จ"
    FAILED = "failed", "ล้มเหลว"
    CANCELLED = "cancelled", "ยกเลิก"


class ImportBatch(models.Model):
    kind = models.CharField("ประเภทไฟล์", max_length=20, choices=ImportKind.choices)
    file = models.FileField("ไฟล์", upload_to="imports/", max_length=500, blank=True)
    original_filename = models.CharField("ชื่อไฟล์", max_length=500)
    available_sheets = models.JSONField(default=list, blank=True)
    sheets = models.JSONField("ชีตที่นำเข้า", default=list, blank=True)
    mapping = models.JSONField("การจับคู่คอลัมน์", default=dict, blank=True)
    status = models.CharField("สถานะ", max_length=20, choices=BatchStatus.choices,
                              default=BatchStatus.UPLOADED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   verbose_name="ผู้นำเข้า")
    created_at = models.DateTimeField("เวลาอัปโหลด", auto_now_add=True)
    committed_at = models.DateTimeField("เวลานำเข้า", null=True, blank=True)

    total_rows = models.PositiveIntegerField("จำนวนแถว", default=0)
    new_count = models.PositiveIntegerField("เพิ่มใหม่", default=0)
    updated_count = models.PositiveIntegerField("อัปเดต", default=0)
    unchanged_count = models.PositiveIntegerField("ไม่เปลี่ยน", default=0)
    issue_count = models.PositiveIntegerField("มีปัญหา", default=0)
    unmatched_count = models.PositiveIntegerField("จับคู่กับอีกไฟล์ไม่ได้", default=0)
    summary = models.JSONField("สรุป", default=dict, blank=True)
    error = models.TextField(blank=True)
    progress = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ประวัติการนำเข้า"
        verbose_name_plural = "ประวัติการนำเข้า"

    def __str__(self):
        return f"{self.get_kind_display()} {self.original_filename}"

    @property
    def is_running(self):
        return self.status in {BatchStatus.ANALYZING, BatchStatus.COMMITTING}


class IssueType(models.TextChoices):
    MISSING_HOUSE = "missing_house", "ไม่มีบ้านเลขที่"
    MISSING_AREA = "missing_area", "ไม่มีตำบล/อำเภอ/จังหวัด"
    DUPLICATE_KEY = "duplicate_key", "เลขรับแจ้งซ้ำในไฟล์"
    BAD_ID = "bad_id", "เลขบัตรไม่ใช่ 13 หลัก"
    BAD_DATE = "bad_date", "วันที่ไม่ถูกต้อง"
    BAD_AMOUNT = "bad_amount", "มูลค่าความเสียหายไม่ใช่ตัวเลข"
    UNMATCHED = "unmatched", "จับคู่กับอีกไฟล์ไม่ได้"
    NEW_STATUS = "new_status", "สถานะใหม่"
    NEW_INVESTIGATOR = "new_investigator", "ชื่อพนักงานสอบสวนที่ยังไม่ได้ผูก"
    BAD_KEY = "bad_key", "รูปแบบเลขรับแจ้งผิด"


class ImportIssue(models.Model):
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="issues")
    sheet = models.CharField("ชีต", max_length=100, blank=True)
    row_number = models.PositiveIntegerField("แถวในไฟล์", null=True, blank=True)
    receipt_no = models.CharField("เลขรับแจ้ง", max_length=100, blank=True, db_index=True)
    issue_type = models.CharField("ปัญหา", max_length=30, choices=IssueType.choices)
    message = models.CharField("รายละเอียด", max_length=500, blank=True)

    class Meta:
        ordering = ["sheet", "row_number"]
        verbose_name = "ปัญหาในการนำเข้า"
        verbose_name_plural = "ปัญหาในการนำเข้า"


class ColumnMapping(models.Model):
    """Remembered 'source header -> field' choices made by users."""

    kind = models.CharField(max_length=20, choices=ImportKind.choices)
    source_header = models.CharField(max_length=300)
    field_name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("kind", "source_header")]
