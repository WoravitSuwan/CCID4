import io

import pytest
from openpyxl import Workbook, load_workbook

from apps.accounts.models import Role, User
from apps.registry import signer_import as si
from apps.registry.models import Case, Investigator, RememberedValue

pytestmark = pytest.mark.django_db


def xlsx(rows, title=None):
    wb = Workbook()
    ws = wb.active
    if title:
        ws.append([title])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = "signers.xlsx"
    return buf


def test_rank_helpers():
    assert si.expand_rank("พ.ต.ท.") == "พันตำรวจโท"
    assert si.expand_rank("พ.ต.ต.หญิง") == "พันตำรวจตรีหญิง"
    assert si.expand_rank("พันตำรวจเอก") == "พันตำรวจเอก"
    assert si.split_rank("พ.ต.ท.ศักดิ์ชัย ใจดีสมมติ") == ("พันตำรวจโท", "ศักดิ์ชัย ใจดีสมมติ")
    assert si.split_rank("ร.ต.อ.หญิง สุดา รักษ์ทดสอบ") == ("ร้อยตำรวจเอกหญิง", "สุดา รักษ์ทดสอบ")
    assert si.split_rank("พันตำรวจโทหญิงสุดา รักษ์ทดสอบ") == ("พันตำรวจโทหญิง", "สุดา รักษ์ทดสอบ")
    assert si.split_rank("ธนพล มั่นคงสมมติ") == ("", "ธนพล มั่นคงสมมติ")
    assert si.fix_phone("812345678") == "0812345678" and si.fix_phone("053 123 456") == "053 123 456"


def test_unit_list_with_combined_rank_and_name(imported):
    # the unit's own list: a title row, rank+name in one column, abbreviations, numeric phone
    f = xlsx([["ลำดับ", "ยศ ชื่อ - สกุล", "ตำแหน่ง", "เบอร์โทร"],
              [1, "พ.ต.ท.ศักดิ์ชัย ใจดีสมมติ", "สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.", 812345678],
              [2, "พ.ต.ต.หญิง สุดา  รักษ์ทดสอบ", "รอง สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.", "0899999999"],
              [3, "ร.ต.อ.ใหม่ ไม่มีในทะเบียน", "พงส.", ""],
              [4, None, None, None]], title="รายชื่อพนักงานสอบสวน กก.๑")
    rows, error = si.parse(f)
    assert error == "" and len(rows) == 3
    planned = {r.name: r for r in si.plan(rows)}
    assert planned["ศักดิ์ชัย ใจดีสมมติ"].action == "update"
    assert planned["สุดา รักษ์ทดสอบ"].rank == "พันตำรวจตรีหญิง" and planned["สุดา รักษ์ทดสอบ"].action == "update"
    assert planned["ใหม่ ไม่มีในทะเบียน"].action == "new"
    counts = si.apply([r.as_dict() for r in planned.values()])
    assert counts == {"new": 1, "update": 2, "same": 0, "skip": 0}
    sak = Investigator.objects.get(sign_name="ศักดิ์ชัย ใจดีสมมติ")
    assert (sak.rank_full, sak.phone) == ("พันตำรวจโท", "0812345678")
    assert Investigator.objects.get(sign_name="สุดา รักษ์ทดสอบ").position.startswith("รอง สว.")
    assert "พันตำรวจตรีหญิง" in RememberedValue.suggestions("rank")[:3]
    # cases of these signers no longer miss signer data
    case = Case.objects.filter(investigator_alias__investigator=sak).first()
    assert not any("ไม่ครบ" in b for b in case.issue_blockers())


def test_empty_cells_do_not_erase(imported):
    inv = Investigator.objects.get(sign_name="ศักดิ์ชัย ใจดีสมมติ")
    inv.rank_full, inv.position, inv.phone = "พันตำรวจโท", "สว.", "081"
    inv.save()
    rows, _ = si.parse(xlsx([["ชื่อ-สกุล", "ยศ", "ตำแหน่ง", "โทรศัพท์"], ["ศักดิ์ชัย  ใจดีสมมติ", "", "", "0999"]]))
    si.apply([r.as_dict() for r in si.plan(rows)])
    inv.refresh_from_db()
    assert (inv.rank_full, inv.position, inv.phone) == ("พันตำรวจโท", "สว.", "0999")


