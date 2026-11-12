"""สรุปผลประจำวัน, ข้อความลงบันทึกประจำวัน, and the optional LINE bot (LINE itself is never called in tests)."""
import base64
import datetime as dt
import hashlib
import hmac
import json
from io import BytesIO

import pytest
from django.utils import timezone

from apps.calls import daily_report as report
from apps.calls import line_bot
from apps.calls.models import CallOutcome as O
from apps.calls.services import ensure_call_pool, record_call
from apps.core.models import SystemSetting
from apps.registry.models import Case

pytestmark = pytest.mark.django_db
DAY = dt.date(2026, 11, 9)


def setting(**values):
    SystemSetting.get()  # the row is made on first use
    SystemSetting.objects.update(**values)


def at(day, hour, minute=0):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


@pytest.fixture
def caller(db):
    from apps.accounts.models import Role, User

    return User.objects.create_user("caller9", password="pass12345", role=Role.OUTSOURCE, first_name="สมใจ")


@pytest.fixture
def day_calls(imported, admin_user, caller):
    """Calls on DAY: 2 not want, 1 want (appointment), 1 info, 2 unreached (one called twice), 1 callback;
    plus calls on other days that must not count."""
    ensure_call_pool()
    c = list(Case.objects.order_by("receipt_no")[:9])
    Case.objects.filter(pk=c[0].pk).update(victim_name="นางสาวทดสอบ หนึ่ง", victim_phone="0811111111")
    Case.objects.filter(pk=c[1].pk).update(victim_name="นายทดสอบ สอง", victim_phone="0822222222")
    for x in c:
        x.refresh_from_db()
    record_call(c[0], caller, outcome=O.NO_ANSWER, called_at=at(DAY, 9))
    record_call(c[0], caller, outcome=O.NOT_PROCEED, called_at=at(DAY, 14))  # last call of the day wins
    record_call(c[1], admin_user, outcome=O.NOT_PROCEED, phone="0899999999", called_at=at(DAY, 10))
    record_call(c[2], caller, outcome=O.APPOINTED, called_at=at(DAY, 10, 30))
    record_call(c[3], caller, outcome=O.INFO, called_at=at(DAY, 11))
    record_call(c[4], caller, outcome=O.OFF, called_at=at(DAY, 11, 30))
    record_call(c[5], caller, outcome=O.NO_ANSWER, called_at=at(DAY, 8))
    record_call(c[5], caller, outcome=O.WRONG, called_at=at(DAY, 16))
    record_call(c[6], caller, outcome=O.CALLBACK, called_at=at(DAY, 15))
    record_call(c[7], caller, outcome=O.NOT_PROCEED, called_at=at(DAY - dt.timedelta(days=1), 23, 59))  # other day
    record_call(c[8], caller, outcome=O.NOT_PROCEED, called_at=at(DAY + dt.timedelta(days=1), 0, 0))  # other day
    return c


def test_each_case_counts_once_by_its_last_call_of_the_day(day_calls):
    d = report.load(DAY)
    assert d.counts == {"not_want": 2, "want": 2, "unreached": 2, "other": 1, "total": 7}
    assert sum(d.counts[g] for g in report.GROUP_LABEL) == d.counts["total"]
    assert [r.case.pk for r in d.not_want] == [day_calls[0].pk, day_calls[1].pk]  # in the order first called
    assert d.not_want[0].calls == 2 and d.not_want[1].phone == "0899999999"  # the number actually called


def test_one_caller_only(day_calls, admin_user, caller):
    assert report.load(DAY, admin_user).counts["total"] == 1
    assert report.load(DAY, caller).counts["total"] == 6
    assert report.caller_ids(DAY) == {admin_user.pk, caller.pk}


