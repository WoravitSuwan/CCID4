"""LINE bot that posts the daily summary (สรุปผลประจำวัน) to the unit's LINE group.

Off until an administrator enters the channel secret and access token in ตั้งค่า > บอท LINE (kept in the
database, never in git), or LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN are set in .env.
This is the only part of the system that talks to an outside service (api.line.me), so it sends only the
counts unless an administrator also turns on "ส่งรายชื่อ".

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


def from_env() -> bool:
    return bool(settings.LINE_CHANNEL_ACCESS_TOKEN and settings.LINE_CHANNEL_SECRET)


def credentials() -> tuple[str, str]:
    """(secret, access token): from .env if set there, else as entered on the settings page."""
    if from_env():
        return settings.LINE_CHANNEL_SECRET, settings.LINE_CHANNEL_ACCESS_TOKEN
    s = SystemSetting.get()
    return s.line_channel_secret.strip(), s.line_access_token.strip()


def configured() -> bool:
    return all(credentials())


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {credentials()[1]}",
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


def _pieces(text: str, limit: int = MAX_TEXT) -> list[str]:
    """A long text in pieces of at most `limit` characters, cut between cases (blank lines), else between lines."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sep in ("\n\n", "\n"):
        parts = text.split(sep)
        if all(len(p) <= limit for p in parts):
            for p in parts:
                if cur and len(cur) + len(sep) + len(p) > limit:
                    out.append(cur)
                    cur = p
                else:
                    cur = f"{cur}{sep}{p}" if cur else p
            return out + ([cur] if cur else [])
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _messages(texts: list[str]) -> list[dict]:
    return [{"type": "text", "text": piece} for text in texts for piece in _pieces(text)]


PER_REQUEST = 5  # LINE takes at most 5 messages in one request


def push(to: str, texts: list[str]) -> int:
    """Send in order, 5 messages per request. Returns the number of requests (each one counts against the quota
    once per member of the group)."""
    msgs = _messages(texts)
    for i in range(0, len(msgs), PER_REQUEST):
        _call("POST", "/message/push", {"to": to, "messages": msgs[i:i + PER_REQUEST]})
    return (len(msgs) + PER_REQUEST - 1) // PER_REQUEST


def reply(token: str, texts: list[str], group: str = "") -> None:
    """Answer a command (free); what does not fit in one answer follows as a push to the group."""
    msgs = _messages(texts)
    _call("POST", "/message/reply", {"replyToken": token, "messages": msgs[:PER_REQUEST]})
    rest = msgs[PER_REQUEST:]
    for i in range(0, len(rest), PER_REQUEST) if group else ():
        _call("POST", "/message/push", {"to": group, "messages": rest[i:i + PER_REQUEST]})


def group_name(group_id: str) -> str:
    try:
        return _call("GET", f"/group/{group_id}/summary").get("groupName", "")
    except LineError:
        return ""


def bot_name() -> str:
    """The bot's display name; raises LineError when the token is wrong (used to check a new token)."""
    return _call("GET", "/info").get("displayName", "")


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
    secret = credentials()[0]
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


# --------------------------------------------------------------------------- what is sent
FILE_KINDS = {"diary": "ลงบันทึกประจำวัน", "summary": "สรุปข้อมูลคดี"}
FILE_SALT = "line-file"


def remember_site(request, https: bool = False) -> None:
    """Keep the address people use (for the links in LINE messages, which are sent without a request)."""
    if settings.SITE_URL:
        return
    host = request.get_host()
    url = f"{'https' if https or request.is_secure() else 'http'}://{host}"
    if host.split(":")[0] in ("localhost", "127.0.0.1", "testserver") and not https:
        return
    s = SystemSetting.get()
    if s.line_site_url != url:
        SystemSetting.objects.filter(pk=s.pk).update(line_site_url=url)


def site_url() -> str:
    return (settings.SITE_URL or SystemSetting.get().line_site_url).rstrip("/")


def file_token(day: dt.date, kind: str) -> str:
    from django.core import signing

    return signing.TimestampSigner(salt=FILE_SALT).sign_object({"d": day.isoformat(), "k": kind})


def read_file_token(token: str) -> tuple[dt.date, str]:
    """(day, kind); raises django.core.signing.BadSignature / SignatureExpired."""
    from django.core import signing

    days = SystemSetting.get().line_link_days or 7
    data = signing.TimestampSigner(salt=FILE_SALT).unsign_object(token, max_age=dt.timedelta(days=days))
    if data.get("k") not in FILE_KINDS:
        raise signing.BadSignature("kind")
    return dt.date.fromisoformat(data["d"]), data["k"]


def texts_for(day: dt.date, with_texts: bool | None = None) -> list[str]:
    """What the bot sends, as separate text messages: 1) the report for the commanders, and (unless turned off)
    2) the ลงบันทึกประจำวัน text 3) the สรุปข้อมูลคดี text."""
    from apps.core.thai_format import thai_date_full

    s = SystemSetting.get()
    d = report.load(day)
    texts = [report.summary_text(d)]
    if s.line_send_files if with_texts is None else with_texts:
        texts.append(report.diary_text(d) if d.not_want
                     else f"ลงบันทึกประจำวัน: ไม่มีผู้เสียหายที่แจ้งไม่ประสงค์ดำเนินคดี{report.period_text(d)}")
        texts.append(report.summary_file_text(d) if d.rows
                     else f"สรุปข้อมูลคดีและบันทึกการติดต่อ: ไม่มีคดีที่โทรวันที่ "
                          f"{thai_date_full(d.day, thai_digits=False)}")
    return texts


