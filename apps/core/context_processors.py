from django.conf import settings

from . import permissions as perms


def pdf_available() -> bool:
    from apps.documents.pdf import soffice_available

    return soffice_available()


def thai_font() -> str:
    from apps.documents.pdf import thai_font_status

    return thai_font_status() if pdf_available() else "n/a"


def logo_version():
    """Timestamp of the uploaded unit logo (None = no logo; the shield icon is shown)."""
    from .models import SystemSetting

    try:
        stamp = SystemSetting.objects.filter(pk=1, logo_type__gt="").values_list("logo_updated", flat=True).first()
    except Exception:  # database not ready (first start, migrations running)
        return None
    return int(stamp.timestamp()) if stamp else None


def brand(version) -> dict:
    """The logo everywhere (menu, login, browser tab): the one uploaded in ตั้งค่า > ระบบ, else the CCID4 shield."""
    from django.templatetags.static import static
    from django.urls import reverse

    if version:
        url = f"{reverse('branding_logo')}?v={version}"
        return {"brand_logo": url, "brand_logo_small": url, "brand_logo_big": url, "brand_icon": url,
                "brand_custom": True}
    return {"brand_logo": static("img/brand/ccid4-logo-128.png"),
            "brand_logo_small": static("img/brand/ccid4-logo-64.png"),
            "brand_logo_big": static("img/brand/ccid4-logo-256.png"),
            "brand_icon": static("img/brand/favicon.ico"), "brand_custom": False}


def app_context(request):
    version = logo_version()
    base = {"demo_mode": settings.DEMO_MODE, "logo_version": version, **brand(version)}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        if settings.DEMO_MODE:
            from .demo import DEMO_ACCOUNTS

            base["demo_accounts"] = DEMO_ACCOUNTS
        return base
    ctx = {
        **base,
        "perm_import": perms.can_import(user),
        "perm_import_update": perms.can_import_update(user),
        "perm_export_register": perms.can_export_register(user),
        "perm_settings": perms.can_manage_settings(user),
        "perm_issue_any": perms.can_issue_any(user),
        "pdf_available": pdf_available(),
        "thai_font": thai_font(),
        "perm_calls_manage": perms.can_manage_calls(user),
        "is_outsource": perms.is_case_staff(user),  # the call-staff screens (call staff and investigators)
        "is_investigator": perms.is_investigator(user),
        "my_investigator": getattr(user, "investigator", None) if perms.scoped_investigator(user) else None,
    }
    if perms.is_admin(user):
        from apps.registry.models import InvestigatorAlias, Status, other_unit_cases

        ctx["admin_alerts"] = {
            "new_statuses": Status.objects.filter(needs_review=True).count(),
            "unlinked_aliases": InvestigatorAlias.objects.filter(investigator__isnull=True).count(),
            "other_unit_cases": other_unit_cases().count(), "own_unit": settings.OWN_UNIT,
        }
    return ctx
