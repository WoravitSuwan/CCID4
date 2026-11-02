"""Run long jobs (imports, large exports) off the request.

BACKGROUND_TASKS setting:
  "celery"  - Docker / server install: queue in Celery (falls back to a thread if Redis is down)
  "thread"  - local install without Redis: run in a background thread of the web process
  "inline"  - run immediately (tests)
"""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connection

logger = logging.getLogger(__name__)


def _thread_target(func, args):
    close_old_connections()
    try:
        func(*args)
    except Exception:  # the task records its own failure state
        logger.exception("background task %s failed", getattr(func, "__name__", func))
    finally:
        connection.close()


def start_thread(func, *args):
    t = threading.Thread(target=_thread_target, args=(func, args), daemon=True)
    t.start()
    return t


def run_background(task, *args):
    mode = "inline" if settings.CELERY_TASK_ALWAYS_EAGER else settings.BACKGROUND_TASKS
    if mode == "inline":
        return task(*args)
    from .tasking import HAS_CELERY

    if mode == "celery" and HAS_CELERY:
        try:
            return task.delay(*args)
        except Exception:
            logger.exception("celery unavailable, running %s in a thread", getattr(task, "name", task))
    return start_thread(task, *args)
