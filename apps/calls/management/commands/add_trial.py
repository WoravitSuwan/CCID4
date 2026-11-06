"""Add ข้อมูลทดลอง (a made-up name + phone to try calling) from the command line.

    docker compose exec web python manage.py add_trial "นางสาวทดลอง ใจดี" 0812345678
    docker compose exec web python manage.py add_trial --list
    docker compose exec web python manage.py add_trial --delete TEST0001
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "เพิ่ม/ดู/ลบข้อมูลทดลองสำหรับลองโทร (ไม่นับรวมในตัวเลขใดๆ)"

    def add_arguments(self, parser):
        parser.add_argument("name", nargs="?", default="")
        parser.add_argument("phone", nargs="?", default="")
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--delete", default="")

    def handle(self, *args, name, phone, list, delete, **options):
        from apps.calls.trial import add_trial, clean_phone, delete_trial, trial_cases
        from apps.core.audit import log
        from apps.core.models import AuditAction
        from apps.registry.models import Case

        if delete:
            case = Case.objects.filter(is_test=True, receipt_no=delete.strip().upper()).first()
            if case is None:
                raise CommandError(f"ไม่พบข้อมูลทดลอง {delete}")
            delete_trial(case)
            log(None, AuditAction.EDIT, None, f"ลบข้อมูลทดลอง {delete} จากคำสั่ง add_trial")
            self.stdout.write(f"ลบข้อมูลทดลอง {delete} แล้ว")
        elif name:
            if len(clean_phone(phone)) < 9:
                raise CommandError("ใส่เบอร์โทรอย่างน้อย 9 หลัก")
            existing = Case.objects.filter(is_test=True, victim_name=name.strip(),
                                           victim_phone=clean_phone(phone)).first()
            case = existing or add_trial(name, phone)
            if not existing:
                log(None, AuditAction.EDIT, case, f"เพิ่มข้อมูลทดลอง {case.receipt_no} {name} จากคำสั่ง add_trial")
            state = "มีอยู่แล้ว" if existing else "เพิ่มแล้ว"
            self.stdout.write(f"{state}: {case.receipt_no} {case.victim_name} {case.victim_phone}")
            self.stdout.write("เปิดหน้า 'รายการที่ต้องโทร' จะเห็นกล่องสีม่วง 'ข้อมูลทดลอง' ด้านบน")
        for c in trial_cases():
            self.stdout.write(f"  {c.receipt_no}  {c.victim_name}  {c.victim_phone}")
        if not trial_cases().exists():
            self.stdout.write("ยังไม่มีข้อมูลทดลอง")
