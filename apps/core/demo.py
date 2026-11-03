"""Try-out mode (Vercel): build a throw-away database with fictional data."""
import logging
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.db import connection

logger = logging.getLogger(__name__)
DEMO_ACCOUNTS = [("admin", "ผู้ดูแลระบบ"), ("call1", "เจ้าหน้าที่ outsource")]


def bootstrap_demo(force: bool = False):
    """Create schema + sample data once per instance (cheap no-op afterwards)."""
    if not settings.DEMO_MODE:
        return
    if not force and "registry_case" in connection.introspection.table_names():
        from apps.registry.models import Case

        if Case.objects.exists():
            return
    out = StringIO()
    call_command("migrate", interactive=False, verbosity=0, stdout=out)
    call_command("init_system", stdout=out)
    call_command("load_demo", stdout=out)
    logger.info("demo database ready: %s", out.getvalue().strip().splitlines()[-1:])
