"""สรุปผลประจำวัน, ข้อความลงบันทึกประจำวัน, and the optional LINE bot (LINE itself is never called in tests)."""
import base64
import datetime as dt
import hashlib
import hmac
import json

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
        "เรียนผู้บังคับบัญชา",
        "",
        "ขอรายงานสรุปผลการดำเนินการ ของ เจ้าหน้าที่ Outsource สะสาง Case ID ประจำวันที่ 9 พฤศจิกายน 2569",
        "จำนวน 7 Case ID",
        "1. ไม่ประสงค์ดำเนินคดี จำนวน 2 Case",
        "2. ประสงค์ดำเนินคดีต่อ จำนวน 2 Case",
        "3. ไม่สามารถติดต่อได้ (รวมถึง ไม่รับสาย, ปิดเครื่อง, ไม่มีหมายเลข, ปลายสายไม่รู้จัก ฯลฯ) จำนวน 2 Case",
        "4. ติดต่อได้ แต่ยังไม่ได้ข้อยุติ (ขอให้โทรกลับ, อื่นๆ) จำนวน 1 Case",  # so it adds up to 7
        "",
        "จึงเรียนมาเพื่อโปรดทราบ",
    ]
    assert day_calls[0].receipt_no not in text and "0811111111" not in text  # counts only


def test_summary_of_a_day_without_calls_is_the_units_text_exactly(db):
    assert report.summary_text(report.load(dt.date(2026, 11, 18))) == (
        "เรียนผู้บังคับบัญชา\n"
        "\n"
        "ขอรายงานสรุปผลการดำเนินการ ของ เจ้าหน้าที่ Outsource สะสาง Case ID ประจำวันที่ 18 พฤศจิกายน 2569\n"
        "จำนวน 0 Case ID\n"
        "1. ไม่ประสงค์ดำเนินคดี จำนวน 0 Case\n"
        "2. ประสงค์ดำเนินคดีต่อ จำนวน 0 Case\n"
        "3. ไม่สามารถติดต่อได้ (รวมถึง ไม่รับสาย, ปิดเครื่อง, ไม่มีหมายเลข, ปลายสายไม่รู้จัก ฯลฯ) จำนวน 0 Case\n"
        "\n"
        "จึงเรียนมาเพื่อโปรดทราบ")


def test_summary_without_open_cases_has_three_lines(imported, caller):
    ensure_call_pool()
    case = Case.objects.first()
    record_call(case, caller, outcome=O.OFF, called_at=at(DAY, 9))
    text = report.summary_text(report.load(DAY))
    assert "จำนวน 1 Case ID" in text and "4." not in text


def lines_of(resp) -> list[str]:
    """The lines of a downloaded .txt file (UTF-8 with a BOM, Windows line endings)."""
    raw = resp.content
    assert raw.startswith("\ufeff".encode()) and b"\r\n" in raw and resp["Content-Type"].startswith("text/plain")
    return raw.decode("utf-8-sig").split("\r\n")


def test_page_texts_and_text_files(client, caller, day_calls):
    client.force_login(caller)  # call staff use it every day
    page = client.get("/reports/daily/", {"date": "2026-11-09"})
    assert page.status_code == 200
    html = page.content.decode()
    assert "วันนี้( 9 พ.ย.69)" in html and "จำนวน 7 Case ID" in html and 'data-copy="#t-diary"' in html
    assert "ยังส่งไม่ได้: ยังไม่ได้ใส่รหัสเชื่อมต่อ LINE" in html and "แจ้งผู้ดูแลระบบ" in html  # not set up
    diary = client.get("/reports/daily/", {"date": "2026-11-09", "format": "diary"})
    assert "%E0%B8%A5%E0%B8%87%E0%B8%9A%E0%B8%B1%E0%B8%99%E0%B8%97%E0%B8%B6%E0%B8%81" in diary["Content-Disposition"]
    assert "9.11.2569.txt" in diary["Content-Disposition"]
    # same layout as the file the staff made by hand: one line each, a blank line between
    c0, c1 = day_calls[0], day_calls[1]
    assert lines_of(diary) == [
        f"ผู้เสียหาย: นางสาวทดสอบ หนึ่ง เลขรับแจ้ง: {c0.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: 0811111111", "",
        f"ผู้เสียหาย: นายทดสอบ สอง เลขรับแจ้ง: {c1.receipt_no} เบอร์โทรศัพท์ผู้เสียหาย: 0899999999", ""]
    resp = client.get("/reports/daily/", {"date": "2026-11-09", "format": "summary"})
    assert "9.11.2569.txt" in resp["Content-Disposition"]
    summary = lines_of(resp)
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


