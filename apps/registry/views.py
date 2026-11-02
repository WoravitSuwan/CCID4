from __future__ import annotations

import io

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from apps.calls.models import CallAssignment
from apps.calls.services import contact_log_with_calls
from apps.calls.views import call_staff
from apps.core import permissions as perms
from apps.core.downloads import content_disposition
from apps.core.audit import log
from apps.core.models import AuditAction
from apps.documents.models import Summons, SummonsResult

from .forms import AddressOverrideForm, CaseFilterForm
from .models import (
    AddressOverride, Case, Complainant, Investigator, Status, complainant_address_snapshot,
)

PAGE_SIZE = 50


# --------------------------------------------------------------------------- dashboard
def dashboard(request):
    now = timezone.now()
    cases = Case.objects.all()
    open_cases = cases.not_final()
    latest_issued = Summons.objects.filter(result=SummonsResult.ISSUED)
    stats = {
        "total": cases.count(),
        "open": open_cases.count(),
        "never": open_cases.never_summoned().count(),
        "incomplete": open_cases.address_incomplete().count(),
        "unmatched": open_cases.unmatched().count(),
        "waiting": latest_issued.filter(appointment_at__gte=now).count(),
        "overdue": latest_issued.filter(appointment_at__lt=now).count(),
        "absent": Summons.objects.filter(result=SummonsResult.ABSENT).count(),
        "complainants_outside": Complainant.objects.filter(case__isnull=True).count(),
    }
    never_ids = set(open_cases.never_summoned().values_list("pk", flat=True))
    per_inv = []
    open_by_inv = (open_cases.values("investigator_alias__investigator")
                   .annotate(n=Count("pk")).order_by())
    open_map = {r["investigator_alias__investigator"]: r["n"] for r in open_by_inv}
    never_map: dict = {}
    for inv_id in open_cases.filter(pk__in=never_ids).values_list("investigator_alias__investigator", flat=True):
        never_map[inv_id] = never_map.get(inv_id, 0) + 1
    waiting_map = dict(latest_issued.filter(appointment_at__gte=now).values("issuer")
                       .annotate(n=Count("pk")).values_list("issuer", "n"))
    absent_map = dict(Summons.objects.filter(result=SummonsResult.ABSENT).values("issuer")
                      .annotate(n=Count("pk")).values_list("issuer", "n"))
    total_map = dict(cases.values("investigator_alias__investigator").annotate(n=Count("pk"))
                     .values_list("investigator_alias__investigator", "n"))
    for inv in Investigator.objects.all():
        per_inv.append({"inv": inv, "total": total_map.get(inv.pk, 0), "open": open_map.get(inv.pk, 0),
                        "never": never_map.get(inv.pk, 0), "waiting": waiting_map.get(inv.pk, 0),
                        "absent": absent_map.get(inv.pk, 0)})
    if total_map.get(None):
        per_inv.append({"inv": None, "total": total_map.get(None, 0), "open": open_map.get(None, 0),
                        "never": never_map.get(None, 0), "waiting": 0, "absent": 0})
    per_inv.sort(key=lambda r: -r["open"])
    from apps.calls.models import REACHED, CallAssignment, CallAttempt

    today_calls = CallAttempt.objects.filter(called_at__date=timezone.localdate())
    call_stats = {
        "assigned": CallAssignment.objects.count(),
        "done": CallAssignment.objects.filter(is_done=True).count(),
        "today": today_calls.count(),
        "today_reached": today_calls.filter(outcome__in=REACHED).count(),
    }
    return render(request, "registry/dashboard.html", {"stats": stats, "per_inv": per_inv,
                                                       "call_stats": call_stats})


# --------------------------------------------------------------------------- case list
def conflict_receipts() -> list[str]:
    out = []
    overrides = {o.receipt_no: o for o in AddressOverride.objects.all()}
    if not overrides:
        return out
    for case in Case.objects.filter(receipt_no__in=overrides.keys()).select_related("complainant"):
        o = overrides[case.receipt_no]
        if case.complainant and complainant_address_snapshot(case.complainant) != (o.source_snapshot or {}):
            out.append(case.receipt_no)
    return out


