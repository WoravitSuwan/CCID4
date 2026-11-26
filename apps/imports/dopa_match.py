"""Which case an uploaded ทะเบียนราษฎร PDF belongs to - only among the cases the user may work on.

ใครคดีมัน: a user linked to a พนักงานสอบสวน (ตั้งค่า > ผู้ใช้) sees and updates only the cases of that
investigator; administrators every case; call staff not linked to anyone the shared list they already
issue summonses from.
"""
from __future__ import annotations

import re

from django.db.models import Q

from apps.core import permissions as perms
from apps.registry.models import Case

from .dopa import Person, format_id

RECEIPT_IN_NAME = re.compile(r"(?<![A-Z0-9])([A-Z]\d{8,13})(?!\d)")


def cases_for(user):
    """The cases this user may update from a registration PDF."""
    qs = Case.objects.all()
    if not perms.can_issue_any(user):
        return qs.none()
    investigator_id = getattr(user, "investigator_id", None)
    if investigator_id and not perms.is_admin(user):
        qs = qs.filter(investigator_alias__investigator_id=investigator_id)
    return qs


def may_use(user, case) -> bool:
    return case is not None and cases_for(user).filter(pk=case.pk).exists()


def _by_id(scope, digits: str):
    if len(digits) != 13:
        return scope.none()
    return scope.filter(Q(complainant__national_id=digits) | Q(complainant__national_id=format_id(digits))
                        | Q(complainant__national_id=" ".join([digits[0], digits[1:5], digits[5:10],
                                                                digits[10:12], digits[12]])))


def _by_name(scope, person: Person):
    if not (person.first_name and person.last_name):
        return scope.none()
    return scope.filter(Q(complainant__first_name=person.first_name, complainant__last_name=person.last_name)
                        | Q(victim_name__contains=f"{person.first_name} {person.last_name}"))


def match(person: Person, file_name: str, user, chosen: Case | None = None) -> tuple[Case | None, str, list]:
    """(the case, how it was matched, other possible cases). A case picked by the user (from its page) wins;
    then the ID number in the file ผู้แจ้ง, the เลขรับแจ้ง in the file name, the name."""
    scope = cases_for(user)
    candidates = []
    for qs in (_by_id(scope, person.national_id), _by_name(scope, person)):
        for c in qs.select_related("complainant")[:10]:
            if c not in candidates:
                candidates.append(c)
    m = RECEIPT_IN_NAME.search((file_name or "").upper())
    named = scope.filter(receipt_no=m.group(1)).first() if m else None
    if named and named not in candidates:
        candidates.insert(0, named)
    if chosen is not None and may_use(user, chosen):
        return chosen, "เลือกจากหน้าคดี", [c for c in candidates if c != chosen]
    by_id = list(_by_id(scope, person.national_id)[:2])
    if len(by_id) == 1:
        return by_id[0], "เลขประจำตัวประชาชนตรงกับไฟล์ผู้แจ้ง", [c for c in candidates if c != by_id[0]]
    if named is not None:
        return named, "เลขรับแจ้งในชื่อไฟล์", [c for c in candidates if c != named]
    by_name = list(_by_name(scope, person)[:2])
    if len(by_name) == 1:
        return by_name[0], "ชื่อ-ชื่อสกุลตรงกับคดี (ตรวจให้แน่ใจ)", [c for c in candidates if c != by_name[0]]
    return None, "", candidates


def id_agrees(case: Case, person: Person) -> bool | None:
    """True/False when both the case's file ผู้แจ้ง and the PDF have an ID number; None when one is missing."""
    if not case or not case.complainant or not person.national_id:
        return None
    theirs = re.sub(r"\D", "", case.complainant.national_id or "")
    return None if len(theirs) != 13 else theirs == person.national_id
