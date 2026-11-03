"""Address box on the call-result page: call staff fix the summons address while on the phone."""
import pytest

from apps.accounts.models import Role, User
from apps.calls.models import CallAssignment
from apps.core.models import AuditLog
from apps.registry.models import AddressOverride, Case

pytestmark = pytest.mark.django_db

ADDRESS = {"addr-recipient_name": "", "addr-house_no": "99/1", "addr-moo": "3", "addr-building": "",
           "addr-soi": "", "addr-road": "", "addr-subdistrict": "สุเทพ", "addr-district": "เมืองเชียงใหม่",
           "addr-province": "เชียงใหม่", "addr-postcode": "", "addr-note": "ผู้เสียหายแจ้งทางโทรศัพท์"}


@pytest.fixture
def caller(client, imported):
    user = User.objects.create_user("caller7", password="pass12345", role=Role.OUTSOURCE)
    client.force_login(user)
    return user


def incomplete_case():
    return next(c for c in Case.objects.not_final().select_related("complainant")
                if c.complainant and c.complainant.address_incomplete)


def test_caller_fixes_address_from_call_page(client, caller):
    case = incomplete_case()
    CallAssignment.objects.create(case=case, assignee=caller)
    assert any("ที่อยู่" in b for b in case.issue_blockers())
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert "ที่อยู่สำหรับออกหมาย" in page and f"/calls/{case.pk}/address/" in page
    assert 'id="id_addr-note"' in page and 'id="id_note"' in page  # no clash with the call form
    assert client.get("/cases/postcode/?subdistrict=สุเทพ&district=เมืองเชียงใหม่&province=เชียงใหม่").json() \
        ["postcode"] == "50200"

    resp = client.post(f"/calls/{case.pk}/address/", ADDRESS)
    assert resp.status_code == 302 and resp["Location"] == f"/calls/{case.pk}/#address"
    override = AddressOverride.objects.get(receipt_no=case.receipt_no)
    assert override.house_no == "99/1" and override.postcode == "50200"  # postcode filled by the system
    assert override.updated_by == caller and override.source_snapshot
    case = Case.objects.get(pk=case.pk)
    assert not any("ที่อยู่" in b for b in case.issue_blockers())
    log = AuditLog.objects.get(action="edit", user=caller)
    assert case.receipt_no in log.detail and "99/1" in log.detail
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert "แก้เองโดย" in page and "99/1" in page


def test_caller_edits_any_case_but_not_from_the_case_page(client, caller):
    case = incomplete_case()  # not assigned to anyone: shared call list
    assert client.post(f"/calls/{case.pk}/address/", ADDRESS).status_code == 302
    assert AddressOverride.objects.get(receipt_no=case.receipt_no).updated_by == caller
    other = Case.objects.not_final().exclude(pk=case.pk).filter(complainant__isnull=False).first()
    # the admin-only endpoint on the case page stays closed to call staff
    client.post(f"/cases/{other.pk}/address/", {k.removeprefix("addr-"): v for k, v in ADDRESS.items()})
    assert not AddressOverride.objects.filter(receipt_no=other.receipt_no).exists()


def test_admin_case_page_still_saves_address(client, imported, admin_user):
    case = incomplete_case()
    client.force_login(admin_user)
    page = client.get(f"/cases/{case.pk}/").content.decode()
    assert 'id="id_house_no"' in page and "postcode_lookup" not in page
    client.post(f"/cases/{case.pk}/address/", {k.removeprefix("addr-"): v for k, v in ADDRESS.items()})
    assert AddressOverride.objects.get(receipt_no=case.receipt_no).postcode == "50200"
