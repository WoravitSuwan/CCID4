from __future__ import annotations

import re

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.registry.models import Case, normalize_receipt_no

from .models import CLOSING, CallAssignment, CallAttempt, CallOutcome


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
