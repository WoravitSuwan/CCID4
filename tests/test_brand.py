"""CCID4 look: the emblem everywhere by default (an uploaded logo replaces it), copy buttons, quick search."""
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db
BRAND = Path(settings.BASE_DIR) / "static" / "img" / "brand"


def test_logo_files_are_there():
    from PIL import Image

    for name, size in (("ccid4-logo-256.png", 256), ("ccid4-logo-128.png", 128), ("ccid4-logo-64.png", 64),
                       ("apple-touch-icon.png", 180), ("favicon-32.png", 32)):
        im = Image.open(BRAND / name)
        assert im.size == (size, size) and im.mode == "RGBA", name
        assert im.getpixel((0, 0))[3] == 0  # transparent corner: the background was cut away
    assert (BRAND / "favicon.ico").stat().st_size > 0


def test_ccid4_logo_by_default_and_an_uploaded_logo_wins(client, admin_user):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    login = client.get("/accounts/login/").content.decode()
    assert "/static/img/brand/ccid4-logo-256.png" in login and "CCID4" in login
    assert 'rel="icon" href="/static/img/brand/favicon.ico"' in login
    client.force_login(admin_user)
    page = client.get("/").content.decode()
    assert "/static/img/brand/ccid4-logo-128.png" in page and 'class="brand-name">CCID4<' in page
    assert "/static/img/brand/ccid4-logo-64.png" in page  # the phone's top bar
    assert "· CCID4</title>" in page
    # upload a logo in ตั้งค่า > ระบบ: it is used everywhere instead
    buf = io.BytesIO()
    Image.new("RGBA", (40, 40), (200, 0, 0, 255)).save(buf, "PNG")
    resp = client.post("/settings/system/", {
        "retention_days": 30, "default_reason": "x", "default_appointment_days": 14, "letter_number": "ตช.0039.52/1",
        "logo": SimpleUploadedFile("l.png", buf.getvalue(), "image/png")})
    assert resp.status_code == 302
    page = client.get("/").content.decode()
    assert "/branding/logo/?v=" in page and "ccid4-logo-128.png" not in page
    assert "โลโก้ที่อัปโหลดไว้" in client.get("/settings/system/").content.decode()


def test_copy_buttons_next_to_numbers(client, admin_user, imported):
    from apps.registry.models import Case

    case = Case.objects.first()
    client.force_login(admin_user)
    for url in (f"/cases/{case.pk}/", f"/calls/{case.pk}/"):
        assert f'class="copy-btn" data-copy-text="{case.receipt_no}"' in client.get(url).content.decode(), url
    assert 'class="copy-btn" data-copy-text="' in client.get("/calls/?tab=all").content.decode()


def test_quick_search_and_keyboard():
    js = (Path(settings.BASE_DIR) / "static" / "js" / "ui.js").read_text()
    assert "function openCmdk" in js and '"/"' in js and "data-copy-text" in js and "prefers-reduced-motion" in js


def test_search_address_for_quick_search(client, admin_user, imported):
    from apps.accounts.models import Role, User

    client.force_login(admin_user)
    assert 'data-search-url="/cases/?scope=all&amp;q="' in client.get("/").content.decode()
    staff = User.objects.create_user("caller30", password="pass12345", role=Role.OUTSOURCE)
    client.force_login(staff)
    assert 'data-search-url="/calls/?tab=all&amp;year=&amp;state=&amp;q="' in client.get("/calls/").content.decode()
