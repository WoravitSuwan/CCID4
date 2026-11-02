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
