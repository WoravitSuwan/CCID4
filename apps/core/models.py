from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    LOGIN = "login", "เข้าสู่ระบบ"
    IMPORT = "import", "นำเข้าข้อมูล"
    ISSUE = "issue", "ออกหมายเรียก"
    EXPORT = "export", "ส่งออก"
    DOWNLOAD = "download", "ดาวน์โหลด"
    VIEW_ID = "view_id", "ดูเลขบัตรประชาชน"
    EDIT = "edit", "แก้ไขข้อมูล"
    SETTINGS = "settings", "แก้ไขการตั้งค่า"
    CALL = "call", "บันทึกผลการติดต่อผู้เสียหาย"
    ASSIGN = "assign", "มอบหมายงานโทร"
    WIPE = "wipe", "ล้างข้อมูลคดีทั้งหมด"


class AuditLog(models.Model):
    created_at = models.DateTimeField("เวลา", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name="ผู้ใช้",
    )
    action = models.CharField("การกระทำ", max_length=20, choices=AuditAction.choices, db_index=True)
    target_type = models.CharField("ประเภทเป้าหมาย", max_length=50, blank=True)
    target_id = models.CharField("รหัสเป้าหมาย", max_length=100, blank=True, db_index=True)
    detail = models.TextField("รายละเอียด", blank=True)
    extra = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "บันทึกการใช้งาน"
        verbose_name_plural = "บันทึกการใช้งาน"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.user} {self.get_action_display()}"


class SystemSetting(models.Model):
    """Singleton row with settings administrators can change in the UI."""

    retention_days = models.PositiveIntegerField(
        "ลบไฟล์ที่ส่งออกอัตโนมัติหลัง (วัน)", default=30,
        help_text="ไฟล์หมายเรียก/ไฟล์ส่งออก/ไฟล์นำเข้าใน media จะถูกลบเมื่อเกินจำนวนวันนี้ (ข้อมูลในฐานข้อมูลยังอยู่และสร้างไฟล์ใหม่ได้)",
    )
    default_reason = models.CharField(
        "เหตุที่เรียกค่าเริ่มต้น", max_length=500,
        default="มาให้การต่อพนักงานสอบสวนตามประเด็นที่เกี่ยวข้องกับคดี",
    )
    default_appointment_days = models.PositiveIntegerField("นัดหลังวันออกหมาย (วัน)", default=14)
    # unit logo (kept in the database so it is part of the database backup and survives a data wipe)
    logo_data = models.BinaryField("โลโก้หน่วย", null=True, blank=True, editable=False)
    logo_type = models.CharField(max_length=30, blank=True, editable=False)
    logo_updated = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "การตั้งค่าระบบ"
        verbose_name_plural = "การตั้งค่าระบบ"

    @classmethod
    def get(cls) -> "SystemSetting":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"retention_days": settings.DEFAULT_RETENTION_DAYS})
        return obj
