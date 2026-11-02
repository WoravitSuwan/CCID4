"""Word -> PDF with LibreOffice headless (same layout as Word when fonts are installed)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class PdfConversionError(RuntimeError):
    pass


def soffice_available() -> bool:
    return shutil.which(settings.SOFFICE_BIN) is not None


def convert_many(docx_paths, out_dir) -> list[Path]:
    """Convert several .docx files in ONE LibreOffice run. Returns the PDF paths."""
    docx_paths = [Path(p) for p in docx_paths]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not docx_paths:
        return []
    if not soffice_available():
        raise PdfConversionError("ไม่พบโปรแกรม LibreOffice (soffice) บนเครื่อง จึงสร้าง PDF ไม่ได้")
    # A private profile per run lets several conversions run in parallel.
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    cmd = [
        settings.SOFFICE_BIN, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
        "--nolockcheck", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(out_dir),
        *[str(p) for p in docx_paths],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.SOFFICE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise PdfConversionError("แปลงเป็น PDF ใช้เวลานานเกินกำหนด") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    results = []
    for p in docx_paths:
        pdf = out_dir / (p.stem + ".pdf")
        if not pdf.exists():
            logger.error("soffice failed: %s %s", proc.stdout, proc.stderr)
            raise PdfConversionError(f"แปลงไฟล์ {p.name} เป็น PDF ไม่สำเร็จ")
        results.append(pdf)
    return results


def convert(docx_path, out_dir=None) -> Path:
    docx_path = Path(docx_path)
    return convert_many([docx_path], out_dir or docx_path.parent)[0]
