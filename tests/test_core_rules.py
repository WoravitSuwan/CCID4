"""Rules from the original specification that every change must keep."""
import datetime as dt
import os

import pytest
from django.core.management import call_command
from django.test import Client

from apps.accounts.models import Role, User
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def test_wrong_password_is_refused_with_a_message(db):
    User.objects.create_user("rule_user", password="right-password-1", role=Role.OUTSOURCE)
    client = Client()
    resp = client.post("/accounts/login/", {"username": "rule_user", "password": "wrong"})
    assert resp.status_code == 200 and "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง" in resp.content.decode()
    assert client.get("/calls/").status_code == 302  # still not logged in
    ok = client.post("/accounts/login/", {"username": "rule_user", "password": "right-password-1"})
    assert ok.status_code == 302


def test_disabled_user_cannot_log_in(db):
    user = User.objects.create_user("rule_off", password="right-password-1", role=Role.OUTSOURCE)
    user.is_active = False
    user.save()
    resp = Client().post("/accounts/login/", {"username": "rule_off", "password": "right-password-1"})
    assert resp.status_code == 200 and "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง" in resp.content.decode()


def test_missing_title_prefix_is_never_guessed():
    from apps.registry.models import Complainant

    assert Complainant(prefix="", first_name="สมใจ", last_name="ทดสอบ").full_name == "สมใจ ทดสอบ"
    assert Complainant(prefix="-", first_name="สมใจ", last_name="ทดสอบ").full_name == "สมใจ ทดสอบ"
    assert Complainant(prefix="นาง", first_name="สมใจ", last_name="ทดสอบ").full_name == "นางสมใจ ทดสอบ"


def test_old_files_are_deleted_after_the_retention_period(settings, tmp_path):
    from apps.core.models import SystemSetting

    root = settings.MEDIA_ROOT
    old = root / "exports" / "2026" / "old.zip"
    new = root / "exports" / "2026" / "new.zip"
    attachment = root / "call_attachments" / "2026" / "slip.png"  # call attachments are evidence: kept
    for f in (old, new, attachment):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    long_ago = (dt.datetime.now() - dt.timedelta(days=40)).timestamp()
    for f in (old, attachment):
        os.utime(f, (long_ago, long_ago))
    s = SystemSetting.get()
    s.retention_days = 30
    s.save()
    call_command("cleanup_media", stdout=open(os.devnull, "w"))
    assert not old.exists() and new.exists() and attachment.exists()


def test_audit_log_records_and_filters(client, setup_signers, admin_user):
    from apps.registry.models import Case

    client.force_login(admin_user)
    case = Case.objects.exclude(complainant__national_id="").exclude(complainant__isnull=True).first()
    client.post(f"/cases/{case.pk}/reveal-id/")
    client.get("/cases/export.xlsx")
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"view_id", "export"} <= actions, actions
    page = client.get("/settings/audit/", {"action": "view_id"})
    rows = list(page.context["page"])
    assert rows and all(r.action == "view_id" for r in rows)
    assert case.receipt_no in page.content.decode() or str(case.pk) in page.content.decode()
    assert client.get("/settings/audit/", {"user": admin_user.pk}).status_code == 200