def send_day(day: dt.date) -> int:
    """Push the summary of one day to the confirmed group. Returns the number of cases in it."""
    s = SystemSetting.get()
    if not configured():
        raise LineError("ยังไม่ได้ใส่รหัสเชื่อมต่อ LINE (ตั้งค่า > บอท LINE)")
    if not s.line_group_id:
        raise LineError("ยังไม่ได้เลือกกลุ่ม LINE (เชิญบอทเข้ากลุ่มแล้วกดยืนยันที่หน้าตั้งค่า LINE)")
    push(s.line_group_id, texts_for(day))
    return report.load(day).counts["total"]


def scheduled_at(now: dt.datetime | None = None) -> dt.datetime:
    """Today's send time."""
    now = timezone.localtime(now)
    return timezone.make_aware(dt.datetime.combine(now.date(), SystemSetting.get().line_send_time),
                               timezone.get_current_timezone())


def due(now: dt.datetime | None = None) -> dt.date | None:
    """The day whose summary should be pushed now, or None. Once per send time: if the time is moved later
    in the day after a send, it is sent again at the new time."""
    s = SystemSetting.get()
    if not (s.line_enabled and configured() and s.line_group_id):
        return None
    now = timezone.localtime(now)
    at = scheduled_at(now)
    if now < at or (s.line_last_sent_at and s.line_last_sent_at >= at):
        return None
    return now.date()


def send_if_due(now: dt.datetime | None = None, worker: bool = False) -> str:
    """Run every few minutes: by Celery beat (Docker), cron (manage.py send_line_report --if-due), and by the
    web pages themselves as a fallback. Whoever comes first sends; the others see it done (no double send)."""
    from django.db.models import Q

    from apps.core.audit import log as audit
    from apps.core.models import AuditAction

    now = timezone.localtime(now)
    seen = {"line_checked_at": now} | ({"line_worker_seen_at": now} if worker else {})
    SystemSetting.objects.filter(pk=SystemSetting.get().pk).update(**seen)
    day = due(now)
    if day is None:
        return "not due"
    at = scheduled_at(now)
    s = SystemSetting.get()
    # claim this send time (one process only)
    claimed = SystemSetting.objects.filter(pk=s.pk).filter(
        Q(line_last_sent_at__isnull=True) | Q(line_last_sent_at__lt=at)).update(line_last_sent_at=now)
    if not claimed:
        return "not due"
    stamp = f"{now:%d/%m %H:%M}"
    if s.line_skip_empty and not report.load(day).rows:
        SystemSetting.objects.filter(pk=s.pk).update(
            line_last_result=f"{stamp} ไม่ได้ส่ง: วันนี้ยังไม่มีการบันทึกผลการโทร (ตั้งไว้ไม่ส่งในวันที่ไม่มีการโทร)",
            line_last_error="")
        return "no calls"
    try:
        total = send_day(day)
    except LineError as exc:
        # give the time back so the next run tries again
        SystemSetting.objects.filter(pk=s.pk).update(line_last_sent_at=s.line_last_sent_at,
                                                     line_last_error=f"{stamp} {exc}",
                                                     line_last_result=f"{stamp} ส่งไม่สำเร็จ: {exc}")
        log.warning("LINE daily summary not sent: %s", exc)
        return f"error: {exc}"
    SystemSetting.objects.filter(pk=s.pk).update(line_last_error="",
                                                 line_last_result=f"{stamp} ส่งแล้ว ({total:,} Case)")
    audit(None, AuditAction.EXPORT, None, f"ส่งสรุปผลประจำวัน {report.file_day(day)} เข้ากลุ่ม LINE "
                                          f"({total:,} Case, อัตโนมัติ)")
    return "sent"


def auto_status(now: dt.datetime | None = None) -> dict:
    """For the settings and daily pages: next send time, what happened last, and whether anything is checking."""
    s = SystemSetting.get()
    now = timezone.localtime(now)
    at = scheduled_at(now)
    done_today = bool(s.line_last_sent_at and s.line_last_sent_at >= at)
    nxt = at if now < at or not done_today else at + dt.timedelta(days=1)
    if now >= at and not done_today:
        nxt = None  # due now: the next check sends it
    stale = s.line_enabled and (not s.line_checked_at or now - s.line_checked_at > dt.timedelta(minutes=15))
    # the server's own job checks every minute; without it, sending needs a page of the system open somewhere
    worker_ok = bool(s.line_worker_seen_at and now - s.line_worker_seen_at <= dt.timedelta(minutes=10))
    return {"enabled": s.line_enabled, "ready": configured() and bool(s.line_group_id), "next": nxt,
            "due_now": s.line_enabled and nxt is None, "result": s.line_last_result, "checked": s.line_checked_at,
            "stale": stale, "time": s.line_send_time, "worker_ok": worker_ok, "worker_seen": s.line_worker_seen_at}


_last_web_check = 0.0


def check_from_web() -> None:
    """Fallback when no background worker runs: any page visit (the daily page asks every 10 s) checks, at most
    once a minute per web process, and sends in a thread so the page does not wait."""
    import threading
    import time

    global _last_web_check
    if time.monotonic() - _last_web_check < 60:
        return
    _last_web_check = time.monotonic()
    try:
        if due() is None:
            return
    except Exception:  # the database is not ready (first start, migrations): try again later
        return
    from apps.core.background import start_thread

    if threading.active_count() < 50:
        start_thread(send_if_due)


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
                    reply(token, texts_for(day), group=group)
        except LineError as exc:
            log.warning("LINE reply failed: %s", exc)
