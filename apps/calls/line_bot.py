"""LINE bot that posts the daily summary (สรุปผลประจำวัน) to the unit's LINE group.

Off unless LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET are set in .env. This is the only part of the
system that talks to an outside service (api.line.me), so it sends only the counts unless an
administrator also turns on "ส่งรายชื่อ".

Quota: a push message to a group counts once per member of the group (a 15-person group uses 15 of the free
messages each day). Replies to a command typed in the group ("สรุป") do not count, so the group can
always ask for the numbers for free.

Setting up: the bot is added to the group -> LINE sends a "join" event to /line/webhook/ -> the group shows
up in ตั้งค่า > LINE as waiting -> an administrator confirms it. Only that group gets messages.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from apps.core.models import SystemSetting

from . import daily_report as report

log = logging.getLogger(__name__)
API = "https://api.line.me/v2/bot"
MAX_TEXT = 5000  # LINE's limit for one text message
HELP = ("คำสั่งที่พิมพ์ในกลุ่มได้ (ไม่เสียโควตา):\n"
        "• สรุป - สรุปผลการโทรวันนี้\n"
        "• สรุปเมื่อวาน - สรุปผลของเมื่อวาน\n"
        "• สรุป 9/11 - สรุปผลของวันที่ 9 เดือน 11")


class LineError(Exception):
    pass


def configured() -> bool:
    return bool(settings.LINE_CHANNEL_ACCESS_TOKEN and settings.LINE_CHANNEL_SECRET)


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=settings.LINE_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        try:
            detail = json.loads(detail).get("message", detail)
        except ValueError:
            pass
        raise LineError(f"LINE ตอบกลับ {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LineError(f"ติดต่อ LINE ไม่ได้: {getattr(exc, 'reason', exc)}") from exc
    return json.loads(body) if body.strip() else {}


def _messages(texts: list[str]) -> list[dict]:
    out = []
    for text in texts:
        while text:
            out.append({"type": "text", "text": text[:MAX_TEXT]})
            text = text[MAX_TEXT:]
    return out[:5]  # LINE takes at most 5 messages per request


def push(to: str, texts: list[str]) -> None:
    _call("POST", "/message/push", {"to": to, "messages": _messages(texts)})


def reply(token: str, texts: list[str]) -> None:
    _call("POST", "/message/reply", {"replyToken": token, "messages": _messages(texts)})


def group_name(group_id: str) -> str:
    try:
        return _call("GET", f"/group/{group_id}/summary").get("groupName", "")
    except LineError:
        return ""


def quota() -> dict | None:
    """{'limit': 300 or None, 'used': n} for this month, or None if LINE cannot be reached."""
    try:
        limit = _call("GET", "/message/quota")
        used = _call("GET", "/message/quota/consumption")
    except LineError:
        return None
    return {"limit": limit.get("value") if limit.get("type") == "limited" else None,
            "used": used.get("totalUsage", 0)}


def valid_signature(body: bytes, signature: str) -> bool:
    secret = settings.LINE_CHANNEL_SECRET
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


# --------------------------------------------------------------------------- what is sent
def texts_for(day: dt.date, with_list: bool | None = None) -> list[str]:
    s = SystemSetting.get()
    d = report.load(day)
    texts = [report.summary_text(d)]
    if (s.line_include_list if with_list is None else with_list) and d.not_want:
        texts.append(report.diary_text(d))
    return texts


def send_day(day: dt.date) -> int:
    """Push the summary of one day to the confirmed group. Returns the number of cases in it."""
    s = SystemSetting.get()
    if not configured():
        raise LineError("ยังไม่ได้ตั้งค่า LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET ในไฟล์ .env")
    if not s.line_group_id:
        raise LineError("ยังไม่ได้เลือกกลุ่ม LINE (เชิญบอทเข้ากลุ่มแล้วกดยืนยันที่หน้าตั้งค่า LINE)")
    push(s.line_group_id, texts_for(day))
    return report.load(day).counts["total"]


def due(now: dt.datetime | None = None) -> dt.date | None:
    """The day whose summary should be pushed now, or None."""
    s = SystemSetting.get()
    if not (s.line_enabled and configured() and s.line_group_id):
        return None
    now = timezone.localtime(now)
    if now.time() < s.line_send_time or s.line_last_sent == now.date():
        return None
    return now.date()


def send_if_due(now: dt.datetime | None = None) -> str:
    """Run every few minutes (Celery beat, or cron with manage.py send_line_report --if-due)."""
    from apps.core.audit import log as audit
    from apps.core.models import AuditAction

    day = due(now)
    if day is None:
        return "not due"
    s = SystemSetting.get()
    if s.line_skip_empty and not report.load(day).rows:
        SystemSetting.objects.filter(pk=s.pk).update(line_last_sent=day, line_last_error="")
        return "no calls"
    try:
        total = send_day(day)
    except LineError as exc:
        SystemSetting.objects.filter(pk=s.pk).update(line_last_error=f"{timezone.localtime():%d/%m %H:%M} {exc}")
        log.warning("LINE daily summary not sent: %s", exc)
        return f"error: {exc}"
    SystemSetting.objects.filter(pk=s.pk).update(line_last_sent=day, line_last_error="")
    audit(None, AuditAction.EXPORT, None, f"ส่งสรุปผลประจำวัน {report.file_day(day)} เข้ากลุ่ม LINE "
                                          f"({total:,} Case, อัตโนมัติ)")
    return "sent"


# --------------------------------------------------------------------------- webhook
def asked_day(text: str, today: dt.date) -> dt.date | None:
    """'สรุป' -> today, 'สรุปเมื่อวาน' -> yesterday, 'สรุป 9/11' -> 9 Nov; anything else -> None."""
    from apps.core.thai_format import to_arabic_digits

    text = to_arabic_digits(text.strip().replace(" ", ""))
    if not text.startswith("สรุป"):
        return None
    rest = text[len("สรุป"):]
    if rest in ("", "วันนี้"):
        return today
    if rest == "เมื่อวาน":
        return today - dt.timedelta(days=1)
    parts = rest.split("/")
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        day, month = int(parts[0]), int(parts[1])
        year = today.year
        if len(parts) == 3:
            year = int(parts[2])
            year = year + 2500 if year < 100 else year
            year = year - 543 if year > 2400 else year
        try:
            picked = dt.date(year, month, day)
        except ValueError:
            return None
        if len(parts) == 2 and picked > today:
            picked = picked.replace(year=year - 1)
        return picked
    return None


def handle_events(events: list[dict]) -> None:
    s = SystemSetting.get()
    for event in events:
        source = event.get("source") or {}
        group = source.get("groupId") or source.get("roomId") or ""
        token = event.get("replyToken")
        kind = event.get("type")
        try:
            if kind == "join" and group:
                if group != s.line_group_id:
                    s.line_pending_group_id, s.line_pending_group_name = group, group_name(group)
                    s.save(update_fields=["line_pending_group_id", "line_pending_group_name"])
                if token:
                    reply(token, ["สวัสดีครับ บอทระบบออกหมายเรียก " + settings.OWN_UNIT
                                  + ("\n" + HELP if group == s.line_group_id
                                     else "\nรอผู้ดูแลระบบกดยืนยันกลุ่มนี้ที่หน้า ตั้งค่า > LINE ก่อนนะครับ")])
            elif kind == "leave" and group:
                if group == s.line_group_id:
                    s.line_group_id = s.line_group_name = ""
                if group == s.line_pending_group_id:
                    s.line_pending_group_id = s.line_pending_group_name = ""
                s.save(update_fields=["line_group_id", "line_group_name", "line_pending_group_id",
                                      "line_pending_group_name"])
            elif (kind == "message" and group and group != s.line_group_id
                  and not s.line_pending_group_id):
                # the bot was invited before the webhook worked (the "join" was missed): any message in that
                # group puts it up for an administrator to confirm, as a join would have
                s.line_pending_group_id, s.line_pending_group_name = group, group_name(group)
                s.save(update_fields=["line_pending_group_id", "line_pending_group_name"])
            elif kind == "message" and token and group and group == s.line_group_id:
                text = (event.get("message") or {}).get("text", "")
                if text.strip() in ("help", "ช่วยเหลือ", "คำสั่ง"):
                    reply(token, [HELP])
                    continue
                day = asked_day(text, timezone.localdate())
                if day is not None:
                    reply(token, texts_for(day))
        except LineError as exc:
            log.warning("LINE reply failed: %s", exc)
