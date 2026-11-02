"""Vercel glue: restore the original request path and build the demo database.

vercel.json rewrites every request to ``/api/index?__path=<original path>``.
Depending on how Vercel runs the function, Django would otherwise see the
path ``/api/index`` for every page (login redirect loop), so the WSGI wrapper
below puts the original path back before Django handles the request.
"""
import logging
from urllib.parse import unquote_to_bytes

logger = logging.getLogger(__name__)
PATH_PARAM = "__path="


def restore_original_path(app):
    def wrapped(environ, start_response):
        query = environ.get("QUERY_STRING", "")
        if PATH_PARAM in query:
            keep, original = [], None
            for part in query.split("&"):
                if original is None and part.startswith(PATH_PARAM):
                    original = part[len(PATH_PARAM):]
                elif part:
                    keep.append(part)
            path = "/" + unquote_to_bytes(original or "").decode("latin-1").lstrip("/")
            environ["PATH_INFO"] = path  # WSGI paths are latin-1 decoded bytes
            environ["SCRIPT_NAME"] = ""
            environ["QUERY_STRING"] = "&".join(keep)
        return app(environ, start_response)

    return wrapped


def setup(application):
    """Called from config/wsgi.py when running on Vercel."""
    from .demo import bootstrap_demo

    try:
        bootstrap_demo()
    except Exception:  # keep serving; the traceback shows up in the Vercel function logs
        logger.exception("demo bootstrap failed")
    return restore_original_path(application)
