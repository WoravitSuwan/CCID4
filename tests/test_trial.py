"""ข้อมูลทดลอง: a made-up name and phone to try calling. Shown on the first page, never counted, deletable."""
import pytest

from apps.accounts.models import Role, User
from apps.calls import funnel
from apps.calls.models import CallAssignment, CallAttempt
from apps.registry.models import Case

pytestmark = pytest.mark.django_db
NAME, PHONE = "นางสาวทดลอง ใจดี", "081-234-5678"


@pytest.fixture
def caller(db):
    return User.objects.create_user("trial_call", password="x" * 12, role=Role.OUTSOURCE)


def counts():
    return funnel.counts(funnel.with_summons_flag(CallAssignment.objects.exclude(case__is_test=True)))


def test_add_try_and_delete(client, imported, caller, admin_user):
    client.force_login(caller)
    before = client.get("/calls/").context["counts"]
    stats_before = client.get("/stats/").context["period"]
    resp = client.post("/calls/trial/add/", {"name": NAME, "phone": PHONE, "next": "/calls/"}, follow=True)
    trial = Case.objects.get(is_test=True)
    assert trial.victim_phone == "0812345678" and trial.receipt_no.startswith("TEST")
    html = resp.content.decode()
    assert "ข้อมูลทดลอง" in html and NAME in html and "0812345678" in html and f"/calls/{trial.pk}/" in html
    # it is not in any number or list
    assert client.get("/calls/").context["counts"] == before
    assert trial.pk not in [a.case_id for a in client.get("/calls/?tab=all").context["page"].paginator.object_list]
    # try a call on it
    page = client.get(f"/calls/{trial.pk}/")
    assert page.status_code == 200 and "คดีนี้เป็นข้อมูลทดลอง" in page.content.decode()
    from django.utils import timezone

    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    client.post(f"/calls/{trial.pk}/", {"outcome": "off", "called_at": now, "go": "stay"})
    assert CallAttempt.objects.filter(case=trial).count() == 1
    assert client.get("/calls/").context["counts"] == before == counts()
    assert client.get("/stats/").context["period"] == stats_before  # the trial call is not counted
    assert "ไม่สามารถติดต่อได้" in client.get("/calls/").content.decode()  # its last result shows in the box
    # admin's first page shows it too; registry and exports leave it out
    client.force_login(admin_user)
    assert NAME in client.get("/").content.decode()
    assert trial.pk not in [c.pk for c in client.get("/cases/?scope=all").context["page"].paginator.object_list]
    # delete: only this one, with what was recorded on it
    client.force_login(caller)
    resp = client.post(f"/calls/trial/{trial.pk}/delete/", {"next": f"/calls/{trial.pk}/"})
    assert resp.status_code == 302 and resp["Location"] == "/calls/"
    assert not Case.objects.filter(pk=trial.pk).exists() and not CallAttempt.objects.filter(case_id=trial.pk).exists()
    assert client.get("/calls/").context["counts"] == before


def test_delete_never_touches_a_register_case(client, imported, caller):
    client.force_login(caller)
    real = Case.objects.first()
    assert client.post(f"/calls/trial/{real.pk}/delete/").status_code == 404
    assert Case.objects.filter(pk=real.pk).exists()


def test_bad_input_is_refused(client, imported, caller):
    client.force_login(caller)
    resp = client.post("/calls/trial/add/", {"name": "", "phone": "12"}, follow=True)
    assert "กรอกชื่อและเบอร์โทร" in resp.content.decode() and not Case.objects.filter(is_test=True).exists()


def test_several_trials_get_their_own_numbers(client, imported, caller):
    client.force_login(caller)
    for i in range(3):
        client.post("/calls/trial/add/", {"name": f"ทดลอง {i}", "phone": f"081000000{i}"})
    assert sorted(Case.objects.filter(is_test=True).values_list("receipt_no", flat=True)) == [
        "TEST0001", "TEST0002", "TEST0003"]
    one = Case.objects.get(receipt_no="TEST0002")
    client.post(f"/calls/trial/{one.pk}/delete/")
    assert sorted(Case.objects.filter(is_test=True).values_list("victim_name", flat=True)) == ["ทดลอง 0", "ทดลอง 2"]
