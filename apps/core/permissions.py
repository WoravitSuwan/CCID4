"""Role-based permission rules (single source of truth for views and templates).

Three roles:
  ผู้ดูแลระบบ (admin): everything - import, register, issue, export, calls, settings
  เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource): every case on the shared call list - record calls, fix the
      address, change the status, issue summonses and download/export them. No register, import,
      settings or full national ID.
  พนักงานสอบสวน (investigator): the same screens as call staff, but only the cases of the พนักงานสอบสวน
      linked to the account (ใครคดีมัน) - no statistics or daily reports of everyone's cases.

The link is User.investigator (a foreign key), so renaming the account never loses its cases. A call staff
account linked to an investigator is limited the same way.
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


def is_investigator(user) -> bool:
    return role_of(user) == Role.INVESTIGATOR


def is_case_staff(user) -> bool:
    """Works from the call-staff screens (call list, call page, issue): call staff and investigators."""
    return is_outsource(user) or is_investigator(user)


def scoped_investigator(user):
    """None = may see every case; else the investigator id whose cases only are allowed (0 = none at all: an
    investigator account not linked yet)."""
    if not user or not user.is_authenticated or is_admin(user):
        return None
    if is_investigator(user):
        return user.investigator_id or 0
    return user.investigator_id or None


def case_scope(qs, user, path: str = ""):
    """Limit a queryset (of cases, or of things with ``path`` to a case, e.g. 'case__') to what the user may see."""
    inv = scoped_investigator(user)
    if inv is None:
        return qs
    return qs.filter(**{f"{path}investigator_alias__investigator_id": inv}) if inv else qs.none()


def owns_case(user, case) -> bool:
    inv = scoped_investigator(user)
    if case is None:
        return False
    if inv is None:
        return True
    alias = case.investigator_alias
    return bool(inv) and alias is not None and alias.investigator_id == inv


# Import, register, settings and call management are for administrators.
can_import = can_export_register = can_manage_settings = can_manage_calls = is_admin


def can_import_update(user) -> bool:
    """The weekly file from the big system (updates cases, deletes nothing): call staff may load it too, and
    investigators (it then updates their own cases only, adding none)."""
    return is_admin(user) or is_case_staff(user)


def can_issue_any(user) -> bool:
    return is_admin(user) or is_case_staff(user)


def can_export(user) -> bool:
    return is_admin(user) or is_case_staff(user)


def can_work_on_case(user, case) -> bool:
    """Call staff work from one shared list: any case, no assignment needed. Investigators: their own cases."""
    return case is not None and (is_admin(user) or is_case_staff(user)) and owns_case(user, case)


def can_issue_for_case(user, case) -> bool:
    return can_work_on_case(user, case)


def can_edit_case(user, case) -> bool:
    """Edit address / status / summons result."""
    return is_admin(user)


def can_view_full_id(user, case) -> bool:
    return is_admin(user)


def can_access_summons(user, summons) -> bool:
    return (is_admin(user) or is_case_staff(user)) and owns_case(user, summons.case)


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