def test_diary_text_follows_the_staff_template(day_calls):
    text = report.diary_text(report.load(DAY))
    lines = text.split("\n")
    assert lines[0].startswith("วันนี้( 9 พ.ย.69) พนักงานสอบสวน กก.1บก.สอท.4  ได้ทำการโทรศัพท์ติดต่อผู้เสียหาย")
    assert "Thaipoliceonline.com/สายด่วน 1441" in lines[0]
    assert lines[1] == "พบว่ามีผู้เสียหายไม่ประสงค์พบพนักงานสอบสวนและไม่ประสงค์ดำเนินคดี ดังนี้"
    assert lines[2] == ""
    c0, c1 = day_calls[0], day_calls[1]
    assert lines[3] == f"1. ผู้เสียหาย: นางสาวทดสอบ หนึ่ง เลขรับแจ้ง: {c0.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: 0811111111"
    assert lines[4] == f"2. ผู้เสียหาย: นายทดสอบ สอง เลขรับแจ้ง: {c1.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: 0899999999"
    assert lines[-1] == "จึงลงบันทึกไว้เป็นหลักฐาน เพื่อที่จะดำเนินการในส่วนที่เกี่ยวข้องต่อไป"
    assert "{" not in text and day_calls[2].receipt_no not in text  # only the not-want cases


def test_summary_text_for_the_commanders(day_calls):
    text = report.summary_text(report.load(DAY))
    assert text.split("\n") == [
        "เรียนผู้บังคับบัญชาเพื่อโปรดทราบ",
        "ขอรายงานสรุปผลการดำเนินการ ของ เจ้าหน้าที่ Outsource สะสาง Case ID ประจำวันที่ 9 พฤศจิกายน 2569",
        "จำนวน 7 Case ID",
        "1. ไม่ประสงค์ดำเนินคดี จำนวน 2 Case",
        "2. ประสงค์ดำเนินคดีต่อ จำนวน 2 Case",
        "3. ไม่สามารถติดต่อได้ (รวมถึง ไม่รับสาย, ปิดเครื่อง, ไม่มีหมายเลข, ปลายสายไม่รู้จัก ฯลฯ) จำนวน 2 Case",
        "4. ติดต่อได้ แต่ยังไม่ได้ข้อยุติ (ขอให้โทรกลับ, อื่นๆ) จำนวน 1 Case",  # so it adds up to 7
    ]
    assert day_calls[0].receipt_no not in text and "0811111111" not in text  # counts only


def test_summary_without_open_cases_has_three_lines(imported, caller):
    ensure_call_pool()
    case = Case.objects.first()
    record_call(case, caller, outcome=O.OFF, called_at=at(DAY, 9))
    text = report.summary_text(report.load(DAY))
    assert "จำนวน 1 Case ID" in text and "4." not in text


def test_page_texts_and_word_files(client, caller, day_calls):
    from docx import Document

    client.force_login(caller)  # call staff use it every day
    page = client.get("/reports/daily/", {"date": "2026-11-09"})
    assert page.status_code == 200
    html = page.content.decode()
    assert "วันนี้( 9 พ.ย.69)" in html and "จำนวน 7 Case ID" in html and 'data-copy="#t-diary"' in html
    assert "ส่งเข้ากลุ่ม LINE" not in html  # admins only
    diary = client.get("/reports/daily/", {"date": "2026-11-09", "format": "diary"})
    assert "%E0%B8%A5%E0%B8%87%E0%B8%9A%E0%B8%B1%E0%B8%99%E0%B8%97%E0%B8%B6%E0%B8%81" in diary["Content-Disposition"]
    assert "9.11.2569.docx" in diary["Content-Disposition"]
    # same layout as the file the staff made by hand: one line each, a blank line between, TH SarabunPSK 16
    doc = Document(BytesIO(diary.content))
    lines = [p.text for p in doc.paragraphs]
    c0, c1 = day_calls[0], day_calls[1]
    assert lines == [
        f"ผู้เสียหาย: นางสาวทดสอบ หนึ่ง - เลขรับแจ้ง: {c0.receipt_no} - เบอร์โทรศัพท์: 0811111111 ", "",
        f"ผู้เสียหาย: นายทดสอบ สอง - เลขรับแจ้ง: {c1.receipt_no} - เบอร์โทรศัพท์: 0899999999 ", ""]
    assert doc.styles["Normal"].font.name == "TH SarabunPSK" and doc.styles["Normal"].font.size.pt == 16
    resp = client.get("/reports/daily/", {"date": "2026-11-09", "format": "summary"})
    assert "9.11.2569.docx" in resp["Content-Disposition"]
    summary = [p.text for p in Document(BytesIO(resp.content)).paragraphs]
    assert summary[0] == "สรุปข้อมูลคดีและบันทึกการติดต่อ" and summary[1] == ""
    first = summary.index("ผู้เสียหาย: นางสาวทดสอบ หนึ่ง")
    assert summary[2].startswith("ผู้เสียหาย: ")  # in the order first called (c[5] at 08:00)
    assert summary[first:first + 7] == [
        "ผู้เสียหาย: นางสาวทดสอบ หนึ่ง", f"- เลขรับแจ้ง: {c0.receipt_no}",
        f"- มูลค่าความเสียหาย: {f'{c0.damage_amount:,.2f} บาท' if c0.damage_amount is not None else '-'}",
        f"- พนักงานสอบสวน: {c0.investigator_raw or '-'}", "- เบอร์โทรศัพท์ผู้เสียหาย: 0811111111",
        "- บันทึกการติดต่อ: ไม่ประสงค์ดำเนินคดี วันที่ 9 พ.ย. 2569 เวลา 14.00 น.", ""]
    assert sum(t.startswith("ผู้เสียหาย:") for t in summary) == 7  # every case called that day
    mine = client.get("/reports/daily/", {"date": "2026-11-09", "caller": caller.pk}).context
    assert mine["counts"]["total"] == 6 and mine["caller"] == caller


