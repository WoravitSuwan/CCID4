"""Reading values from the address bar safely: a mistyped or tampered URL gives "no filter", never an error."""
from __future__ import annotations

import datetime as dt


def date_param(request, name: str) -> dt.date | None:
    value = (request.GET.get(name) or "").strip()
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None
