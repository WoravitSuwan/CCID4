"""Call page: a case cannot be skipped before a call is recorded; called cases say so; going back to the case
before. สรุปผลประจำวัน: the wording of the report can be changed on the page, then locked."""
import datetime as dt

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.calls import daily_report as report
from apps.calls.models import CallOutcome
from apps.calls.services import NEED_CALL_MESSAGE, record_call
from apps.core.models import AuditLog, SystemSetting
from apps.registry.models import Case

pytestmark = pytest.mark.django_db


@pytest.fixture
def caller(db):
    return User.objects.create_user("caller9", password="pass12345", role=Role.OUTSOURCE, first_name="สมใจ")


def _never_called(n=2):
    return list(Case.objects.not_final().filter(contact_log="", calls__isnull=True)[:n])


def test_case_not_called_cannot_be_skipped(client, imported, caller):
    case, _ = _never_called()
    client.force_login(caller)
    page = client.get(f"/calls/{case.pk}/")
    html = page.content.decode()
    assert page.context["state"] is None and not page.context["can_go_next"]
    assert "data-need-call" in html and "btn-locked" in html and "ยังไม่บันทึกการโทร" in html
    assert NEED_CALL_MESSAGE == "ยังไม่มีการบันทึกการโทร กรุณาบันทึกการโทรก่อน"
    # going to the next case by its address is refused too: back to this case, with the message
    resp = client.get(f"/calls/{case.pk}/next/")
    assert resp.status_code == 302 and resp["Location"] == f"/calls/{case.pk}/?need_call=1#call-form"
    shown = client.get(resp["Location"].split("#")[0]).content.decode()
    assert NEED_CALL_MESSAGE in shown and 'id="need-call" class="need-call "' in shown  # visible


def test_called_case_says_so_and_moves_on(client, imported, caller):
    case, other = _never_called()
    record_call(case, caller, outcome=CallOutcome.OFF, called_at=timezone.now() - dt.timedelta(minutes=3))
    client.force_login(caller)
    page = client.get(f"/calls/{case.pk}/")
    html = page.content.decode()
    assert page.context["can_go_next"] and "คดีนี้โทรแล้ว" in html and "ไปคดีถัดไป" in html
    assert "btn-locked" not in html and NEED_CALL_MESSAGE not in html
    assert page.context["state"]["by"] == "สมใจ" and page.context["state"]["number"] == 1
    resp = client.get(f"/calls/{case.pk}/next/")
    assert resp.status_code == 302 and resp["Location"] != f"/calls/{case.pk}/?need_call=1#call-form"


def test_contact_log_in_the_register_counts_as_called(client, imported, caller):
    case = Case.objects.not_final().filter(calls__isnull=True).first()
    case.contact_log = "ไม่สามารถติดต่อได้ (ไม่รับสาย) วันที่ 9 พ.ย. 2569 เวลา 09.27 น."
    case.save(update_fields=["contact_log"])
    client.force_login(caller)
    page = client.get(f"/calls/{case.pk}/")
    assert page.context["can_go_next"] and page.context["state"]["by"] == "บันทึกในไฟล์ทะเบียน"
    assert "คดีนี้โทรแล้ว" in page.content.decode()


def test_back_to_the_case_before(client, imported, caller):
    first, second = _never_called()
    record_call(first, caller, outcome=CallOutcome.APPOINTED)
    client.force_login(caller)
    assert client.get(f"/calls/{first.pk}/").context["prev_case"] is None
    page = client.get(f"/calls/{second.pk}/")
    assert page.context["prev_case"] == first
    html = page.content.decode()
    assert "ย้อนกลับคดีก่อนหน้า" in html and f'href="/calls/{first.pk}/"' in html
    back = client.get(f"/calls/{first.pk}/")  # going back: the case before it is gone from the trail
    assert back.context["prev_case"] is None and "คดีนี้โทรแล้ว" in back.content.decode()


def test_someone_else_on_the_case_can_be_skipped(client, imported, caller):
    case, _ = _never_called()
    other = User.objects.create_user("call7", password="x" * 10, role=Role.OUTSOURCE)
    client.force_login(other)
    client.get(f"/calls/{case.pk}/")  # the other one has it open
    client.force_login(caller)
    page = client.get(f"/calls/{case.pk}/")
    assert page.context["busy_with"] == other and page.context["can_go_next"]
    assert client.get(f"/calls/{case.pk}/next/")["Location"] != f"/calls/{case.pk}/?need_call=1#call-form"


# --------------------------------------------------------------------------- the report's wording
def test_standard_wording_matches_the_built_in_report(imported, caller):
    case, _ = _never_called()
    record_call(case, caller, outcome=CallOutcome.CALLBACK, callback_at=timezone.now() + dt.timedelta(hours=1))
    d = report.load(timezone.localdate())
    text = report.summary_text(d)
    assert text.startswith("เรียนผู้บังคับบัญชา\n\nขอรายงานสรุปผลการดำเนินการ ของ เจ้าหน้าที่ Outsource")
    assert "4. ติดต่อได้ แต่ยังไม่ได้ข้อยุติ" in text and text.endswith("จึงเรียนมาเพื่อโปรดทราบ")
    assert "{" not in text


