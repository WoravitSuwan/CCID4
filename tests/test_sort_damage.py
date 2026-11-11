"""Lists can be ordered by มูลค่าความเสียหาย, smallest or largest first; cases without an amount go last."""
from decimal import Decimal

import pytest

from apps.registry.models import Case

pytestmark = pytest.mark.django_db


@pytest.fixture
def amounts(imported):
    Case.objects.update(damage_amount=None)
    picked = list(Case.objects.not_final().order_by("receipt_no")[:4])
    for case, value in zip(picked, ("5000", "120.50", "99999", "0"), strict=True):
        Case.objects.filter(pk=case.pk).update(damage_amount=Decimal(value))
    return picked


def _amounts(rows):
    return [r.damage_amount for r in rows]


def test_call_list_by_damage(client, admin_user, amounts):
    client.force_login(admin_user)
    up = client.get("/calls/", {"tab": "all", "sort": "damage"})
    values = [a.case.damage_amount for a in up.context["page"]]
    assert values[:4] == [Decimal("0"), Decimal("120.50"), Decimal("5000"), Decimal("99999")]
    assert all(v is None for v in values[4:])  # no amount: last
    down = client.get("/calls/", {"tab": "all", "sort": "-damage"})
    assert [a.case.damage_amount for a in down.context["page"]][:4] == [
        Decimal("99999"), Decimal("5000"), Decimal("120.50"), Decimal("0")]
    html = up.content.decode()
    assert "มูลค่า 99,999.00 บาท" in html and 'value="damage" selected' in html
    # the order is kept when changing tab or page
    assert "sort=damage" in html
    assert client.get("/calls/", {"sort": "nonsense"}).context["sort"] == ""


def test_case_register_by_damage(client, admin_user, amounts):
    client.force_login(admin_user)
    up = _amounts(client.get("/cases/", {"scope": "all", "sort": "damage"}).context["page"])
    assert up[:4] == [Decimal("0"), Decimal("120.50"), Decimal("5000"), Decimal("99999")]
    assert all(v is None for v in up[4:])
    down = _amounts(client.get("/cases/", {"scope": "all", "sort": "-damage"}).context["page"])
    assert down[:4] == [Decimal("99999"), Decimal("5000"), Decimal("120.50"), Decimal("0")]


def test_call_list_remembers_the_order_and_the_back_link_returns_to_the_same_list(client, admin_user, amounts):
    client.force_login(admin_user)
    client.get("/calls/", {"tab": "all", "sort": "damage", "page": "1"})
    case = amounts[0]
    page = client.get(f"/calls/{case.pk}/").content.decode()
    assert "/calls/?tab=all&amp;sort=damage&amp;page=1" in page  # "← รายการที่ต้องโทร" goes back to that list
    # coming back by any way (menu, plain address) keeps the order
    assert client.get("/calls/").context["sort"] == "damage"
    assert client.get("/calls/", {"tab": "not_reached"}).context["sort"] == "damage"
    # picking the work order again puts it back
    assert client.get("/calls/", {"sort": ""}).context["sort"] == ""
    assert client.get("/calls/").context["sort"] == ""


def test_next_case_follows_the_chosen_order(client, admin_user, amounts):
    from django.utils import timezone

    client.force_login(admin_user)
    client.get("/calls/", {"sort": "damage"})
    by_amount = sorted(amounts, key=lambda c: Case.objects.get(pk=c.pk).damage_amount)  # 0, 120.50, 5000, 99999
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    resp = client.post(f"/calls/{by_amount[0].pk}/", {"outcome": "off", "called_at": now, "go": "next"})
    assert resp["Location"] == f"/calls/{by_amount[1].pk}/"
    resp = client.post(f"/calls/{by_amount[1].pk}/", {"outcome": "off", "called_at": now, "go": "next"})
    assert resp["Location"] == f"/calls/{by_amount[2].pk}/"  # onwards, not back to the first one


def test_case_register_remembers_the_order(client, admin_user, amounts):
    client.force_login(admin_user)
    client.get("/cases/", {"sort": "-damage"})
    page = client.get("/cases/", {"open": "on"})  # e.g. the menu or the case page's back link
    assert page.context["form"].current_sort == "-damage"
    assert _amounts(page.context["page"])[0] == Decimal("99999")