def test_summary_text_file_has_only_the_cases_called_that_day(client, caller, day_calls):
    """สรุปข้อมูลคดี is daily: calls of the day before or after are not in it."""
    client.force_login(caller)
    resp = client.get("/reports/daily/", {"date": "2026-11-09", "format": "summary", "from": "2026-11-01"})
    text = "\n".join(lines_of(resp))
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


def test_call_staff_send_to_line_too(client, caller, line, day_calls):
    setting(line_group_id="Cgroup1")
    client.force_login(caller)
    html = client.get("/reports/daily/", {"date": "2026-11-09"}).content.decode()
    assert "ส่งสรุปผลวันที่ 9 พ.ย. 2569 ตอนนี้" in html and "ยังส่งไม่ได้" not in html
    resp = client.post("/reports/daily/?date=2026-11-09")
    assert resp.status_code == 302 and line[-1][1] == "/message/push"
    assert "จำนวน 7 Case ID" in line[-1][2]["messages"][0]["text"]


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
    assert "จำนวน 7 Case ID" in payload["messages"][0]["text"]
    assert [m["type"] for m in payload["messages"]] == ["text", "flex"]  # the counts, then the files card
    assert day_calls[0].receipt_no not in str(payload)  # no names in the chat
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
    s = SystemSetting.get()
    assert s.line_last_sent_at == at(DAY, 18, 5) and "ส่งแล้ว (7 Case)" in s.line_last_result
    assert s.line_checked_at == at(DAY, 18, 5)
    assert line_bot.send_if_due(at(DAY, 18, 10)) == "not due"  # once per send time
    pushes = [x for x in line if x[1] == "/message/push"]
    assert len(pushes) == 1
    # the next day, at the time again
    assert line_bot.send_if_due(at(DAY + dt.timedelta(days=1), 18, 1)) == "sent"  # (one call that day)
    # a day without calls: nothing sent (saves the quota), and the page says why
    quiet = DAY + dt.timedelta(days=5)
    assert line_bot.send_if_due(at(quiet, 18, 5)) == "no calls"
    assert "ยังไม่มีการบันทึกผลการโทร" in SystemSetting.get().line_last_result
    assert len([x for x in line if x[1] == "/message/push"]) == 2


def test_time_moved_later_the_same_day_sends_again(line, day_calls):
    """What happened on 18 Nov: sent (or skipped) at 10:41, then the time was changed to 16:30 - it must still
    go out at 16:30."""
    setting(line_group_id="Cgroup1", line_enabled=True, line_send_time=dt.time(10, 41))
    assert line_bot.send_if_due(at(DAY, 10, 45)) == "sent"
    setting(line_send_time=dt.time(16, 30))
    assert line_bot.send_if_due(at(DAY, 16, 0)) == "not due"
    assert line_bot.send_if_due(at(DAY, 16, 31)) == "sent"
    assert len([x for x in line if x[1] == "/message/push"]) == 2


def test_two_checkers_at_once_send_one_message(line, day_calls, monkeypatch):
    """The worker and a web page may check at the same moment: only one of them sends."""
    setting(line_group_id="Cgroup1", line_enabled=True, line_send_time=dt.time(16, 30))
    real_due = line_bot.due
    monkeypatch.setattr(line_bot, "due", lambda now=None: DAY)  # both see it due ...
    assert line_bot.send_if_due(at(DAY, 16, 31)) == "sent"
    assert line_bot.send_if_due(at(DAY, 16, 31)) == "not due"  # ... the second finds it claimed
    monkeypatch.setattr(line_bot, "due", real_due)
    assert len([x for x in line if x[1] == "/message/push"]) == 1


