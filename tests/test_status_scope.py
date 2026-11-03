import importlib

import pytest
from django.apps import apps as django_apps

from apps.accounts.models import Role, User
from apps.registry.models import Case, Status

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, db):
    client.force_login(User.objects.create_user("adm2", password="pass12345", role=Role.ADMIN))
    return client


def test_scope_tabs_open_final_all(admin_client, setup_signers):
    page = admin_client.get("/cases/?scope=open")
    assert page.context["total"] == 32
    assert page.context["form"].scope_counts == {"all": 40, "open": 32, "final": 8}
    assert admin_client.get("/cases/?scope=final").context["total"] == 8
    assert admin_client.get("/cases/?scope=all").context["total"] == 40
    assert admin_client.get("/cases/?open=on").context["total"] == 32  # old links still work
    finals = admin_client.get("/cases/?scope=final").context["page"]
    assert all(c.status.is_final for c in finals)


def test_sorted_by_date(admin_client, setup_signers):
    newest = [c.report_date for c in admin_client.get("/cases/?scope=all&sort=-date").context["page"]]
    oldest = [c.report_date for c in admin_client.get("/cases/?scope=all&sort=date").context["page"]]
    assert newest == sorted(newest, reverse=True) and oldest == sorted(oldest)
    case = Case.objects.order_by("report_date").first()
    case.set_status(Status.objects.get(name="ส่งสำนวนอัยการ"))
    latest = admin_client.get("/cases/?scope=all&sort=-updated").context["page"][0]
    assert latest.pk == case.pk  # the case whose status was just changed comes first


def test_inline_status_edit_from_register(admin_client, setup_signers):
    case = Case.objects.not_final().first()
    final = Status.objects.get(name="สิ้นสุด")
    resp = admin_client.post(f"/cases/{case.pk}/status/", {"row_status": final.pk}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and "บันทึกแล้ว" in resp.content.decode()
    case.refresh_from_db()
    assert case.status == final
    # it now appears under "สิ้นสุดแล้ว"
    assert case.pk in [c.pk for c in admin_client.get("/cases/?scope=final").context["page"]]
    page = admin_client.get("/cases/?scope=all").content.decode()
    assert 'name="row_status"' in page and 'hx-params="row_status"' in page


def test_old_demo_accounts_switched_off(db):
    for name in ("head", "clerk", "inv", "call2"):
        User.objects.create_user(name, password="demo12345", role=Role.ADMIN)
    real = User.objects.create_user("clerk_real", password="another-pass-1", role=Role.ADMIN)
    renamed = User.objects.create_user("inv2", password="demo12345", role=Role.ADMIN)
    migration = importlib.import_module("apps.accounts.migrations.0005_disable_old_demo_accounts")
    migration.disable_old_demo_accounts(django_apps, None)
    assert not User.objects.filter(username__in=["head", "clerk", "inv", "call2"], is_active=True).exists()
    real.refresh_from_db()
    renamed.refresh_from_db()
    assert real.is_active and renamed.is_active
    client_ok = User.objects.get(username="clerk")
    assert not client_ok.is_active


def test_login_page_lists_two_demo_accounts(client, settings):
    settings.DEMO_MODE = True
    page = client.get("/accounts/login/").content.decode()
    assert "call1" in page and "admin" in page and "call2" not in page and "clerk" not in page
