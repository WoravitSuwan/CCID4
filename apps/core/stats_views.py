"""Statistics dashboard: totals now, and what happened day by day over a chosen period."""
from __future__ import annotations

import datetime as dt
import io
import math

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.core import permissions as perms

User = get_user_model()
PRESETS = [("7", "7 วัน"), ("30", "30 วัน"), ("90", "90 วัน"), ("month", "เดือนนี้"), ("year", "ปีนี้")]
MAX_DAYS = 366

# chart geometry (SVG user units; the SVG scales to the card width)
CHART_W, CHART_H = 760, 230
PAD_L, PAD_R, PAD_T, PAD_B = 40, 8, 12, 28


def _period(request) -> tuple[dt.date, dt.date, str]:
    today = timezone.localdate()
    preset = request.GET.get("range", "30")
    start = end = None
    try:
        if request.GET.get("from"):
            start = dt.date.fromisoformat(request.GET["from"])
        if request.GET.get("to"):
            end = dt.date.fromisoformat(request.GET["to"])
    except ValueError:
        start = end = None
    if start or end:
        preset = "custom"
        end = end or today
        start = start or end - dt.timedelta(days=29)
    elif preset == "month":
        start, end = today.replace(day=1), today
    elif preset == "year":
        start, end = today.replace(month=1, day=1), today
    else:
        days = int(preset) if preset.isdigit() and int(preset) in (7, 30, 90) else 30
        preset = str(days)
        start, end = today - dt.timedelta(days=days - 1), today
    if start > end:
        start, end = end, start
    if (end - start).days >= MAX_DAYS:
        start = end - dt.timedelta(days=MAX_DAYS - 1)
    return start, end, preset


def _by_day(qs, field) -> dict:
    return dict(qs.annotate(d=TruncDate(field)).values_list("d").annotate(n=Count("pk")).values_list("d", "n"))


def _nice_max(value: int) -> int:
    """Top of the y axis: four even grid steps of a round size (0, 1,500, 3,000 ... 6,000)."""
    if value <= 4:
        return 4
    quarter = value / 4
    mag = 10 ** int(math.floor(math.log10(quarter)))
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        size = step * mag
        if size >= quarter and size == int(size):
            return int(size) * 4
    return value


def _line_chart(days, series, width=CHART_W, height=CHART_H, dashed=(), extra=()):
    """Geometry for a daily line chart: series = [(key, label, css_var, {date: n})].

    Small cards get a narrower viewBox so axis text keeps its size instead of shrinking with the chart.
    ``dashed`` = keys drawn as a dashed reference line; ``extra`` = [(label, css_var, {date: n})] shown only
    in the hover tooltip.
    """
    plot_w, plot_h = width - PAD_L - PAD_R, height - PAD_T - PAD_B
    top = _nice_max(max([s[3].get(d, 0) for s in series for d in days] or [0]))
    slot = plot_w / max(len(days), 1)
    base_y = PAD_T + plot_h

    def y_of(n):
        return round(base_y - n / top * plot_h, 2)

    lines = []
    for key, label, color, data in series:
        pts = [(round(PAD_L + (i + 0.5) * slot, 2), y_of(data.get(d, 0))) for i, d in enumerate(days)]
        lines.append({"key": key, "label": label, "color": color, "dashed": key in dashed,
                      "points": " ".join(f"{x},{y}" for x, y in pts),
                      "markers": pts if len(days) <= 45 else [],
                      "last": {"x": pts[-1][0], "y": pts[-1][1], "n": data.get(days[-1], 0)} if pts else None})
    hover = []
    for i, d in enumerate(days):
        cx = round(PAD_L + (i + 0.5) * slot, 2)
        hover.append({"x": round(PAD_L + i * slot, 2), "w": round(slot, 2), "cx": cx, "date": d,
                      "points": [{"y": y_of(s[3].get(d, 0)), "color": s[2]} for s in series],
                      "values": [(s[1], s[2], s[3].get(d, 0)) for s in series]
                      + [(label, color, data.get(d, 0)) for label, color, data in extra]})
    grid = [{"y": round(base_y - k / 4 * plot_h, 2), "v": int(top * k / 4)} for k in range(5)]
    every = max(1, math.ceil(len(days) / max(int(plot_w / 70), 2)))
    ticks = [{"x": h["cx"], "label": f"{h['date'].day}/{h['date'].month}"} for i, h in enumerate(hover)
             if i % every == 0]
    empty = not any(s[3].get(d, 0) for s in series for d in days)
    return {"w": width, "h": height, "lines": lines, "hover": hover, "grid": grid, "ticks": ticks,
            "pad_l": PAD_L, "right": width - PAD_R, "top_y": PAD_T, "base_y": base_y, "empty": empty}


