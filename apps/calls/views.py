import datetime as dt
from collections import Counter
import io
import json
import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from apps.accounts.models import Role
from apps.core import permissions as perms
from apps.core.audit import log
from apps.core.downloads import content_disposition, file_download
from apps.core.models import AuditAction, SystemSetting
from apps.registry.address_edit import address_form, save_address
from apps.registry.forms import StyledForm
from apps.registry.models import Case, Status

from . import funnel
from .models import OPEN_LOCK_MINUTES, REACHED, SHORT_LABELS, CallAssignment, CallAttachment, CallAttempt, CallOutcome
from .services import (
    ALLOWED_EXTENSIONS,
    assign,
    case_phones,
    check_attachments,
    ensure_call_pool,
    parse_receipt_list,
    record_call,
    save_attachments,
)

User = get_user_model()
DT_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
ADDRESS_PREFIX = "addr"  # keeps address field ids apart from the call form (both have "note")
TABS = [(key, funnel.STAGES[key][0]) for key in funnel.TAB_KEYS]
RESULTS = [("reached", "ติดต่อได้ (ทุกแบบ รวมจากไฟล์)"), ("not_reached", "ติดต่อไม่ได้ (ทุกแบบ รวมจากไฟล์)")]


def call_staff():
    return User.objects.filter(role=Role.OUTSOURCE, is_active=True).order_by("first_name", "username")


# --------------------------------------------------------------------------- queue
def _queue(request):
    """The shared call list: every open case (plus closed ones that were called), for all call staff."""
    if not (perms.is_outsource(request.user) or perms.can_manage_calls(request.user)):
        raise PermissionDenied
    ensure_call_pool()
    qs = funnel.with_summons_flag(CallAssignment.objects.select_related(
        "case", "case__complainant", "case__status", "assignee", "opened_by"))
    who = request.GET.get("assignee", "")
    if who == "me":
        qs = qs.filter(assignee=request.user)
    elif who == "none":
        qs = qs.filter(assignee__isnull=True)
    elif who.isdigit():
        qs = qs.filter(assignee_id=who)
    return qs


OPEN_CASE, NO_SYSTEM_CALL, FILE_LOG, FILE_REACHED = (funnel.OPEN_CASE, funnel.NO_SYSTEM_CALL, funnel.FILE_LOG,
                                                     funnel.FILE_REACHED)
FILE_DONE = NO_SYSTEM_CALL & FILE_REACHED  # reached before this system: nothing left to do


def _apply_tab(qs, tab):
    if tab == "todo":  # for "next case": open, not finished, call-back due, not reached before this system
        now = timezone.now()
        return (qs.filter(OPEN_CASE, is_done=False).filter(Q(callback_at__isnull=True) | Q(callback_at__lte=now))
                .exclude(FILE_DONE))
    return funnel.stage(qs, tab)


def _apply_filters(request, qs):
    """Search box, case status and latest call result - kept across tabs."""
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(case__receipt_no__icontains=q.replace(" ", "")) | Q(case__victim_name__icontains=q)
                       | Q(case__victim_phone__icontains=q))
    result = request.GET.get("result", "")
    if result == "reached":
        qs = qs.filter(funnel.REACHED_Q)
    elif result == "not_reached":
        qs = qs.filter(funnel.NOT_REACHED_Q)
    elif result in CallOutcome.values:
        qs = qs.filter(last_outcome=result)
    year = _year(request)
    if year:
        qs = qs.filter(case__report_year=year)
    state = _state(request)
    if state == "open":
        qs = qs.filter(funnel.OPEN_CASE)
    elif state == "final":
        qs = qs.exclude(funnel.OPEN_CASE)
    elif state.startswith("s"):
        qs = qs.filter(case__status_id=int(state[1:]))
    return qs


def _sticky(request, name: str) -> str:
    """A choice kept for this user (session) until changed: ?name=... sets it, ?name= clears it."""
    key = f"calls_{name}"
    if name in request.GET:
        request.session[key] = request.GET.get(name, "").strip()
    return request.session.get(key, "")


def _year(request) -> int | None:
    """?year=2569 (Buddhist era, as shown) or 2026 -> 2026; remembered like the order."""
    value = _sticky(request, "year")
    if not value.isdigit():
        return None
    year = int(value)
    return year - 543 if year > 2400 else year


