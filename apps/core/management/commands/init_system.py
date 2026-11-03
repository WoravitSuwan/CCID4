"""First-run setup: bundled templates, settings row, optional admin from env vars."""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.core.models import SystemSetting


class Command(BaseCommand):
    help = "เตรียมระบบครั้งแรก (แบบฟอร์มตั้งต้น, การตั้งค่า, ผู้ดูแลระบบ)"

    def handle(self, *args, **options):
        from apps.accounts.models import Role
        from apps.documents.services import ensure_default_templates

        from django.core.management import call_command

        SystemSetting.get()
        call_command("fill_postcodes", stdout=self.stdout)
        from apps.registry.models import link_register_names

        linked = link_register_names()
        if linked:
            self.stdout.write(f"สร้าง/ผูกผู้ลงนามให้ชื่อพนักงานสอบสวนในทะเบียน {linked} ชื่อ")
        created = ensure_default_templates()
        for tpl in created:
            self.stdout.write(f"เพิ่มแบบฟอร์ม: {tpl}")
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        User = get_user_model()
        if username and password and not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, password=password, role=Role.ADMIN,
                                          email=os.environ.get("DJANGO_SUPERUSER_EMAIL", ""))
            self.stdout.write(f"สร้างผู้ดูแลระบบ: {username}")
        self.stdout.write("พร้อมใช้งาน")