def _progress(start, end, days):
    """Total cases as the base, split into สิ้นสุด / ยังไม่สิ้นสุด. สิ้นสุด = status final or ไม่ประสงค์ดำเนินคดี.

    The day a case became สิ้นสุด: the status change made in the system, or the call where the person said
    they do not want to go on. Cases already closed in the register file, or marked ไม่ประสงค์ in its contact
    log, count as สิ้นสุด from the start.
    """
    from apps.calls import funnel
    from apps.calls.models import CallAssignment

    qs = CallAssignment.objects.exclude(case__is_test=True)
    status_final = ~funnel.STATUS_OPEN
    total = qs.count()
    # สิ้นสุด = ไม่ประสงค์ดำเนินคดี (same number as on the call list) + other cases whose status is final
    n_not_want = qs.filter(funnel.NOT_WANT_Q).count()
    n_status = qs.filter(status_final & ~funnel.NOT_WANT_Q).count()

    before_system = object()
    when: dict = {}  # case id -> date it became สิ้นสุด, or before_system

    def mark(case_id, moment):  # keep the earliest; "before the system" beats any date
        day = timezone.localdate(moment) if moment else before_system
        old = when.get(case_id)
        if old is None:
            when[case_id] = day
        elif old is before_system or day is before_system:
            when[case_id] = before_system
        else:
            when[case_id] = min(old, day)

    for case_id, manual_at in qs.filter(status_final).values_list("case_id", "case__status_manual_at"):
        mark(case_id, manual_at)
    for case_id, n, last in qs.filter(funnel.NOT_WANT_Q).values_list("case_id", "attempt_count", "last_call_at"):
        mark(case_id, last if n else None)

    by_day: dict = {}
    for day in when.values():
        if day is not before_system:
            by_day[day] = by_day.get(day, 0) + 1
    final_before = sum(1 for d in when.values() if d is before_system or d < start)
    remaining, left = {}, total - final_before
    for d in days:
        left -= by_day.get(d, 0)
        remaining[d] = left
    closed = sum(by_day.get(d, 0) for d in days)
    prev_start = start - dt.timedelta(days=len(days))
    prev_closed = sum(n for d, n in by_day.items() if prev_start <= d < start)
    final_now = len(when)
    open_now = total - final_now
    per_day = closed / len(days) if days else 0
    return {
        "total": total, "final": final_now, "open": open_now,
        "final_status": n_status, "final_not_want": n_not_want,
        "final_pct": round(final_now / total * 100) if total else 0,
        "open_pct": 100 - round(final_now / total * 100) if total else 0,
        "open_at_start": total - final_before, "open_at_end": left,
        "closed": closed, "per_day": per_day, "prev_closed": prev_closed, "trend": closed - prev_closed,
        "eta_days": math.ceil(open_now / per_day) if per_day and open_now else None,
        "open_by_day": remaining, "closed_by_day": by_day,
    }