def test_template_round_trip_and_web_flow(client, imported):
    client.force_login(User.objects.create_user("adm4", password="pass12345", role=Role.ADMIN))
    resp = client.get("/settings/investigators/template.xlsx")
    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb["ผู้ลงนาม"]
    names = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert set(names) == set(Investigator.objects.values_list("sign_name", flat=True))
    assert ws.data_validations.dataValidation  # rank drop-down
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=2, value="พันตำรวจโท")
        ws.cell(row=r, column=3, value="สว.(สอบสวน) กก.๑ บก.สอท.๔ บช.สอท.")
        ws.cell(row=r, column=4, value="0810000000")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = "filled.xlsx"
    preview = client.post("/settings/investigators/import/", {"file": buf})
    assert preview.context["summary"]["update"] == len(names)
    done = client.post("/settings/investigators/import/", {"action": "confirm"})
    assert done["Location"] == "/settings/investigators/"
    assert not [i for i in Investigator.objects.all() if i.missing_signer_fields()]


def test_bad_file(client, db):
    client.force_login(User.objects.create_user("adm5", password="pass12345", role=Role.ADMIN))
    f = xlsx([["คอลัมน์อื่น"], ["x"]])
    page = client.post("/settings/investigators/import/", {"file": f}, follow=True)
    assert "ไม่พบคอลัมน์ชื่อ" in page.content.decode()


def test_separate_name_and_surname_columns_match_register(imported):
    from apps.registry.models import link_register_names

    link_register_names()
    inv = Investigator.objects.exclude(sign_name="").first()
    first, last = inv.sign_name.split(maxsplit=1)
    f = xlsx([["ลำดับ", "ยศ", "ชื่อ", "นามสกุล", "ตำแหน่ง", "โทร"],
              [1, "พ.ต.ต", first, f"  {last} ", "รอง สว.(สอบสวน) กก.๒ บก.สอท.๔ บช.สอท.", "0800000001"]])
    rows, error = si.parse(f)
    assert error == "" and rows[0].name == inv.sign_name and rows[0].rank == "พันตำรวจตรี"
    planned = si.plan(rows)[0]
    assert planned.action == "update" and planned.investigator_id == inv.pk
    si.apply([planned.as_dict()])
    inv.refresh_from_db()
    assert inv.position.startswith("รอง สว.") and inv.rank_full == "พันตำรวจตรี"


def test_rank_written_without_dots_or_with_spaces():
    assert si.split_rank("พ.ต.ท สมชาย ใจดี") == ("พันตำรวจโท", "สมชาย ใจดี")
    assert si.split_rank("พ. ต. ท. สมชาย ใจดี") == ("พันตำรวจโท", "สมชาย ใจดี")
    assert si.split_rank("พ.ต.อ.(พิเศษ)สมชาย ใจดี") == ("พันตำรวจเอก (พิเศษ)", "สมชาย ใจดี")
    assert si.split_rank("พรทิพย์ ใจดี") == ("", "พรทิพย์ ใจดี")  # no dot: never treated as a rank
    assert si.expand_rank("พตท.") == "พันตำรวจโท"


def test_acting_rank_prefix():
    assert si.split_rank("ว่าที่ พ.ต.ต. ธนพัฒน์ ใจดี") == ("ว่าที่พันตำรวจตรี", "ธนพัฒน์ ใจดี")
    assert si.expand_rank("ว่าที่ พ.ต.ต.") == "ว่าที่พันตำรวจตรี"
    assert si.split_rank("ว่าที่ ธนพัฒน์ ใจดี") == ("", "ว่าที่ ธนพัฒน์ ใจดี")  # no rank after it: untouched


def test_new_name_with_known_surname_is_flagged(imported):
    from apps.registry.models import link_register_names

    link_register_names()
    inv = Investigator.objects.exclude(sign_name="").first()
    first, last = inv.sign_name.split(maxsplit=1)
    rows, _ = si.parse(xlsx([["ชื่อ-สกุล", "ยศ"], [f"{first[:-1]} {last}", "พ.ต.ต."]]))
    row = si.plan(rows)[0]
    assert row.action == "new" and inv.sign_name in row.problem
