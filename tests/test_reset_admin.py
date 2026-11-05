"""Getting back in when the admin password is lost (e.g. an old database volume with an older password)."""
import pytest
from django.core.management import call_command

from apps.accounts.models import Role, User
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def test_reset_admin_sets_a_new_password_and_creates_the_account(client, settings, tmp_path):
    settings.BASE_DIR = tmp_path
    call_command("reset_admin", password="ใหม่-รหัส-123")  # account missing: created
    admin = User.objects.get(username="admin")
    assert admin.role == Role.ADMIN and admin.is_active and admin.is_superuser
    assert client.post("/accounts/login/", {"username": "admin", "password": "ใหม่-รหัส-123"}).status_code == 302
    admin.is_active = False
    admin.set_password("old")
    admin.save()
    call_command("reset_admin")  # random password, account switched back on
    saved = (tmp_path / "data" / "admin-password.txt").read_text(encoding="utf-8").strip()
    new_password = saved.split(" / ", 1)[1]
    assert len(new_password) == 12 and User.objects.get(username="admin").check_password(new_password)
    client.logout()
    assert client.post("/accounts/login/", {"username": "admin", "password": new_password}).status_code == 302
    assert AuditLog.objects.filter(detail__contains="reset_admin").count() == 2
