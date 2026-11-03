import pytest

from apps.core.background import run_background
from apps.core.downloads import content_disposition


def test_content_disposition_has_ascii_fallback():
    header = content_disposition("ส่งออกหมายเรียก_20261102.zip")
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''%E0%B8%AA" in header
    assert header.split('"')[1].isascii() and header.split('"')[1].endswith(".zip")
    assert content_disposition("a.pdf", inline=True).startswith("inline;")


@pytest.mark.django_db(transaction=True)
def test_thread_mode_runs_task(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.BACKGROUND_TASKS = "thread"
    done = []
    thread = run_background(lambda x: done.append(x), 7)
    thread.join(5)
    assert done == [7]


def test_soffice_autodetect(settings):
    from apps.documents.pdf import soffice_path

    settings.SOFFICE_BIN = ""
    assert soffice_path()  # LibreOffice is installed on the test machine
    settings.SOFFICE_BIN = "/nonexistent/soffice"
    assert soffice_path()  # falls back to PATH


def test_vercelignore_keeps_runtime_files(tmp_path):
    """.vercelignore uses .gitignore rules: make sure no runtime file is dropped from the deployment."""
    import shutil
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    if not files:
        pytest.skip("not a git checkout")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    shutil.copy(root / ".vercelignore", tmp_path / ".gitignore")
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=tmp_path,
                             input="\n".join(files), capture_output=True, text=True).stdout.split()
    runtime = ("apps/", "config/", "templates/", "static/", "templates_docx/", "sample_data/", "api/")
    assert [f for f in ignored if f.startswith(runtime) or f in ("manage.py", "requirements.txt")] == []


@pytest.mark.django_db
def test_export_without_libreoffice_offers_word_only(client, setup_signers, admin_user, monkeypatch):
    import datetime as dt

    from apps.documents import pdf
    from apps.documents.models import ExportJob, JobStatus
    from apps.documents.services import IssueOptions, issue, plan_issue
    from apps.registry.models import Case

    monkeypatch.setattr(pdf, "soffice_path", lambda: None)
    client.force_login(admin_user)
    cases = [c for c in Case.objects.not_final() if not c.issue_blockers()][:2]
    o = IssueOptions(issue_date=dt.date(2026, 2, 9), appointment_date=dt.date(2026, 2, 23))
    created = issue(admin_user, plan_issue(admin_user, cases, o), o)
    page = client.get("/exports/new/?" + "&".join(f"ids={s.pk}" for s in created))
    assert [v for v, _ in page.context["form"].fields["format"].choices] == ["docx"]
    assert "LibreOffice" in page.content.decode()
    # an older PDF job that failed can be re-run as Word with one click
    job = ExportJob.objects.create(format="pdf", mode="merged", created_by=admin_user, status=JobStatus.FAILED,
                                   message="no LibreOffice")
    job.summonses.set(created)
    assert "ส่งออกเป็น Word แทน" in client.get(f"/exports/{job.pk}/").content.decode()
    resp = client.post(f"/exports/{job.pk}/retry-word/")
    new = ExportJob.objects.exclude(pk=job.pk).get()
    assert resp["Location"] == f"/exports/{new.pk}/" and new.format == "docx" and new.status == JobStatus.DONE