def _state(request) -> str:
    """'open' (ยังไม่สิ้นสุด), 'final' (สิ้นสุดแล้ว), 's<status id>' or '' (all); remembered like the order."""
    value = _sticky(request, "state")
    return value if value in ("open", "final") or re.fullmatch(r"s\d+", value) else ""


def _years():
    """The report years present, newest first, as (CE, BE)."""
    years = (Case.objects.exclude(report_year__isnull=True).values_list("report_year", flat=True)
             .distinct().order_by("-report_year"))
    return [(y, y + 543) for y in years]


def _ordered(qs):
    # due call-backs first, then never called, then the longest-waiting
    return qs.order_by("is_done", "callback_at", "attempt_count", "last_call_at", "case__receipt_no")


# list order the user can pick ("" = the work order above); cases without an amount or date go last
SORTS = [("", "ลำดับงานโทร"), ("damage", "มูลค่าความเสียหาย น้อย → มาก"), ("-damage", "มูลค่าความเสียหาย มาก → น้อย"),
         ("-date", "วันที่รับแจ้ง ใหม่ → เก่า"), ("date", "วันที่รับแจ้ง เก่า → ใหม่")]


SORT_KEY, LIST_KEY = "calls_sort", "calls_list_query"  # remembered per user (session)


def _current_sort(request) -> str:
    """The order picked on the call list; kept until the user picks another one."""
    if "sort" in request.GET:
        sort = request.GET.get("sort", "")
        request.session[SORT_KEY] = sort if sort in dict(SORTS) else ""
    sort = request.session.get(SORT_KEY, "")
    return sort if sort in dict(SORTS) else ""


# button: label, first click, second click, and how each direction reads
SORT_BUTTONS = [
    ("ลำดับงานโทร", "", "", {"": ""}),
    ("วันที่รับแจ้ง", "-date", "date", {"-date": "ใหม่ → เก่า", "date": "เก่า → ใหม่"}),
    ("มูลค่าความเสียหาย", "damage", "-damage", {"damage": "น้อย → มาก", "-damage": "มาก → น้อย"}),
]


def _sort_buttons(sort: str) -> list[dict]:
    """Buttons for the order; clicking the active one turns its direction around."""
    out = []
    for label, first, second, hints in SORT_BUTTONS:
        active = sort in hints
        target = (second if sort == first else first) if active else first
        out.append({"label": label, "active": active, "hint": hints.get(sort, "") if active else "",
                    "sort": target, "up": active and sort in ("date", "damage"),
                    "down": active and sort in ("-date", "-damage"),
                    "title": f"เรียงตาม{label}" + (f" ({hints[target]})" if hints.get(target) else "")})
    return out


def _sorted(qs, sort):
    from django.db.models import F

    if sort in ("damage", "-damage"):
        amount = F("case__damage_amount")
        return qs.order_by(amount.asc(nulls_last=True) if sort == "damage" else amount.desc(nulls_last=True),
                           "case__receipt_no")
    if sort in ("date", "-date"):
        day = F("case__report_date")
        return qs.order_by(day.asc(nulls_last=True) if sort == "date" else day.desc(nulls_last=True),
                           "case__receipt_no")
    return _ordered(qs)


