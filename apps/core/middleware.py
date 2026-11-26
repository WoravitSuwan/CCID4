import re

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# LINE: the webhook checks LINE's signature; file links are signed and expire
PUBLIC_PREFIXES = ("/accounts/login", "/static/", "/healthz", "/branding/", "/line/webhook/", "/line/file/")
# Call staff (outsource) may only use the call pages - everything else redirects there.
OUTSOURCE_PREFIXES = ("/calls/", "/accounts/", "/static/", "/healthz", "/summons/", "/exports/", "/branding/",
                      "/stats/", "/reports/", "/imports/")  # imports: the weekly update file only (checked there)
# other pages call staff may open: start issuing a summons for one of their cases, and the
# postcode lookup used by the address form on the call page
OUTSOURCE_PATTERNS = (re.compile(r"^/cases/\d+/issue/$"), re.compile(r"^/cases/postcode/$"))
# investigators: the same pages, without the statistics and daily reports (they list everyone's cases)
INVESTIGATOR_PREFIXES = tuple(p for p in OUTSOURCE_PREFIXES if p not in ("/stats/", "/reports/"))


class LoginRequiredMiddleware:
    """Every page requires a logged-in user (except login/static/health)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(PUBLIC_PREFIXES):
            if request.headers.get("HX-Request"):
                from django.http import HttpResponse

                response = HttpResponse(status=401)
                response["HX-Redirect"] = reverse(settings.LOGIN_URL)
                return response
            return redirect(f"{reverse(settings.LOGIN_URL)}?next={request.get_full_path()}")
        role = request.user.effective_role if request.user.is_authenticated else ""
        allowed = INVESTIGATOR_PREFIXES if role == "investigator" else OUTSOURCE_PREFIXES
        if (role in ("outsource", "investigator") and not request.path.startswith(allowed)
                and not any(p.match(request.path) for p in OUTSOURCE_PATTERNS)):
            return redirect("calls_queue")
        response = self.get_response(request)
        if settings.LINE_WEB_CHECK:
            from apps.calls.line_bot import check_from_web

            check_from_web()  # the LINE daily summary, if its time has come (when no worker runs)
        return response