def filtered_cases(request):
    years = list(Case.objects.exclude(report_year__isnull=True).values_list("report_year", flat=True)
                 .distinct().order_by("-report_year"))
    case_types = list(Case.objects.exclude(case_type="").values_list("case_type", flat=True)
                      .distinct().order_by("case_type"))
    form = CaseFilterForm(request.GET or None, years=years, case_types=case_types)
    qs = Case.objects.select_related("status", "unit", "complainant", "investigator_alias__investigator")
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            q = d["q"].strip()
            qs = qs.filter(
                Q(receipt_no__icontains=q.replace(" ", "")) | Q(victim_name__icontains=q)
                | Q(complainant__first_name__icontains=q) | Q(complainant__last_name__icontains=q)
                | Q(criminal_case_no__icontains=q) | Q(victim_phone__icontains=q)
                | Q(complainant__phone__icontains=q)
            )
        if d["status"]:
            qs = qs.filter(status=d["status"])
        if d["year"]:
            qs = qs.filter(report_year=d["year"])
        if d["unit"]:
            qs = qs.filter(unit=d["unit"])
        if d["investigator"] == "none":
            qs = qs.filter(Q(investigator_alias__isnull=True) | Q(investigator_alias__investigator__isnull=True))
        elif d["investigator"]:
            qs = qs.filter(investigator_alias__investigator_id=d["investigator"])
        if d["case_type"]:
            qs = qs.filter(case_type=d["case_type"])
        if d["open"]:
            qs = qs.not_final()
        if d["incomplete"]:
            qs = qs.address_incomplete()
        if d["unmatched"]:
            qs = qs.unmatched()
        if d["never"]:
            qs = qs.never_summoned()
        if d["conflict"]:
            qs = qs.filter(receipt_no__in=conflict_receipts())
    override = AddressOverride.objects.filter(receipt_no=OuterRef("receipt_no"))
    qs = qs.annotate(has_override=Exists(override),
                     summons_count=Count("summonses", distinct=True))
    return form, qs.order_by("-report_date", "receipt_no")


def case_list(request):
    form, qs = filtered_cases(request)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "registry/case_list.html", {
        "form": form, "page": page, "total": page.paginator.count,
        "can_issue": perms.can_issue_any(request.user), "statuses": Status.objects.all(),
        "can_bulk_status": perms.can_issue_any(request.user),
        "call_staff": call_staff() if perms.can_manage_calls(request.user) else [],
    })


# --------------------------------------------------------------------------- case detail
def _case_or_404(pk):
    return get_object_or_404(
        Case.objects.select_related("status", "unit", "complainant", "investigator_alias__investigator"), pk=pk
    )


def case_detail(request, pk):
    case = _case_or_404(pk)
    override = case.get_override()
    summonses = case.summonses.select_related("issuer", "created_by").order_by("-number")
    can_edit = perms.can_edit_case(request.user, case)
    form = AddressOverrideForm(instance=override) if can_edit else None
    if form is not None and override is None and case.complainant:
        c = case.complainant
        form.initial.update({f: getattr(c, f) for f in ["house_no", "moo", "building", "soi", "road",
                                                        "subdistrict", "district", "province"]})
        form.initial["postcode"] = c.effective_postcode
    return render(request, "registry/case_detail.html", {
        "case": case, "c": case.complainant, "override": override, "summonses": summonses,
        "form": form, "can_edit": can_edit, "can_issue": perms.can_issue_for_case(request.user, case),
        "can_view_id": perms.can_view_full_id(request.user, case), "blockers": case.issue_blockers(),
        "conflict": case.address_conflict(), "results": SummonsResult.choices,
        "effective": case.effective_address(), "statuses": Status.objects.all(),
        "calls": case.calls.select_related("created_by"),
        "call_assignment": CallAssignment.objects.filter(case=case).select_related("assignee").first(),
        "can_record_call": perms.can_record_call(request.user, case),
    })


