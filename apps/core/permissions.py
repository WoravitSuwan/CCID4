"""Role-based permission rules (single source of truth for views and templates).

Two roles only:
  ผู้ดูแลระบบ (admin): everything - import, register, issue, export, calls, settings
  เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource): every case on the shared call list - record calls, fix the
      address, change the status, issue summonses and download/export them. No register, import,
      settings or full national ID.
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


# Import, register, settings and call management are for administrators.
can_import = can_export_register = can_manage_settings = can_manage_calls = is_admin


def can_issue_any(user) -> bool:
    return is_admin(user) or is_outsource(user)


def can_export(user) -> bool:
    return is_admin(user) or is_outsource(user)


def can_work_on_case(user, case) -> bool:
    """Call staff work from one shared list: any case, no assignment needed."""
    return case is not None and (is_admin(user) or is_outsource(user))


def can_issue_for_case(user, case) -> bool:
    return can_work_on_case(user, case)


def can_edit_case(user, case) -> bool:
    """Edit address / status / summons result."""
    return is_admin(user)


def can_view_full_id(user, case) -> bool:
    return is_admin(user)


def can_access_summons(user, summons) -> bool:
    return is_admin(user) or is_outsource(user)


def can_record_call(user, case) -> bool:
    return can_work_on_case(user, case)


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


def can_change_status(user, case) -> bool:
    """Admins and call staff (from the call page)."""
    return can_work_on_case(user, case)


def can_access_attachment(user, attachment) -> bool:
    """See/download a call attachment: admins and call staff (never anonymous users)."""
    return can_work_on_case(user, attachment.case)

