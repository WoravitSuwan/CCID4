"""Role-based permission rules (single source of truth for views and templates).

Two roles only:
  ผู้ดูแลระบบ (admin): everything - import, register, issue, export, calls, settings
  เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource): for the cases assigned to them only - record calls,
      issue summonses (choosing the signer's rank/position) and download/export those summonses
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


def is_assigned(user, case) -> bool:
    """The case is on this outsource caller's list."""
    from apps.calls.models import CallAssignment

    return bool(user and user.is_authenticated and case is not None
                and CallAssignment.objects.filter(case_id=case.pk, assignee_id=user.pk).exists())


def can_issue_any(user) -> bool:
    return is_admin(user) or is_outsource(user)


def can_export(user) -> bool:
    return is_admin(user) or is_outsource(user)


def can_issue_for_case(user, case) -> bool:
    return is_admin(user) or (is_outsource(user) and is_assigned(user, case))


def can_edit_case(user, case) -> bool:
    """Edit address / status / summons result."""
    return is_admin(user)


def can_view_full_id(user, case) -> bool:
    return is_admin(user)


def can_access_summons(user, summons) -> bool:
    if is_admin(user):
        return True
    return is_outsource(user) and (summons.created_by_id == user.pk or is_assigned(user, summons.case))


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


def can_change_status(user, case) -> bool:
    """Admins, and call staff for the cases on their call list."""
    return is_admin(user) or can_record_call(user, case)


def can_access_attachment(user, attachment) -> bool:
    """See/download a call attachment: admins and the assigned call staff of that case."""
    return is_admin(user) or can_record_call(user, attachment.case)

