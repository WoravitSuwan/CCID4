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
    assert "99,999.00" in html and 'data-label="มูลค่า (บาท)"' in html  # its own column
    assert 'class="sort-btn is-active"' in html and "น้อย → มาก" in html
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
    shown = [be for be, label, on in page.context["f"]["year_groups"][0][1]]
    assert shown == [y + 543 for y in sorted(years, reverse=True)]
    for ce in years:
        for value in (ce + 543, ce):  # Buddhist era as shown, or the CE year
            resp = client.get("/calls/", {"tab": "all", "year": value})
            listed = {a.case.report_year for a in resp.context["page"].paginator.object_list}
            assert listed == {ce}, value
            assert resp.context["counts"]["all"] == Case.objects.filter(report_year=ce).count()
            assert resp.context["filtered"]
    html = page.content.decode()
    assert f'name="year" value="{years[0] + 543}" form="list-filters"' in html
    assert client.get("/calls/", {"year": "abc"}).status_code == 200


def test_several_years_at_once(client, admin_user, imported):
    years = sorted(set(Case.objects.exclude(report_year__isnull=True).values_list("report_year", flat=True)))
    client.force_login(admin_user)
    both = client.get("/calls/", {"tab": "all", "year": [y + 543 for y in years]}).context
    assert both["counts"]["all"] == Case.objects.filter(report_year__in=years).count()
    assert both["year"] == sorted(years, reverse=True)
    assert all(on for _, _, on in both["f"]["year_groups"][0][1])  # ticked in the column filter
    # unticking everything (the form still sends an empty year) clears the remembered choice
    assert client.get("/calls/", {"tab": "all", "year": ""}).context["year"] == []


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
    html = resp.content.decode()
    assert resp.context["filtered"] and 'name="state" value="open" form="list-filters" checked' in html
    assert 'class="filter-chip"' in html and "ยังไม่สิ้นสุด" in html
    # still filtered when coming back (menu, back link, plain address)
    assert client.get("/calls/").context["state"] == ["open"]
    assert client.get("/calls/").context["counts"]["all"] == len(open_ids)
    final = client.get("/calls/", {"state": "final"}).context
    assert final["counts"]["all"] == all_cases - len(open_ids)
    st = Status.objects.filter(case__isnull=False).first()
    one = client.get("/calls/", {"tab": "all", "state": f"s{st.pk}"}).context
    assert {a.case.status_id for a in one["page"].paginator.object_list} == {st.pk}
    # several ticked: any of them
    two = Status.objects.filter(case__isnull=False).distinct()[:2]
    many = client.get("/calls/", {"tab": "all", "state": [f"s{x.pk}" for x in two]}).context
    assert many["counts"]["all"] == Case.objects.filter(status__in=two).count()
    # "ล้างตัวกรองทั้งหมด" clears the remembered year and status
    client.get("/calls/", {"year": "2569"})
    html = client.get("/calls/").content.decode()
    assert "?tab=all&amp;year=&amp;state=" in html
    cleared = client.get("/calls/", {"tab": "all", "year": "", "state": ""}).context
    assert cleared["state"] == [] and cleared["year"] == [] and cleared["counts"]["all"] == all_cases
    assert client.get("/calls/", {"state": "zzz"}).context["state"] == []


def test_chip_removes_one_value_only(client, admin_user, imported):
    from apps.registry.models import Status

    client.force_login(admin_user)
    st = Status.objects.filter(case__isnull=False).first()
    resp = client.get("/calls/", {"tab": "all", "state": ["open", f"s{st.pk}"], "result": "reached"})
    chips = dict(resp.context["f"]["chips"])
    url = chips["ยังไม่สิ้นสุด"]
    assert "state=open" not in url and f"state=s{st.pk}" in url and "result=reached" in url
    after = client.get("/calls/" + url).context
    assert after["state"] == [f"s{st.pk}"]
    assert "state=" in chips[st.name] and f"s{st.pk}" not in chips[st.name]


def test_damage_range_filter(client, admin_user, amounts):
    client.force_login(admin_user)
    ctx = client.get("/calls/", {"tab": "all", "dmin": "100", "dmax": "6,000"}).context
    values = sorted(a.case.damage_amount for a in ctx["page"].paginator.object_list)
    assert values == [Decimal("120.50"), Decimal("5000")]
    assert ctx["f"]["active"]["damage"] and any("มูลค่า ≥ 100.00" == c for c, _ in ctx["f"]["chips"])
    assert client.get("/calls/", {"dmin": "abc"}).status_code == 200  # ignored


