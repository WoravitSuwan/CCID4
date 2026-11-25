import datetime as dt

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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
    letter_number = models.CharField(
        "เลขที่หนังสือ (บนซอง)", max_length=100, default="ตช.0039.52/.....",
        help_text="พิมพ์ใต้ที่อยู่ผู้ส่งบนซอง: 'เลขที่หนังสือ: … ลงวันที่: (วันที่ออกหมาย)'")
    # unit logo (kept in the database so it is part of the database backup and survives a data wipe)
    logo_data = models.BinaryField("โลโก้หน่วย", null=True, blank=True, editable=False)
    logo_type = models.CharField(max_length=30, blank=True, editable=False)
    logo_updated = models.DateTimeField(null=True, blank=True, editable=False)
    # LINE bot: off until an administrator enters the channel's secret and access token on the settings page
    # (or LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN in .env, which win). Never shown again once saved.
    line_channel_secret = models.CharField(max_length=100, blank=True, editable=False)
    line_access_token = models.TextField(blank=True, editable=False)
    line_enabled = models.BooleanField(
        "ส่งสรุปผลประจำวันเข้ากลุ่ม LINE อัตโนมัติ", default=False,
        help_text="วันละครั้งตามเวลาที่ตั้งไว้")
    line_send_time = models.TimeField("เวลาส่งอัตโนมัติ", default=dt.time(18, 0))
    line_skip_empty = models.BooleanField("ไม่ส่งในวันที่ไม่มีการโทร (ประหยัดโควตา)", default=True)
    line_send_files = models.BooleanField(
        "ส่งข้อความ ลงบันทึกประจำวัน และลิงก์ PDF สรุปข้อมูลคดี ไปด้วย (รวม 3 ข้อความ)", default=True,
        help_text="1) รายงานผู้บังคับบัญชา 2) ลงบันทึกประจำวัน (ผู้ไม่ประสงค์ดำเนินคดี เป็นข้อความ) "
                  "3) ลิงก์เปิด PDF สรุปข้อมูลคดี (ไม่ต้องเข้าระบบ ใช้ได้ตามจำนวนวันที่ตั้งไว้ด้านล่าง; "
                  "บอท LINE แนบไฟล์เข้ากลุ่มเองไม่ได้) · ข้อความ 2 มีชื่อ เลขรับแจ้ง เบอร์โทรผู้เสียหาย "
                  "ซึ่งจะอยู่บนเซิร์ฟเวอร์ของ LINE · ข้อความที่ยาวเกิน 5,000 ตัวอักษร (LINE จำกัด) "
                  "แบ่งเป็นหลายข้อความต่อกัน")
    # links to the text files sent before (kept working until they expire)
    line_link_days = models.PositiveIntegerField("ลิงก์ PDF สรุปข้อมูลคดีเปิดได้ (วัน)", default=7,
                                                 help_text="นับจากวันที่ส่ง 1–30 วัน หลังจากนั้นลิงก์ใช้ไม่ได้",
                                                 validators=[MinValueValidator(1), MaxValueValidator(30)])
    line_site_url = models.CharField(max_length=200, blank=True, editable=False)  # for links in messages
    line_group_id = models.CharField("กลุ่ม LINE ที่ส่ง", max_length=64, blank=True, editable=False)
    line_group_name = models.CharField(max_length=200, blank=True, editable=False)
    line_pending_group_id = models.CharField(max_length=64, blank=True, editable=False)
    line_pending_group_name = models.CharField(max_length=200, blank=True, editable=False)
    # the automatic send: when it was last done (or skipped), what happened, and when it was last checked
    line_last_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    line_last_result = models.CharField(max_length=300, blank=True, editable=False)
    line_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    # last check by the server's own background job (Celery worker, or cron): sends with every computer off
    line_worker_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    line_last_error = models.TextField(blank=True, editable=False)
    # the report for the commanders (สรุปผลประจำวัน): its wording can be changed on that page, then locked,
    # so a new wording needs no new version of the system. Empty = the standard wording.
    report_template = models.TextField("รูปแบบข้อความรายงานผู้บังคับบัญชา", blank=True, editable=False)
    report_locked = models.BooleanField(default=True, editable=False)
    report_updated_by = models.CharField(max_length=150, blank=True, editable=False)
    report_updated_at = models.DateTimeField(null=True, blank=True, editable=False)
    # the same for ข้อความลงบันทึกประจำวัน
    diary_template = models.TextField("รูปแบบข้อความลงบันทึกประจำวัน", blank=True, editable=False)
    diary_locked = models.BooleanField(default=True, editable=False)
    diary_updated_by = models.CharField(max_length=150, blank=True, editable=False)
    diary_updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "การตั้งค่าระบบ"
        verbose_name_plural = "การตั้งค่าระบบ"

    @classmethod
    def get(cls) -> "SystemSetting":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"retention_days": settings.DEFAULT_RETENTION_DAYS})
        return obj