def test_summary_word_file_has_only_the_cases_called_that_day(client, caller, day_calls):
    """สรุปข้อมูลคดี is daily: calls of the day before or after are not in it."""
    from docx import Document

    client.force_login(caller)
    resp = client.get("/reports/daily/", {"date": "2026-11-09", "format": "summary", "from": "2026-11-01"})
    text = "\n".join(p.text for p in Document(BytesIO(resp.content)).paragraphs)
    assert sum(line.startswith("ผู้เสียหาย:") for line in text.split("\n")) == 7
    assert day_calls[7].receipt_no not in text and day_calls[8].receipt_no not in text
    assert "วันที่ 8 พ.ย." not in text and "วันที่ 10 พ.ย." not in text


def test_contact_log_lines_in_the_registers_words(day_calls):
    rows = {r.case.pk: r for r in report.load(DAY).rows}
    c = day_calls
    assert report.log_line(rows[c[0].pk].attempt) == "ไม่ประสงค์ดำเนินคดี วันที่ 9 พ.ย. 2569 เวลา 14.00 น."
    assert report.log_line(rows[c[2].pk].attempt) == "ประสงค์ดำเนินคดี วันที่ 9 พ.ย. 2569 เวลา 10.30 น."
    assert report.log_line(rows[c[4].pk].attempt) == "ครั้งที่ 1 ไม่สามารถติดต่อได้ (ปิดเครื่อง) วันที่ 9 พ.ย. 2569 เวลา 11.30 น."
    assert report.log_line(rows[c[5].pk].attempt) == (
        "ครั้งที่ 2 ไม่สามารถติดต่อได้ (เบอร์ไม่ถูกต้อง / ไม่มีผู้ใช้เบอร์นี้) วันที่ 9 พ.ย. 2569 เวลา 16.00 น.")
    assert report.log_line(rows[c[6].pk].attempt) == "ติดต่อได้ (ขอให้โทรกลับภายหลัง) วันที่ 9 พ.ย. 2569 เวลา 15.00 น."


def test_page_with_bad_or_future_dates(client, caller, imported):
    client.force_login(caller)
    assert client.get("/reports/daily/", {"date": "2026-13-45"}).context["day"] == timezone.localdate()
    future = timezone.localdate() + dt.timedelta(days=3)
    assert client.get("/reports/daily/", {"date": future.isoformat()}).context["day"] == timezone.localdate()
    empty = client.get("/reports/daily/", {"date": "2020-01-01", "caller": "x"})
    assert empty.status_code == 200 and "ยังไม่มีการบันทึกผลการโทร" in empty.content.decode()
    assert client.get("/calls/").content.decode().count('href="/reports/daily/"') >= 1


def test_only_admins_send_to_line(client, caller):
    client.force_login(caller)
    assert client.post("/reports/daily/").status_code == 403


# --------------------------------------------------------------------------- LINE bot
@pytest.fixture
def line(settings, monkeypatch):
    """LINE configured; every API call is recorded instead of sent."""
    settings.LINE_CHANNEL_ACCESS_TOKEN, settings.LINE_CHANNEL_SECRET = "token", "secret"
    sent = []

    def fake_call(method, path, payload=None):
        sent.append((method, path, payload))
        if path.endswith("/summary"):
            return {"groupName": "กก.1 ห้องรายงาน"}
        return {}

    monkeypatch.setattr(line_bot, "_call", fake_call)
    return sent