def queue(request):
    base = _queue(request)
    tab = request.GET.get("tab", "all")
    if tab not in funnel.STAGES:
        tab = "all"
    filtered = _apply_filters(request, base)
    counts = funnel.counts(filtered)  # one set of numbers for the tiles, the diagram and the tabs
    qs = _apply_tab(filtered, tab)
    today = timezone.localdate()
    today_calls = CallAttempt.objects.filter(called_at__date=today)
    if perms.is_outsource(request.user):
        today_calls = today_calls.filter(created_by=request.user)
    stats = {
        "today": today_calls.count(),
        "today_reached": today_calls.filter(outcome__in=REACHED).count(),
    }
    per_staff = []
    if perms.can_manage_calls(request.user):
        rows = (CallAssignment.objects.filter(assignee__isnull=False)
                .values("assignee", "assignee__first_name", "assignee__last_name", "assignee__username")
                .annotate(total=Count("pk"), done=Count("pk", filter=Q(is_done=True)),
                          never=Count("pk", filter=Q(attempt_count=0)))
                .order_by("assignee__first_name"))
        per_staff = list(rows)
    sort = _current_sort(request)
    request.session[LIST_KEY] = request.GET.urlencode()  # the call page's "back" returns to this exact list
    page = Paginator(_sorted(qs, sort), 50).get_page(request.GET.get("page"))
    return render(request, "calls/queue.html", {
        "page": page, "tab": tab, "tabs": TABS, "counts": counts, "stats": stats, "per_staff": per_staff,
        "sort": sort, "sorts": SORTS,
        "tab_label": funnel.STAGES[tab][0], "tab_in_nav": tab in funnel.TAB_KEYS,
        "sankey": funnel.sankey(counts),
        "filtered": bool(request.GET.get("q") or request.GET.get("result") or _year(request) or _state(request)),
        "years": _years(), "year": _year(request), "state": _state(request),
        "case_statuses": [(f"s{st.pk}", st.name) for st in Status.objects.order_by("sort_order", "name")],
        "sort_buttons": _sort_buttons(sort),
        "results": RESULTS, "outcomes": CallOutcome.choices,
        "staff": call_staff(),
        "lock_minutes": OPEN_LOCK_MINUTES,
        "can_manage": perms.can_manage_calls(request.user),
        "can_issue": perms.can_issue_any(request.user),
    })


@require_POST
def issue_filtered(request):
    """"ติดต่อไม่ได้ · รอออกหมายเรียก" (with the current search/filter): send every case of it to the issue page."""
    from django.http import QueryDict

    from apps.documents.views import SESSION_CASES, SESSION_EXCLUDED, SESSION_OPTS

    if not perms.can_issue_any(request.user):
        raise PermissionDenied
    request.GET = QueryDict(request.POST.get("query", ""))
    qs = _apply_tab(_apply_filters(request, _queue(request)), "to_issue")
    ids = list(qs.order_by("case__receipt_no").values_list("case_id", flat=True)[:MAX_ISSUE_AT_ONCE])
    if not ids:
        messages.info(request, "ไม่มีคดีที่ติดต่อไม่ได้และรอออกหมายเรียก")
        return redirect(f"{reverse('calls_queue')}?{request.GET.urlencode()}")
    request.session[SESSION_CASES] = ids
    for key in (SESSION_OPTS, SESSION_EXCLUDED):
        request.session.pop(key, None)
    if len(ids) == MAX_ISSUE_AT_ONCE:
        messages.info(request, f"ออกหมายได้ครั้งละ {MAX_ISSUE_AT_ONCE:,} คดี เมื่อออกชุดนี้แล้วกดปุ่มอีกครั้งสำหรับชุดถัดไป")
    return redirect("issue_form")


MAX_ISSUE_AT_ONCE = 1000


# --------------------------------------------------------------------------- record a call
class CallForm(StyledForm):
    outcome = forms.ChoiceField(label="ผลการติดต่อ", choices=CallOutcome.choices, widget=forms.RadioSelect)
    phone = forms.CharField(label="เบอร์ที่โทร", max_length=50, required=False)
    called_at = forms.DateTimeField(label="เวลาที่โทร", widget=DT_WIDGET)
    note = forms.CharField(label="หมายเหตุ / สิ่งที่ผู้เสียหายแจ้ง", required=False,
                           widget=forms.Textarea(attrs={"rows": 3}))
    callback_at = forms.DateTimeField(label="นัดโทรกลับ", required=False, widget=DT_WIDGET)
    appointment_at = forms.DateTimeField(label="วันเวลานัดเข้าพบพนักงานสอบสวน", required=False, widget=DT_WIDGET)
    # the date can be left out (not agreed yet); it can be added later with another call record

    def clean(self):
        data = super().clean()
        outcome = data.get("outcome")
        if outcome == CallOutcome.CALLBACK and not data.get("callback_at"):
            self.add_error("callback_at", "ระบุวันเวลาที่จะโทรกลับ")
        if outcome == CallOutcome.REACHED_OTHER and not data.get("note", "").strip():
            self.add_error("note", "กรุณาระบุรายละเอียด")
        if data.get("called_at") and data["called_at"] > timezone.now() + dt.timedelta(minutes=5):
            self.add_error("called_at", "เวลาที่โทรต้องไม่เป็นเวลาในอนาคต")
        return data


