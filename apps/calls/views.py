import datetime as dt
import io

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from apps.accounts.models import Role
from apps.core import permissions as perms
from apps.core.audit import log
from apps.core.downloads import content_disposition
from apps.core.models import AuditAction
from apps.registry.forms import StyledForm
from apps.registry.models import Case

from .models import REACHED, SHORT_LABELS, CallAssignment, CallAttempt, CallOutcome
from .services import assign, case_phones, parse_receipt_list, record_call

User = get_user_model()
DT_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
TABS = [("todo", "ต้องโทร"), ("callback", "นัดโทรกลับ"), ("never", "ยังไม่เคยโทร"), ("done", "เสร็จแล้ว"),
        ("all", "ทั้งหมด")]


def call_staff():
    return User.objects.filter(role=Role.OUTSOURCE, is_active=True).order_by("first_name", "username")


# --------------------------------------------------------------------------- queue
def _queue(request):
    qs = CallAssignment.objects.select_related("case", "case__complainant", "case__status", "assignee")
    if perms.is_outsource(request.user):
        qs = qs.filter(assignee=request.user)
    elif not perms.can_manage_calls(request.user):
        raise PermissionDenied
    else:
        assignee = request.GET.get("assignee")
        if assignee and assignee.isdigit():
            qs = qs.filter(assignee_id=assignee)
    return qs


def _apply_tab(qs, tab):
    now = timezone.now()
    if tab == "todo":
        return qs.filter(is_done=False).filter(Q(callback_at__isnull=True) | Q(callback_at__lte=now))
    if tab == "callback":
        return qs.filter(is_done=False, callback_at__isnull=False)
    if tab == "never":
        return qs.filter(attempt_count=0)
    if tab == "done":
        return qs.filter(is_done=True)
    return qs


def _ordered(qs):
    # due call-backs first, then never called, then the longest-waiting
    return qs.order_by("is_done", "callback_at", "attempt_count", "last_call_at", "case__receipt_no")


def queue(request):
    base = _queue(request)
    tab = request.GET.get("tab", "todo")
    if tab not in dict(TABS):
        tab = "todo"
    qs = _apply_tab(base, tab)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(case__receipt_no__icontains=q.replace(" ", "")) | Q(case__victim_name__icontains=q)
                       | Q(case__victim_phone__icontains=q))
    counts = {key: _apply_tab(base, key).count() for key, _ in TABS}
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
        rows = (CallAssignment.objects.values("assignee", "assignee__first_name", "assignee__last_name",
                                              "assignee__username")
                .annotate(total=Count("pk"), done=Count("pk", filter=Q(is_done=True)),
                          never=Count("pk", filter=Q(attempt_count=0)))
                .order_by("assignee__first_name"))
        per_staff = list(rows)
    page = Paginator(_ordered(qs), 50).get_page(request.GET.get("page"))
    return render(request, "calls/queue.html", {
        "page": page, "tab": tab, "tabs": TABS, "counts": counts, "stats": stats, "per_staff": per_staff,
        "staff": call_staff() if perms.can_manage_calls(request.user) else [],
        "can_manage": perms.can_manage_calls(request.user),
    })


# --------------------------------------------------------------------------- record a call
class CallForm(StyledForm):
    outcome = forms.ChoiceField(label="ผลการติดต่อ", choices=CallOutcome.choices, widget=forms.RadioSelect)
    phone = forms.CharField(label="เบอร์ที่โทร", max_length=50, required=False)
    called_at = forms.DateTimeField(label="เวลาที่โทร", widget=DT_WIDGET)
    note = forms.CharField(label="หมายเหตุ / สิ่งที่ผู้เสียหายแจ้ง", required=False,
                           widget=forms.Textarea(attrs={"rows": 3}))
    callback_at = forms.DateTimeField(label="นัดโทรกลับ", required=False, widget=DT_WIDGET)
    appointment_at = forms.DateTimeField(label="นัดเข้าพบพนักงานสอบสวน", required=False, widget=DT_WIDGET)

    def clean(self):
        data = super().clean()
        outcome = data.get("outcome")
        if outcome == CallOutcome.APPOINTED and not data.get("appointment_at"):
            self.add_error("appointment_at", "ระบุวันเวลาที่นัดเข้าพบพนักงานสอบสวน")
        if outcome == CallOutcome.CALLBACK and not data.get("callback_at"):
            self.add_error("callback_at", "ระบุวันเวลาที่จะโทรกลับ")
        if outcome == CallOutcome.REACHED_OTHER and not data.get("note", "").strip():
            self.add_error("note", "กรุณาระบุรายละเอียด")
        if data.get("called_at") and data["called_at"] > timezone.now() + dt.timedelta(minutes=5):
            self.add_error("called_at", "เวลาที่โทรต้องไม่เป็นเวลาในอนาคต")
        return data