def signed(client, payload, secret="secret"):
    body = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.post("/line/webhook/", body, content_type="application/json", HTTP_X_LINE_SIGNATURE=sig)


def test_line_is_off_without_settings(client, settings):
    settings.LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_SECRET = ""
    assert not line_bot.configured()
    assert client.post("/line/webhook/", b"{}", content_type="application/json").status_code == 404
    assert line_bot.due() is None
    with pytest.raises(line_bot.LineError):
        line_bot.send_day(DAY)


def test_webhook_needs_a_valid_signature(client, line):
    assert client.post("/line/webhook/", b'{"events": []}', content_type="application/json").status_code == 403
    assert signed(client, {"events": []}, secret="wrong").status_code == 403
    assert signed(client, {"events": []}).status_code == 200  # LINE's "Verify" button
    assert not line


def test_group_is_used_only_after_an_admin_confirms_it(client, admin_user, line, day_calls):
    join = {"type": "join", "replyToken": "r1", "source": {"type": "group", "groupId": "Cgroup1"}}
    signed(client, {"events": [join]})
    s = SystemSetting.get()
    assert s.line_pending_group_id == "Cgroup1" and s.line_group_name == "" and s.line_group_id == ""
    assert line[-1][1] == "/message/reply" and "รอผู้ดูแลระบบ" in line[-1][2]["messages"][0]["text"]
    # a command from a group that is not confirmed is ignored
    line.clear()
    signed(client, {"events": [{"type": "message", "replyToken": "r2", "message": {"type": "text", "text": "สรุป"},
                                "source": {"type": "group", "groupId": "Cgroup1"}}]})
    assert not line
    client.force_login(admin_user)
    assert "กก.1 ห้องรายงาน" in client.get("/settings/line/").content.decode()
    client.post("/settings/line/", {"action": "confirm"})
    s.refresh_from_db()
    assert s.line_group_id == "Cgroup1" and s.line_group_name == "กก.1 ห้องรายงาน" and not s.line_pending_group_id
    # now the group can ask (a reply: free)
    signed(client, {"events": [{"type": "message", "replyToken": "r3",
                                "message": {"type": "text", "text": "สรุป 9/11/69"},
                                "source": {"type": "group", "groupId": "Cgroup1"}}]})
    method, path, payload = line[-1]
    assert path == "/message/reply" and payload["replyToken"] == "r3"
    assert "จำนวน 7 Case ID" in payload["messages"][0]["text"] and len(payload["messages"]) == 1  # no names
    # the bot is removed from the group
    signed(client, {"events": [{"type": "leave", "source": {"type": "group", "groupId": "Cgroup1"}}]})
    s.refresh_from_db()
    assert s.line_group_id == ""


def test_group_found_by_a_message_when_the_join_was_missed(client, line):
    """Invited before the webhook was set up: typing anything in the group makes it show up for confirming."""
    message = {"type": "message", "replyToken": "r1", "message": {"type": "text", "text": "ทดสอบ"},
               "source": {"type": "group", "groupId": "Cgroup2", "userId": "U1"}}
    signed(client, {"events": [message]})
    s = SystemSetting.get()
    assert s.line_pending_group_id == "Cgroup2" and s.line_pending_group_name == "กก.1 ห้องรายงาน"
    assert not s.line_group_id and not [x for x in line if x[1] == "/message/reply"]  # nothing said in the group
    # a message from another group does not replace the one waiting
    signed(client, {"events": [dict(message, source={"type": "group", "groupId": "Cother"})]})
    assert SystemSetting.get().line_pending_group_id == "Cgroup2"


def test_push_sends_counts_and_the_list_only_when_allowed(line, day_calls):
    setting(line_group_id="Cgroup1")
    assert line_bot.send_day(DAY) == 7
    method, path, payload = line[-1]
    assert path == "/message/push" and payload["to"] == "Cgroup1" and len(payload["messages"]) == 1
    assert day_calls[0].receipt_no not in json.dumps(payload, ensure_ascii=False)
    setting(line_include_list=True)
    line_bot.send_day(DAY)
    texts = [m["text"] for m in line[-1][2]["messages"]]
    assert len(texts) == 2 and day_calls[0].receipt_no in texts[1]


