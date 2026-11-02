# Celery is only installed for the Docker/server setup (requirements-server.txt).
try:
    from .celery import app as celery_app
except ImportError:  # local / Vercel install without Celery
    celery_app = None

__all__ = ("celery_app",)
