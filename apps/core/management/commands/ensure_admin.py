"""Create the first administrator with a random password if there are no users yet."""
import secrets
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "สร้างบัญชีผู้ดูแลระบบครั้งแรก (ถ้ายังไม่มีผู้ใช้)"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password-file", default="")

    def handle(self, *args, username="admin", password_file="", **options):
        from apps.accounts.models import Role

        User = get_user_model()
        if User.objects.exists():
            return
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        password = "".join(secrets.choice(alphabet) for _ in range(12))
        User.objects.create_superuser(username=username, password=password, role=Role.ADMIN, email="")
        if password_file:
            path = Path(password_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"ชื่อผู้ใช้: {username}\nรหัสผ่าน: {password}\n"
                            "(เปลี่ยนรหัสผ่านได้ที่เมนู 'เปลี่ยนรหัสผ่าน' แล้วลบไฟล์นี้ทิ้ง)\n", encoding="utf-8")
        self.stdout.write(f"CREATED {username} {password}")
