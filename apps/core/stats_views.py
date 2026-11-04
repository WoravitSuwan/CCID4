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
    if value <= 4:
        return 4
    mag = 10 ** int(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        top = step * mag
        if top >= value:
            return int(math.ceil(top / 4) * 4)
    return value


def _bar_chart(days, series, width=CHART_W, height=CHART_H):
    """Geometry for a (stacked) daily bar chart: series = [(key, label, css_var, {date: n})].

    Small cards get a narrower viewBox so axis text keeps its size instead of shrinking with the chart.
    """
    plot_w, plot_h = width - PAD_L - PAD_R, height - PAD_T - PAD_B
    totals = [sum(s[3].get(d, 0) for s in series) for d in days]
    top = _nice_max(max(totals or [0]))
    slot = plot_w / max(len(days), 1)
    bar_w = max(min(slot - 2, 28), 1.5)  # 2-unit gap between neighbours
    bars = []
    for i, d in enumerate(days):
        x = PAD_L + i * slot + (slot - bar_w) / 2
        y_base = PAD_T + plot_h
        segs = []
        for s_i, (key, label, color, data) in enumerate(series):
            n = data.get(d, 0)
            if not n:
                continue
            h = n / top * plot_h
            gap = 1 if segs else 0  # surface gap between stacked segments
            segs.append({"x": round(x, 2), "y": round(y_base - h + gap, 2), "w": round(bar_w, 2),
                         "h": round(max(h - gap, 0.8), 2), "color": color, "n": n, "label": label})
            y_base -= h
        bars.append({"x": round(PAD_L + i * slot, 2), "w": round(slot, 2), "segs": segs, "date": d,
                     "total": totals[i], "values": [(s[1], s[2], s[3].get(d, 0)) for s in series]})
    grid = [{"y": round(PAD_T + plot_h - k / 4 * plot_h, 2), "v": int(top * k / 4)} for k in range(5)]
    every = max(1, math.ceil(len(days) / max(int(plot_w / 70), 2)))
    ticks = [{"x": round(b["x"] + b["w"] / 2, 2), "label": f"{b['date'].day}/{b['date'].month}"}
             for i, b in enumerate(bars) if i % every == 0]
    return {"w": width, "h": height, "bars": bars, "grid": grid, "ticks": ticks, "pad_l": PAD_L,
            "right": width - PAD_R, "base_y": PAD_T + plot_h, "series": series, "empty": not any(totals)}


def _collect(start, end):
    from apps.calls.models import REACHED, CallAssignment, CallAttempt, CallOutcome
    from apps.calls.services import ensure_call_pool
    from apps.calls.views import FILE_DONE, FILE_LOG, NO_SYSTEM_CALL, OPEN_CASE
    from apps.documents.models import Summons
    from apps.registry.models import Case

    ensure_call_pool()
    calls = CallAttempt.objects.filter(called_at__date__gte=start, called_at__date__lte=end)
    reached = calls.filter(outcome__in=REACHED)
    summonses = Summons.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    ems = Summons.objects.filter(ems_at__date__gte=start, ems_at__date__lte=end)
    n_calls, n_reached = calls.count(), reached.count()
    period = {
        "calls": n_calls, "reached": n_reached, "not_reached": n_calls - n_reached,
        "reach_rate": round(n_reached / n_calls * 100) if n_calls else 0,
        "cases_called": calls.values("case_id").distinct().count(),
        "appointed": calls.filter(outcome=CallOutcome.APPOINTED).count(),
        "summonses": summonses.count(), "ems": ems.count(),
    }
    pool = CallAssignment.objects.all()
    open_pool = pool.filter(OPEN_CASE)
    now = timezone.now()
    all_summons = Summons.objects.all()
    overall = {
        "cases": Case.objects.count(), "open": Case.objects.not_final().count(),
        "final": Case.objects.final().count(),
        "never": open_pool.filter(NO_SYSTEM_CALL).exclude(FILE_LOG).count(),
        "called": pool.filter(~NO_SYSTEM_CALL | FILE_LOG | ~OPEN_CASE).count(),
        "todo": open_pool.filter(is_done=False).filter(Q(callback_at__isnull=True) | Q(callback_at__lte=now))
        .exclude(FILE_DONE).count(),
        "done": pool.filter(Q(is_done=True) | FILE_DONE).count(),
        "summonses": all_summons.count(), "ems_sent": all_summons.exclude(ems_number="").count(),
        "ems_waiting": all_summons.filter(ems_number="").count(),
    }
    overall["called_pct"] = round(overall["called"] / overall["cases"] * 100) if overall["cases"] else 0

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
    if request.GET.get("format") == "xlsx":
        return _excel(start, end, period, daily, people)
    calls_chart = _bar_chart(days, [("reached", "ติดต่อได้", "var(--series-1)", reached),
                                    ("not_reached", "ติดต่อไม่ได้", "var(--series-2)", not_reached)])
    summons_chart = _bar_chart(days, [("summonses", "ออกหมายเรียก", "var(--series-1)", summons)], width=380, height=210)
    ems_chart = _bar_chart(days, [("ems", "บันทึกเลข EMS", "var(--series-1)", ems)], width=380, height=210)
    return render(request, "stats/dashboard.html", {
        "start": start, "end": end, "preset": preset, "presets": PRESETS, "period": period, "overall": overall,
        "daily": [d for d in reversed(daily) if any(d[k] for k in ("calls", "summonses", "ems"))],
        "calls_chart": calls_chart, "summons_chart": summons_chart, "ems_chart": ems_chart,
        "outcomes": outcomes, "people": people, "n_days": len(days),
    })


def _excel(start, end, period, daily, people):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from apps.core.downloads import content_disposition

    wb = Workbook()
    ws = wb.active
    ws.title = "รายวัน"
    ws.append(["วันที่", "โทรทั้งหมด", "ติดต่อได้", "ติดต่อไม่ได้", "นัดเข้าพบ พงส.", "ออกหมายเรียก", "บันทึกเลข EMS"])
    for d in daily:
        ws.append([d["date"], d["calls"], d["reached"], d["not_reached"], d["appointed"], d["summonses"], d["ems"]])
    ws.append(["รวม", period["calls"], period["reached"], period["not_reached"], period["appointed"],
               period["summonses"], period["ems"]])
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
        for col in "ABCDEFGH":
            sheet.column_dimensions[col].width = 16
    ws2.column_dimensions["A"].width = 28
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = content_disposition(f"สถิติ_{start:%Y%m%d}-{end:%Y%m%d}.xlsx")
    return resp