def test_web_pages_check_when_no_worker_runs(client, caller, line, day_calls, settings, monkeypatch):
    import threading

    settings.LINE_WEB_CHECK = True
    setting(line_group_id="Cgroup1", line_enabled=True, line_send_time=dt.time(0, 0))
    monkeypatch.setattr(line_bot, "_last_web_check", 0.0)
    started = []
    monkeypatch.setattr("apps.core.background.start_thread", lambda f, *a: started.append(f))
    client.force_login(caller)
    client.get("/reports/daily/", {"format": "stamp"})
    assert started == [line_bot.send_if_due]
    client.get("/reports/daily/", {"format": "stamp"})
    assert len(started) == 1  # at most once a minute
    assert threading.active_count() >= 1


def test_settings_page_shows_the_automatic_send(client, admin_user, line, day_calls):
    setting(line_group_id="Cgroup1", line_enabled=True, line_send_time=dt.time(16, 30))
    client.force_login(admin_user)
    html = client.get("/settings/line/").content.decode()
    assert "ส่งครั้งต่อไป" in html and "ยังไม่เคยส่งอัตโนมัติ" in html
    assert "งานส่งอัตโนมัติบนเซิร์ฟเวอร์ (worker) ไม่ได้ทำงาน" in html  # nothing has checked yet
    line_bot.send_if_due()  # a page of the system checking does not count: the computers could be off
    assert "(worker) ไม่ได้ทำงาน" in client.get("/settings/line/").content.decode()
    from apps.calls.tasks import line_daily_summary
    line_daily_summary()  # the server's own job (Celery beat, every minute)
    html = client.get("/settings/line/").content.decode()
    assert "ไม่ได้ทำงาน" not in html and "ส่งได้แม้ปิดคอมทุกเครื่อง" in html
    assert "เซิร์ฟเวอร์ส่งเองตามเวลา แม้ปิดคอมทุกเครื่อง" in client.get("/reports/daily/").content.decode()


def test_cron_command_counts_as_the_server_job(line, day_calls):
    from django.core.management import call_command

    setting(line_group_id="Cgroup1", line_enabled=True)
    call_command("send_line_report", "--if-due")
    assert line_bot.auto_status()["worker_ok"]


def test_daily_push_error_is_shown_and_retried(line, day_calls, monkeypatch, client, admin_user):
    setting(line_group_id="Cgroup1", line_enabled=True)

    def down(*a, **k):
        raise line_bot.LineError("ติดต่อ LINE ไม่ได้: timeout")

    monkeypatch.setattr(line_bot, "push", down)
    assert line_bot.send_if_due(at(DAY, 18, 5)).startswith("error")
    s = SystemSetting.get()
    assert s.line_last_sent_at is None and "timeout" in s.line_last_error and "ส่งไม่สำเร็จ" in s.line_last_result
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
    assert "ยังส่งไม่ได้" not in client.get("/reports/daily/").content.decode()
    resp = client.post("/reports/daily/?date=2026-11-09")
    assert resp.status_code == 302 and "date=2026-11-09" in resp["Location"]
    assert line[-1][1] == "/message/push"
    assert AuditLog.objects.filter(detail__contains="เข้ากลุ่ม LINE").exists()


def test_line_settings_page(client, admin_user, settings, line):
    client.force_login(admin_user)
    html = client.get("/settings/line/").content.decode()
    assert "http://testserver/line/webhook/" in html and "ส่งอัตโนมัติ" in html
    resp = client.post("/settings/line/", {"action": "save", "line_enabled": "on", "line_send_time": "17:30",
                                           "line_link_days": "7", "line_skip_empty": "on"})
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


