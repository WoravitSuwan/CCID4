from __future__ import annotations

import re

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.registry.models import Case, normalize_receipt_no

from .models import CLOSING, CallAssignment, CallAttachment, CallAttempt, CallOutcome

# What call staff may attach. No html/svg/scripts/executables: these could run in the browser or on a PC.
ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "heic", "heif",       # photos / screenshots
    "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv",          # documents
    "mp3", "m4a", "wav", "ogg", "amr", "aac", "mp4", "mov",     # call recordings / video clips
    "zip",
}


def case_phones(case: Case) -> list[str]:
    """Phone numbers to try, register first, without duplicates."""
    phones = []
    for raw in (case.victim_phone, case.complainant.phone if case.complainant else ""):
        for part in re.split(r"[,/;\s]+", raw or ""):
            digits = re.sub(r"[^\d+]", "", part)
            if len(digits) >= 9 and digits not in phones:
                phones.append(digits)
    return phones


def parse_receipt_list(text: str) -> list[str]:
    """Pasted case ids (one per line, or separated by commas/spaces) -> normalised keys."""
    keys = []
    for token in re.split(r"[\s,;]+", text or ""):
        key = normalize_receipt_no(token)
        if key and key not in keys:
            keys.append(key)
    return keys


@transaction.atomic
def assign(cases, assignee, by_user, reassign: bool = True) -> dict:
    created = moved = kept = 0
    for case in cases:
        current = CallAssignment.objects.filter(case=case).first()
        if current is None:
            CallAssignment.objects.create(case=case, assignee=assignee, assigned_by=by_user)
            created += 1
        elif current.assignee_id != assignee.pk and reassign:
            current.assignee = assignee
            current.assigned_by = by_user
            current.assigned_at = timezone.now()
            current.save(update_fields=["assignee", "assigned_by", "assigned_at"])
            moved += 1
        else:
            kept += 1
    return {"created": created, "moved": moved, "kept": kept}


@transaction.atomic
def record_call(case: Case, user, *, outcome: str, phone: str = "", note: str = "", called_at=None,
                callback_at=None, appointment_at=None) -> CallAttempt:
    number = (CallAttempt.objects.select_for_update().filter(case=case).aggregate(m=Max("number"))["m"] or 0) + 1
    attempt = CallAttempt.objects.create(
        case=case, number=number, outcome=outcome, phone=phone, note=note.strip(),
        called_at=called_at or timezone.now(), callback_at=callback_at, appointment_at=appointment_at,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    assignment, _ = CallAssignment.objects.get_or_create(
        case=case, defaults={"assignee": user, "assigned_by": user})
    assignment.attempt_count = number
    assignment.last_call_at = attempt.called_at
    assignment.last_outcome = outcome
    assignment.callback_at = callback_at if outcome == CallOutcome.CALLBACK or callback_at else None
    assignment.is_done = outcome in CLOSING
    assignment.done_at = timezone.now() if assignment.is_done else None
    assignment.save()
    return attempt


def contact_log_with_calls(case: Case) -> str:
    """Register 'บันทึกการติดต่อ' text: what the file had + calls recorded in this system."""
    lines = [case.contact_log.strip()] if case.contact_log.strip() else []
    for attempt in sorted(case.calls.all(), key=lambda a: (a.called_at, a.number)):
        text = attempt.register_text()
        if text not in case.contact_log:
            lines.append(text)
    return "\n".join(lines)


def check_attachments(files) -> list[str]:
    """Problems with uploaded files (empty list = all fine)."""
    from django.conf import settings

    problems = []
    max_bytes = settings.CALL_ATTACHMENT_MAX_MB * 1024 * 1024
    if len(files) > settings.CALL_ATTACHMENT_MAX_FILES:
        problems.append(f"แนบได้ครั้งละไม่เกิน {settings.CALL_ATTACHMENT_MAX_FILES} ไฟล์")
    for f in files:
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ALLOWED_EXTENSIONS:
            problems.append(f"{f.name}: ไม่รองรับไฟล์ชนิดนี้ (รองรับ รูปภาพ, PDF, Word, Excel, ไฟล์เสียง, วิดีโอ, ZIP)")
        elif f.size > max_bytes:
            problems.append(f"{f.name}: ใหญ่เกิน {settings.CALL_ATTACHMENT_MAX_MB} MB")
        elif f.size == 0:
            problems.append(f"{f.name}: ไฟล์ว่างเปล่า")
    return problems


def save_attachments(attempt: CallAttempt, files, user) -> list[CallAttachment]:
    saved = []
    for f in files:
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", f.name).strip()[:255] or "file"
        saved.append(CallAttachment.objects.create(
            attempt=attempt, case_id=attempt.case_id, file=f, original_name=name, size=f.size,
            uploaded_by=user if getattr(user, "is_authenticated", False) else None))
    return saved