def test_several_call_results_at_once(client, admin_user, imported):
    from django.utils import timezone

    from apps.calls.models import CallAssignment, CallOutcome

    client.force_login(admin_user)
    client.get("/calls/")
    a, b, c = Case.objects.not_final().order_by("receipt_no")[:3]
    now = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
    client.post(f"/calls/{a.pk}/", {"outcome": "off", "called_at": now})
    client.post(f"/calls/{b.pk}/", {"outcome": "busy", "called_at": now})
    client.post(f"/calls/{c.pk}/", {"outcome": "no_answer", "called_at": now})
    ctx = client.get("/calls/", {"tab": "all", "result": [CallOutcome.OFF, CallOutcome.BUSY]}).context
    assert {x.case_id for x in ctx["page"].paginator.object_list} == {a.pk, b.pk}
    assert CallAssignment.objects.filter(last_outcome=CallOutcome.NO_ANSWER).exists()


@pytest.mark.parametrize("key", ["receipt", "date", "name", "damage", "status", "calls", "last"])
def test_every_column_heading_sorts_both_ways(client, admin_user, amounts, key):
    from apps.calls.views import SORT_FIELDS

    client.force_login(admin_user)
    resp = client.get("/calls/", {"tab": "all", "sort": key})
    assert resp.status_code == 200 and resp.context["sort"] == key and resp.context["col"][key]["up"]
    assert resp.context["col"][key]["sort"] == "-" + key  # clicking again turns it round
    down = client.get("/calls/", {"tab": "all", "sort": "-" + key}).context
    assert down["col"][key]["down"] and down["col"][key]["sort"] == key
    field = SORT_FIELDS[key]
    path = field.split("__")

    def value(a):
        v = a
        for part in path:
            v = getattr(v, part, None) if v is not None else None
        return v

    rows = [value(a) for a in down["page"]]
    present = [v for v in rows if v is not None]
    assert present == sorted(present, reverse=True)
    assert rows[:len(present)] == present  # empty values last


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
    assert 'class="table table-tight table-cards has-head-controls"' in html  # headings stay as buttons on phones
    assert html.count("data-col-filter") == 4  # year, damage, status, result


def test_damage_ranges_to_tick(client, admin_user, amounts):
    """Amounts 0, 120.50, 5000, 99999 and the rest without an amount."""
    client.force_login(admin_user)
    ctx = client.get("/calls/", {"tab": "all", "drange": ["lt10k", "50k"]}).context
    values = sorted(a.case.damage_amount for a in ctx["page"].paginator.object_list)
    assert values == [Decimal("0"), Decimal("120.50"), Decimal("5000"), Decimal("99999")]
    only_big = client.get("/calls/", {"tab": "all", "drange": "50k"}).context
    assert [a.case.damage_amount for a in only_big["page"].paginator.object_list] == [Decimal("99999")]
    # each range shows how many cases it has (with the other filters, whatever range is ticked)
    labels = [text for _, text, _ in only_big["f"]["damage_groups"][0][1]]
    assert labels[0] == "ต่ำกว่า 10,000 · 3 คดี" and labels[2] == "50,000 – 99,999 · 1 คดี"
    none = Case.objects.filter(damage_amount__isnull=True).count()
    assert labels[-1] == f"ไม่ระบุมูลค่า · {none:,} คดี"
    assert only_big["f"]["active"]["damage"] and ("มูลค่า 50,000 – 99,999" in dict(only_big["f"]["chips"]))
    html = client.get("/calls/").content.decode()
    assert 'name="drange" value="1m" form="list-filters"' in html


def test_open_and_a_register_status_together_mean_both(client, admin_user, imported):
    """ยังไม่สิ้นสุด + อายัดบัญชี = อายัดบัญชี cases still open (not every open case)."""
    from apps.calls import funnel
    from apps.calls.models import CallAssignment
    from apps.registry.models import Status

    client.force_login(admin_user)
    client.get("/calls/")
    st = Status.objects.filter(case__isnull=False, is_final=False).first()
    both = client.get("/calls/", {"tab": "all", "state": ["open", f"s{st.pk}"]}).context
    listed = {a.case_id for a in both["page"].paginator.object_list}
    expected = set(CallAssignment.objects.filter(funnel.OPEN_CASE, case__status=st).values_list("case_id", flat=True))
    assert listed == expected and all(Case.objects.get(pk=pk).status_id == st.pk for pk in listed)
    # ยังไม่สิ้นสุด + สิ้นสุดแล้ว: every case
    every = client.get("/calls/", {"tab": "all", "state": ["open", "final"]}).context
    assert every["counts"]["all"] == Case.objects.count()
