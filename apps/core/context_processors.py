from . import permissions as perms


def app_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    ctx = {
        "perm_import": perms.can_import(user),
        "perm_export_register": perms.can_export_register(user),
        "perm_settings": perms.can_manage_settings(user),
        "perm_issue_any": perms.can_issue_any(user),
    }
    if perms.is_admin(user):
        from apps.registry.models import InvestigatorAlias, Status

        ctx["admin_alerts"] = {
            "new_statuses": Status.objects.filter(needs_review=True).count(),
            "unlinked_aliases": InvestigatorAlias.objects.filter(investigator__isnull=True).count(),
        }
    return ctx
