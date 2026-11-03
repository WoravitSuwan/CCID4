"""Call history attachments and status changes from the call page."""
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.calls.models import CallAssignment, CallAttachment, CallAttempt
from apps.core.models import AuditLog
from apps.registry.models import Case, Status

pytestmark = pytest.mark.django_db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def caller(client, imported):
    user = User.objects.create_user("caller5", password="pass12345", role=Role.OUTSOURCE)
    case = Case.objects.not_final().first()
    CallAssignment.objects.create(case=case, assignee=user)
    client.force_login(user)
    return user


def own_case(user):
    return CallAssignment.objects.get(assignee=user).case


def record(client, case, files=(), **extra):
    data = {"outcome": "info", "phone": "0811111111", "called_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "note": "ผู้เสียหายส่งรูปแชทมาให้", "go": "stay", **extra}
    if files:
        data["attachments"] = list(files)
    return client.post(f"/calls/{case.pk}/", data)


def test_record_call_with_attachments(client, caller):
    case = own_case(caller)
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert 'enctype="multipart/form-data"' in page and 'name="attachments"' in page
    resp = record(client, case, [SimpleUploadedFile("แชท ไลน์.png", PNG, "image/png"),
                                 SimpleUploadedFile("สลิป.pdf", b"%PDF-1.4 test", "application/pdf")])
    assert resp.status_code == 302
    attempt = CallAttempt.objects.get(case=case)
    files = list(attempt.attachments.all())
    assert [f.original_name for f in files] == ["แชท ไลน์.png", "สลิป.pdf"]
    stored = Path(settings.MEDIA_ROOT) / files[0].file.name
    assert stored.is_file() and stored.parent.parts[-3] == "call_attachments"
    assert "แชท" not in stored.name  # stored under a random name, original kept in the database
    assert files[0].uploaded_by == caller
    assert "แนบไฟล์ 2 ไฟล์" in AuditLog.objects.filter(action="call").latest("pk").detail
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert "แชท ไลน์.png" in page and f"/calls/attachments/{files[0].pk}/" in page
    dl = client.get(f"/calls/attachments/{files[0].pk}/")
    assert dl.status_code == 200 and b"".join(dl.streaming_content) == PNG
    assert dl["X-Content-Type-Options"] == "nosniff" and "inline" in dl["Content-Disposition"]
    assert "attachment" in client.get(f"/calls/attachments/{files[1].pk}/?download=1")["Content-Disposition"]


def test_bad_file_keeps_call_unsaved(client, caller):
    case = own_case(caller)
    resp = record(client, case, [SimpleUploadedFile("evil.html", b"<script>alert(1)</script>", "text/html")])
    assert resp.status_code == 200 and "ไม่รองรับไฟล์ชนิดนี้" in resp.content.decode()
    assert not CallAttempt.objects.filter(case=case).exists()
    settings.CALL_ATTACHMENT_MAX_MB = 0  # pytest-django restores settings after the test
    resp = record(client, case, [SimpleUploadedFile("big.jpg", b"x" * 10, "image/jpeg")])
    assert "ใหญ่เกิน" in resp.content.decode() and not CallAttempt.objects.filter(case=case).exists()


def test_attach_to_earlier_call_and_delete(client, caller):
    case = own_case(caller)
    record(client, case)
    attempt = CallAttempt.objects.get(case=case)
    resp = client.post(f"/calls/{case.pk}/calls/{attempt.pk}/attach/",
                       {"attachments": [SimpleUploadedFile("เสียงสนทนา.m4a", b"audio", "audio/mp4")]})
    assert resp["Location"].endswith(f"#call-{attempt.pk}")
    att = CallAttachment.objects.get(attempt=attempt)
    path = Path(settings.MEDIA_ROOT) / att.file.name
    assert path.is_file()
    client.post(f"/calls/attachments/{att.pk}/delete/", {"next": "https://evil.example/"})
    assert not CallAttachment.objects.exists() and not path.exists()
    assert AuditLog.objects.filter(action="call", detail__contains="ลบไฟล์แนบ").exists()


def test_attachments_are_private(client, caller, admin_user):
    case = own_case(caller)
    record(client, case, [SimpleUploadedFile("a.png", PNG, "image/png")])
    att = CallAttachment.objects.get()
    client.logout()
    assert client.get(f"/calls/attachments/{att.pk}/").status_code == 302  # login required
    other = User.objects.create_user("caller6", password="pass12345", role=Role.OUTSOURCE)
    client.force_login(other)  # shared call list: other call staff can open it, but not delete it
    assert client.get(f"/calls/attachments/{att.pk}/").status_code == 200
    assert client.post(f"/calls/attachments/{att.pk}/delete/").status_code == 403
    client.force_login(admin_user)  # admins see them on the case page too
    assert "a.png" in client.get(f"/cases/{case.pk}/").content.decode()
    assert client.get(f"/calls/attachments/{att.pk}/").status_code == 200
    export = client.get("/calls/export.xlsx")
    assert export.status_code == 200


def test_caller_changes_case_status(client, caller):
    case = own_case(caller)
    final = Status.objects.filter(is_final=True).first() or Status.objects.create(name="ยุติ", is_final=True)
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert 'id="status-select"' in page
    client.post(f"/calls/{case.pk}/status/", {"status": final.pk, "note": "ผู้เสียหายไม่ประสงค์ดำเนินคดีต่อ"})
    case.refresh_from_db()
    assert case.status == final and case.status_manual_by == caller
    log = AuditLog.objects.get(action="edit", user=caller)
    assert final.name in log.detail and "ไม่ประสงค์" in log.detail
    # any case on the shared list, but never from the admin register endpoint
    other = Case.objects.not_final().exclude(pk=case.pk).first()
    assert client.post(f"/calls/{other.pk}/status/", {"status": final.pk}).status_code == 302
    before = Case.objects.exclude(pk__in=[case.pk, other.pk]).not_final().first()
    client.post(f"/cases/{before.pk}/status/", {"status": final.pk})
    assert Case.objects.get(pk=before.pk).status != final