def test_keys_entered_on_the_settings_page(client, admin_user, settings, monkeypatch):
    """No .env needed: an administrator pastes the secret and token on the page; they are never shown again."""
    settings.LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_SECRET = ""
    calls = []

    def fake_call(method, path, payload=None):
        calls.append(path)
        return {"displayName": "สรุปผลการโทร กก.1"}

    monkeypatch.setattr(line_bot, "_call", fake_call)
    client.force_login(admin_user)
    assert not line_bot.configured()
    resp = client.post("/settings/line/", {"action": "keys", "secret": " abc123 \n", "token": ""}, follow=True)
    assert "ยังต้องใส่อีกช่อง" in resp.content.decode() and not line_bot.configured() and not calls
    resp = client.post("/settings/line/", {"action": "keys", "secret": "", "token": "tok-en\n"}, follow=True)
    html = resp.content.decode()
    assert "เชื่อมต่อบอท &quot;สรุปผลการโทร กก.1&quot; สำเร็จ" in html and calls == ["/info"]
    assert line_bot.credentials() == ("abc123", "tok-en")  # blank kept the secret; spaces removed
    assert "abc123" not in html and "tok-en" not in html  # never shown again
    # the webhook checks signatures with the saved secret
    assert signed(client, {"events": []}, secret="abc123").status_code == 200
    assert signed(client, {"events": []}, secret="secret").status_code == 403
    from apps.core.models import AuditLog
    assert not AuditLog.objects.filter(detail__contains="abc123").exists()
    # .env wins when set
    settings.LINE_CHANNEL_ACCESS_TOKEN, settings.LINE_CHANNEL_SECRET = "env-token", "env-secret"
    assert line_bot.credentials() == ("env-secret", "env-token")
    assert "ตั้งไว้ในไฟล์ .env" in client.get("/settings/line/").content.decode()
    settings.LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_SECRET = ""
    client.post("/settings/line/", {"action": "clear_keys"})
    assert not line_bot.configured()


def test_wrong_token_is_reported(client, admin_user, settings, monkeypatch):
    settings.LINE_CHANNEL_ACCESS_TOKEN = settings.LINE_CHANNEL_SECRET = ""

    def refuse(*a, **k):
        raise line_bot.LineError("LINE ตอบกลับ 401: Authentication failed")

    monkeypatch.setattr(line_bot, "_call", refuse)
    client.force_login(admin_user)
    resp = client.post("/settings/line/", {"action": "keys", "secret": "s", "token": "bad"}, follow=True)
    assert "LINE ไม่รับ access token นี้" in resp.content.decode()


def test_only_admins_enter_keys(client, caller):
    client.force_login(caller)
    resp = client.post("/settings/line/", {"action": "keys", "secret": "x", "token": "y"})
    assert resp.status_code in (302, 403) and not line_bot.configured()


def test_bot_sends_the_two_text_files_with_the_message(client, line, day_calls, settings):
    """LINE does not let bots attach files: a card with two buttons (signed links that expire) follows the text."""
    setting(line_group_id="Cgroup1")
    assert SystemSetting.get().line_send_files  # on by default
    line_bot.send_day(DAY)
    assert len(line[-1][2]["messages"]) == 1  # the system's address is not known yet: text only
    signed(client, {"events": []})  # LINE's request tells the system its public address
    assert SystemSetting.get().line_site_url == "https://testserver"
    line_bot.send_day(DAY)
    text, card = line[-1][2]["messages"]
    assert text["type"] == "text" and card["type"] == "flex" and "สรุปข้อมูลคดี" in card["altText"]
    buttons = card["contents"]["footer"]["contents"]
    assert [b["action"]["label"] for b in buttons] == ["📄 สรุปข้อมูลคดี", "📄 ลงบันทึกประจำวัน"]
    assert all(len(b["action"]["label"]) <= 20 for b in buttons)  # LINE's limit for a button label
    links = [b["action"]["uri"] for b in buttons]
    assert all(u.startswith("https://testserver/line/file/") for u in links)
    assert day_calls[0].receipt_no not in str(line[-1][2])  # names only inside the files
    client.logout()
    summary = client.get(links[0].replace("https://testserver", ""))  # no login needed
    assert lines_of(summary)[0] == "สรุปข้อมูลคดีและบันทึกการติดต่อ"
    diary = client.get(links[1].replace("https://testserver", ""))
    assert diary.status_code == 200 and "9.11.2569.txt" in diary["Content-Disposition"]
    rows = [line for line in lines_of(diary) if line]
    assert len(rows) == 2 and rows[0].startswith("ผู้เสียหาย: นางสาวทดสอบ หนึ่ง")
    # the files are there every day, even with nobody who does not want to proceed
    quiet = DAY + dt.timedelta(days=30)
    line_bot.send_day(quiet)
    empty = client.get(line[-1][2]["messages"][1]["contents"]["footer"]["contents"][1]["action"]["uri"]
                       .replace("https://testserver", ""))
    assert lines_of(empty)[0].startswith("ไม่มีผู้เสียหายที่แจ้งไม่ประสงค์ดำเนินคดี")
    from apps.core.models import AuditLog
    assert AuditLog.objects.filter(detail__contains="ผ่านลิงก์ใน LINE").count() == 3
    # a changed link does not work; an old one has expired
    assert client.get(links[0].replace("https://testserver", "")[:-3] + "xx/").status_code == 404
    import time

    from django.core import signing
    real = time.time
    signing.time.time = lambda: real() - 8 * 86400  # a link made 8 days ago (links last 7 days)
    try:
        old = line_bot.file_token(DAY, "diary")
    finally:
        signing.time.time = real
    assert client.get(f"/line/file/{old}/").status_code == 410


