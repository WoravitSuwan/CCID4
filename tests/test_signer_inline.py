import pytest

from apps.accounts.models import Role, User
from apps.registry.models import Case, Investigator, RememberedValue

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, db):
    from apps.documents.services import ensure_default_templates

    ensure_default_templates()
    client.force_login(User.objects.create_user("adm3", password="pass12345", role=Role.ADMIN))
    return client


def test_signer_completed_on_issue_page_and_remembered(admin_client, imported):
    case = Case.objects.not_final().first()
    inv = case.investigator
    assert inv.missing_signer_fields() == ["ยศ", "ตำแหน่ง", "โทรศัพท์"]
    admin_client.post("/summons/issue/start/", {"case_ids": [case.pk]})
    page = admin_client.get("/summons/issue/")
    entries = page.context["signer_forms"][inv]
    assert entries[0]["attention"] and entries[0]["form"]["rank_full"].field.widget.attrs["form"]
    html = page.content.decode()
    assert 'id="dl-rank"' in html and "พันตำรวจโท" in html and 'id="dl-position"' in html
    prefix = f"i{inv.pk}"
    resp = admin_client.post("/summons/issue/signer/", {
        "target": f"inv:{inv.pk}", f"{prefix}-rank_full": "พันตำรวจเอก (พิเศษ)", f"{prefix}-sign_name": inv.sign_name,
        f"{prefix}-position": "ผกก.กก.๙ บก.สอท.๔ บช.สอท. (ตำแหน่งใหม่)", f"{prefix}-phone": "๐๘๑ ๑๑๑ ๑๑๑๑"})
    assert resp["Location"] == "/summons/issue/"
    inv.refresh_from_db()
    assert inv.missing_signer_fields() == []
    assert not any("ไม่ครบ" in b for b in Case.objects.get(pk=case.pk).issue_blockers())
    # typed values are offered next time (remembered first in the list)
    assert RememberedValue.suggestions("position")[0] == "ผกก.กก.๙ บก.สอท.๔ บช.สอท. (ตำแหน่งใหม่)"
    assert "พันตำรวจเอก (พิเศษ)" in RememberedValue.suggestions("rank")


def test_issue_options_kept_after_saving_signer(admin_client, imported):
    case = Case.objects.not_final().first()
    admin_client.post("/summons/issue/start/", {"case_ids": [case.pk]})
    form = admin_client.get("/summons/issue/").context["form"]
    data = {"issue_date": "2026-02-09", "appointment_date": "2026-03-02", "start_time": "13:30",
            "reason": "เหตุทดสอบ", "template": form.fields["template"].queryset.first().pk, "thai_digits": "on",
            "action": "refresh"}
    admin_client.post("/summons/issue/", data)
    again = admin_client.get("/summons/issue/").context["form"]
    assert again["appointment_date"].value() == "2026-03-02" and again["start_time"].value() == "13:30"
    assert again["reason"].value() == "เหตุทดสอบ"


def test_female_rank_suggestions(admin_client, imported):
    suda = Investigator.objects.get(sign_name="สุดา รักษ์ทดสอบ")
    case = Case.objects.filter(investigator_alias__investigator=suda).first()
    admin_client.post("/summons/issue/start/", {"case_ids": [case.pk]})
    entry = admin_client.get("/summons/issue/").context["signer_forms"][suda][0]
    assert entry["form"]["rank_full"].field.widget.attrs["list"] == "dl-rank-f"
    from apps.core.templatetags.ui import signer_datalists

    assert signer_datalists()["ranks_female"][0].endswith("หญิง")
