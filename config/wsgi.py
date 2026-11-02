import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

if os.environ.get("VERCEL"):
    # Vercel may use this module directly (Django framework detection) or via api/index.py.
    from apps.core.vercel import setup

    application = setup(application)

app = application
