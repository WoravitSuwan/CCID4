"""Role-based permission rules (single source of truth for views and templates).

ผู้ดูแลระบบ: ทุกอย่าง
หัวหน้า: ดูทั้งหมด ออกหมาย ส่งออก
พนักงานสอบสวน: ดูทั้งหมด ออกหมายในคดีของตัวเอง
ธุรการ: นำเข้า ออกหมาย ส่งออก
"""
from functools import wraps

from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role


def role_of(user) -> str | None:
    if not user or not user.is_authenticated:
        return None
    return user.effective_role


def is_admin(user) -> bool:
    return role_of(user) == Role.ADMIN


def can_import(user) -> bool:
    return role_of(user) in {Role.ADMIN, Role.CLERK}


def can_export(user) -> bool:
    return role_of(user) in {Role.ADMIN, Role.HEAD, Role.CLERK, Role.INVESTIGATOR}


def can_export_register(user) -> bool:
    return role_of(user) in {Role.ADMIN, Role.HEAD, Role.CLERK}


def can_manage_settings(user) -> bool:
    return is_admin(user)


def owns_case(user, case) -> bool:
    inv = case.investigator
    return bool(inv and user.investigator_id and inv.pk == user.investigator_id)


def can_issue_for_case(user, case) -> bool:
    role = role_of(user)
    if role in {Role.ADMIN, Role.HEAD, Role.CLERK}:
        return True
    if role == Role.INVESTIGATOR:
        return owns_case(user, case)
    return False


def can_issue_any(user) -> bool:
    role = role_of(user)
    if role == Role.INVESTIGATOR:
        return bool(user.investigator_id)
    return role in {Role.ADMIN, Role.HEAD, Role.CLERK}


def can_edit_case(user, case) -> bool:
    """Edit address / summons result."""
    return can_issue_for_case(user, case)


def can_view_full_id(user, case) -> bool:
    role = role_of(user)
    if role in {Role.ADMIN, Role.HEAD}:
        return True
    return role == Role.INVESTIGATOR and owns_case(user, case)


def can_access_summons(user, summons) -> bool:
    role = role_of(user)
    if role in {Role.ADMIN, Role.HEAD, Role.CLERK}:
        return True
    if role == Role.INVESTIGATOR:
        return owns_case(user, summons.case) or summons.created_by_id == user.pk
    return False


def require(check):
    """View decorator: ``@require(can_import)``."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied("ไม่มีสิทธิ์ใช้งานส่วนนี้")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
