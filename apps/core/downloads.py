"""File download responses with Thai file names that work in every browser."""
import re
from pathlib import Path
from urllib.parse import quote

from django.http import FileResponse


def content_disposition(filename: str, inline: bool = False) -> str:
    """RFC 6266: ASCII fallback name + UTF-8 name (Thai) for modern browsers."""
    suffix = Path(filename).suffix
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("_") or "file"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_stem}{suffix}\"; filename*=UTF-8''{quote(filename)}"


def file_download(path, filename: str, inline: bool = False) -> FileResponse:
    response = FileResponse(open(path, "rb"))
    response["Content-Disposition"] = content_disposition(filename, inline)
    return response
