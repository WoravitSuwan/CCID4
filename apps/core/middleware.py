import re

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

PUBLIC_PREFIXES = ("/accounts/login", "/static/", "/healthz", "/branding/")
# Call staff (outsource) may only use the call pages - everything else redirects there.
OUTSOURCE_PREFIXES = ("/calls/", "/accounts/", "/static/", "/healthz", "/summons/", "/exports/", "/branding/")
# other pages call staff may open: start issuing a summons for one of their cases, and the
# postcode lookup used by the address form on the call page
OUTSOURCE_PATTERNS = (re.compile(r"^/cases/\d+/issue/$"), re.compile(r"^/cases/postcode/$"))


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
        if (request.user.is_authenticated and request.user.effective_role == "outsource"
                and not request.path.startswith(OUTSOURCE_PREFIXES)
                and not any(p.match(request.path) for p in OUTSOURCE_PATTERNS)):
            return redirect("calls_queue")
        return self.get_response(request)