def _next_case(request, current_pk):
    """Next case in the list the user was looking at - same tab, search, filters (year, case status) and order -
    skipping ones another staff member has open right now.
    With an order picked (date, damage) it is the row after this one, going round at the end; this case counts
    for its place even if it has just left the list (e.g. it was called from the tab ยังไม่ได้โทร).
    With the work order it is the first case of that list still to call."""
    from django.http import QueryDict

    busy = Q(opened_at__gte=timezone.now() - dt.timedelta(minutes=OPEN_LOCK_MINUTES)) & ~Q(opened_by=request.user)
    saved = QueryDict(request.session.get(LIST_KEY, ""))
    original, request.GET = request.GET, saved  # the filters read the list's own address
    try:
        tab = saved.get("tab", "all")
        base = _queue(request)
        listed = _apply_tab(_apply_filters(request, base), tab if tab in funnel.STAGES else "all")
        sort = _current_sort(request)
    finally:
        request.GET = original
    free = listed.exclude(busy).exclude(case_id=current_pk)
    if not sort:
        nxt = _ordered(_apply_tab(free, "todo")).first() or _ordered(free).first()
        return nxt.case_id if nxt else None
    order = list(_sorted(base.filter(Q(pk__in=listed.values("pk")) | Q(case_id=current_pk)), sort)
                 .values_list("case_id", flat=True))
    wanted = set(free.values_list("case_id", flat=True))
    start = order.index(current_pk) + 1 if current_pk in order else 0
    return next((pk for pk in order[start:] + order[:start] if pk in wanted), None)


def call_case(request, pk):
    case = get_object_or_404(Case.objects.select_related("complainant", "status", "unit",
                                                         "investigator_alias__investigator"), pk=pk)
    if not perms.can_record_call(request.user, case):
        raise PermissionDenied
    phones = case_phones(case)
    now = timezone.localtime().replace(second=0, microsecond=0)
    form = CallForm(request.POST or None, initial={"called_at": now, "phone": phones[0] if phones else ""})
    files = request.FILES.getlist("attachments") if request.method == "POST" else []
    valid = request.method == "POST" and form.is_valid()
    file_problems = check_attachments(files) if valid else []
    if file_problems:
        form.add_error(None, "ไฟล์แนบ: " + " · ".join(file_problems) + " — เลือกไฟล์ใหม่แล้วบันทึกอีกครั้ง")
    if valid and not file_problems:
        d = form.cleaned_data
        with transaction.atomic():
            attempt = record_call(case, request.user, outcome=d["outcome"], phone=d["phone"], note=d["note"],
                                  called_at=d["called_at"], callback_at=d["callback_at"],
                                  appointment_at=d["appointment_at"])
            saved = save_attachments(attempt, files, request.user)
        CallAssignment.objects.filter(case=case, opened_by=request.user).update(opened_at=None)  # done with it
        log(request, AuditAction.CALL, case, f"{case.receipt_no}: {attempt.register_text()}"
            + (f" (แนบไฟล์ {len(saved)} ไฟล์: {', '.join(a.original_name for a in saved)})" if saved else ""))
        messages.success(request, f"บันทึกผลการโทรครั้งที่ {attempt.number} แล้ว"
                         + (f" พร้อมไฟล์แนบ {len(saved)} ไฟล์" if saved else ""))
        if request.POST.get("go") == "next":
            nxt = _next_case(request, case.pk)
            if nxt:
                return redirect("call_case", pk=nxt)
            messages.info(request, "ไม่มีคดีที่ต้องโทรเหลือในรายการแล้ว")
            back = request.session.get(LIST_KEY, "")
            return redirect(f"{reverse('calls_queue')}?{back}" if back else "calls_queue")
        return redirect("call_case", pk=case.pk)
    assignment = CallAssignment.objects.filter(case=case).select_related("assignee", "opened_by").first()
    if assignment is None:
        assignment = CallAssignment.objects.create(case=case)
    busy_with = assignment.opened_by_other(request.user)
    if request.method == "GET" and not busy_with:  # mark "I have this case open" for the others
        CallAssignment.objects.filter(pk=assignment.pk).update(opened_by=request.user, opened_at=timezone.now())
    return render(request, "calls/call.html", {
        "busy_with": busy_with, "busy_since": assignment.opened_at if busy_with else None,
        "case": case, "form": form, "phones": phones, "assignment": assignment,
        "attempts": case.calls.select_related("created_by").prefetch_related("attachments__uploaded_by"),
        "reached": REACHED,
        "outcomes_reached": [(c.value, SHORT_LABELS[c]) for c in CallOutcome if c in REACHED],
        "outcomes_not": [(c.value, SHORT_LABELS[c]) for c in CallOutcome if c not in REACHED],
        "next_pk": _next_case(request, case.pk),
        "back_query": request.session.get(LIST_KEY, ""),
        "summonses": case.summonses.select_related("issuer", "ems_by").order_by("-number"),
        "can_issue": perms.can_issue_for_case(request.user, case),
        "issue_blockers": case.issue_blockers(),
        "address_form": address_form(case, prefix=ADDRESS_PREFIX), "override": case.get_override(),
        "effective": case.effective_address(), "address_conflict": case.address_conflict(),
        "statuses": Status.objects.all(), "can_change_status": perms.can_change_status(request.user, case),
        "attachment_max_mb": settings.CALL_ATTACHMENT_MAX_MB,
        "attachment_accept": ",".join(f".{e}" for e in sorted(ALLOWED_EXTENSIONS)),
    })


