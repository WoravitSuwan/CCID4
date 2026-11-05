"""Django settings for the summons system (บก.สอท.4).

All deployment-specific values come from environment variables so the same
image runs in Docker Compose, on a developer machine, and in tests.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Vercel (serverless) = try-out mode: no persistent storage, data is rebuilt from the
# fictional sample files on every cold start. See api/index.py.
VERCEL = bool(env("VERCEL"))
DEMO_MODE = env_bool("DEMO_MODE", VERCEL)
if VERCEL:
    os.environ.setdefault("SQLITE_PATH", "/tmp/summons-demo.sqlite3")
    os.environ.setdefault("MEDIA_ROOT", "/tmp/summons-media")
    os.environ.setdefault("BACKGROUND_TASKS", "inline")  # no work may run after the response
    os.environ.setdefault("DJANGO_SECRET_KEY", "demo-only-set-DJANGO_SECRET_KEY-in-vercel-env-7f3a9c")

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.core",
    "apps.registry",
    "apps.imports",
    "apps.documents",
    "apps.calls",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.app_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: PostgreSQL when POSTGRES_HOST is set, otherwise SQLite (local dev).
if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "NAME": env("POSTGRES_DB", "summons"),
            "USER": env("POSTGRES_USER", "summons"),
            "PASSWORD": env("POSTGRES_PASSWORD", "summons"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    # Local install (start-local): one SQLite file in data/, WAL mode so the
    # background thread and web requests do not block each other.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(env("SQLITE_PATH", BASE_DIR / "data" / "db.sqlite3")),
            "OPTIONS": {
                "timeout": 30,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            },
        }
    }

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 8 * 3600)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

LANGUAGE_CODE = "th"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Uploaded and generated files. NEVER exposed via a public URL: downloads go
# through permission-checked, audited views.
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media-not-public/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
# files attached to call records (screenshots, slips, recordings, documents)
CALL_ATTACHMENT_MAX_MB = env_int("CALL_ATTACHMENT_MAX_MB", 25)
CALL_ATTACHMENT_MAX_FILES = env_int("CALL_ATTACHMENT_MAX_FILES", 10)

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
# "celery" (Docker), "thread" (local install without Redis) or "inline"
BACKGROUND_TASKS = env("BACKGROUND_TASKS", "celery")
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "cleanup-media-daily": {
        "task": "apps.core.tasks.cleanup_media",
        "schedule": 24 * 3600,
    },
}

# Application settings
SOFFICE_BIN = env("SOFFICE_BIN", "")  # empty = auto-detect (PATH, then standard install folders)
SOFFICE_TIMEOUT = env_int("SOFFICE_TIMEOUT", 600)
EXPORT_ASYNC_THRESHOLD = env_int("EXPORT_ASYNC_THRESHOLD", 20)
DEFAULT_RETENTION_DAYS = env_int("MEDIA_RETENTION_DAYS", 30)
# This system is for one unit only: the register import keeps only its cases (คอลัมน์ "หน่วยที่รับผิดชอบ")
OWN_UNIT = env("OWN_UNIT", "กก.1 บก.สอท.4")
TEMPLATES_DOCX_DIR = BASE_DIR / "templates_docx"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# PDFs are shown inside the system's own viewer page (iframe + print button); other sites may not frame it
X_FRAME_OPTIONS = "SAMEORIGIN"
if not DEBUG:
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = False

if env_bool("DJANGO_HTTPS", False):
    # Served through an HTTPS reverse proxy (nginx / Traefik / load balancer) that sets X-Forwarded-Proto
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if VERCEL:
    # Each serverless instance has its own /tmp database, so keep the login in a
    # signed cookie instead of the database; serve static files straight from the
    # source folders (no collectstatic step on Vercel).
    SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    WHITENOISE_USE_FINDERS = True
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    import warnings

    warnings.filterwarnings("ignore", message="No directory at", module="whitenoise.base")