def _next_case(request, current_pk):
    qs = _ordered(_apply_tab(_queue(request), "todo")).exclude(case_id=current_pk)
    nxt = qs.first()
    return nxt.case_id if nxt else None


def call_case(request, pk):
    case = get_object_or_404(Case.objects.select_related("complainant", "status", "unit",
                                                         "investigator_alias__investigator"), pk=pk)
    if not perms.can_record_call(request.user, case):
        raise PermissionDenied
    phones = case_phones(case)
    now = timezone.localtime().replace(second=0, microsecond=0)
    form = CallForm(request.POST or None, initial={"called_at": now, "phone": phones[0] if phones else ""})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        attempt = record_call(case, request.user, outcome=d["outcome"], phone=d["phone"], note=d["note"],
                              called_at=d["called_at"], callback_at=d["callback_at"],
                              appointment_at=d["appointment_at"])
        log(request, AuditAction.CALL, case, f"{case.receipt_no}: {attempt.register_text()}")
        messages.success(request, f"บันทึกผลการโทรครั้งที่ {attempt.number} แล้ว")
        if request.POST.get("go") == "next":
            nxt = _next_case(request, case.pk)
            if nxt:
                return redirect("call_case", pk=nxt)
            messages.info(request, "ไม่มีคดีที่ต้องโทรเหลือในรายการแล้ว")
            return redirect("calls_queue")
        return redirect("call_case", pk=case.pk)
    assignment = CallAssignment.objects.filter(case=case).select_related("assignee").first()
    return render(request, "calls/call.html", {
        "case": case, "form": form, "phones": phones, "assignment": assignment,
        "attempts": case.calls.select_related("created_by"), "reached": REACHED,
        "outcomes_reached": [(c.value, SHORT_LABELS[c]) for c in CallOutcome if c in REACHED],
        "outcomes_not": [(c.value, SHORT_LABELS[c]) for c in CallOutcome if c not in REACHED],
        "next_pk": _next_case(request, case.pk),
    })


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
    date_from, date_to = request.GET.get("from"), request.GET.get("to")
    if date_from:
        qs = qs.filter(called_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(called_at__date__lte=date_to)
    wb = Workbook()
    ws = wb.active
    ws.title = "ผลการติดต่อ"
    headers = ["เลขรับแจ้ง", "ผู้เสียหาย", "ครั้งที่", "วันเวลาที่โทร", "เบอร์ที่โทร", "ผลการติดต่อ", "ติดต่อได้",
               "หมายเหตุ", "นัดโทรกลับ", "นัดเข้าพบ พงส.", "ผู้โทร", "ข้อความสำหรับทะเบียน"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    def local(value):
        return timezone.localtime(value).replace(tzinfo=None) if value else None

    for a in qs.iterator():
        ws.append([a.case.receipt_no, a.case.victim_name, a.number, local(a.called_at), a.phone,
                   a.get_outcome_display(), "ได้" if a.reached else "ไม่ได้", a.note, local(a.callback_at),
                   local(a.appointment_at), a.created_by.display_name() if a.created_by else "",
                   a.register_text()])
    for col, width in zip("ABCDEFGHIJKL", (16, 26, 8, 18, 14, 40, 9, 40, 18, 18, 18, 60)):
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
