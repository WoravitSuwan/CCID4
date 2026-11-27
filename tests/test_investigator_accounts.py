"""The 12 พนักงานสอบสวน accounts (manage.py seed_investigators), editing accounts in ตั้งค่า > ผู้ใช้, and
ใครคดีมัน: an investigator account sees and works on its own cases only, whatever its username becomes."""
import pytest
from django.core.management import call_command

from apps.accounts.management.commands.seed_investigators import (
    DEFAULT_PASSWORD,
    INVESTIGATOR_ACCOUNTS,
    split_rank,
)
from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.registry.models import Case, Investigator, InvestigatorAlias

pytestmark = pytest.mark.django_db


def seed():
    call_command("seed_investigators", stdout=open("/dev/null", "w"))


def test_twelve_accounts_created_linked_and_able_to_log_in(client):
    seed()
    accounts = User.objects.filter(role=Role.INVESTIGATOR).order_by("username")
    assert [u.username for u in accounts] == [f"ccid14_{i:03d}" for i in range(1, 13)]
    first = accounts[0]
    assert first.display_name() == "พ.ต.ท.หญิงนิติยา เอกวงษา" and first.seed_key == "ccid14_001"
    assert (first.investigator.rank_full, first.investigator.sign_name) == ("พันตำรวจโทหญิง", "นิติยา เอกวงษา")
    assert accounts[7].investigator.rank_full == "ร้อยตำรวจเอก" and accounts[11].display_name() == "ร.ต.ต.เสฐียรพงษ์ มั่นทอง"
    assert all(u.investigator_id for u in accounts) and len({u.investigator_id for u in accounts}) == 12
    assert not any(u.is_staff or u.is_superuser for u in accounts)
    assert client.login(username="ccid14_005", password=DEFAULT_PASSWORD)
    assert not client.login(username="ccid14_005", password="wrong-password")
    assert AuditLog.objects.filter(detail__contains="สร้างบัญชีพนักงานสอบสวน ccid14_001").exists()


def test_split_rank():
    assert split_rank("พ.ต.ท.หญิงนิติยา เอกวงษา") == ("พ.ต.ท.หญิง", "พันตำรวจโทหญิง", "นิติยา เอกวงษา")
    assert split_rank("ร.ต.อ.กฤศทสร อุปะละสกุล") == ("ร.ต.อ.", "ร้อยตำรวจเอก", "กฤศทสร อุปะละสกุล")
    assert len(INVESTIGATOR_ACCOUNTS) == 12


def test_running_again_never_resets_a_renamed_account_or_its_password(client, admin_user):
    seed()
    officer = User.objects.get(username="ccid14_001")
    inv = officer.investigator
    client.force_login(admin_user)
    resp = client.post(f"/settings/users/{officer.pk}/", {
        "username": "nitiya.e", "first_name": "พ.ต.ท.หญิงนิติยา", "last_name": "เอกวงษา", "role": Role.INVESTIGATOR,
        "investigator": inv.pk, "is_active": "on", "password": "Kk1-new-pass-2569", "password2": "Kk1-new-pass-2569",
        "position": "สว.(สอบสวน) กก.๑ บก.สอท.๔"})
    assert resp.status_code == 302
    seed()
    seed()
    assert User.objects.filter(role=Role.INVESTIGATOR).count() == 12  # no new ccid14_001 made
    officer.refresh_from_db()
    assert officer.username == "nitiya.e" and officer.investigator == inv and officer.seed_key == "ccid14_001"
    assert officer.check_password("Kk1-new-pass-2569") and not officer.check_password(DEFAULT_PASSWORD)
    inv.refresh_from_db()
    assert inv.position == "สว.(สอบสวน) กก.๑ บก.สอท.๔"
    client.logout()
    assert client.login(username="nitiya.e", password="Kk1-new-pass-2569")
    assert not client.login(username="ccid14_001", password=DEFAULT_PASSWORD)
    details = list(AuditLog.objects.values_list("detail", flat=True))
    assert "เปลี่ยนชื่อผู้ใช้ ccid14_001 → nitiya.e" in details and "เปลี่ยนรหัสผ่านของ nitiya.e" in details
    assert not any("Kk1-new-pass-2569" in d for d in details)  # the password itself is never written down


def test_an_account_made_by_hand_is_taken_over_not_reset():
    hand = User.objects.create_user("ccid14_003", password="Own-pass-9988", role=Role.INVESTIGATOR)
    seed()
    hand.refresh_from_db()
    assert hand.seed_key == "ccid14_003" and hand.check_password("Own-pass-9988")
    assert hand.investigator.sign_name == "พงศภัคพล อันท้าว"
    assert User.objects.filter(role=Role.INVESTIGATOR).count() == 12


