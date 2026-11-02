"""Vercel entry point (WSGI). Try-out mode only: nothing is stored permanently.

On every cold start the instance creates a fresh SQLite database in /tmp and loads
the fictional sample data with the demo accounts (see apps/core/demo.py).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("VERCEL", "1")

import django  # noqa: E402

django.setup()

import logging  # noqa: E402

from apps.core.demo import bootstrap_demo  # noqa: E402

try:
    bootstrap_demo()
except Exception:  # keep serving; the traceback shows up in the Vercel function logs
    logging.getLogger("api").exception("demo bootstrap failed")

from django.core.wsgi import get_wsgi_application  # noqa: E402

app = get_wsgi_application()