def _collect(start, end):
    from apps.calls import funnel
    from apps.calls.models import REACHED, CallAssignment, CallAttempt, CallOutcome
    from apps.calls.services import ensure_call_pool
    from apps.documents.models import Summons

    ensure_call_pool()
    calls = CallAttempt.objects.exclude(case__is_test=True).filter(called_at__date__gte=start, called_at__date__lte=end)
    reached = calls.filter(outcome__in=REACHED)
    real_summonses = Summons.objects.exclude(case__is_test=True)
    summonses = real_summonses.filter(created_at__date__gte=start, created_at__date__lte=end)
    ems = real_summonses.filter(ems_at__date__gte=start, ems_at__date__lte=end)
    n_calls, n_reached = calls.count(), reached.count()
    period = {
        "calls": n_calls, "reached": n_reached, "not_reached": n_calls - n_reached,
        "reach_rate": round(n_reached / n_calls * 100) if n_calls else 0,
        "cases_called": calls.values("case_id").distinct().count(),
        "appointed": calls.filter(outcome=CallOutcome.APPOINTED).count(),
        "summonses": summonses.count(), "ems": ems.count(),
    }
    pool = CallAssignment.objects.exclude(case__is_test=True)
    stages = funnel.counts(funnel.with_summons_flag(pool))
    open_cases = pool.filter(funnel.OPEN_CASE).count()
    all_summons = real_summonses
    overall = {
        # every figure from the same partition as the call list, so the pages agree
        "cases": stages["all"], "open": open_cases, "final": stages["all"] - open_cases,
        "stages": stages, "sankey": funnel.sankey(stages),
        "summonses": all_summons.count(), "ems_sent": all_summons.exclude(ems_number="").count(),
        "ems_waiting": all_summons.filter(ems_number="").count(),
    }

    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    reached_by_day = _by_day(reached, "called_at")
    not_reached_by_day = _by_day(calls.exclude(outcome__in=REACHED), "called_at")
    summons_by_day = _by_day(summonses, "created_at")
    ems_by_day = _by_day(ems, "ems_at")
    appointed_by_day = _by_day(calls.filter(outcome=CallOutcome.APPOINTED), "called_at")
    daily = [{"date": d, "calls": reached_by_day.get(d, 0) + not_reached_by_day.get(d, 0),
              "reached": reached_by_day.get(d, 0), "not_reached": not_reached_by_day.get(d, 0),
              "appointed": appointed_by_day.get(d, 0), "summonses": summons_by_day.get(d, 0),
              "ems": ems_by_day.get(d, 0)} for d in days]

    outcome_counts = dict(calls.values_list("outcome").annotate(n=Count("pk")).values_list("outcome", "n"))
    top = max(outcome_counts.values() or [0])
    outcomes = [{"label": c.label, "n": outcome_counts.get(c.value, 0), "reached": c in REACHED,
                 "pct": round(outcome_counts.get(c.value, 0) / top * 100) if top else 0}
                for c in CallOutcome if outcome_counts.get(c.value)]
    outcomes.sort(key=lambda o: -o["n"])

    staff = {}
    for row in calls.values("created_by").annotate(
            n=Count("pk"), ok=Count("pk", filter=Q(outcome__in=REACHED)),
            appt=Count("pk", filter=Q(outcome=CallOutcome.APPOINTED)), cases=Count("case", distinct=True)):
        staff.setdefault(row["created_by"], {}).update(calls=row["n"], reached=row["ok"], appointed=row["appt"],
                                                        cases=row["cases"])
    for row in summonses.values("created_by").annotate(n=Count("pk")):
        staff.setdefault(row["created_by"], {})["summonses"] = row["n"]
    for row in ems.values("ems_by").annotate(n=Count("pk")):
        staff.setdefault(row["ems_by"], {})["ems"] = row["n"]
    users = {u.pk: u for u in User.objects.filter(pk__in=[k for k in staff if k])}
    people = []
    for pk, vals in staff.items():
        u = users.get(pk)
        row = {"name": u.display_name() if u else "(ไม่ทราบผู้ใช้)", "calls": 0, "reached": 0, "appointed": 0,
               "cases": 0, "summonses": 0, "ems": 0, **vals}
        row["rate"] = round(row["reached"] / row["calls"] * 100) if row["calls"] else 0
        people.append(row)
    people.sort(key=lambda r: (-r["calls"], -r["summonses"], r["name"]))
    return period, overall, daily, days, outcomes, people, (reached_by_day, not_reached_by_day,
                                                           summons_by_day, ems_by_day)


