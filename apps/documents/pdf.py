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


SOFFICE_CANDIDATES = [
    # Windows ("soffice.com" waits for the conversion to finish in a console)
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    # macOS
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def soffice_path() -> str | None:
    """LibreOffice executable: SOFFICE_BIN setting, PATH, then standard install folders."""
    if settings.SOFFICE_BIN:
        found = shutil.which(settings.SOFFICE_BIN) or (settings.SOFFICE_BIN if Path(settings.SOFFICE_BIN).exists()
                                                       else None)
        if found:
            return found
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def pdf_unavailable_reason() -> str:
    """Why PDF cannot be made here, worded for where the system runs."""
    if getattr(settings, "VERCEL", False):
        return ("ระบบทดลองบน Vercel สร้าง PDF ไม่ได้ (Vercel ติดตั้งโปรแกรม LibreOffice ไม่ได้) "
                "— ส่งออกเป็น Word แล้วเปิดใน Microsoft Word เพื่อพิมพ์หรือบันทึกเป็น PDF ได้")
    return ("ไม่พบโปรแกรม LibreOffice บนเครื่องที่รันระบบ จึงสร้าง PDF ไม่ได้ (ส่งออกเป็น Word ได้ตามปกติ) "
            "— ติดตั้ง LibreOffice จาก https://www.libreoffice.org แล้วเปิดระบบใหม่")


def soffice_available() -> bool:
    return soffice_path() is not None


def convert_many(docx_paths, out_dir, export_filter="writer_pdf_Export") -> list[Path]:
    """Convert several .docx files in ONE LibreOffice run. Returns the PDF paths.
    A spreadsheet needs export_filter="calc_pdf_Export"."""
    docx_paths = [Path(p) for p in docx_paths]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not docx_paths:
        return []
    soffice = soffice_path()
    if not soffice:
        raise PdfConversionError(pdf_unavailable_reason())
    # A private profile per run lets several conversions run in parallel.
    profile = Path(tempfile.gettempdir()) / f"lo_profile_{uuid.uuid4().hex}"
    cmd = [
        soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
        "--nolockcheck", "--convert-to", f"pdf:{export_filter}", "--outdir", str(out_dir),
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


_font_status_cache: dict = {}


def thai_font_status() -> str:
    """'real' (TH SarabunIT๙ installed), 'compat' (Sarabun Compat fallback) or 'missing'."""
    if "status" in _font_status_cache:
        return _font_status_cache["status"]
    names = ""
    if shutil.which("fc-list"):
        try:
            names = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            names = ""
    else:  # Windows: look at the font folders
        import os

        for folder in (Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts",
                       Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"):
            if folder.exists():
                names += " ".join(p.name for p in folder.iterdir())
    compact = names.lower().replace(" ", "")
    if "sarabunit" in compact:
        status = "real"
    elif "sarabuncompat" in compact:
        status = "compat"
    else:
        status = "missing"
    _font_status_cache["status"] = status
    return status