@require_POST
def call_status(request, pk):
    """Change the case status from the call page (e.g. the victim withdraws, case closed)."""
    case = get_object_or_404(Case.objects.select_related("status"), pk=pk)
    if not perms.can_change_status(request.user, case):
        raise PermissionDenied
    status = Status.objects.filter(pk=request.POST.get("status")).first()
    if status is None:
        messages.error(request, "กรุณาเลือกสถานะ")
        return redirect("call_case", pk=pk)
    old = case.status.name if case.status_id else "-"
    note = request.POST.get("note", "").strip()[:300]
    if case.set_status(status, request.user, note):
        log(request, AuditAction.EDIT, case,
            f"เปลี่ยนสถานะ {case.receipt_no}: {old} → {status.name}" + (f" ({note})" if note else ""))
        messages.success(request, f"เปลี่ยนสถานะคดีเป็น '{status.name}' แล้ว")
    return redirect("call_case", pk=pk)


@require_POST
def call_attach(request, pk, attempt_pk):
    """Add files to a call that was already recorded."""
    case = get_object_or_404(Case, pk=pk)
    if not perms.can_record_call(request.user, case):
        raise PermissionDenied
    attempt = get_object_or_404(CallAttempt, pk=attempt_pk, case=case)
    files = request.FILES.getlist("attachments")
    problems = check_attachments(files) or ([] if files else ["เลือกไฟล์ที่จะแนบก่อน"])
    if problems:
        messages.error(request, "แนบไฟล์ไม่สำเร็จ: " + " · ".join(problems))
    else:
        saved = save_attachments(attempt, files, request.user)
        log(request, AuditAction.CALL, case, f"{case.receipt_no}: แนบไฟล์ในการโทรครั้งที่ {attempt.number} "
            f"({', '.join(a.original_name for a in saved)})")
        messages.success(request, f"แนบไฟล์ {len(saved)} ไฟล์ในการโทรครั้งที่ {attempt.number} แล้ว")
    return redirect(f"{reverse('call_case', args=[pk])}#call-{attempt.pk}")


def attachment_download(request, pk):
    att = get_object_or_404(CallAttachment.objects.select_related("case", "case__call_assignment"), pk=pk)
    if not perms.can_access_attachment(request.user, att):
        raise PermissionDenied
    path = Path(settings.MEDIA_ROOT) / att.file.name
    if not path.is_file():
        raise Http404("ไม่พบไฟล์")
    inline = att.opens_inline and request.GET.get("download") != "1"
    response = file_download(path, att.original_name, inline=inline)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = ("default-src 'none'; img-src 'self'; media-src 'self'; "
                                           "style-src 'unsafe-inline'")
    return response


