from apps.imports.postcode import lookup_postcode


def test_plain_names():
    assert lookup_postcode("ดอนแก้ว", "แม่ริม", "เชียงใหม่") == "50180"


def test_prefixed_names():
    assert lookup_postcode("ต.ดอนแก้ว", "อ.แม่ริม", "จ.เชียงใหม่") == "50180"
    assert lookup_postcode("ตำบลดอนแก้ว", "อำเภอแม่ริม", "จังหวัดเชียงใหม่") == "50180"
    assert lookup_postcode("ตำบล ดอนแก้ว", "อำเภอ แม่ริม", "จังหวัด เชียงใหม่") == "50180"


def test_same_subdistrict_name_other_district():
    assert lookup_postcode("ดอนแก้ว", "สารภี", "เชียงใหม่") == "50140"


def test_mueang_short_form():
    assert lookup_postcode("สุเทพ", "เมือง", "เชียงใหม่") == "50200"
    assert lookup_postcode("สุเทพ", "อ.เมืองเชียงใหม่", "เชียงใหม่") == "50200"


def test_bangkok():
    assert lookup_postcode("แขวงลาดยาว", "เขตจตุจักร", "กรุงเทพฯ") == "10900"
    assert lookup_postcode("ลาดยาว", "จตุจักร", "กทม.") == "10900"


def test_unknown_returns_blank():
    assert lookup_postcode("", "", "") == ""
    assert lookup_postcode("ไม่มีจริง", "ไม่มีจริง", "เชียงใหม่") == ""
