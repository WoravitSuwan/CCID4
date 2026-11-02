"""Delete generated/uploaded files older than the retention period (SystemSetting)."""
import datetime as dt
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import SystemSetting

CLEAN_DIRS = ["exports", "summons", "imports"]


class Command(BaseCommand):
    help = "ลบไฟล์ใน media ที่เก่ากว่าจำนวนวันที่ตั้งไว้"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, days=None, dry_run=False, **options):
        from apps.documents.models import ExportJob, Summons
        from apps.imports.models import ImportBatch

        days = days if days is not None else SystemSetting.get().retention_days
        cutoff = timezone.now() - dt.timedelta(days=days)
        root = Path(settings.MEDIA_ROOT)
        removed = 0
        for sub in CLEAN_DIRS:
            base = root / sub
            if not base.exists():
                continue
            for path in sorted(base.rglob("*"), reverse=True):
                if path.is_file():
                    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
                    if mtime < cutoff:
                        removed += 1
                        if not dry_run:
                            path.unlink(missing_ok=True)
                elif path.is_dir() and not any(path.iterdir()) and not dry_run:
                    path.rmdir()
        if not dry_run:
            # Clear references to deleted files (summons files are re-created on demand).
            for model, fields in ((Summons, ["docx_file", "pdf_file"]), (ExportJob, ["result_file"]),
                                  (ImportBatch, ["file"])):
                for obj in model.objects.exclude(**{fields[0]: ""}).only("pk", *fields):
                    changed = False
                    for f in fields:
                        ff = getattr(obj, f)
                        if ff and not (root / ff.name).exists():
                            setattr(obj, f, "")
                            changed = True
                    if changed:
                        obj.save(update_fields=fields)
        self.stdout.write(f"ลบไฟล์ {removed} ไฟล์ (เก่ากว่า {days} วัน)")
