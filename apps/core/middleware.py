from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

PUBLIC_PREFIXES = ("/accounts/login", "/static/", "/healthz")
# Call staff (outsource) may only use the call pages - everything else redirects there.
OUTSOURCE_PREFIXES = ("/calls/", "/accounts/", "/static/", "/healthz")


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
                and not request.path.startswith(OUTSOURCE_PREFIXES)):
            return redirect("calls_queue")
        return self.get_response(request)
