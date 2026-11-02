from django.conf import settings

from . import permissions as perms


def pdf_available() -> bool:
    from apps.documents.pdf import soffice_available

    return soffice_available()


def app_context(request):
    base = {"demo_mode": settings.DEMO_MODE}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        if settings.DEMO_MODE:
            from .demo import DEMO_ACCOUNTS

            base["demo_accounts"] = DEMO_ACCOUNTS
        return base
    ctx = {
        **base,
        "perm_import": perms.can_import(user),
        "perm_export_register": perms.can_export_register(user),
        "perm_settings": perms.can_manage_settings(user),
        "perm_issue_any": perms.can_issue_any(user),
        "pdf_available": pdf_available(),
    }
    if perms.is_admin(user):
        from apps.registry.models import InvestigatorAlias, Status

        ctx["admin_alerts"] = {
            "new_statuses": Status.objects.filter(needs_review=True).count(),
            "unlinked_aliases": InvestigatorAlias.objects.filter(investigator__isnull=True).count(),
        }
    return ctx
