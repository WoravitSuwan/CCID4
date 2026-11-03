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


def _delete_files(dirs):
    root = Path(settings.MEDIA_ROOT)
    for sub in dirs:
        shutil.rmtree(root / sub, ignore_errors=True)