def test_linked_to_the_investigator_the_register_already_has(imported):
    """The register's spelling (with หญิง and odd spacing) is the same person: no second record."""
    alias = InvestigatorAlias.objects.filter(investigator__isnull=False).first()
    inv = alias.investigator
    Investigator.objects.filter(pk=inv.pk).update(sign_name="นิติยา  เอกวงษา")
    InvestigatorAlias.objects.filter(pk=alias.pk).update(raw_name="หญิงนิติยา เอกวงษา")
    before = Investigator.objects.count()
    seed()
    assert User.objects.get(username="ccid14_001").investigator_id == inv.pk
    assert Investigator.objects.count() == before + 11  # the other 11 are not in the sample register


def test_settings_form_checks(client, admin_user):
    seed()
    officer = User.objects.get(username="ccid14_002")
    client.force_login(admin_user)
    base = {"username": "ccid14_002", "first_name": "x", "last_name": "y", "role": Role.INVESTIGATOR,
            "investigator": officer.investigator_id, "is_active": "on"}
    for change, message in [
        ({"username": "CCID14_003"}, "ชื่อผู้ใช้นี้มีอยู่แล้ว"),
        ({"username": "  "}, ""),  # Django's own "required" message
        ({"password": "Abcd-1234-x", "password2": "Abcd-1234-y"}, "รหัสผ่านทั้งสองช่องไม่ตรงกัน"),
        ({"password": "password", "password2": "password"}, "รหัสผ่านนี้เดาง่ายเกินไป"),
        ({"password": "Ab1-x", "password2": "Ab1-x"}, "รหัสผ่านสั้นเกินไป"),
        ({"investigator": ""}, "บัญชีพนักงานสอบสวนต้องผูกกับพนักงานสอบสวน"),
    ]:
        resp = client.post(f"/settings/users/{officer.pk}/", {**base, **change})
        assert resp.status_code == 200 and resp.context["form"].errors, change  # not saved
        assert message in resp.content.decode(), (change, message)
    officer.refresh_from_db()
    assert officer.username == "ccid14_002" and officer.check_password(DEFAULT_PASSWORD)
    other = User.objects.get(username="ccid14_003")
    taken = client.get(f"/settings/users/{officer.pk}/").content.decode()
    assert f'value="{other.investigator_id}"' not in taken  # one investigator, one account
    # switching the account off is logged, and it can no longer log in
    client.post(f"/settings/users/{officer.pk}/", {k: v for k, v in base.items() if k != "is_active"})
    officer.refresh_from_db()
    assert not officer.is_active and AuditLog.objects.filter(detail="ปิดใช้งานบัญชี ccid14_002").exists()
    client.logout()
    assert not client.login(username="ccid14_002", password=DEFAULT_PASSWORD)


