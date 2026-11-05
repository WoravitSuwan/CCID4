"""Set a new password for the administrator (creates the account if it is missing).

    docker compose exec web python manage.py reset_admin            # new random password
    docker compose exec web python manage.py reset_admin --password "..."
"""
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class Command(BaseCommand):
    help = "ตั้งรหัสผ่านใหม่ให้ผู้ดูแลระบบ (สร้างบัญชีให้ถ้ายังไม่มี)"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", default="")

    def handle(self, *args, username, password, **options):
        from apps.accounts.models import Role
        from apps.core.models import AuditAction, AuditLog

        password = password or "".join(secrets.choice(ALPHABET) for _ in range(12))
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        created = user is None
        if created:
            user = User(username=username)
        user.set_password(password)
        user.is_active = True
        user.is_staff = user.is_superuser = True
        user.role = Role.ADMIN
        user.save()
        what = "สร้าง" if created else "ตั้งรหัสผ่านใหม่ให้"
        try:
            AuditLog.objects.create(user=None, action=AuditAction.EDIT, target_type="user", target_id=str(user.pk),
                                    detail=f"{what}ผู้ดูแลระบบ {username} จากคำสั่ง reset_admin")
        except Exception:  # never let the log stop someone getting back in
            pass
        out = Path(settings.BASE_DIR) / "data" / "admin-password.txt"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(f"{username} / {password}\r\n", encoding="utf-8")
        except OSError:
            pass
        self.stdout.write(f"{'สร้าง' if created else 'ตั้งรหัสผ่านใหม่ให้'}บัญชี {username} แล้ว")
        self.stdout.write(f"ชื่อผู้ใช้: {username}")
        self.stdout.write(f"รหัสผ่าน: {password}")
