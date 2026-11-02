from pathlib import Path

import pytest
from django.core.files import File

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"


@pytest.fixture(autouse=True)
def _media(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def make_batch(kind, path, user=None, sheets=None):
    from apps.imports.models import ImportBatch
    from apps.imports.services import prepare_batch

    batch = ImportBatch(kind=kind, original_filename=Path(path).name, created_by=user)
    with open(path, "rb") as fh:
        batch.file.save(Path(path).name, File(fh), save=True)
    prepare_batch(batch)
    if sheets is not None:
        batch.sheets = sheets
        batch.save()
    return batch


def run_import(kind, path, user=None, sheets=None):
    from apps.imports.services import commit

    batch = make_batch(kind, path, user, sheets)
    commit(batch)
    batch.refresh_from_db()
    return batch


@pytest.fixture
def imported(db):
    """Both sample files imported (register first, like a typical first run)."""
    from apps.imports.models import ImportKind

    reg = run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    comp = run_import(ImportKind.COMPLAINANTS, SAMPLE / "mock_complainants.xlsx")
    return {"register": reg, "complainants": comp}


@pytest.fixture
def setup_signers(imported):
    """Bind every register name to a signer with complete data; configure กก.2."""
    from apps.registry.models import Investigator, InvestigatorAlias, Unit, normalize_person_key

    unit2 = Unit.objects.get(name="กก.2 บก.สอท.4")
    unit2.appointment_place = "กองกำกับการ ๒ เลขที่ ๙๙ ตำบลช้างเผือก อำเภอเมืองเชียงใหม่ จังหวัดเชียงใหม่ ๕๐๓๐๐"
    unit2.sender_address = "เลขที่ ๙๙ ตำบลช้างเผือก อำเภอเมืองเชียงใหม่ จังหวัดเชียงใหม่ ๕๐๓๐๐"
    unit2.save()
    by_key = {}
    for alias in InvestigatorAlias.objects.all():
        key = normalize_person_key(alias.raw_name.replace("หญิง", ""))
        if key not in by_key:
            by_key[key] = Investigator.objects.create(
                rank_full="พันตำรวจโท", sign_name=alias.raw_name.replace("หญิง", "").strip(),
                position="สว.(สอบสวน) กก.๑ บก.สอท.๔", phone="081 234 5678",
            )
        alias.investigator = by_key[key]
        alias.save()
    from apps.documents.services import ensure_default_templates

    ensure_default_templates()
    return by_key


@pytest.fixture
def admin_user(db):
    from apps.accounts.models import Role, User

    return User.objects.create_user("admin1", password="pass12345", role=Role.ADMIN)
