import pytest

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.documents.models import ExportJob, Summons
from apps.registry.models import AddressOverride, Case

from .conftest import SAMPLE

pytestmark = pytest.mark.django_db


def login(client, role, **kw):
    user = User.objects.create_user(f"u_{role}", password="pass12345", role=role, **kw)
    client.force_login(user)
    return user


def test_login_required(client):
    resp = client.get("/")
    assert resp.status_code == 302 and "/accounts/login/" in resp["Location"]
    assert client.get("/accounts/login/").status_code == 200


def test_all_pages_render_for_admin(client, setup_signers):
    login(client, Role.ADMIN)
    case = Case.objects.first()
    urls = ["/", "/cases/", "/cases/?open=on&incomplete=on", "/cases/?unmatched=on&never=on&conflict=on",
            f"/cases/{case.pk}/", "/imports/", "/summons/", "/exports/", "/exports/new/",
            "/settings/investigators/", "/settings/units/", "/settings/statuses/", "/settings/templates/",
            "/settings/users/", "/settings/users/new/", "/settings/system/", "/settings/audit/",
            "/settings/investigators/new/", "/accounts/password/"]
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 200, url


def test_role_permissions(client, setup_signers):
    """Only two roles: admin uses everything, outsource only the call pages."""
    login(client, Role.ADMIN)
    for url in ("/imports/", "/settings/users/", "/cases/", "/calls/", "/calls/assign/"):
        assert client.get(url).status_code == 200, url
    client.logout()
    login(client, Role.OUTSOURCE)
    for url in ("/imports/", "/settings/users/", "/cases/", "/summons/", "/exports/new/"):
        resp = client.get(url)
        assert resp.status_code == 302 and resp["Location"] == "/calls/", url
    assert client.get("/calls/").status_code == 200
    assert client.get("/calls/assign/").status_code == 403


def test_only_two_roles_offered(client, db):
    login(client, Role.ADMIN)
    form = client.get("/settings/users/new/").context["form"]
    assert [v for v, _ in form.fields["role"].choices if v] == ["admin", "outsource"]


def test_admin_cannot_demote_self(client, db):
    me = login(client, Role.ADMIN)
    resp = client.post(f"/settings/users/{me.pk}/", {"username": me.username, "first_name": "", "last_name": "",
                                                     "role": "outsource", "is_active": "on", "password": ""})
    assert resp.status_code == 200 and "role" in resp.context["form"].errors
    me.refresh_from_db()
    assert me.role == "admin"


def test_dashboard_numbers(client, setup_signers):
    login(client, Role.ADMIN)
    resp = client.get("/")
    stats = resp.context["stats"]
    assert stats["total"] == 40 and stats["open"] == 32 and stats["incomplete"] == 6 and stats["unmatched"] == 0
    assert stats["never"] == 32


def test_case_filters(client, setup_signers):
    login(client, Role.ADMIN)
    assert client.get("/cases/?open=on").context["total"] == 32
    assert client.get("/cases/?open=on&incomplete=on").context["total"] == 6
    assert client.get("/cases/?unmatched=on").context["total"] == 0


def test_register_excel_export(client, setup_signers):
    from io import BytesIO

    from openpyxl import load_workbook

    login(client, Role.ADMIN)
    resp = client.get("/cases/export.xlsx")
    assert resp.status_code == 200
    wb = load_workbook(BytesIO(resp.content))
    assert sorted(wb.sheetnames) == ["ปี 2568 (2025)", "ปี 2569 (2026)"]
    assert wb["ปี 2569 (2026)"].max_row == 26
    assert [c.value for c in wb["ปี 2569 (2026)"][1]][:3] == ["ลำดับ", "เลขรับแจ้ง", "เลขคดีอาญา"]
    assert AuditLog.objects.filter(action="export").exists()


def test_reveal_id_is_logged(client, setup_signers):
    login(client, Role.ADMIN)
    case = Case.objects.exclude(complainant__national_id="").first()
    page = client.get(f"/cases/{case.pk}/").content.decode()
    assert case.complainant.national_id not in page  # masked
    resp = client.post(f"/cases/{case.pk}/reveal-id/")
    assert resp.status_code == 200 and case.complainant.national_id[-3:] in resp.content.decode().replace("-", "")
    assert AuditLog.objects.filter(action="view_id", target_id=str(case.pk)).count() == 1


def test_outsource_cannot_reveal_id(client, setup_signers):
    login(client, Role.OUTSOURCE)
    case = Case.objects.exclude(complainant__national_id="").first()
    resp = client.post(f"/cases/{case.pk}/reveal-id/")
    assert resp.status_code == 302 and resp["Location"] == "/calls/"
    assert not AuditLog.objects.filter(action="view_id").exists()


