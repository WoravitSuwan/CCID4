"""Settings → ล้างข้อมูล: delete all case data after typing the phrase, the deleter's name and the password."""
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.calls.models import CallAttachment
from apps.core.models import AuditLog
from apps.imports.models import ImportBatch
from apps.registry.models import Case, Complainant, Investigator, Status, Unit

pytestmark = pytest.mark.django_db

GOOD = {"confirm": "ลบข้อมูลทั้งหมด", "deleted_by": "ร.ต.ท. ทดสอบ ระบบ", "password": "admin-pass-123"}


@pytest.fixture
def admin(client, imported):
    user = User.objects.create_user("boss", password="admin-pass-123", role=Role.ADMIN)
    client.force_login(user)
    return user


def test_wipe_needs_phrase_name_and_password(client, admin):
    n = Case.objects.count()
    page = client.get("/settings/wipe/")
    assert page.status_code == 200 and "จะถูกลบ" in page.content.decode()
    for bad in ({**GOOD, "confirm": "ลบ"}, {**GOOD, "deleted_by": ""}, {**GOOD, "password": "wrong"}):
        resp = client.post("/settings/wipe/", bad)
        assert resp.status_code == 200 and Case.objects.count() == n
    assert not AuditLog.objects.filter(action="wipe").exists()


def test_wipe_deletes_case_data_and_keeps_setup(client, admin, django_capture_on_commit_callbacks):
    case = Case.objects.not_final().first()
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    client.post(f"/calls/{case.pk}/", {"outcome": "info", "called_at": now, "go": "stay",
                                      "attachments": [SimpleUploadedFile("a.png", b"\\x89PNG data", "image/png")]})
    att = CallAttachment.objects.get()
    path = Path(settings.MEDIA_ROOT) / att.file.name
    assert path.exists()
    kept = (User.objects.count(), Investigator.objects.count(), Unit.objects.count(), Status.objects.count())
    with django_capture_on_commit_callbacks(execute=True):  # files go once the deletion is committed
        resp = client.post("/settings/wipe/", GOOD)
    assert resp.status_code == 302 and resp["Location"] == "/imports/"
    assert not Case.objects.exists() and not Complainant.objects.exists() and not ImportBatch.objects.exists()
    assert not CallAttachment.objects.exists() and not path.exists()
    assert (User.objects.count(), Investigator.objects.count(), Unit.objects.count(), Status.objects.count()) == kept
    log = AuditLog.objects.get(action="wipe")
    assert log.user == admin and "ร.ต.ท. ทดสอบ ระบบ" in log.detail and "คดีในทะเบียน 40" in log.detail


def test_only_admins_can_wipe(client, imported):
    caller = User.objects.create_user("c1w", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(caller)
    client.post("/settings/wipe/", {**GOOD, "password": "x" * 10})
    assert Case.objects.exists()
