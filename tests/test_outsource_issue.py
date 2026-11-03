import pytest

from apps.accounts.models import Role, User
from apps.calls.models import CallAssignment
from apps.core.models import AuditLog
from apps.documents.models import ExportJob, Summons
from apps.registry.models import Case, Investigator

pytestmark = pytest.mark.django_db


@pytest.fixture
def caller(client, imported):
    from apps.documents.services import ensure_default_templates

    ensure_default_templates()
    user = User.objects.create_user("caller9", password="pass12345", role=Role.OUTSOURCE)
    mine = [c for c in Case.objects.not_final().select_related("complainant") if not c.complainant.address_incomplete]
    for case in mine[:3]:
        CallAssignment.objects.create(case=case, assignee=user)
    client.force_login(user)
    return user


def form_data(client):
    form = client.get("/summons/issue/").context["form"]
    return {"issue_date": "2026-02-09", "appointment_date": "2026-02-23", "start_time": "09:00",
            "reason": "มาให้การต่อพนักงานสอบสวนตามประเด็นที่เกี่ยวข้องกับคดี",
            "template": form.fields["template"].queryset.first().pk, "thai_digits": "on"}


def test_caller_issues_with_chosen_rank_and_position(client, caller):
    case = CallAssignment.objects.filter(assignee=caller).first().case
    inv = case.investigator
    assert inv.missing_signer_fields()  # the register has no rank/position
    # from the call screen
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert f"/cases/{case.pk}/issue/" in page
    assert client.get(f"/cases/{case.pk}/issue/")["Location"] == "/summons/issue/"
    issue_page = client.get("/summons/issue/")
    html = issue_page.content.decode()
    assert 'id="dl-rank"' in html and f"/calls/{case.pk}/" in html  # receipt links go to the call page
    prefix = f"i{inv.pk}"
    client.post("/summons/issue/signer/", {
        "target": f"inv:{inv.pk}", f"{prefix}-rank_full": "พันตำรวจโท", f"{prefix}-sign_name": inv.sign_name,
        f"{prefix}-position": "สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.", f"{prefix}-phone": "0811111111"})
    inv.refresh_from_db()
    assert inv.rank_full == "พันตำรวจโท"
    resp = client.post("/summons/issue/", {**form_data(client), "action": "confirm"})
    assert resp["Location"] == "/exports/new/"
    summons = Summons.objects.get(case=case)
    assert summons.created_by == caller and summons.issuer == inv  # signer = case investigator
    assert AuditLog.objects.filter(action="issue", user=caller).exists()
    assert client.get(f"/summons/{summons.pk}/docx/").status_code == 200
    client.post("/exports/new/", {"format": "docx", "mode": "merged", "include_summons": "on",
                                  "include_envelope": "on"})
    job = ExportJob.objects.get(created_by=caller)
    assert client.get(f"/exports/{job.pk}/download/").status_code == 200
    assert [s.pk for s in client.get("/summons/").context["page"]] == [summons.pk]


def test_caller_bulk_issue_from_queue_only_own_cases(client, caller):
    own = list(CallAssignment.objects.filter(assignee=caller).values_list("case_id", flat=True))
    other = Case.objects.not_final().exclude(pk__in=own).first()
    for inv in Investigator.objects.all():
        inv.rank_full, inv.position, inv.phone = "พันตำรวจโท", "สว.", "081"
        inv.save()
    queue = client.get("/calls/").content.decode()
    assert 'name="case_ids"' in queue and "ออกหมายเรียก (" in queue
    client.post("/summons/issue/start/", {"case_ids": own + [other.pk]})
    plan = client.get("/summons/issue/").context["plan"]
    ok = {i.case.pk for i in plan.ok_items}
    assert ok <= set(own) and other.pk not in ok
    blocked = [i for i in plan.blocked_items if i.case.pk == other.pk][0]
    assert any("ไม่มีสิทธิ์" in p for p in blocked.problems)


def test_caller_cannot_edit_unrelated_signer(client, caller):
    own_invs = set(CallAssignment.objects.filter(assignee=caller)
                   .values_list("case__investigator_alias__investigator_id", flat=True))
    other = Investigator.objects.exclude(pk__in=own_invs).first()
    if other is None:
        pytest.skip("all signers are on the caller's cases")
    prefix = f"i{other.pk}"
    resp = client.post("/summons/issue/signer/", {"target": f"inv:{other.pk}", f"{prefix}-rank_full": "x",
                                                  f"{prefix}-sign_name": "x", f"{prefix}-position": "x",
                                                  f"{prefix}-phone": "x"})
    assert resp.status_code == 403
    assert client.get("/cases/").status_code == 302  # still no register access
