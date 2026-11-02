"""Phone follow-up of victims by (outsourced) call staff."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core import thai_format as tf


class CallOutcome(models.TextChoices):
    APPOINTED = "appointed", "ติดต่อได้ – นัดเข้าพบพนักงานสอบสวน"
    INFO = "info", "ติดต่อได้ – ให้ข้อมูลเพิ่มเติมแล้ว"
    CALLBACK = "callback", "ติดต่อได้ – ขอให้โทรกลับภายหลัง"
    NOT_PROCEED = "not_proceed", "ติดต่อได้ – ไม่ประสงค์ดำเนินคดีต่อ"
    REACHED_OTHER = "reached_other", "ติดต่อได้ – อื่นๆ (ระบุในหมายเหตุ)"
    OFF = "off", "ไม่สามารถติดต่อได้ (ปิดเครื่อง)"
    NO_ANSWER = "no_answer", "ไม่สามารถติดต่อได้ (ไม่รับสาย)"
    BUSY = "busy", "ไม่สามารถติดต่อได้ (สายไม่ว่าง)"
    VOICEMAIL = "voicemail", "ไม่สามารถติดต่อได้ (ฝากข้อความไว้)"
    UNKNOWN_PERSON = "unknown_person", "ไม่สามารถติดต่อได้ (ปลายทางแจ้งว่าไม่รู้จักผู้เสียหาย)"
    WRONG = "wrong", "เบอร์ไม่ถูกต้อง / ไม่มีผู้ใช้เบอร์นี้"


REACHED = {CallOutcome.APPOINTED, CallOutcome.INFO, CallOutcome.CALLBACK, CallOutcome.NOT_PROCEED,
           CallOutcome.REACHED_OTHER}
# short labels for the big option buttons on the call screen
SHORT_LABELS = {
    CallOutcome.APPOINTED: "นัดเข้าพบพนักงานสอบสวน",
    CallOutcome.INFO: "ให้ข้อมูลเพิ่มเติมแล้ว",
    CallOutcome.CALLBACK: "ขอให้โทรกลับภายหลัง",
    CallOutcome.NOT_PROCEED: "ไม่ประสงค์ดำเนินคดีต่อ",
    CallOutcome.REACHED_OTHER: "อื่นๆ (ระบุในหมายเหตุ)",
    CallOutcome.OFF: "ปิดเครื่อง",
    CallOutcome.NO_ANSWER: "ไม่รับสาย",
    CallOutcome.BUSY: "สายไม่ว่าง",
    CallOutcome.VOICEMAIL: "ฝากข้อความไว้",
    CallOutcome.UNKNOWN_PERSON: "ปลายทางไม่รู้จักผู้เสียหาย",
    CallOutcome.WRONG: "เบอร์ไม่ถูกต้อง / ไม่มีผู้ใช้",
}
# outcomes after which nobody needs to call again (the assignment is closed)
CLOSING = {CallOutcome.APPOINTED, CallOutcome.INFO, CallOutcome.NOT_PROCEED, CallOutcome.REACHED_OTHER,
           CallOutcome.WRONG, CallOutcome.UNKNOWN_PERSON}


class CallAssignment(models.Model):
    """A case given to a call-staff member to phone the victim."""

    case = models.OneToOneField("registry.Case", on_delete=models.CASCADE, related_name="call_assignment",
                                verbose_name="คดี")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="call_assignments",
                                 verbose_name="ผู้รับผิดชอบโทร")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name="ผู้มอบหมาย")
    assigned_at = models.DateTimeField("มอบหมายเมื่อ", default=timezone.now)
    is_done = models.BooleanField("ติดต่อเสร็จแล้ว", default=False, db_index=True)
    done_at = models.DateTimeField(null=True, blank=True)
    callback_at = models.DateTimeField("นัดโทรกลับ", null=True, blank=True, db_index=True)
    last_call_at = models.DateTimeField("โทรล่าสุด", null=True, blank=True)
    last_outcome = models.CharField("ผลล่าสุด", max_length=30, choices=CallOutcome.choices, blank=True)
    attempt_count = models.PositiveIntegerField("จำนวนครั้งที่โทร", default=0)

    class Meta:
        verbose_name = "การมอบหมายโทร"
        verbose_name_plural = "การมอบหมายโทร"

    def __str__(self):
        return f"{self.case.receipt_no} → {self.assignee}"

    @property
    def last_reached(self) -> bool:
        return self.last_outcome in REACHED

    @property
    def callback_due(self) -> bool:
        return bool(not self.is_done and self.callback_at and self.callback_at <= timezone.now())


class CallAttempt(models.Model):
    case = models.ForeignKey("registry.Case", on_delete=models.CASCADE, related_name="calls", verbose_name="คดี")
    number = models.PositiveIntegerField("ครั้งที่")
    called_at = models.DateTimeField("เวลาโทร", default=timezone.now)
    phone = models.CharField("เบอร์ที่โทร", max_length=50, blank=True)
    outcome = models.CharField("ผลการติดต่อ", max_length=30, choices=CallOutcome.choices)
    note = models.TextField("หมายเหตุ / สิ่งที่ผู้เสียหายแจ้ง", blank=True)
    callback_at = models.DateTimeField("นัดโทรกลับ", null=True, blank=True)
    appointment_at = models.DateTimeField("นัดเข้าพบพนักงานสอบสวน", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   verbose_name="ผู้โทร")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-called_at", "-number"]
        verbose_name = "บันทึกการโทร"
        verbose_name_plural = "บันทึกการโทร"

    def __str__(self):
        return f"{self.case.receipt_no} ครั้งที่ {self.number}"

    @property
    def reached(self) -> bool:
        return self.outcome in REACHED

    def register_text(self) -> str:
        """Line in the register's 'บันทึกการติดต่อ' style:
        'ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง) 9 ตค 68 เวลา 10.30 น.'"""
        at = timezone.localtime(self.called_at)
        month = tf.thai_month_abbr(at.month).replace(".", "")
        text = f"ครั้งที่ {self.number} {self.get_outcome_display()}"
        if self.note.strip():
            text += f" {self.note.strip()}"
        text += f" {at.day} {month} {tf.ce_to_be(at) % 100:02d} เวลา {at:%H.%M} น."
        if self.appointment_at:
            ap = timezone.localtime(self.appointment_at)
            text += f" (นัดพบ พงส. {ap.day} {tf.thai_month_abbr(ap.month).replace('.', '')} " \
                    f"{tf.ce_to_be(ap) % 100:02d} เวลา {ap:%H.%M} น.)"
        return text