def test_site_address_from_env(settings, line):
    settings.SITE_URL = "https://callcenter.example.org/"
    assert line_bot.site_url() == "https://callcenter.example.org"


def test_page_updates_itself_when_a_call_is_recorded(client, caller, imported):
    """The page asks for a stamp every few seconds; it changes as soon as a call of the day is recorded."""
    client.force_login(caller)
    page = client.get("/reports/daily/")
    html = page.content.decode()
    assert 'data-live-url="?format=stamp"' in html and "อัปเดตยอดอัตโนมัติ" in html
    first = client.get("/reports/daily/", {"format": "stamp"})
    assert first["Cache-Control"] == "no-store"
    ensure_call_pool()
    record_call(Case.objects.first(), caller, outcome=O.NOT_PROCEED)
    second = client.get("/reports/daily/", {"format": "stamp"}).content
    assert second != first.content
    assert "จำนวน 1 Case ID" in client.get("/reports/daily/").content.decode()
    # an earlier day does not change any more: no polling
    old = client.get("/reports/daily/", {"date": "2026-01-05"}).content.decode()
    assert "data-live-url" not in old


def test_call_staff_see_the_line_box_and_set_the_automatic_send(client, caller, line, day_calls):
    """The box is always there for call staff: why it cannot send yet, the send button, the daily time."""
    from apps.core.models import AuditLog

    client.force_login(caller)
    html = client.get("/reports/daily/").content.decode()
    assert 'id="line"' in html and "ยังส่งไม่ได้: ยังไม่ได้ยืนยันกลุ่ม LINE" in html  # keys set, no group yet
    assert "ส่งอัตโนมัติทุกวัน" in html and 'name="line_send_time"' in html
    resp = client.post("/reports/daily/", {"action": "auto", "line_enabled": "on", "line_send_time": "16:30"})
    assert resp.status_code == 302 and resp["Location"].endswith("#line")
    s = SystemSetting.get()
    assert s.line_enabled and s.line_send_time == dt.time(16, 30)
    assert AuditLog.objects.filter(detail__contains="อัตโนมัติ: เปิด เวลา 16:30").exists()
    client.post("/reports/daily/", {"action": "auto", "line_send_time": "16:30"})  # unticked: off
    assert not SystemSetting.get().line_enabled
    assert client.post("/reports/daily/", {"action": "auto", "line_send_time": "25:99"}).status_code == 302
    assert SystemSetting.get().line_send_time == dt.time(16, 30)  # a wrong time changes nothing
    # once the group is confirmed: send buttons on the daily page and on the call list
    setting(line_group_id="Cgroup1")
    assert "ส่งสรุปเข้า LINE" in client.get("/calls/").content.decode()
    resp = client.post("/reports/daily/")
    assert resp.status_code == 302 and line[-1][1] == "/message/push"


def test_a_day_without_calls_can_be_sent_too(client, caller, line, imported):
    setting(line_group_id="Cgroup1")
    client.force_login(caller)
    html = client.get("/reports/daily/", {"date": "2020-01-01"}).content.decode()
    assert "disabled" not in html.split('id="line"')[1].split("</form>")[0]
    client.post("/reports/daily/?date=2020-01-01")
    assert "จำนวน 0 Case ID" in line[-1][2]["messages"][0]["text"]
