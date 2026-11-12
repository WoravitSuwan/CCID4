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
    assert "มูลค่า 99,999.00 บาท" in html and 'class="sort-btn is-active"' in html and "น้อย → มาก" in html
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


def test_year_filter_on_the_call_list(client, admin_user, imported):
    years = sorted(set(Case.objects.exclude(report_year__isnull=True).values_list("report_year", flat=True)))
    assert len(years) >= 2  # the sample register has two years
    client.force_login(admin_user)
    page = client.get("/calls/")
    assert [ce for ce, be in page.context["years"]] == sorted(years, reverse=True)
    for ce in years:
        for value in (ce + 543, ce):  # Buddhist era as shown, or the CE year
            resp = client.get("/calls/", {"tab": "all", "year": value})
            listed = {a.case.report_year for a in resp.context["page"].paginator.object_list}
            assert listed == {ce}, value
            assert resp.context["counts"]["all"] == Case.objects.filter(report_year=ce).count()
            assert resp.context["filtered"]
    assert f'value="{years[0] + 543}"' in page.content.decode()
    assert client.get("/calls/", {"year": "abc"}).status_code == 200


def test_sort_buttons_turn_the_direction_around(client, admin_user, amounts):
    client.force_login(admin_user)
    buttons = {b["label"]: b for b in client.get("/calls/", {"sort": "damage"}).context["sort_buttons"]}
    damage, date, work = buttons["มูลค่าความเสียหาย"], buttons["วันที่รับแจ้ง"], buttons["ลำดับงานโทร"]
    assert damage["active"] and damage["up"] and damage["hint"] == "น้อย → มาก" and damage["sort"] == "-damage"
    assert not date["active"] and date["sort"] == "-date" and not work["active"] and work["sort"] == ""
    buttons = {b["label"]: b for b in client.get("/calls/", {"sort": "-damage"}).context["sort_buttons"]}
    assert buttons["มูลค่าความเสียหาย"]["down"] and buttons["มูลค่าความเสียหาย"]["sort"] == "damage"
    html = client.get("/calls/", {"sort": "-date"}).content.decode()
    assert 'class="sort-btn is-active"' in html and "ใหม่ → เก่า" in html


def test_case_status_filter_on_the_call_list_is_remembered(client, admin_user, imported):
    from apps.calls import funnel
    from apps.calls.models import CallAssignment
    from apps.registry.models import Status

    client.force_login(admin_user)
    all_cases = Case.objects.count()
    resp = client.get("/calls/", {"tab": "all", "state": "open"})  # (the first visit fills the call pool)
    open_ids = set(CallAssignment.objects.filter(funnel.OPEN_CASE).values_list("case_id", flat=True))
    listed = {a.case_id for a in resp.context["page"].paginator.object_list}
    assert listed == open_ids and resp.context["counts"]["all"] == len(open_ids) < all_cases
    assert resp.context["filtered"] and 'value="open" selected' in resp.content.decode()
    # still filtered when coming back (menu, back link, plain address)
    assert client.get("/calls/").context["state"] == "open"
    assert client.get("/calls/").context["counts"]["all"] == len(open_ids)
    final = client.get("/calls/", {"state": "final"}).context
    assert final["counts"]["all"] == all_cases - len(open_ids)
    st = Status.objects.filter(case__isnull=False).first()
    one = client.get("/calls/", {"tab": "all", "state": f"s{st.pk}"}).context
    assert {a.case.status_id for a in one["page"].paginator.object_list} == {st.pk}
    # "ล้างตัวกรอง" clears the remembered year and status
    client.get("/calls/", {"year": "2569"})
    html = client.get("/calls/").content.decode()
    assert "?tab=all&amp;year=&amp;state=" in html
    cleared = client.get("/calls/", {"tab": "all", "year": "", "state": ""}).context
    assert cleared["state"] == "" and cleared["year"] is None and cleared["counts"]["all"] == all_cases
    assert client.get("/calls/", {"state": "zzz"}).context["state"] == ""


def test_next_case_follows_the_filters_and_the_order(client, admin_user, amounts):
    """ปี + สถานะคดี + เรียงมูลค่า: "ข้ามไปคดีถัดไป" walks the same list the user sees."""
    from django.utils import timezone

    by_amount = sorted(amounts, key=lambda c: Case.objects.get(pk=c.pk).damage_amount)  # 0, 120.50, 5000, 99999
    year = Case.objects.get(pk=by_amount[0].pk).report_year
    Case.objects.filter(pk__in=[c.pk for c in by_amount]).update(report_year=year)
    Case.objects.filter(pk=by_amount[1].pk).update(report_year=year - 1)  # filtered out by the year
    client.force_login(admin_user)
    client.get("/calls/", {"tab": "all", "year": year + 543, "state": "open", "sort": "damage"})
    page = client.get(f"/calls/{by_amount[0].pk}/")
    assert page.context["next_pk"] == by_amount[2].pk  # 120.50 is another year: skipped
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    resp = client.post(f"/calls/{by_amount[2].pk}/", {"outcome": "off", "called_at": now, "go": "next"})
    assert resp["Location"] == f"/calls/{by_amount[3].pk}/"


def test_next_case_keeps_its_place_when_the_case_leaves_the_tab(client, admin_user, amounts):
    """From the tab ยังไม่ได้โทร: once called, the case leaves the tab, but "next" is still the row after it."""
    from django.utils import timezone

    by_amount = sorted(amounts, key=lambda c: Case.objects.get(pk=c.pk).damage_amount)
    Case.objects.filter(pk__in=[c.pk for c in by_amount]).update(contact_log="")
    client.force_login(admin_user)
    client.get("/calls/", {"tab": "never", "sort": "damage", "year": "", "state": ""})
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    resp = client.post(f"/calls/{by_amount[1].pk}/", {"outcome": "off", "called_at": now, "go": "next"})
    assert resp["Location"] == f"/calls/{by_amount[2].pk}/"  # not back to the first row of the tab


def test_list_controls_keep_the_scroll_position(client, admin_user, imported):
    client.force_login(admin_user)
    html = client.get("/calls/").content.decode()
    assert html.count("data-keep-scroll") >= 3 and 'id="list"' in html
