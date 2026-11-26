"""Accounts of the unit's 12 พนักงานสอบสวน (ccid14_001 ... ccid14_012), each tied to their own cases.

    python manage.py seed_investigators            # also run by init_system at every start (Docker)
    python manage.py seed_investigators --dry-run

Safe to run again and again: each account is found by its seed key (the first username), not by its
current username, so an account renamed or given a new password in ตั้งค่า > ผู้ใช้ is left exactly as it
is. Only accounts that do not exist yet are created, with the starting password; a missing link to the
พนักงานสอบสวน record is filled in (never changed once set).
"""
from __future__ import annotations

import os
import re

from django.core.management.base import BaseCommand
from django.db import transaction

INVESTIGATOR_ACCOUNTS = [
    ("ccid14_001", "พ.ต.ท.หญิงนิติยา เอกวงษา"),
    ("ccid14_002", "พ.ต.ท.ภีรวุฒิ สุวรรณสว่าง"),
    ("ccid14_003", "พ.ต.ต.พงศภัคพล อันท้าว"),
    ("ccid14_004", "พ.ต.ต.จรินทร์ วิริยา"),
    ("ccid14_005", "พ.ต.ต.อำพล ทองอร่าม"),
    ("ccid14_006", "พ.ต.ต.ธนพัฒน์ แก่นดีลัง"),
    ("ccid14_007", "พ.ต.ต.กิตติมศักดิ์ อินทชยาคม"),
    ("ccid14_008", "ร.ต.อ.กฤศทสร อุปะละสกุล"),
    ("ccid14_009", "ร.ต.ท.อำนาจ ธรรมกันทะ"),
    ("ccid14_010", "ร.ต.ท.ชัชวาลย์ ญะวิญชาญ"),
    ("ccid14_011", "ร.ต.ต.ฉัตรณพัฒน์ เรือนโนวา"),
    ("ccid14_012", "ร.ต.ต.เสฐียรพงษ์ มั่นทอง"),
]
# the starting password (change it in ตั้งค่า > ผู้ใช้ after the first login); a server may set its own in .env
DEFAULT_PASSWORD = "ccid14@1441"

_FULL_RANK = {"พ.ต.อ.": "พันตำรวจเอก", "พ.ต.ท.": "พันตำรวจโท", "พ.ต.ต.": "พันตำรวจตรี", "ร.ต.อ.": "ร้อยตำรวจเอก",
              "ร.ต.ท.": "ร้อยตำรวจโท", "ร.ต.ต.": "ร้อยตำรวจตรี", "ด.ต.": "ดาบตำรวจ", "จ.ส.ต.": "จ่าสิบตำรวจ"}
_RANK_RE = re.compile(r"^(" + "|".join(re.escape(r) for r in sorted(_FULL_RANK, key=len, reverse=True)) + r")(หญิง)?\s*")


def split_rank(text: str) -> tuple[str, str, str]:
    """'พ.ต.ท.หญิงนิติยา เอกวงษา' -> ('พ.ต.ท.หญิง', 'พันตำรวจโทหญิง', 'นิติยา เอกวงษา')."""
    m = _RANK_RE.match(text.strip())
    if not m:
        return "", "", text.strip()
    short = m.group(1) + (m.group(2) or "")
    return short, _FULL_RANK[m.group(1)] + (m.group(2) or ""), text[m.end():].strip()


def find_or_make_investigator(rank_full: str, name: str):
    """The พนักงานสอบสวน record of this person: the same one the register's spellings are linked to (spacing
    and a leading หญิง ignored), else a new one with the rank, which later register imports will reuse."""
    from apps.registry.models import Investigator, InvestigatorAlias, person_core_key

    core = person_core_key(name)
    for inv in Investigator.objects.all():
        if person_core_key(inv.sign_name) == core:
            return inv, False
    for alias in InvestigatorAlias.objects.filter(investigator__isnull=False).select_related("investigator"):
        if person_core_key(alias.raw_name) == core:
            return alias.investigator, False
    inv = Investigator.objects.create(rank_full=rank_full, sign_name=name)
    # spellings of this name already in the register (not linked yet) now point to this record
    for alias in InvestigatorAlias.objects.filter(investigator__isnull=True):
        if person_core_key(alias.raw_name) == core:
            alias.investigator = inv
            alias.save(update_fields=["investigator"])
    return inv, True


class Command(BaseCommand):
    help = "สร้างบัญชีพนักงานสอบสวน 12 นาย (ccid14_001-012) ผูกกับคดีของแต่ละคน (รันซ้ำได้ ไม่ทับที่แก้ไว้)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="แสดงว่าจะทำอะไร โดยไม่บันทึก")

    def handle(self, *args, dry_run=False, **options):
        from apps.accounts.models import Role, User
        from apps.core.models import AuditAction, AuditLog

        password = os.environ.get("CCID4_INVESTIGATOR_PASSWORD") or DEFAULT_PASSWORD
        created = linked = kept = 0
        with transaction.atomic():
            for key, label in INVESTIGATOR_ACCOUNTS:
                short, rank_full, name = split_rank(label)
                user = User.objects.filter(seed_key=key).first()
                adopted = False
                if user is None:  # made by hand before, with this username: take it over, change nothing else
                    user = User.objects.filter(username=key, seed_key__isnull=True).first()
                    adopted = user is not None
                if user is not None:
                    kept += 1
                    if not dry_run and adopted:
                        user.seed_key = key
                        user.save(update_fields=["seed_key"])
                    if user.investigator_id is None and not dry_run:
                        linked += self._link(user, rank_full, name)
                    continue
                created += 1
                if dry_run:
                    self.stdout.write(f"จะสร้าง {key}: {label}")
                    continue
                first, _, last = (short + name).rpartition(" ")
                user = User(username=key, role=Role.INVESTIGATOR, first_name=first or short + name, last_name=last
                            if first else "", seed_key=key, is_active=True)
                user.set_password(password)
                user.save()
                linked += self._link(user, rank_full, name)
                AuditLog.objects.create(action=AuditAction.SETTINGS, target_type="User", target_id=str(user.pk),
                                        detail=f"สร้างบัญชีพนักงานสอบสวน {key} ({label}) จากคำสั่ง seed_investigators")
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"บัญชีพนักงานสอบสวน: สร้างใหม่ {created} · มีอยู่แล้ว {kept} (ไม่แตะชื่อผู้ใช้/รหัสผ่าน) "
                          f"· ผูกกับพนักงานสอบสวน {linked}")

    def _link(self, user, rank_full: str, name: str) -> int:
        inv, _ = find_or_make_investigator(rank_full, name)
        other = getattr(inv, "user", None)
        if other is not None and other.pk != user.pk:
            self.stderr.write(f"ข้าม: {inv} ผูกกับบัญชี {other.username} อยู่แล้ว (แก้ได้ที่ ตั้งค่า > ผู้ใช้)")
            return 0
        if not inv.rank_full and rank_full:
            inv.rank_full = rank_full
            inv.save(update_fields=["rank_full"])
        user.investigator = inv
        user.save(update_fields=["investigator"])
        return 1