def test_daily_push_once_after_the_set_time(line, day_calls):
    setting(line_group_id="Cgroup1", line_send_time=dt.time(18, 0))
    assert line_bot.send_if_due(at(DAY, 19)) == "not due"  # switched off
    setting(line_enabled=True)
    assert line_bot.send_if_due(at(DAY, 17, 59)) == "not due"
    assert line_bot.send_if_due(at(DAY, 18, 5)) == "sent"
    assert SystemSetting.get().line_last_sent == DAY
    assert line_bot.send_if_due(at(DAY, 18, 10)) == "not due"  # once a day
    pushes = [x for x in line if x[1] == "/message/push"]
    assert len(pushes) == 1
    # a day without calls: nothing sent (saves the quota)
    quiet = DAY + dt.timedelta(days=5)
    assert line_bot.send_if_due(at(quiet, 18, 5)) == "no calls"
    assert len([x for x in line if x[1] == "/message/push"]) == 1


def test_daily_push_error_is_shown_and_retried(line, day_calls, monkeypatch, client, admin_user):
    setting(line_group_id="Cgroup1", line_enabled=True)

    def down(*a, **k):
        raise line_bot.LineError("ติดต่อ LINE ไม่ได้: timeout")

    monkeypatch.setattr(line_bot, "push", down)
    assert line_bot.send_if_due(at(DAY, 18, 5)).startswith("error")
    s = SystemSetting.get()
    assert s.line_last_sent is None and "timeout" in s.line_last_error
    client.force_login(admin_user)
    assert "timeout" in client.get("/settings/line/").content.decode()
    monkeypatch.undo()
    monkeypatch.setattr(line_bot, "_call", lambda *a, **k: {})
    assert line_bot.send_if_due(at(DAY, 18, 10)) == "sent"  # tried again on the next run
    assert SystemSetting.get().line_last_error == ""


def test_admin_sends_from_the_daily_page(client, admin_user, line, day_calls):
    from apps.core.models import AuditLog

    client.force_login(admin_user)
    assert "ตั้งค่าบอท LINE" in client.get("/reports/daily/").content.decode()  # no group yet
    setting(line_group_id="Cgroup1", line_group_name="กก.1 ห้องรายงาน")
    assert "ส่งเข้ากลุ่ม LINE" in client.get("/reports/daily/").content.decode()
    resp = client.post("/reports/daily/?date=2026-11-09")
    assert resp.status_code == 302 and "date=2026-11-09" in resp["Location"]
    assert line[-1][1] == "/message/push"
    assert AuditLog.objects.filter(detail__contains="เข้ากลุ่ม LINE").exists()


def test_line_settings_page(client, admin_user, settings, line):
    client.force_login(admin_user)
    html = client.get("/settings/line/").content.decode()
    assert "http://testserver/line/webhook/" in html and "ส่งอัตโนมัติ" in html
    resp = client.post("/settings/line/", {"action": "save", "line_enabled": "on", "line_send_time": "17:30",
                                           "line_skip_empty": "on"})
    assert resp.status_code == 302
    s = SystemSetting.get()
    assert s.line_enabled and s.line_send_time == dt.time(17, 30) and not s.line_include_list
    settings.LINE_CHANNEL_ACCESS_TOKEN = ""
    assert "ยังไม่ได้เชื่อมต่อ LINE" in client.get("/settings/line/").content.decode()


@pytest.mark.parametrize("text, expected", [
    ("สรุป", DAY), ("สรุปวันนี้", DAY), (" สรุป เมื่อวาน ", DAY - dt.timedelta(days=1)),
    ("สรุป 5/11", dt.date(2026, 11, 5)), ("สรุป ๕/๑๑/๒๕๖๙", dt.date(2026, 11, 5)),
    ("สรุป 31/12", dt.date(2025, 12, 31)),  # a date ahead of today means last year
    ("สรุป 31/2", None), ("สวัสดี", None), ("สรุปอะไร", None),
])
def test_commands_typed_in_the_group(text, expected):
    assert line_bot.asked_day(text, DAY) == expected


def test_command_line_print(day_calls, capsys):
    from django.core.management import call_command

    call_command("send_line_report", "--date", "2026-11-09", "--print")
    assert "จำนวน 7 Case ID" in capsys.readouterr().out
