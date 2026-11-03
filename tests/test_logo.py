"""Unit logo: uploaded in ตั้งค่า → ระบบ, shown in the sidebar, on the login page and as the tab icon."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import SystemSetting

pytestmark = pytest.mark.django_db

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def form(**extra):
    s = SystemSetting.get()
    return {"retention_days": s.retention_days, "default_reason": s.default_reason,
            "default_appointment_days": s.default_appointment_days, **extra}


def test_upload_show_and_remove_logo(client, admin_user):
    client.force_login(admin_user)
    assert "branding/logo" not in client.get("/").content.decode()  # shield icon until a logo is uploaded
    client.post("/settings/system/", form(logo=SimpleUploadedFile("logo.png", PNG, "image/png")))
    s = SystemSetting.get()
    assert s.logo_type == "image/png" and bytes(s.logo_data) == PNG
    html = client.get("/").content.decode()
    assert 'rel="icon"' in html and "/branding/logo/?v=" in html
    client.logout()
    login = client.get("/accounts/login/").content.decode()
    assert "/branding/logo/?v=" in login and "กองกำกับการ ๑" in login
    logo = client.get("/branding/logo/")  # public: shown before signing in
    assert logo.status_code == 200 and logo["Content-Type"] == "image/png" and logo.content == PNG
    client.force_login(admin_user)
    client.post("/settings/system/", form(remove_logo="on"))
    assert client.get("/branding/logo/").status_code == 404


def test_rejects_non_images(client, admin_user):
    client.force_login(admin_user)
    fake = SimpleUploadedFile("x.png", b"<svg onload=alert(1)>", "image/png")
    resp = client.post("/settings/system/", form(logo=fake))
    assert resp.status_code == 200 and "รองรับเฉพาะรูป" in resp.content.decode()
    assert not SystemSetting.get().logo_type
