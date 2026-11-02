"""Vercel rewrites every request to /api/index?__path=<original>; the app must still route correctly."""
import pytest
from django.test import Client

from apps.core.vercel import restore_original_path

pytestmark = pytest.mark.django_db


def vercel_rewrite(app):
    """Simulate vercel.json: '/(.*)' -> '/api/index?__path=$1' with PATH_INFO=/api/index."""
    def wrapped(environ, start_response):
        path = environ["PATH_INFO"].lstrip("/")
        query = environ.get("QUERY_STRING", "")
        environ["PATH_INFO"] = "/api/index"
        environ["QUERY_STRING"] = f"__path={path}" + (f"&{query}" if query else "")
        return app(environ, start_response)
    return wrapped


def test_restore_original_path_unit():
    seen = {}

    def app(environ, start_response):
        seen.update(path=environ["PATH_INFO"], query=environ["QUERY_STRING"])
        return []

    restore_original_path(app)({"PATH_INFO": "/api/index", "QUERY_STRING": "__path=cases/12/&open=on&q=a%20b"}, None)
    assert seen == {"path": "/cases/12/", "query": "open=on&q=a%20b"}
    restore_original_path(app)({"PATH_INFO": "/api/index", "QUERY_STRING": "__path="}, None)
    assert seen["path"] == "/" and seen["query"] == ""
    restore_original_path(app)({"PATH_INFO": "/cases/", "QUERY_STRING": "open=on"}, None)  # no rewrite
    assert seen == {"path": "/cases/", "query": "open=on"}


def test_login_flow_through_vercel_rewrite(settings, imported):
    from apps.accounts.models import Role, User

    User.objects.create_user("v", password="pass12345", role=Role.ADMIN)
    from django.test.client import ClientHandler

    client = Client()

    class RewritingHandler(ClientHandler):
        def __call__(self, environ):
            captured = {}

            def fake_app(env, start_response):
                captured.update(env)
                return []
            vercel_rewrite(restore_original_path(fake_app))(environ, None)
            environ.update(PATH_INFO=captured["PATH_INFO"], QUERY_STRING=captured["QUERY_STRING"])
            return super().__call__(environ)

    client.handler = RewritingHandler(enforce_csrf_checks=False)
    resp = client.get("/")
    assert resp.status_code == 302 and resp["Location"] == "/accounts/login/?next=/"
    assert client.get("/accounts/login/").status_code == 200  # no redirect loop
    resp = client.post("/accounts/login/", {"username": "v", "password": "pass12345", "next": "/cases/?open=on"})
    assert resp["Location"] == "/cases/?open=on"
    page = client.get("/cases/?open=on")
    assert page.status_code == 200 and page.context["total"] == 32
