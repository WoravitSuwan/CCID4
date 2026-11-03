"""Role-based permission rules (single source of truth for views and templates).

Two roles only:
  ผู้ดูแลระบบ (admin): everything - import, register, issue, export, calls, settings
  เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource): only the call pages for the cases assigned to them
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


def is_outsource(user) -> bool:
    return role_of(user) == Role.OUTSOURCE


# Everything except the call pages is for administrators.
can_import = can_export = can_export_register = can_manage_settings = can_manage_calls = is_admin
can_issue_any = is_admin


def can_issue_for_case(user, case) -> bool:
    return is_admin(user)


def can_edit_case(user, case) -> bool:
    """Edit address / status / summons result."""
    return is_admin(user)


def can_view_full_id(user, case) -> bool:
    return is_admin(user)


def can_access_summons(user, summons) -> bool:
    return is_admin(user)


def can_record_call(user, case) -> bool:
    if is_admin(user):
        return True
    if is_outsource(user):
        assignment = getattr(case, "call_assignment", None)
        return bool(assignment and assignment.assignee_id == user.pk)
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
