"""ข้อมูลทดลอง: a made-up case staff add to try the call page. Shown apart on the first page, never counted
in any number, and deleted with everything recorded on it (calls, attachments, summonses)."""
from __future__ import annotations

import re

from django.db import transaction

from apps.registry.models import AddressOverride, Case

from .models import CallAssignment

PREFIX = "TEST"


def trial_cases():
    return (Case.objects.filter(is_test=True).select_related("call_assignment", "call_assignment__assignee")
            .order_by("-created_at"))


def clean_phone(value: str) -> str:
    return re.sub(r"[^\d+]", "", value or "")


@transaction.atomic
def add_trial(name: str, phone: str) -> Case:
    last = (Case.objects.filter(receipt_no__startswith=PREFIX).order_by("-receipt_no")
            .values_list("receipt_no", flat=True).first())
    number = int(re.sub(r"\D", "", last or "") or 0) + 1
    case = Case.objects.create(receipt_no=f"{PREFIX}{number:04d}", victim_name=name.strip(),
                               victim_phone=clean_phone(phone), case_type="ข้อมูลทดลอง", source_sheet="ข้อมูลทดลอง",
                               is_test=True)
    CallAssignment.objects.get_or_create(case=case)
    return case


@transaction.atomic
def delete_trial(case: Case) -> None:
    """Only a ข้อมูลทดลอง case can be deleted this way."""
    if not case.is_test:
        raise ValueError("ลบได้เฉพาะข้อมูลทดลอง")
    for summons in case.summonses.all():
        for f in (summons.docx_file, summons.pdf_file):
            if f:
                f.delete(save=False)
        summons.delete()
    for attachment in case.call_attachments.all():
        attachment.file.delete(save=False)
    AddressOverride.objects.filter(receipt_no=case.receipt_no).delete()
    case.delete()
