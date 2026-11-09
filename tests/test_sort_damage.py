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
