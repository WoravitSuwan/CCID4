"""``shared_task`` that works with or without Celery installed.

Without Celery (local or Vercel install) a task is a plain function whose
``.delay()`` runs it straight away; apps.core.background decides where it runs.
"""
try:
    from celery import shared_task
except ImportError:  # pragma: no cover - depends on the install
    def shared_task(func=None, **_options):
        def wrap(f):
            f.delay = lambda *args, **kwargs: f(*args, **kwargs)
            f.name = f"{f.__module__}.{f.__name__}"
            return f

        return wrap(func) if func is not None else wrap

    HAS_CELERY = False
else:
    HAS_CELERY = True

__all__ = ["shared_task", "HAS_CELERY"]