def test_wording_unlock_edit_lock(client, imported, caller):
    client.force_login(caller)
    page = client.get("/reports/daily/").content.decode()
    editor = 'class="flex flex-1 flex-col" data-template-editor>'
    assert "ล็อกรูปแบบแล้ว" in page and editor not in page
    client.post("/reports/daily/", {"action": "template_unlock"})
    s = SystemSetting.get()
    assert not s.report_locked and s.report_updated_by == "สมใจ"
    page = client.get("/reports/daily/").content.decode()
    assert editor in page and "บันทึกและล็อก" in page
    # an unknown {…} is refused, so it can never reach a report
    bad = client.post("/reports/daily/", {"action": "template_save", "template": "เรียน {ใครก็ได้} {จำนวนทั้งหมด}"},
                      follow=True)
    assert "ไม่รู้จัก {ใครก็ได้}" in bad.content.decode()
    assert SystemSetting.get().report_template == "" and not SystemSetting.get().report_locked
    new = "เรียน ผกก.1\nวันที่ {วันที่} โทร {จำนวนทั้งหมด} Case\n{บรรทัดยังไม่ได้ข้อยุติ}\nจบรายงาน"
    client.post("/reports/daily/", {"action": "template_save", "template": new})
    s = SystemSetting.get()
    assert s.report_locked and s.report_template == new
    shown = client.get("/reports/daily/")
    text = shown.context["summary"]
    assert text.startswith("เรียน ผกก.1\nวันที่ ") and " โทร 0 Case\nจบรายงาน" in text  # the empty line 4 is gone
    assert "ล็อกรูปแบบแล้ว" in shown.content.decode()
    assert AuditLog.objects.filter(detail__contains="บันทึกรูปแบบข้อความรายงานผู้บังคับบัญชา").exists()
    # the LINE message uses the same wording
    from apps.calls import line_bot
    assert line_bot.texts_for(timezone.localdate())[0] == text
    # back to the standard wording
    client.post("/reports/daily/", {"action": "template_reset"})
    assert SystemSetting.get().report_template == "" and SystemSetting.get().report_locked
    assert client.get("/reports/daily/").context["summary"].startswith("เรียนผู้บังคับบัญชา")


def test_template_problems():
    assert report.template_problems(report.DEFAULT_SUMMARY_TEMPLATE) == []
    assert report.template_problems("  ") == ["ข้อความว่างเปล่า"]
    assert report.template_problems("ยอด {จำนวนทั้งหมด") == ["มีวงเล็บปีกกา { } ไม่ครบคู่"]


def test_diary_wording_can_be_changed_and_locked_too(client, imported, caller):
    case, _ = _never_called()
    record_call(case, caller, outcome=CallOutcome.NOT_PROCEED, phone="0811111111")
    client.force_login(caller)
    page = client.get("/reports/daily/")
    assert page.context["diary"].startswith("ไม่ประสงค์ดำเนินคดี จำนวน 1 Case ดังนี้\n\n")
    assert case.receipt_no in page.context["diary"]
    assert page.context["diary"] == report.diary_text(report.load(timezone.localdate()), report.DEFAULT_DIARY_TEMPLATE)
    client.post("/reports/daily/", {"action": "template_unlock", "which": "diary"})
    s = SystemSetting.get()
    assert not s.diary_locked and s.report_locked  # only the diary was opened
    html = client.get("/reports/daily/").content.decode()
    assert 'name="which" value="diary"' in html and "รายชื่อแยกพนักงานสอบสวน" in html
    bad = client.post("/reports/daily/", {"action": "template_save", "which": "diary", "template": "{จำนวนทั้งหมด}"},
                      follow=True)  # a field of the other text
    assert "ไม่รู้จัก {จำนวนทั้งหมด}" in bad.content.decode()
    new = "บันทึกประจำวัน {วันที่}\nไม่ประสงค์ {จำนวน} ราย\n{รายชื่อแยกพนักงานสอบสวน}\nลงชื่อ ................"
    client.post("/reports/daily/", {"action": "template_save", "which": "diary", "template": new})
    s = SystemSetting.get()
    assert s.diary_locked and s.diary_template == new and s.report_template == ""
    diary = client.get("/reports/daily/").context["diary"]
    assert diary.startswith("บันทึกประจำวัน ") and "\nไม่ประสงค์ 1 ราย\n" in diary and case.receipt_no in diary
    assert diary.endswith("ลงชื่อ ................") and "{" not in diary
    # the LINE message and the .txt file are the same text
    from apps.calls import line_bot
    assert line_bot.texts_for(timezone.localdate(), with_texts=True)[1] == diary
    client.post("/reports/daily/", {"action": "template_reset", "which": "diary"})
    assert client.get("/reports/daily/").context["diary"].startswith("ไม่ประสงค์ดำเนินคดี จำนวน 1 Case ดังนี้")


def test_diary_wording_can_be_opened_on_a_day_with_nobody_listed(client, imported, caller):
    client.force_login(caller)
    html = client.get("/reports/daily/").content.decode()
    assert "ไม่มีผู้เสียหายที่แจ้งไม่ประสงค์ดำเนินคดี" in html and 'name="which" value="diary"' in html
    assert client.post("/reports/daily/", {"action": "template_unlock", "which": "nope"}).status_code == 404
