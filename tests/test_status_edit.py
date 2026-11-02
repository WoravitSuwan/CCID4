import pytest

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.imports.models import ImportKind
from apps.registry.models import Case, Status

from .conftest import SAMPLE, run_import

pytestmark = pytest.mark.django_db


def login(client, role, **kw):
    user = User.objects.create_user(f"s_{role}", password="pass12345", role=role, **kw)
    client.force_login(user)
    return user


def test_change_status_on_case_page(client, setup_signers):
    login(client, Role.CLERK)
    case = Case.objects.not_final().first()
    final = Status.objects.get(name="สิ้นสุด")
    resp = client.post(f"/cases/{case.pk}/status/", {"status": final.pk, "note": "ผู้เสียหายถอนแจ้งความ"})
    assert resp.status_code == 302
    case.refresh_from_db()
    assert case.status == final and case.status_manual_at is not None
    assert Case.objects.not_final().count() == 31
    log = AuditLog.objects.get(action="edit", target_id=str(case.pk))
    assert "สิ้นสุด" in log.detail and "ถอนแจ้งความ" in log.detail


def test_bulk_status_change(client, setup_signers):
    login(client, Role.HEAD)
    ids = list(Case.objects.not_final().values_list("pk", flat=True)[:3])
    status = Status.objects.get(name="ส่งสำนวนอัยการ")
    client.post("/cases/status/bulk/", {"status": status.pk, "case_ids": ids})
    assert Case.objects.filter(pk__in=ids, status=status).count() == 3


def test_investigator_cannot_change_other_cases(client, setup_signers):
    case = Case.objects.exclude(investigator_alias__isnull=True).first()
    other = Case.objects.exclude(investigator_alias__investigator=case.investigator).first()
    login(client, Role.INVESTIGATOR, investigator=case.investigator)
    final = Status.objects.get(name="สิ้นสุด")
    assert client.post(f"/cases/{other.pk}/status/", {"status": final.pk}).status_code == 403


def test_manual_status_survives_reimport_of_same_register(setup_signers):
    case = Case.objects.not_final().first()
    file_status = case.status.name
    final = Status.objects.get(name="สิ้นสุด")
    case.set_status(final)
    batch = run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    case.refresh_from_db()
    assert case.status == final  # register still says the old value -> keep staff's status
    assert case.status_file_value == file_status
    assert batch.summary["manual_status_kept"] == 1
    assert batch.updated_count == 0


def test_newer_register_status_replaces_manual(setup_signers):
    """If the register itself changes later, the file wins (newer information)."""
    case = Case.objects.not_final().first()
    case.set_status(Status.objects.get(name="สิ้นสุด"))
    case.refresh_from_db()
    case.status_file_value = "ค่าเก่าที่ไม่มีในไฟล์แล้ว"  # simulate: file changed since the manual edit
    case.save()
    run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    case.refresh_from_db()
    assert case.status.name != "สิ้นสุด"
    assert case.status_manual_at is None


def test_demo_bootstrap(db, settings):
    from apps.core.demo import bootstrap_demo

    settings.DEMO_MODE = True
    bootstrap_demo()
    assert Case.objects.count() == 40
    assert User.objects.filter(username__in=["admin", "head", "clerk", "inv"]).count() == 4
    assert User.objects.get(username="admin").check_password("demo12345")
    bootstrap_demo()  # second call is a no-op
    assert Case.objects.count() == 40


def test_login_page_shows_demo_accounts(client, settings):
    settings.DEMO_MODE = True
    page = client.get("/accounts/login/").content.decode()
    assert "โหมดทดลอง" in page and "demo12345" in page
    settings.DEMO_MODE = False
    assert "demo12345" not in client.get("/accounts/login/").content.decode()
