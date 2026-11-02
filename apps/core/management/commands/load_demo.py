"""Load the fictional sample files and demo users so the system can be tried out.

    python manage.py load_demo

Never run this on the production database: it creates demo accounts.
"""
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

SAMPLE = Path(settings.BASE_DIR) / "sample_data"
DEMO_PASSWORD = "demo12345"


class Command(BaseCommand):
    help = "โหลดข้อมูลตัวอย่าง (สมมติ) และผู้ใช้ทดลอง"

    def handle(self, *args, **options):
        from apps.accounts.models import Role, User
        from apps.documents.services import ensure_default_templates
        from apps.imports.models import ImportBatch, ImportKind
        from apps.imports.services import commit, prepare_batch
        from apps.registry.models import Investigator, InvestigatorAlias, Unit

        ensure_default_templates()
        for kind, name in ((ImportKind.REGISTER, "mock_case_register.xlsx"),
                           (ImportKind.COMPLAINANTS, "mock_complainants.xlsx")):
            batch = ImportBatch(kind=kind, original_filename=name)
            with open(SAMPLE / name, "rb") as fh:
                batch.file.save(name, File(fh), save=True)
            prepare_batch(batch)
            commit(batch)
            self.stdout.write(f"นำเข้า {name}: {batch.summary['new']} ใหม่, {batch.summary['updated']} อัปเดต")

        unit2 = Unit.objects.filter(name="กก.2 บก.สอท.4").first()
        if unit2 and not unit2.appointment_place:
            unit2.appointment_place = "กองกำกับการ ๒ (ที่อยู่สมมติ) ตำบลช้างเผือก อำเภอเมืองเชียงใหม่ จังหวัดเชียงใหม่ ๕๐๓๐๐"
            unit2.sender_address = "(ที่อยู่สมมติ) ตำบลช้างเผือก อำเภอเมืองเชียงใหม่ จังหวัดเชียงใหม่ ๕๐๓๐๐"
            unit2.save()
        demo_signers = {
            "ศักดิ์ชัยใจดีสมมติ": ("พันตำรวจโท", "ศักดิ์ชัย ใจดีสมมติ", "สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.", "๐๘๑ ๐๐๐ ๐๐๐๑"),
            "สุดารักษ์ทดสอบ": ("พันตำรวจตรีหญิง", "สุดา รักษ์ทดสอบ", "รอง สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.", "๐๘๑ ๐๐๐ ๐๐๐๒"),
        }
        for key, (rank, name, position, phone) in demo_signers.items():
            inv, _ = Investigator.objects.get_or_create(sign_name=name, defaults={
                "rank_full": rank, "position": position, "phone": phone})
            for alias in InvestigatorAlias.objects.all():
                if alias.key.replace("หญิง", "") == key:
                    alias.investigator = inv
                    alias.save()
        for username, role in (("admin", Role.ADMIN), ("head", Role.HEAD), ("clerk", Role.CLERK)):
            if not User.objects.filter(username=username).exists():
                User.objects.create_user(username, password=DEMO_PASSWORD, role=role, first_name=f"ทดลอง-{username}",
                                         is_superuser=role == Role.ADMIN, is_staff=role == Role.ADMIN)
        inv = Investigator.objects.filter(sign_name="ศักดิ์ชัย ใจดีสมมติ").first()
        if inv and not User.objects.filter(username="inv").exists():
            User.objects.create_user("inv", password=DEMO_PASSWORD, role=Role.INVESTIGATOR, investigator=inv,
                                     first_name="ศักดิ์ชัย")
        self.stdout.write(self.style.SUCCESS(
            f"พร้อมแล้ว ผู้ใช้ทดลอง admin / head / clerk / inv รหัสผ่าน {DEMO_PASSWORD} "
            "(ชื่อ 'ธนพล มั่นคงสมมติ' ปล่อยไว้ยังไม่ผูก เพื่อลองหน้าผูกชื่อ)"))