def test_changing_my_own_password_keeps_me_logged_in(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(f"/settings/users/{admin_user.pk}/", {
        "username": admin_user.username, "first_name": "", "last_name": "", "role": Role.ADMIN, "is_active": "on",
        "password": "Admin-new-77xx", "password2": "Admin-new-77xx"})
    assert resp.status_code == 302 and client.get("/settings/users/").status_code == 200


def test_users_page_lists_the_links(client, admin_user):
    seed()
    client.force_login(admin_user)
    html = client.get("/settings/users/").content.decode()
    assert "ccid14_012" in html and "พันตำรวจตรี จรินทร์ วิริยา" in html and "พนักงานสอบสวน (เฉพาะคดีของตนเอง)" in html


# --------------------------------------------------------------------------- ใครคดีมัน
@pytest.fixture
def two_cases(imported):
    """Two open cases: one belongs to ccid14_004 (จรินทร์ วิริยา), the other to someone else."""
    seed()
    officer = User.objects.get(username="ccid14_004")
    mine, theirs = list(Case.objects.not_final().filter(investigator_alias__isnull=False)[:2])
    alias = InvestigatorAlias.objects.create(raw_name="จรินทร์ วิริยา", key="จรินทร์วิริยา",
                                             investigator=officer.investigator)
    Case.objects.filter(pk=mine.pk).update(investigator_alias=alias, investigator_raw="จรินทร์ วิริยา")
    return officer, Case.objects.get(pk=mine.pk), theirs


def test_an_investigator_sees_and_works_on_only_their_cases(client, two_cases):
    officer, mine, theirs = two_cases
    client.force_login(officer)
    queue = client.get("/calls/?tab=all")
    receipts = {a.case.receipt_no for a in queue.context["page"]}
    assert receipts == {mine.receipt_no} and "คดีของฉัน" in queue.content.decode()
    assert client.get(f"/calls/{mine.pk}/").status_code == 200
    assert client.get(f"/calls/{theirs.pk}/").status_code == 403
    assert client.get(f"/cases/{theirs.pk}/issue/").status_code == 403
    assert client.get(f"/cases/{mine.pk}/issue/").status_code == 302
    # the reports and statistics list everyone's cases: not for investigators
    for url in ("/stats/", "/reports/daily/", "/reports/status-final/", "/cases/", "/settings/"):
        assert client.get(url)["Location"] == "/calls/", url
    assert client.get("/imports/dopa/").status_code == 200


def test_renaming_the_account_keeps_its_cases(client, admin_user, two_cases):
    officer, mine, _ = two_cases
    client.force_login(admin_user)
    client.post(f"/settings/users/{officer.pk}/", {
        "username": "jarin.v", "first_name": officer.first_name, "last_name": officer.last_name,
        "role": Role.INVESTIGATOR, "investigator": officer.investigator_id, "is_active": "on"})
    officer.refresh_from_db()
    assert officer.username == "jarin.v"
    client.force_login(officer)
    assert {a.case_id for a in client.get("/calls/?tab=all").context["page"]} == {mine.pk}


def test_the_excel_update_touches_only_their_own_cases(tmp_path, two_cases):
    from apps.imports.models import ImportKind
    from apps.imports.services import commit
    from tests.conftest import make_batch
    from tests.test_weekly_update import grid_file, row

    officer, mine, theirs = two_cases
    batch = make_batch(ImportKind.UPDATE, grid_file(tmp_path, [
        row(mine, **{"มูลค่าความเสียหาย": 1234.5}), row(theirs, **{"มูลค่าความเสียหาย": 9999}),
        row(receipt="L6899999999", **{"หน่วยที่รับผิดชอบ": "กก.1 บก.สอท.4 บช.สอท."})]), user=officer)
    commit(batch)
    batch.refresh_from_db()
    assert batch.summary["not_yours"] == 2  # someone else's case, and a new case: left alone
    assert Case.objects.get(pk=mine.pk).damage_amount == 1234.5
    assert Case.objects.get(pk=theirs.pk).damage_amount != 9999
    assert not Case.objects.filter(receipt_no="L6899999999").exists()


def test_an_investigator_account_not_linked_sees_nothing(client, imported):
    lone = User.objects.create_user("lone_inv", password="pass12345x", role=Role.INVESTIGATOR)
    client.force_login(lone)
    resp = client.get("/calls/?tab=all")
    assert list(resp.context["page"]) == [] and "ยังไม่ได้ผูกกับพนักงานสอบสวน" in resp.content.decode()
    case = Case.objects.first()
    assert client.get(f"/calls/{case.pk}/").status_code == 403


def test_summonses_of_other_investigators_are_hidden(client, setup_signers, two_cases):
    from django.utils import timezone

    from apps.documents.models import DocTemplate, Summons, TemplateKind

    officer, mine, theirs = two_cases
    s = Summons.objects.create(case=theirs, number=1, issue_date=timezone.localdate(), appointment_at=timezone.now(),
                               issuer=Investigator.objects.exclude(pk=officer.investigator_id).first(),
                               template=DocTemplate.active(TemplateKind.SUMMONS), template_version=1)
    client.force_login(officer)
    assert s not in client.get("/summons/").context["page"].object_list
    assert client.get(f"/summons/{s.pk}/docx/").status_code == 403


def test_my_cases_split_by_address_for_the_summons(client, two_cases):
    """คดีของฉัน: the same tabs and filters as call staff, plus ที่อยู่ไม่ครบ / ที่อยู่ครบ."""
    from apps.registry.models import AddressOverride

    officer, mine, theirs = two_cases
    second = Case.objects.not_final().exclude(pk__in=[mine.pk, theirs.pk]).filter(complainant__isnull=False).first()
    Case.objects.filter(pk=second.pk).update(investigator_alias=mine.investigator_alias)
    AddressOverride.objects.create(receipt_no=mine.receipt_no, house_no="", subdistrict="", district="",
                                   province="เชียงใหม่")  # typed in, but not enough to send a summons
    AddressOverride.objects.create(receipt_no=second.receipt_no, house_no="9", subdistrict="ศรีภูมิ",
                                   district="เมืองเชียงใหม่", province="เชียงใหม่")
    client.force_login(officer)
    page = client.get("/calls/?tab=all")
    assert [(v, n) for v, _label, n in page.context["addr_buttons"]] == [("", 2), ("missing", 1), ("complete", 1)]
    html = page.content.decode()
    assert "ที่อยู่สำหรับออกหมาย" in html and html.count("ที่อยู่ไม่ครบ</a>") == 1  # the badge on that row
    missing = client.get("/calls/?tab=all&addr=missing")
    assert [a.case_id for a in missing.context["page"]] == [mine.pk]
    chip = dict(missing.context["f"]["chips"])["ที่อยู่ไม่ครบ"]  # shown as a filter chip that takes it off
    assert "addr=" not in chip and "tab=all" in chip
    assert [a.case_id for a in client.get("/calls/?tab=all&addr=complete").context["page"]] == [second.pk]
    # with a funnel tab too: never called + address missing
    assert [a.case_id for a in client.get("/calls/?tab=never&addr=missing").context["page"]] == [mine.pk]
    assert client.get("/calls/?addr=nonsense").status_code == 200  # an unknown value is ignored
