"""Delete all case data (to start the trial over with fresh imports).

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
    transaction.on_commit(_delete_files)
    return before


def _delete_files():
    root = Path(settings.MEDIA_ROOT)
    for sub in MEDIA_DIRS:
        shutil.rmtree(root / sub, ignore_errors=True)
