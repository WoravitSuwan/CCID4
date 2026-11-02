"""Vercel entry point (WSGI). Try-out mode only: nothing is stored permanently.

On every cold start the instance creates a fresh SQLite database in /tmp and loads
the fictional sample data with the demo accounts (see apps/core/demo.py and
apps/core/vercel.py, which also restores the original request path).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("VERCEL", "1")

from config.wsgi import application  # noqa: E402

app = application