def test_address_override_and_conflict(client, setup_signers):
    login(client, Role.ADMIN)
    case = Case.objects.not_final().address_incomplete().first()
    data = {"house_no": "55/1", "moo": "", "building": "", "soi": "", "road": "", "subdistrict": "สุเทพ",
            "district": "เมืองเชียงใหม่", "province": "เชียงใหม่", "postcode": "50200", "recipient_name": "", "note": ""}
    client.post(f"/cases/{case.pk}/address/", data)
    case = Case.objects.get(pk=case.pk)
    assert case.issue_blockers() == []
    assert not case.address_conflict()
    # a new complainant file changes the address -> warning, override kept
    c = case.complainant
    c.house_no = "77"
    c.save()
    case = Case.objects.get(pk=case.pk)
    assert case.address_conflict()
    assert case.effective_address().house_no == "55/1"
    assert "ที่อยู่ในไฟล์ผู้แจ้งเปลี่ยนไป" in client.get(f"/cases/{case.pk}/").content.decode()
    client.post(f"/cases/{case.pk}/address/resolve/", {"choice": "file"})
    assert not AddressOverride.objects.filter(receipt_no=case.receipt_no).exists()


def test_import_wizard(client, db):
    login(client, Role.ADMIN)
    with open(SAMPLE / "mock_case_register.xlsx", "rb") as fh:
        resp = client.post("/imports/", {"kind": "register", "file": fh})
    batch_url = resp["Location"]
    assert "/sheets/" in batch_url
    pk = int(batch_url.strip("/").split("/")[1])
    resp = client.post(f"/imports/{pk}/sheets/", {"sheets": ["ปี 2568 (2025)", "ปี 2569 (2026)"]})
    assert resp.status_code == 302
    page = client.get(f"/imports/{pk}/mapping/")
    data = {f"map_{r['field']}": r["selected"] for r in page.context["rows"]}
    resp = client.post(f"/imports/{pk}/mapping/", data)
    assert resp.status_code == 302
    preview = client.get(f"/imports/{pk}/preview/")
    assert preview.context["batch"].new_count == 40
    assert Case.objects.count() == 0  # nothing saved before confirm
    client.post(f"/imports/{pk}/confirm/")
    assert Case.objects.count() == 40
    assert client.get(f"/imports/{pk}/issues.xlsx").status_code == 200
    assert AuditLog.objects.filter(action="import").exists()


def test_issue_flow_via_web(client, setup_signers):
    login(client, Role.ADMIN)
    ids = [c.pk for c in Case.objects.not_final()[:5]]
    client.post("/summons/issue/start/", {"case_ids": ids})
    page = client.get("/summons/issue/")
    assert page.status_code == 200
    plan = page.context["plan"]
    ok = len(plan.ok_items)
    form = page.context["form"]
    data = {"issue_date": "2026-02-09", "appointment_date": "2026-02-23", "start_time": "09:00",
            "reason": "มาให้การต่อพนักงานสอบสวนตามประเด็นที่เกี่ยวข้องกับคดี",
            "template": form.fields["template"].queryset.first().pk, "thai_digits": "on"}
    preview = client.post("/summons/issue/", {**data, "action": "preview"})
    assert preview["Content-Type"] == "application/pdf" and preview.content[:4] == b"%PDF"
    resp = client.post("/summons/issue/", {**data, "action": "confirm"})
    assert resp.status_code == 302 and resp["Location"] == "/exports/new/"
    assert Summons.objects.count() == ok
    resp = client.post("/exports/new/", {"format": "pdf", "mode": "merged", "include_summons": "on"})
    job = ExportJob.objects.get()
    assert resp["Location"] == f"/exports/{job.pk}/"
    assert client.get(f"/exports/{job.pk}/download/").status_code == 200
    s = Summons.objects.first()
    assert client.get(f"/summons/{s.pk}/docx/").status_code == 200
    assert AuditLog.objects.filter(action="download").count() >= 2


def test_outsource_cannot_issue(client, setup_signers):
    case = Case.objects.not_final().first()
    login(client, Role.OUTSOURCE)
    assert client.get(f"/cases/{case.pk}/issue/")["Location"] == "/calls/"
    assert client.post("/summons/issue/start/", {"case_ids": [case.pk]})["Location"] == "/calls/"


def test_register_names_become_signers_automatically(client, imported):
    """Like the real register: names differing only by spacing or a leading 'หญิง' are one person."""
    from apps.registry.models import Investigator, InvestigatorAlias

    assert InvestigatorAlias.objects.count() == 4  # 5 spellings in the file, 2 differ only by spaces
    assert not InvestigatorAlias.objects.filter(investigator__isnull=True).exists()
    assert Investigator.objects.count() == 3  # 'หญิง สุดา รักษ์ทดสอบ' and 'สุดา  รักษ์ทดสอบ' are the same signer
    sudas = InvestigatorAlias.objects.filter(raw_name__contains="สุดา")
    assert sudas.count() == 2 and len({a.investigator_id for a in sudas}) == 1
    assert sudas.first().investigator.sign_name == "สุดา รักษ์ทดสอบ" and sudas.first().investigator.is_female
    login(client, Role.ADMIN)
    assert client.get("/settings/investigators/").context["unlinked"] == []