@require_POST
def case_address_save(request, pk):
    case = _case_or_404(pk)
    if not perms.can_edit_case(request.user, case):
        raise PermissionDenied
    override = case.get_override() or AddressOverride(receipt_no=case.receipt_no)
    before = override.address().format() if override.pk else "(ที่อยู่จากไฟล์ผู้แจ้ง)"
    form = AddressOverrideForm(request.POST, instance=override)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.source_snapshot = complainant_address_snapshot(case.complainant)
        obj.save()
        log(request, AuditAction.EDIT, case, f"แก้ที่อยู่ผู้กล่าวหา {case.receipt_no}: {before} → {obj.address().format()}")
        messages.success(request, "บันทึกที่อยู่แล้ว ระบบจะใช้ที่อยู่นี้ในการออกหมาย (ไม่ถูกทับเมื่อนำเข้าไฟล์ใหม่)")
    else:
        messages.error(request, "ข้อมูลไม่ถูกต้อง: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("case_detail", pk=pk)


@require_POST
def case_address_resolve(request, pk):
    """Imported address changed after an override was saved: keep override or use the file."""
    case = _case_or_404(pk)
    if not perms.can_edit_case(request.user, case):
        raise PermissionDenied
    override = case.get_override()
    if override is None:
        return redirect("case_detail", pk=pk)
    choice = request.POST.get("choice")
    if choice == "file":
        old = override.address().format()
        override.delete()
        log(request, AuditAction.EDIT, case, f"เลิกใช้ที่อยู่ที่แก้เอง ({old}) กลับไปใช้ที่อยู่จากไฟล์ผู้แจ้ง")
        messages.success(request, "ใช้ที่อยู่จากไฟล์ผู้แจ้งแล้ว")
    else:
        override.source_snapshot = complainant_address_snapshot(case.complainant)
        override.updated_by = request.user
        override.save()
        log(request, AuditAction.EDIT, case, "ยืนยันใช้ที่อยู่ที่แก้เองต่อ")
        messages.success(request, "ใช้ที่อยู่ที่แก้เองต่อ")
    return redirect("case_detail", pk=pk)


@require_POST
def reveal_id(request, pk):
    case = _case_or_404(pk)
    if not perms.can_view_full_id(request.user, case) or not case.complainant:
        raise PermissionDenied
    log(request, AuditAction.VIEW_ID, case, f"ดูเลขบัตรประชาชนของ {case.receipt_no}")
    from apps.core.templatetags.thai import format_id

    return HttpResponse(f'<span class="font-mono">{format_id(case.complainant.national_id)}</span>')


@require_POST
def summons_result(request, pk):
    summons = get_object_or_404(Summons.objects.select_related("case"), pk=pk)
    if not perms.can_edit_case(request.user, summons.case):
        raise PermissionDenied
    result = request.POST.get("result")
    if result in SummonsResult.values:
        old = summons.get_result_display()
        summons.result = result
        summons.result_note = request.POST.get("note", "")[:500]
        summons.save(update_fields=["result", "result_note"])
        log(request, AuditAction.EDIT, summons,
            f"ผลหมายเรียก {summons.case.receipt_no} ครั้งที่ {summons.number}: {old} → {summons.get_result_display()}")
        messages.success(request, "บันทึกผลหมายเรียกแล้ว")
    return redirect("case_detail", pk=summons.case_id)


# --------------------------------------------------------------------------- Excel export
REGISTER_EXPORT = [
    ("ลำดับ", lambda c: c.seq), ("เลขรับแจ้ง", lambda c: c.receipt_no), ("เลขคดีอาญา", lambda c: c.criminal_case_no),
    ("วันที่รับแจ้ง", lambda c: c.report_date.day if c.report_date else None),
    ("เดือนที่รับแจ้ง", lambda c: c.report_date.month if c.report_date else None),
    ("ปีที่รับแจ้ง", lambda c: c.report_date.year if c.report_date else None),
    ("ผู้เสียหาย", lambda c: c.victim_name), ("เพศ", lambda c: c.gender), ("อายุ", lambda c: c.age),
    ("ประเภทคดี", lambda c: c.case_type), ("ประเภทคดีระดับ 1", lambda c: c.case_type_l1),
    ("ประเภทคดีระดับ 2", lambda c: c.case_type_l2), ("ประเภทคดีระดับ 3", lambda c: c.case_type_l3),
    ("ประเภทกลุ่มคดี", lambda c: c.case_group), ("รายละเอียด", lambda c: c.detail),
    ("มูลค่าความเสียหาย", lambda c: float(c.damage_amount) if c.damage_amount is not None else None),
    ("บช.", lambda c: c.bureau), ("บก./ภ.จว.", lambda c: c.division), ("หน่วยที่รับผิดชอบ", lambda c: c.unit_raw),
    ("พนักงานสอบสวน", lambda c: c.investigator_raw), ("พนักงานสืบสวน", lambda c: c.detective),
    ("สถานะ", lambda c: c.status.name if c.status_id else ""), ("เบอร์โทรศัพท์ผู้เสียหาย", lambda c: c.victim_phone),
    ("เลขอ้างอิงธนาคาร", lambda c: c.bank_ref), ("บันทึกการติดต่อ", lambda c: contact_log_with_calls(c)),
    ("ประจำวัน", lambda c: c.daily_log), ("หมายเหตุ", lambda c: c.remark),
]
TEXT_COLUMNS = {"เลขรับแจ้ง", "เบอร์โทรศัพท์ผู้เสียหาย", "เลขอ้างอิงธนาคาร", "เลขคดีอาญา"}


def export_register(request):
    if not perms.can_export_register(request.user):
        raise PermissionDenied
    _form, qs = filtered_cases(request)
    qs = qs.order_by("source_sheet", "report_date", "receipt_no").prefetch_related("calls")
    wb = Workbook(write_only=False)
    wb.remove(wb.active)
    sheets = {}
    count = 0
    for case in qs:
        name = (case.source_sheet or "ทะเบียน")[:31]
        ws = sheets.get(name)
        if ws is None:
            ws = wb.create_sheet(name)
            ws.append([h for h, _ in REGISTER_EXPORT])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            sheets[name] = ws
        ws.append([fn(case) for _, fn in REGISTER_EXPORT])
        count += 1
    if not sheets:
        wb.create_sheet("ทะเบียน").append([h for h, _ in REGISTER_EXPORT])
    for ws in wb.worksheets:
        for idx, (header, _) in enumerate(REGISTER_EXPORT, start=1):
            if header in TEXT_COLUMNS:
                for row in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                    row[0].number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    log(request, AuditAction.EXPORT, None, f"ส่งออกทะเบียนคดีเป็น Excel {count} คดี", query=request.GET.urlencode())
    stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
    response = HttpResponse(buf.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    response["Content-Disposition"] = content_disposition(f"ทะเบียนคดี_{stamp}.xlsx")
    return response


# --------------------------------------------------------------------------- status editing
@require_POST
def case_status_update(request, pk):
    case = _case_or_404(pk)
    if not perms.can_edit_case(request.user, case):
        raise PermissionDenied
    status = Status.objects.filter(pk=request.POST.get("status")).first()
    if status is None:
        messages.error(request, "กรุณาเลือกสถานะ")
        return redirect("case_detail", pk=pk)
    old = case.status.name if case.status_id else "-"
    note = request.POST.get("note", "").strip()[:300]
    if case.set_status(status, request.user, note):
        log(request, AuditAction.EDIT, case,
            f"เปลี่ยนสถานะ {case.receipt_no}: {old} → {status.name}" + (f" ({note})" if note else ""))
        messages.success(request, f"เปลี่ยนสถานะเป็น '{status.name}' แล้ว")
    return redirect(request.POST.get("next") or reverse("case_detail", args=[pk]))


@require_POST
def case_status_bulk(request):
    status = Status.objects.filter(pk=request.POST.get("status")).first()
    ids = [int(i) for i in request.POST.getlist("case_ids") if i.isdigit()]
    back = request.POST.get("next") or reverse("case_list")
    if status is None or not ids:
        messages.error(request, "เลือกคดีและสถานะใหม่ก่อน")
        return redirect(back)
    changed = skipped = same = 0
    cases = Case.objects.filter(pk__in=ids).select_related("status", "investigator_alias__investigator")
    for case in cases:
        if not perms.can_edit_case(request.user, case):
            skipped += 1
            continue
        old = case.status.name if case.status_id else "-"
        if case.set_status(status, request.user):
            changed += 1
            log(request, AuditAction.EDIT, case, f"เปลี่ยนสถานะ {case.receipt_no}: {old} → {status.name} (หลายคดี)")
        else:
            same += 1
    msg = f"เปลี่ยนสถานะเป็น '{status.name}' แล้ว {changed} คดี"
    if same:
        msg += f" · {same} คดีมีสถานะนี้อยู่แล้ว"
    if skipped:
        msg += f" · ข้าม {skipped} คดีที่ไม่มีสิทธิ์แก้ไข"
    messages.success(request, msg)
    return redirect(back)
