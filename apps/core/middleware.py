from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

PUBLIC_PREFIXES = ("/accounts/login", "/static/", "/healthz")


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
        return self.get_response(request)
