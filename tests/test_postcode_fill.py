import pytest

from apps.accounts.models import Role, User
from apps.imports.postcode import postcode_candidates
from apps.registry.models import AddressOverride, Case, Complainant

pytestmark = pytest.mark.django_db


def test_candidates():
    assert postcode_candidates("ดอนแก้ว", "แม่ริม", "เชียงใหม่") == ["50180"]
    assert postcode_candidates("", "", "") == []
    # a district with several postcodes, no ตำบล given -> several choices
    assert len(postcode_candidates("", "เมืองเชียงใหม่", "เชียงใหม่")) > 1


def test_lookup_endpoint(client, db):
    client.force_login(User.objects.create_user("a", password="pass12345", role=Role.ADMIN))
    r = client.get("/cases/postcode/", {"subdistrict": "ต.ดอนแก้ว", "district": "อ.แม่ริม", "province": "จ.เชียงใหม่"})
    assert r.json() == {"postcode": "50180", "candidates": ["50180"]}
    r = client.get("/cases/postcode/", {"subdistrict": "", "district": "เมืองเชียงใหม่", "province": "เชียงใหม่"})
    assert r.json()["postcode"] == "" and len(r.json()["candidates"]) > 1


def test_override_without_postcode_is_filled(db):
    o = AddressOverride.objects.create(receipt_no="L1", house_no="1", subdistrict="สุเทพ", district="เมืองเชียงใหม่",
                                       province="เชียงใหม่")
    assert o.postcode == "50200"
    kept = AddressOverride.objects.create(receipt_no="L2", house_no="1", subdistrict="สุเทพ",
                                          district="เมืองเชียงใหม่", province="เชียงใหม่", postcode="50201")
    assert kept.postcode == "50201"  # what staff typed is never replaced


def test_fill_postcodes_command(imported):
    from django.core.management import call_command

    Complainant.objects.update(postcode_auto="")
    call_command("fill_postcodes")
    assert Complainant.objects.filter(postcode="", flag_missing_area=False, postcode_auto="").count() == 0
    case = Case.objects.filter(complainant__postcode="", complainant__flag_missing_area=False).first()
    assert case.effective_address().postcode  # used on the summons / envelope
