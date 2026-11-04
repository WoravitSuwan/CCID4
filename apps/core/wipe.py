"""Delete trial data: everything about cases (start over with fresh imports), or only the
issued summonses (issue again with the same cases).

Kept: users, settings, units, statuses, signers (rank/position), templates, saved column
mappings and the audit log (the wipe itself is recorded there).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction

# media sub-folders that only hold files belonging to the deleted data
MEDIA_DIRS = ["imports", "exports", "summons", "call_attachments"]
SUMMONS_MEDIA_DIRS = ["exports", "summons"]


def counts() -> dict:
    from apps.calls.models import CallAttachment, CallAttempt
    from apps.documents.models import ExportJob, Summons
    from apps.imports.models import ImportBatch
    from apps.registry.models import AddressOverride, Case, Complainant

    return {
        "คดีในทะเบียน": Case.objects.count(),
        "ข้อมูลผู้แจ้ง": Complainant.objects.count(),
        "ที่อยู่ที่แก้เอง": AddressOverride.objects.count(),
        "หมายเรียกที่ออก": Summons.objects.count(),
        "งานส่งออก": ExportJob.objects.count(),
        "บันทึกการโทร": CallAttempt.objects.count(),
        "ไฟล์แนบการโทร": CallAttachment.objects.count(),
        "ประวัติการนำเข้า": ImportBatch.objects.count(),
    }


@transaction.atomic
def wipe_case_data() -> dict:
    from apps.calls.models import CallAssignment, CallAttachment, CallAttempt
    from apps.documents.models import ExportJob, Summons
    from apps.imports.models import ImportBatch
    from apps.registry.models import AddressOverride, Case, Complainant

    before = counts()
    ExportJob.objects.all().delete()
    Summons.objects.all().delete()  # PROTECTs the case, so it goes first
    CallAttachment.objects.all().delete()
    CallAttempt.objects.all().delete()
    CallAssignment.objects.all().delete()
    AddressOverride.objects.all().delete()
    Case.objects.all().delete()
    Complainant.objects.all().delete()
    ImportBatch.objects.all().delete()
    transaction.on_commit(lambda: _delete_files(MEDIA_DIRS))
    return before


def summons_counts() -> dict:
    from apps.documents.models import ExportJob, Summons

    return {"หมายเรียกที่ออก": Summons.objects.count(), "งานส่งออก": ExportJob.objects.count(),
            "คดีที่สถานะถูกเปลี่ยนเป็น 'ออกหมายเรียก' ตอนออกหมาย": _status_set_by_issue().count()}


def _status_set_by_issue():
    """Cases the issue step moved to 'ออกหมายเรียก' (status changed in the system, file value remembered)."""
    from apps.registry.defaults import SUMMONS_STATUS_NAME
    from apps.registry.models import Case

    return Case.objects.filter(status__name=SUMMONS_STATUS_NAME, status_manual_at__isnull=False,
                               summonses__isnull=False).distinct()


@transaction.atomic
def wipe_summonses(restore_status: bool = True) -> dict:
    """Delete every issued summons and export; cases, complainants and calls stay."""
    from apps.documents.models import ExportJob, Summons
    from apps.registry.models import Case, Status

    before = summons_counts()
    restored = 0
    if restore_status:
        statuses = {s.name: s for s in Status.objects.all()}
        for case in _status_set_by_issue().select_related("status"):
            Case.objects.filter(pk=case.pk).update(
                status=statuses.get(case.status_file_value), status_manual_at=None, status_manual_by=None,
                status_file_value="")
            restored += 1
    ExportJob.objects.all().delete()
    Summons.objects.all().delete()
    transaction.on_commit(lambda: _delete_files(SUMMONS_MEDIA_DIRS))
    before["คืนสถานะคดีเดิม"] = restored
    return before


def call_counts() -> dict:
    from django.utils import timezone

    from apps.calls.models import CallAttempt

    today = CallAttempt.objects.filter(called_at__date=timezone.localdate())
    return {"today": today.count(), "all": CallAttempt.objects.count(),
            "today_files": sum(a.attachments.count() for a in today.prefetch_related("attachments"))}


@transaction.atomic
def wipe_calls(today_only: bool) -> dict:
    """Delete call records (today's or all) and put each affected case back as it was before them."""
    from django.utils import timezone

    from apps.calls.models import CLOSING, CallAssignment, CallAttachment, CallAttempt, CallOutcome

    attempts = CallAttempt.objects.all()
    if today_only:
        attempts = attempts.filter(called_at__date=timezone.localdate())
    case_ids = set(attempts.values_list("case_id", flat=True))
    files = list(CallAttachment.objects.filter(attempt__in=attempts).values_list("file", flat=True))
    before = {"บันทึกการโทร": attempts.count(), "ไฟล์แนบ": len(files), "คดีที่ได้รับผล": len(case_ids)}
    attempts.delete()  # attachments go with them (cascade)
    for assignment in CallAssignment.objects.filter(case_id__in=case_ids):
        left = list(CallAttempt.objects.filter(case_id=assignment.case_id).order_by("called_at", "number"))
        last = left[-1] if left else None
        assignment.attempt_count = len(left)
        assignment.last_call_at = last.called_at if last else None
        assignment.last_outcome = last.outcome if last else ""
        assignment.callback_at = (last.callback_at if last and (last.outcome == CallOutcome.CALLBACK
                                                                or last.callback_at) else None)
        assignment.is_done = bool(last and last.outcome in CLOSING)
        assignment.done_at = assignment.done_at if assignment.is_done else None
        if not left and assignment.assigned_by_id is None:
            assignment.assignee = None  # became responsible only by calling; nobody assigned it
        assignment.save()
    transaction.on_commit(lambda: _delete_media_files(files))
    return before


def _delete_media_files(names):
    root = Path(settings.MEDIA_ROOT)
    for name in names:
        if name:
            (root / name).unlink(missing_ok=True)


def _delete_files(dirs):
    root = Path(settings.MEDIA_ROOT)
    for sub in dirs:
        shutil.rmtree(root / sub, ignore_errors=True)