@require_POST
def attachment_delete(request, pk):
    att = get_object_or_404(CallAttachment.objects.select_related("case", "attempt"), pk=pk)
    if not (perms.is_admin(request.user)
            or (perms.can_access_attachment(request.user, att) and att.uploaded_by_id == request.user.pk)):
        raise PermissionDenied
    name, case, attempt = att.original_name, att.case, att.attempt
    att.file.delete(save=False)
    att.delete()
    log(request, AuditAction.CALL, case, f"{case.receipt_no}: ลบไฟล์แนบ '{name}' ในการโทรครั้งที่ {attempt.number}")
    messages.success(request, f"ลบไฟล์ '{name}' แล้ว")
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = f"{reverse('call_case', args=[case.pk])}#call-{attempt.pk}"
    return redirect(back)


@require_POST
def call_address_save(request, pk):
    """Call staff fix/enter the address for the summons while on the phone with the complainant."""
    case = get_object_or_404(Case.objects.select_related("complainant"), pk=pk)
    if not perms.can_record_call(request.user, case):
        raise PermissionDenied
    save_address(request, case, prefix=ADDRESS_PREFIX)
    return redirect(f"{reverse('call_case', args=[case.pk])}#address")


# --------------------------------------------------------------------------- assignment
class AssignForm(StyledForm):
    receipts = forms.CharField(label="เลขรับแจ้ง (case id)", widget=forms.Textarea(attrs={
        "rows": 8, "placeholder": "วางเลขรับแจ้ง บรรทัดละ 1 เลข หรือคั่นด้วยเครื่องหมายจุลภาค/ช่องว่าง"}))
    assignee = forms.ModelChoiceField(label="มอบหมายให้", queryset=User.objects.none(), empty_label=None)
    reassign = forms.BooleanField(label="ย้ายคดีที่มอบหมายให้คนอื่นอยู่แล้วมาให้คนนี้", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = call_staff()


@perms.require(perms.can_manage_calls)
def assign_view(request):
    form = AssignForm(request.POST or None)
    result = None
    if request.method == "POST" and form.is_valid():
        keys = parse_receipt_list(form.cleaned_data["receipts"])
        cases = list(Case.objects.filter(receipt_no__in=keys))
        found = {c.receipt_no for c in cases}
        counts = assign(cases, form.cleaned_data["assignee"], request.user, form.cleaned_data["reassign"])
        result = {**counts, "not_found": [k for k in keys if k not in found], "total": len(keys)}
        log(request, AuditAction.ASSIGN, None,
            f"มอบหมายงานโทร {len(cases)} คดี ให้ {form.cleaned_data['assignee'].display_name()}",
            receipts=sorted(found)[:500])
        messages.success(request, f"มอบหมายให้ {form.cleaned_data['assignee'].display_name()} แล้ว: "
                                  f"ใหม่ {counts['created']} · ย้าย {counts['moved']} · คงเดิม {counts['kept']}")
    if not call_staff().exists():
        messages.warning(request, "ยังไม่มีผู้ใช้บทบาท 'เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource)' — สร้างได้ที่ ตั้งค่า → ผู้ใช้และสิทธิ์")
    return render(request, "calls/assign.html", {"form": form, "result": result})


@require_POST
@perms.require(perms.can_manage_calls)
def assign_bulk(request):
    ids = [int(i) for i in request.POST.getlist("case_ids") if i.isdigit()]
    assignee = call_staff().filter(pk=request.POST.get("assignee")).first()
    back = request.POST.get("next") or "/cases/"
    if not ids or assignee is None:
        messages.error(request, "เลือกคดีและเจ้าหน้าที่ผู้โทรก่อน")
        return redirect(back)
    cases = list(Case.objects.filter(pk__in=ids))
    counts = assign(cases, assignee, request.user)
    log(request, AuditAction.ASSIGN, None, f"มอบหมายงานโทร {len(cases)} คดี ให้ {assignee.display_name()}",
        receipts=[c.receipt_no for c in cases])
    messages.success(request, f"มอบหมายให้ {assignee.display_name()} โทร {len(cases)} คดีแล้ว "
                              f"(ใหม่ {counts['created']} · ย้าย {counts['moved']})")
    return redirect(back)


@require_POST
@perms.require(perms.can_manage_calls)
def reopen(request, pk):
    assignment = get_object_or_404(CallAssignment, case_id=pk)
    assignment.is_done = False
    assignment.done_at = None
    assignment.save(update_fields=["is_done", "done_at"])
    log(request, AuditAction.ASSIGN, assignment.case, f"เปิดงานโทร {assignment.case.receipt_no} อีกครั้ง")
    messages.success(request, "เปิดงานโทรคดีนี้อีกครั้งแล้ว")
    return redirect("call_case", pk=pk)


# --------------------------------------------------------------------------- export
@perms.require(perms.can_manage_calls)
def export_calls(request):
    qs = CallAttempt.objects.select_related("case", "created_by", "case__call_assignment__assignee").order_by(
        "called_at")
    from apps.core.params import date_param

    date_from, date_to = date_param(request, "from"), date_param(request, "to")
    if date_from:
        qs = qs.filter(called_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(called_at__date__lte=date_to)
    wb = Workbook()
    ws = wb.active
    ws.title = "ผลการติดต่อ"
    headers = ["เลขรับแจ้ง", "ผู้เสียหาย", "ครั้งที่", "วันเวลาที่โทร", "เบอร์ที่โทร", "ผลการติดต่อ", "ติดต่อได้",
               "หมายเหตุ", "นัดโทรกลับ", "นัดเข้าพบ พงส.", "ผู้โทร", "ข้อความสำหรับทะเบียน", "ไฟล์แนบ"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    def local(value):
        return timezone.localtime(value).replace(tzinfo=None) if value else None

    files = {}
    for att in CallAttachment.objects.filter(attempt__in=qs).only("attempt_id", "original_name"):
        files.setdefault(att.attempt_id, []).append(att.original_name)
    for a in qs.iterator():
        ws.append([a.case.receipt_no, a.case.victim_name, a.number, local(a.called_at), a.phone,
                   a.get_outcome_display(), "ได้" if a.reached else "ไม่ได้", a.note, local(a.callback_at),
                   local(a.appointment_at), a.created_by.display_name() if a.created_by else "",
                   a.register_text(), ", ".join(files.get(a.pk, []))])
    for col, width in zip("ABCDEFGHIJKLM", (16, 26, 8, 18, 14, 40, 9, 40, 18, 18, 18, 60, 30)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for idx in (3, 8, 9):
            row[idx].number_format = "dd/mm/yyyy hh:mm"
    buf = io.BytesIO()
    wb.save(buf)
    log(request, AuditAction.EXPORT, None, f"ส่งออกผลการติดต่อผู้เสียหาย {qs.count()} รายการ")
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = content_disposition(f"ผลการติดต่อผู้เสียหาย_{timezone.localdate():%Y%m%d}.xlsx")
    return resp


# --------------------------------------------------------------------------- report for the big system
def status_report(request):
    """ตรวจสอบการอัปเดตสถานะสิ้นสุด: page, Excel (?format=xlsx) and PDF (?format=pdf)."""
    import tempfile

    from apps.core.downloads import content_disposition
    from apps.documents.pdf import PdfConversionError, convert_many, soffice_available

    from . import status_report as report

    everything = report.rows()
    skip = [s for s in request.GET.getlist("skip") if s]
    data = [r for r in everything if r["status"] not in skip] if skip else everything
    for i, r in enumerate(data, start=1):
        r["no"] = i
    today = timezone.localdate()
    fmt = request.GET.get("format", "")
    if fmt in ("xlsx", "pdf"):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = report.write_xlsx(data, Path(tmp) / f"{report.file_stem(today)}.xlsx", today)
            if fmt == "pdf":
                try:
                    pdf = convert_many([xlsx], tmp, export_filter="calc_pdf_Export")[0]
                except PdfConversionError as exc:
                    messages.error(request, f"สร้าง PDF ไม่ได้: {exc} (ดาวน์โหลดเป็น Excel แทนได้)")
                    return redirect("status_report")
                body, ctype, name = pdf.read_bytes(), "application/pdf", pdf.name
            else:
                body = xlsx.read_bytes()
                ctype, name = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx.name
        log(request, AuditAction.EXPORT, None, f"ส่งออก{report.TITLE} {len(data):,} คดี ({fmt.upper()})")
        resp = HttpResponse(body, content_type=ctype)
        resp["Content-Disposition"] = content_disposition(name, inline=fmt == "pdf" and "inline" in request.GET)
        return resp
    return render(request, "calls/status_report.html", {
        "rows": data, "title": report.TITLE, "today": today, "pdf_ok": soffice_available(),
        "by_investigator": Counter(r["investigator"] for r in data).most_common(),
        "statuses": [(name, n, name in skip) for name, n in Counter(r["status"] for r in everything).most_common()],
        "skip": skip, "query": request.GET.urlencode(),
    })


# --------------------------------------------------------------------------- daily summary (สรุปผลประจำวัน)
def daily_report(request):
    """สรุปผลประจำวัน + ข้อความลงบันทึกประจำวัน for one day: copy, Word files, and (admins) send to LINE."""
    import tempfile

    from apps.core.params import date_param
    from apps.core.thai_format import thai_date_full

    from . import daily_report as report
    from . import line_bot

    today = timezone.localdate()
    day = min(date_param(request, "date") or today, today)
    User = get_user_model()
    caller_id = request.GET.get("caller", "")
    caller = User.objects.filter(pk=caller_id).first() if caller_id.isdigit() else None
    start = date_param(request, "from")
    start = start if start and start < day else None  # several days, e.g. a diary that was not written in time
    d = report.load(day, caller, start)

    if request.method == "POST":
        if not perms.is_admin(request.user):
            raise PermissionDenied("ไม่มีสิทธิ์ส่งข้อความเข้า LINE")
        try:
            total = line_bot.send_day(day)
        except line_bot.LineError as exc:
            messages.error(request, f"ส่งเข้า LINE ไม่ได้: {exc}")
        else:
            log(request, AuditAction.EXPORT, None, f"ส่งสรุปผลประจำวัน {report.file_day(day)} เข้ากลุ่ม LINE "
                                                   f"({total:,} Case)")
            messages.success(request, f"ส่งสรุปผลวันที่ {thai_date_full(day, thai_digits=False)} เข้ากลุ่ม LINE แล้ว")
        return redirect(f"{reverse('daily_report')}?date={day.isoformat()}")

    fmt = request.GET.get("format", "")
    if fmt in ("diary", "summary"):
        name = report.file_name("ลงบันทึกประจำวัน" if fmt == "diary" else "สรุปข้อมูลคดี", d)
        with tempfile.TemporaryDirectory() as tmp:
            write = report.write_diary_docx if fmt == "diary" else report.write_summary_docx
            body = write(d, Path(tmp) / "out.docx").read_bytes()
        log(request, AuditAction.EXPORT, None, f"ดาวน์โหลด {name} ({d.counts['total']:,} Case)")
        resp = HttpResponse(
            body, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        resp["Content-Disposition"] = content_disposition(name)
        return resp

    s = SystemSetting.get()
    return render(request, "calls/daily_report.html", {
        "d": d, "day": day, "today": today, "counts": d.counts, "caller": caller,
        "callers": User.objects.filter(pk__in=report.caller_ids(day, start)).order_by("first_name", "username"),
        "start": start, "period_text": report.period_text(d),
        "diary": report.diary_text(d), "summary": report.summary_text(d),
        "groups": [(g, report.GROUP_SHORT[g], d.of(g)) for g in report.GROUP_LABEL],
        "prev_day": day - dt.timedelta(days=1), "next_day": day + dt.timedelta(days=1) if day < today else None,
        "date_text": thai_date_full(day, thai_digits=False),
        "line_ready": line_bot.configured() and bool(s.line_group_id), "line_group": s.line_group_name,
        "query": request.GET.urlencode(),
    })


@csrf_exempt
@require_POST
def line_webhook(request):
    """Events from LINE (the bot joins/leaves a group, someone types a command). No login: the request is
    checked by its X-Line-Signature instead."""
    from . import line_bot

    if not line_bot.configured():
        return HttpResponse(status=404)
    if not line_bot.valid_signature(request.body, request.headers.get("X-Line-Signature", "")):
        return HttpResponse(status=403)
    try:
        events = json.loads(request.body or b"{}").get("events", [])
    except ValueError:
        return HttpResponse(status=400)
    line_bot.handle_events(events)
    return HttpResponse("OK")
