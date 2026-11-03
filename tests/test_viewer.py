"""Open exported PDFs inside the web page, with print buttons."""
import io

import pytest
from pypdf import PdfReader

from apps.accounts.models import Role, User
from apps.documents.models import ExportJob, ExportMode
from apps.documents.services import issue, plan_issue, run_export
from tests.test_summons import issuable_cases, opts

pytestmark = pytest.mark.django_db


@pytest.fixture
def job(setup_signers, admin_user):
    created = issue(admin_user, plan_issue(admin_user, issuable_cases()[:2], opts()), opts())
    job = ExportJob.objects.create(format="pdf", mode=ExportMode.ZIP, include_envelope=True, created_by=admin_user)
    job.summonses.set(created)
    run_export(job)
    job.refresh_from_db()
    return job


def test_view_and_print_exported_pdfs(client, admin_user, job):
    client.force_login(admin_user)
    assert f"/exports/{job.pk}/view/" in client.get(f"/exports/{job.pk}/").content.decode()
    page = client.get(f"/exports/{job.pk}/view/")
    html = page.content.decode()
    assert page.status_code == 200 and 'id="btn-print"' in html and "พิมพ์ทั้งหมด (2 ไฟล์)" in html
    assert len(page.context["files"]) == 2 and '<iframe id="viewer"' in html
    one = client.get(f"/exports/{job.pk}/file/0/")
    assert one["Content-Type"] == "application/pdf" and "inline" in one["Content-Disposition"]
    assert one["X-Frame-Options"] == "SAMEORIGIN"  # shown in our own page, not on other sites
    pages = PdfReader(io.BytesIO(one.content)).pages
    assert pages[-1].mediabox.width > pages[-1].mediabox.height  # the envelope page is landscape
    both = client.get(f"/exports/{job.pk}/print-all/")
    assert len(PdfReader(io.BytesIO(both.content)).pages) == 2 * len(pages)
    assert client.get(f"/exports/{job.pk}/file/9/").status_code == 404


def test_viewer_is_private(client, job):
    other = User.objects.create_user("call7v", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(other)
    assert client.get(f"/exports/{job.pk}/view/").status_code == 403
    assert client.get(f"/exports/{job.pk}/file/0/").status_code == 403


def test_summons_viewer(client, admin_user, job):
    client.force_login(admin_user)
    s = job.summonses.first()
    page = client.get(f"/summons/{s.pk}/view/?back=https://evil.example/")
    html = page.content.decode()
    assert page.status_code == 200 and f"/summons/{s.pk}/pdf/" in html and 'id="btn-print"' in html
    assert 'href="/summons/"' in html  # unsafe "back" link replaced
    assert "attachment" in client.get(f"/summons/{s.pk}/pdf/?download=1")["Content-Disposition"]