def dashboard(request):
    if not (perms.is_admin(request.user) or perms.is_outsource(request.user)):
        raise PermissionDenied
    start, end, preset = _period(request)
    period, overall, daily, days, outcomes, people, (reached, not_reached, summons, ems) = _collect(start, end)
    progress = _progress(start, end, days)
    for d in daily:
        d["closed"], d["open"] = progress["closed_by_day"].get(d["date"], 0), progress["open_by_day"][d["date"]]
    if request.GET.get("format") == "xlsx":
        return _excel(start, end, period, daily, people, progress)
    total_line = {d: progress["total"] for d in days}
    progress_chart = _line_chart(days, [("total", "ยอดคดีทั้งหมด", "var(--text-muted)", total_line),
                                        ("open", "ยังไม่สิ้นสุด", "var(--series-1)", progress["open_by_day"])],
                                 dashed=("total",),
                                 extra=[("สิ้นสุดวันนี้", "var(--series-3)", progress["closed_by_day"])])
    calls_chart = _line_chart(days, [("reached", "ติดต่อได้", "var(--series-1)", reached),
                                    ("not_reached", "ติดต่อไม่ได้", "var(--series-2)", not_reached)])
    summons_chart = _line_chart(days, [("summonses", "ออกหมายเรียก", "var(--series-1)", summons)], width=560, height=220)
    ems_chart = _line_chart(days, [("ems", "บันทึกเลข EMS", "var(--series-1)", ems)], width=560, height=220)
    return render(request, "stats/dashboard.html", {
        "start": start, "end": end, "preset": preset, "presets": PRESETS, "period": period, "overall": overall,
        "daily": [d for d in reversed(daily) if any(d[k] for k in ("calls", "summonses", "ems", "closed"))],
        "calls_chart": calls_chart, "summons_chart": summons_chart, "ems_chart": ems_chart,
        "progress": progress, "progress_chart": progress_chart,
        "outcomes": outcomes, "people": people, "n_days": len(days),
    })


def _excel(start, end, period, daily, people, progress):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from apps.core.downloads import content_disposition

    wb = Workbook()
    ws = wb.active
    ws.title = "รายวัน"
    ws.append(["วันที่", "โทรทั้งหมด", "ติดต่อได้", "ติดต่อไม่ได้", "นัดเข้าพบ พงส.", "ออกหมายเรียก", "บันทึกเลข EMS",
               "สิ้นสุดเพิ่ม", "ยังไม่สิ้นสุด"])
    for d in daily:
        ws.append([d["date"], d["calls"], d["reached"], d["not_reached"], d["appointed"], d["summonses"], d["ems"],
                   d["closed"], d["open"]])
    ws.append(["รวม", period["calls"], period["reached"], period["not_reached"], period["appointed"],
               period["summonses"], period["ems"], progress["closed"], progress["open_at_end"]])
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "dd/mm/yyyy"
    ws2 = wb.create_sheet("รายเจ้าหน้าที่")
    ws2.append(["เจ้าหน้าที่", "โทร", "ติดต่อได้", "อัตราติดต่อได้ (%)", "จำนวนคดีที่โทร", "นัดเข้าพบ", "ออกหมาย",
                "บันทึก EMS"])
    for p in people:
        ws2.append([p["name"], p["calls"], p["reached"], p["rate"], p["cases"], p["appointed"], p["summonses"],
                    p["ems"]])
    for sheet in (ws, ws2):
        for c in sheet[1]:
            c.font = Font(bold=True)
        for col in "ABCDEFGHI":
            sheet.column_dimensions[col].width = 16
    ws2.column_dimensions["A"].width = 28
    ws3 = wb.create_sheet("ความคืบหน้า")
    ws3.append(["รายการ", "จำนวนคดี", "%"])
    total = progress["total"]
    for label, n in [("ยอดคดีทั้งหมด", total), ("สิ้นสุด", progress["final"]),
                     ("  - ไม่ประสงค์ดำเนินคดี", progress["final_not_want"]),
                     ("  - สถานะคดีสิ้นสุด (อื่นๆ)", progress["final_status"]), ("ยังไม่สิ้นสุด", progress["open"])]:
        ws3.append([label, n, round(n / total * 100, 1) if total else 0])
    ws3.append([f"สิ้นสุดเพิ่มในช่วง {start:%d/%m/%Y}-{end:%d/%m/%Y}", progress["closed"], None])
    for c in ws3[1]:
        c.font = Font(bold=True)
    ws3.column_dimensions["A"].width = 40
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = content_disposition(f"สถิติ_{start:%Y%m%d}-{end:%Y%m%d}.xlsx")
    return resp
