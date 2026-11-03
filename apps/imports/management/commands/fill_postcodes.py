"""Fill missing postcodes from ตำบล + อำเภอ + จังหวัด for data already in the database.

    python manage.py fill_postcodes

Complainants keep the value from the file in ``postcode``; the filled value goes to
``postcode_auto``. Staff-entered addresses (AddressOverride) get ``postcode``.
Uses the bundled postcode table only - no outside service is contacted.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.imports.postcode import lookup_postcode
from apps.registry.models import AddressOverride, Complainant


class Command(BaseCommand):
    help = "เติมรหัสไปรษณีย์ที่ว่างจาก ตำบล/อำเภอ/จังหวัด"

    def handle(self, *args, **options):
        changed = []
        for c in Complainant.objects.filter(postcode="").filter(Q(postcode_auto="") | Q(postcode_auto__isnull=True)):
            code = lookup_postcode(c.subdistrict, c.district, c.province)
            if code:
                c.postcode_auto = code
                changed.append(c)
        Complainant.objects.bulk_update(changed, ["postcode_auto"], batch_size=1000)
        overrides = 0
        for o in AddressOverride.objects.filter(postcode=""):
            before = o.postcode
            o.save()  # save() fills a blank postcode
            overrides += o.postcode != before
        still = Complainant.objects.filter(postcode="", postcode_auto="").count()
        self.stdout.write(f"เติมรหัสไปรษณีย์ผู้แจ้ง {len(changed)} ราย, ที่อยู่ที่แก้เอง {overrides} รายการ "
                          f"(ยังหาไม่ได้ {still} ราย ส่วนใหญ่เพราะไม่มีตำบล/อำเภอ/จังหวัด หรือสะกดผิด)")
