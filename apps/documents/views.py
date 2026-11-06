from __future__ import annotations

import datetime as dt
from pathlib import Path

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import permissions as perms
from apps.core.downloads import file_download
from apps.core.audit import log
from apps.core.models import AuditAction, SystemSetting
from apps.registry.forms import StyledForm
from apps.registry.models import Case, Investigator
from apps.registry.views import filtered_cases

from .models import (
    DocTemplate, ExportFormat, ExportJob, ExportMode, JobStatus, Summons, SummonsResult, TemplateKind,
)
from .pdf import PdfConversionError
from .placeholders import default_appointment_date
from .services import (
    IssueOptions, TemplateMissing, ensure_docx, ensure_pdf, issue, plan_issue, preview_pdf, start_export,
)

SESSION_CASES = "issue_case_ids"
SESSION_EXPORT = "export_summons_ids"
MAX_PREVIEW = 40


def time_choices():
    out = []
    t = dt.datetime(2000, 1, 1, 8, 0)
    while t.hour < 17 or (t.hour == 17 and t.minute == 0):
        out.append((t.strftime("%H:%M"), t.strftime("%H.%M น.")))
        t += dt.timedelta(minutes=30)
    return out


class IssueForm(StyledForm):
    issue_date = forms.DateField(label="วันที่ออกหมาย", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    appointment_date = forms.DateField(label="วันนัด",
                                       widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    start_time = forms.ChoiceField(label="เวลานัด", choices=time_choices)
    stagger = forms.BooleanField(label="ไล่เวลาทีละ 30 นาที (แยกตามพนักงานสอบสวน)", required=False)
    reason = forms.CharField(label="เหตุที่เรียก", widget=forms.Textarea(attrs={"rows": 2}))
    template = forms.ModelChoiceField(label="แบบฟอร์ม", queryset=DocTemplate.objects.none(), empty_label=None)
    thai_digits = forms.BooleanField(label="แปลงตัวเลขในข้อมูลเป็นเลขไทย", required=False, initial=True)
    change_status = forms.BooleanField(label="เปลี่ยนสถานะคดีเป็น 'ออกหมายเรียก'", required=False, initial=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = DocTemplate.objects.filter(kind=TemplateKind.SUMMONS).order_by(
            "-is_active", "-version")

    def clean(self):
        data = super().clean()
        if data.get("issue_date") and data.get("appointment_date") and data["appointment_date"] < data["issue_date"]:
            self.add_error("appointment_date", "วันนัดต้องไม่ก่อนวันออกหมาย")
        return data

    def options(self) -> IssueOptions:
        d = self.cleaned_data
        return IssueOptions(
            issue_date=d["issue_date"], appointment_date=d["appointment_date"],
            start_time=dt.datetime.strptime(d["start_time"], "%H:%M").time(), stagger=d["stagger"],
            reason=d["reason"].strip(), change_status=d["change_status"], thai_digits=d["thai_digits"],
            template_id=d["template"].pk,
        )


# --------------------------------------------------------------------------- issuing
@require_POST
def issue_start(request):
    if not perms.can_issue_any(request.user):
        raise PermissionDenied
    if request.POST.get("all_matching") and perms.is_admin(request.user):
        from django.http import QueryDict

        request.GET = QueryDict(request.POST.get("query", ""))
        _form, qs = filtered_cases(request)
        ids = list(qs.values_list("pk", flat=True)[:2000])
    else:
        ids = [int(i) for i in request.POST.getlist("case_ids") if i.isdigit()]
    if not ids:
        messages.error(request, "ยังไม่ได้เลือกคดี")
        return redirect(request.POST.get("next") or "case_list")
    request.session[SESSION_CASES] = ids
    for key in (SESSION_OPTS, SESSION_EXCLUDED):
        request.session.pop(key, None)
    return redirect("issue_form")


SESSION_OPTS = "issue_options"
SESSION_EXCLUDED = "issue_excluded"
OPTION_FIELDS = ["issue_date", "appointment_date", "start_time", "stagger", "reason", "template", "thai_digits",
                 "change_status"]


def _selected_cases(request):
    ids = request.session.get(SESSION_CASES) or []
    if request.method == "POST":
        excluded = {int(i) for i in request.POST.getlist("exclude") if i.isdigit()}
        request.session[SESSION_EXCLUDED] = sorted(excluded)
    else:
        excluded = set(request.session.get(SESSION_EXCLUDED) or [])
    qs = (Case.objects.filter(pk__in=ids).exclude(pk__in=excluded)
          .select_related("status", "unit", "complainant", "investigator_alias__investigator"))
    return list(qs), excluded


def issue_form(request):
    if not perms.can_issue_any(request.user):
        raise PermissionDenied
    cases, excluded = _selected_cases(request)
    if not cases and request.method == "GET":
        if perms.is_outsource(request.user):
            messages.info(request, "เลือกคดีจากรายการที่ต้องโทรก่อน แล้วกด 'ออกหมายเรียก'")
            return redirect("calls_queue")
        messages.info(request, "เลือกคดีจากทะเบียนคดีก่อน แล้วกด 'ออกหมายเรียก'")
        return redirect("case_list")
    settings_row = SystemSetting.get()
    today = timezone.localdate()
    initial = {
        "issue_date": today, "appointment_date": default_appointment_date(today, settings_row.default_appointment_days),
        "start_time": "09:00", "reason": settings_row.default_reason, "thai_digits": True,
    }
    form = IssueForm(request.POST or None, initial=initial)
    saved = request.session.get(SESSION_OPTS)
    if request.method == "GET":
        form = IssueForm(saved) if saved else IssueForm(
            initial={**initial, "template": DocTemplate.active(TemplateKind.SUMMONS)})
    plan = None
    error = None
    if request.method == "POST" and form.is_valid():
        request.session[SESSION_OPTS] = {k: request.POST.get(k) for k in OPTION_FIELDS if request.POST.get(k)}
        opts = form.options()
        try:
            plan = plan_issue(request.user, cases, opts)
        except TemplateMissing as exc:
            error = str(exc)
        action = request.POST.get("action")
        if plan and action == "preview":
            items = plan.ok_items[:MAX_PREVIEW] or list(plan.items)[:1]
            try:
                pdf = preview_pdf(items, plan.template)
            except PdfConversionError as exc:
                messages.error(request, str(exc))
            else:
                resp = HttpResponse(pdf, content_type="application/pdf")
                resp["Content-Disposition"] = "inline; filename=preview.pdf"
                return resp
        elif plan and action == "confirm":
            if not plan.ok_items:
                messages.error(request, "ไม่มีรายการที่ออกหมายได้")
            else:
                created = issue(request.user, plan, opts, request=request)
                for key in (SESSION_CASES, SESSION_OPTS, SESSION_EXCLUDED):
                    request.session.pop(key, None)
                request.session[SESSION_EXPORT] = [s.pk for s in created]
                skipped = len(plan.blocked_items)
                messages.success(request, f"ออกหมายเรียกแล้ว {len(created)} ฉบับ"
                                 + (f" (ข้าม {skipped} รายการที่ข้อมูลไม่ครบ)" if skipped else ""))
                return redirect("export_new")
    elif request.method == "GET":
        bound = IssueForm(saved or {**initial, "thai_digits": "on",
                                    "template": getattr(DocTemplate.active(TemplateKind.SUMMONS), "pk", "")})
        if saved and bound.is_valid():
            form = bound
        if bound.is_valid():
            try:
                plan = plan_issue(request.user, cases, bound.options())
            except TemplateMissing as exc:
                error = str(exc)
    return render(request, "documents/issue.html", {
        "form": form, "plan": plan, "cases": cases, "excluded": excluded, "error": error,
        # signer rank/position come from the admin's Excel import; call staff only see what is missing
        "signer_forms": _signer_forms(plan) if plan and perms.is_admin(request.user) else {},
        "all_signers": Investigator.objects.filter(is_active=True),
    })


def _signer_forms(plan) -> dict:
    """Per signer group: forms to complete/fix the signer right on the issue page.

    {group investigator or None: [(target, label, SignerForm, needs_attention)]}
    """
    import re

    from apps.registry.forms import SignerForm

    out = {}
    for inv, items in plan.groups.items():
        entries = []
        if inv is None:
            aliases = {}
            for item in items:
                alias = item.case.investigator_alias
                if alias is not None:
                    aliases.setdefault(alias.pk, alias)
            for alias in aliases.values():
                name = re.sub(r"^หญิง\s*", "", alias.raw_name).strip()
                entries.append((f"alias:{alias.pk}", alias.raw_name,
                                SignerForm(prefix=f"a{alias.pk}", initial={"sign_name": name}), True))
        else:
            entries.append((f"inv:{inv.pk}", str(inv), SignerForm(prefix=f"i{inv.pk}", instance=inv),
                            bool(inv.missing_signer_fields())))
        out[inv] = [
            {"target": target, "label": label, "form": form, "attention": attention,
             "form_id": "signer-" + target.replace(":", "-")}
            for target, label, form, attention in entries
        ]
        for entry in out[inv]:  # inputs sit inside the issue form; point them at their own small form
            for field in entry["form"].fields.values():
                field.widget.attrs["form"] = entry["form_id"]
            female = entry["label"].startswith("หญิง") if inv is None else inv.is_female
            if female:  # register wrote 'หญิง...': offer ranks like พันตำรวจโทหญิง first
                entry["form"].fields["rank_full"].widget.attrs["list"] = "dl-rank-f"
    return out


@require_POST
def issue_signer(request):
    """Save signer data typed on the issue page (and link a register name to it)."""
    from apps.registry.forms import SignerForm
    from apps.registry.models import InvestigatorAlias

    if not perms.can_manage_settings(request.user):  # admin only; call staff wait for the signer import
        raise PermissionDenied
    kind, _, pk = request.POST.get("target", "").partition(":")
    if kind == "inv":
        inv = get_object_or_404(Investigator, pk=pk)
        form = SignerForm(request.POST, instance=inv, prefix=f"i{inv.pk}")
        alias = None
    elif kind == "alias":
        alias = get_object_or_404(InvestigatorAlias, pk=pk)
        link_to = request.POST.get("link_to")
        if request.POST.get("link_only") and not link_to:
            messages.error(request, "เลือกผู้ลงนามที่จะผูกก่อน")
            return redirect("issue_form")
        if link_to and link_to.isdigit():  # same person as an existing signer
            inv = get_object_or_404(Investigator, pk=link_to)
            alias.investigator = inv
            alias.save()
            log(request, AuditAction.SETTINGS, alias, f"ผูกชื่อ '{alias.raw_name}' กับ {inv} (จากหน้าออกหมาย)")
            messages.success(request, f"ผูก '{alias.raw_name}' กับ {inv} แล้ว")
            return redirect("issue_form")
        form = SignerForm(request.POST, prefix=f"a{alias.pk}")
    else:
        return redirect("issue_form")
    if form.is_valid():
        inv = form.save()
        if alias is not None:
            alias.investigator = inv
            alias.save()
        log(request, AuditAction.SETTINGS, inv, f"บันทึกข้อมูลผู้ลงนาม {inv} ({inv.position}) จากหน้าออกหมาย")
        messages.success(request, f"บันทึกข้อมูลผู้ลงนาม {inv} แล้ว ระบบจำยศและตำแหน่งไว้ให้เลือกครั้งต่อไป")
    else:
        errors = "; ".join(f"{form.fields[k].label}: {v[0]}" for k, v in form.errors.items() if k in form.fields)
        messages.error(request, "ข้อมูลผู้ลงนามไม่ครบ — " + errors)
    return redirect("issue_form")


# --------------------------------------------------------------------------- summons list & downloads
def _own_summonses(request, qs):
    """Call staff share one call list, so they see every summons (filter 'mine' to narrow it)."""
    if perms.is_outsource(request.user) and request.GET.get("mine"):
        qs = qs.filter(created_by=request.user)
    return qs


def summons_list(request):
    if not perms.can_export(request.user):
        raise PermissionDenied
    qs = _own_summonses(request, Summons.objects.select_related("case", "issuer", "created_by", "ems_by"))
    result = request.GET.get("result")
    if result in SummonsResult.values:
        qs = qs.filter(result=result)
    issuer = request.GET.get("issuer")
    if issuer and issuer.isdigit():
        qs = qs.filter(issuer_id=issuer)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(case__receipt_no__icontains=q.replace(" ", "")) | qs.filter(recipient_name__icontains=q)
    from apps.core.params import date_param

    date_from, date_to = date_param(request, "from"), date_param(request, "to")
    if date_from:
        qs = qs.filter(issue_date__gte=date_from)
    if date_to:
        qs = qs.filter(issue_date__lte=date_to)
    ems_counts = {"all": qs.count(), "no": qs.filter(ems_number="").count()}
    ems_counts["yes"] = ems_counts["all"] - ems_counts["no"]
    sent = request.GET.get("ems")
    if sent == "yes":
        qs = qs.exclude(ems_number="")
    elif sent == "no":
        qs = qs.filter(ems_number="")
    page = Paginator(qs.order_by("-created_at"), 50).get_page(request.GET.get("page"))
    return render(request, "documents/summons_list.html", {
        "page": page, "results": SummonsResult.choices, "issuers": Investigator.objects.all(),
        "ems_counts": ems_counts, "ems_tabs": [("", "ทั้งหมด", "all"), ("no", "ยังไม่ส่ง", "no"),
                                               ("yes", "ส่งแล้ว (มีเลข EMS)", "yes")],
    })


# --------------------------------------------------------------------------- EMS number (proof of sending)
def _save_ems(request, summons, raw) -> str:
    """Validate and save; returns an error message ('' = saved). An empty value removes the number."""
    from . import ems

    old = summons.ems_number
    if not (raw or "").strip():
        if old:
            summons.ems_number, summons.ems_at, summons.ems_by = "", None, None
            summons.save(update_fields=["ems_number", "ems_at", "ems_by"])
            log(request, AuditAction.EDIT, summons,
                f"ลบเลข EMS {old} ของหมายเรียก {summons.case.receipt_no} ครั้งที่ {summons.number}")
        return ""
    number, error = ems.validate(raw)
    if error:
        return error
    other = Summons.objects.filter(ems_number=number).exclude(pk=summons.pk).select_related("case").first()
    if other:
        return f"เลข {number} ใช้กับหมายเรียก {other.case.receipt_no} ครั้งที่ {other.number} แล้ว"
    if number != old:
        summons.ems_number, summons.ems_at, summons.ems_by = number, timezone.now(), request.user
        summons.save(update_fields=["ems_number", "ems_at", "ems_by"])
        log(request, AuditAction.EDIT, summons, f"บันทึกเลข EMS {number} ของหมายเรียก {summons.case.receipt_no} "
                                               f"ครั้งที่ {summons.number}" + (f" (เดิม {old})" if old else ""))
    return ""


@require_POST
def summons_ems(request, pk):
    summons = get_object_or_404(Summons.objects.select_related("case", "ems_by"), pk=pk)
    if not perms.can_access_summons(request.user, summons):
        raise PermissionDenied
    # each row's box has its own name: the list puts every box inside one form and htmx sends them all
    raw = request.POST.get(f"ems_{summons.pk}", request.POST.get("ems", ""))
    error = _save_ems(request, summons, raw)
    if request.headers.get("HX-Request"):
        summons.refresh_from_db()
        return render(request, "documents/_ems_cell.html", {"s": summons, "ems_error": error, "ems_value": raw})
    if error:
        messages.error(request, f"{summons.case.receipt_no}: {error}")
    else:
        messages.success(request, f"บันทึกเลข EMS ของ {summons.case.receipt_no} แล้ว")
    back = request.POST.get("next", "")
    from django.utils.http import url_has_allowed_host_and_scheme

    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("summons_list")
    return redirect(back)


SCAN_PENDING_LIMIT = 500


def summons_scan(request):
    """Scan station: pick the summons (typed reference or the list of those without a number), scan the EMS
    label - saved at once - then print the envelopes, which carry the EMS barcode. Scanning an EMS barcode
    with nothing picked tells whose envelope it is (a check after printing)."""
    if not perms.can_export(request.user):
        raise PermissionDenied
    pending = _own_summonses(request, Summons.objects.filter(ems_number="").exclude(
        result=SummonsResult.CANCELLED).select_related("case")).order_by("case__receipt_no", "number")
    return render(request, "documents/scan.html", {
        "pending": [_scan_payload(s) for s in pending[:SCAN_PENDING_LIMIT]],
        "pending_total": pending.count(), "pending_limit": SCAN_PENDING_LIMIT,
    })


def _scan_payload(summons) -> dict:
    return {"ok": True, "id": summons.pk, "receipt": summons.case.receipt_no, "number": summons.number,
            "name": summons.recipient_name, "address": summons.recipient_address, "ems": summons.ems_number,
            "tracking": summons.ems_tracking_url}


@require_POST
def summons_scan_lookup(request):
    """The scanned/typed code -> which summons: 'เลขรับแจ้ง-ครั้งที่', เลขรับแจ้ง, an old envelope barcode
    (9 + id) or an EMS number already recorded (verify=True: checking a printed envelope)."""
    import re

    from django.http import JsonResponse

    from . import ems
    from .barcodes import summons_id_from_code

    if not perms.can_export(request.user):
        raise PermissionDenied
    code = re.sub(r"\s", "", request.POST.get("code", "")).upper()
    qs = _own_summonses(request, Summons.objects.select_related("case"))
    summons = None
    pk = summons_id_from_code(code)
    if pk:
        summons = qs.filter(pk=pk).first()
    else:
        m = re.match(r"^([LWTC]\d{5,12})(?:-(\d+))?$", code)
        if m:
            found = qs.filter(case__receipt_no=m.group(1))
            summons = (found.filter(number=int(m.group(2))) if m.group(2) else found.order_by("-number")).first()
    if summons is None:
        number, problem = ems.validate(code)
        if not problem:
            owner = qs.filter(ems_number=number).first()
            if owner:
                return JsonResponse({**_scan_payload(owner), "verify": True})
            return JsonResponse({"ok": False, "error": f"เลข EMS {number} ยังไม่ได้บันทึกให้หมายฉบับใด — "
                                                       "เลือกหมายจากรายการ หรือพิมพ์เลขรับแจ้งก่อน แล้วค่อยยิงเลข EMS"})
        return JsonResponse({"ok": False, "error": f"ไม่พบหมายเรียกของรหัส {code or '(ว่าง)'}"})
    return JsonResponse(_scan_payload(summons))


@require_POST
def summons_scan_save(request):
    from django.http import JsonResponse

    summons = get_object_or_404(Summons.objects.select_related("case"), pk=request.POST.get("id") or 0)
    if not perms.can_access_summons(request.user, summons):
        raise PermissionDenied
    code = request.POST.get("ems", "")
    from .barcodes import summons_id_from_code

    if summons_id_from_code(code):
        return JsonResponse({"ok": False, "error": "นี่คือบาร์โค้ดซอง ไม่ใช่เลข EMS — ยิงบาร์โค้ดบนใบรับฝาก EMS"})
    error = _save_ems(request, summons, code)
    if error:
        return JsonResponse({"ok": False, "error": error})
    summons.refresh_from_db()
    return JsonResponse(_scan_payload(summons))


def summons_ems_bulk(request):
    """Paste 'เลขรับแจ้ง เลข EMS' lines (e.g. from the post office receipt) to record many at once."""
    import re

    if not perms.can_export(request.user):
        raise PermissionDenied
    rows = []
    if request.method == "POST":
        for line in request.POST.get("lines", "").splitlines():
            line = line.strip()
            if not line:
                continue
            receipt = re.search(r"[LWTC]\d{5,12}", line.upper())
            rest = line.upper().replace(receipt.group(0), " ") if receipt else line
            number = re.search(r"[A-Z]{2}[\s\-]?\d[\d\s\-]{7,12}[A-Z]{2}", rest)
            row = {"line": line, "receipt": receipt.group(0) if receipt else "", "ems": "", "error": ""}
            rows.append(row)
            if not receipt or not number:
                row["error"] = "ไม่พบเลขรับแจ้งหรือเลข EMS ในบรรทัดนี้"
                continue
            qs = _own_summonses(request, Summons.objects.filter(case__receipt_no=receipt.group(0))).order_by(
                "-number").select_related("case")
            summons = qs.filter(ems_number="").first() or qs.first()
            if summons is None:
                row["error"] = "ไม่พบหมายเรียกของเลขรับแจ้งนี้"
                continue
            row["error"] = _save_ems(request, summons, number.group(0))
            row["ems"] = summons.ems_number if not row["error"] else number.group(0)
            row["number"] = summons.number
        ok = sum(1 for r in rows if not r["error"])
        if rows:
            (messages.success if ok == len(rows) else messages.warning)(
                request, f"บันทึกเลข EMS แล้ว {ok} จาก {len(rows)} รายการ")
    return render(request, "documents/ems_bulk.html", {"rows": rows})


def _summons_file(request, pk, kind):
    summons = get_object_or_404(Summons.objects.select_related("case", "template", "issuer"), pk=pk)
    if not perms.can_access_summons(request.user, summons):
        raise PermissionDenied
    try:
        path = ensure_pdf(summons) if kind == "pdf" else ensure_docx(summons)
    except PdfConversionError as exc:
        messages.error(request, str(exc))
        return redirect("case_detail", pk=summons.case_id)
    log(request, AuditAction.DOWNLOAD, summons,
        f"ดาวน์โหลดหมายเรียก {summons.case.receipt_no} ครั้งที่ {summons.number} ({kind})")
    filename = f"{summons.base_filename()}.{kind}"
    return file_download(path, filename, inline=kind == "pdf" and request.GET.get("download") != "1")


def summons_docx(request, pk):
    return _summons_file(request, pk, "docx")


def summons_pdf(request, pk):
    return _summons_file(request, pk, "pdf")


# --------------------------------------------------------------------------- export
class ExportForm(StyledForm):
    format = forms.ChoiceField(label="รูปแบบไฟล์", choices=ExportFormat.choices, widget=forms.RadioSelect,
                               initial=ExportFormat.PDF)
    mode = forms.ChoiceField(label="การรวมไฟล์", choices=ExportMode.choices, widget=forms.RadioSelect,
                             initial=ExportMode.ZIP)
    include_summons = forms.BooleanField(label="หมายเรียก", required=False, initial=True)
    include_envelope = forms.BooleanField(label="ใบจ่าหน้าซอง", required=False, initial=True)

    def __init__(self, *args, pdf=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not pdf:  # no LibreOffice here: only Word can be produced
            self.fields["format"].choices = [(ExportFormat.DOCX, ExportFormat.DOCX.label)]
            self.fields["format"].initial = ExportFormat.DOCX


def _export_queryset(request):
    return _own_summonses(request, Summons.objects.select_related("case", "issuer"))


def export_new(request):
    if not perms.can_export(request.user):
        raise PermissionDenied
    if request.method == "GET" and request.GET.getlist("ids"):
        request.session[SESSION_EXPORT] = [int(i) for i in request.GET.getlist("ids") if i.isdigit()]
    ids = request.session.get(SESSION_EXPORT) or []
    summonses = list(_export_queryset(request).filter(pk__in=ids).order_by("issuer__sign_name", "case__receipt_no"))
    from .pdf import soffice_available

    pdf_ok = soffice_available()
    form = ExportForm(request.POST or None, pdf=pdf_ok, initial={"format": "pdf" if pdf_ok else "docx"})
    if request.method == "POST" and form.is_valid():
        chosen = {int(i) for i in request.POST.getlist("ids") if i.isdigit()}
        selected = [s for s in summonses if s.pk in chosen] if chosen else summonses
        d = form.cleaned_data
        if not selected:
            messages.error(request, "ยังไม่ได้เลือกหมายเรียก")
        elif not (d["include_summons"] or d["include_envelope"]):
            messages.error(request, "เลือกอย่างน้อย 1 อย่าง: หมายเรียก หรือ ใบจ่าหน้าซอง")
        else:
            job = ExportJob.objects.create(format=d["format"], mode=d["mode"], include_summons=d["include_summons"],
                                           include_envelope=d["include_envelope"], created_by=request.user)
            job.summonses.set(selected)
            log(request, AuditAction.EXPORT, job,
                f"ส่งออก {len(selected)} ฉบับ ({job.get_format_display()}, {job.get_mode_display()}"
                f"{', พร้อมใบจ่าหน้าซอง' if job.include_envelope else ''})")
            start_export(job)
            return redirect("export_job", pk=job.pk)
    from .pdf import pdf_unavailable_reason

    return render(request, "documents/export_new.html", {
        "form": form, "summonses": summonses, "pdf_reason": "" if pdf_ok else pdf_unavailable_reason(),
    })


def export_job(request, pk):
    job = get_object_or_404(ExportJob, pk=pk)
    if job.created_by_id != request.user.pk and not perms.is_admin(request.user):
        raise PermissionDenied
    template = "documents/_job_status.html" if request.headers.get("HX-Request") else "documents/export_job.html"
    return render(request, template, {"job": job})


@require_POST
def export_retry_word(request, pk):
    """Re-run a failed export (e.g. no LibreOffice) as Word files."""
    old = get_object_or_404(ExportJob, pk=pk)
    if old.created_by_id != request.user.pk and not perms.is_admin(request.user):
        raise PermissionDenied
    job = ExportJob.objects.create(format=ExportFormat.DOCX, mode=old.mode, include_summons=old.include_summons,
                                   include_envelope=old.include_envelope, created_by=request.user)
    job.summonses.set(old.summonses.all())
    log(request, AuditAction.EXPORT, job, f"ส่งออก {job.summonses.count()} ฉบับเป็น Word (แทนงาน #{old.pk})")
    start_export(job)
    return redirect("export_job", pk=job.pk)


def export_download(request, pk):
    job = get_object_or_404(ExportJob, pk=pk)
    if job.created_by_id != request.user.pk and not perms.is_admin(request.user):
        raise PermissionDenied
    if job.status != JobStatus.DONE or not job.result_file:
        raise Http404("ไฟล์ยังไม่พร้อมหรือถูกลบตามกำหนดเวลาแล้ว")
    path = Path(job.result_file.path)
    if not path.exists():
        raise Http404("ไฟล์ถูกลบตามกำหนดเวลาแล้ว กรุณาส่งออกใหม่")
    log(request, AuditAction.DOWNLOAD, job, f"ดาวน์โหลดไฟล์ส่งออก {path.name}")
    return file_download(path, path.name, inline=path.suffix == ".pdf")


# --------------------------------------------------------------------------- view / print in the browser
def _own_job(request, pk) -> ExportJob:
    job = get_object_or_404(ExportJob, pk=pk)
    if job.created_by_id != request.user.pk and not perms.is_admin(request.user):
        raise PermissionDenied
    if job.status != JobStatus.DONE or not job.result_file or not Path(job.result_file.path).exists():
        raise Http404("ไฟล์ยังไม่พร้อมหรือถูกลบตามกำหนดเวลาแล้ว กรุณาส่งออกใหม่")
    return job


def _job_pdf_names(job) -> list[str]:
    """PDF files in an export: the file itself, or the PDFs inside its ZIP (one per person)."""
    import zipfile

    path = Path(job.result_file.path)
    if path.suffix == ".pdf":
        return [path.name]
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            return [n for n in zf.namelist() if n.lower().endswith(".pdf")]
    return []


def _job_pdf_bytes(job, idx: int) -> tuple[str, bytes]:
    import zipfile

    names = _job_pdf_names(job)
    if not 0 <= idx < len(names):
        raise Http404
    path = Path(job.result_file.path)
    if path.suffix == ".pdf":
        return names[0], path.read_bytes()
    with zipfile.ZipFile(path) as zf:
        return names[idx], zf.read(names[idx])


def _inline_pdf(data: bytes, filename: str) -> HttpResponse:
    from apps.core.downloads import content_disposition

    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = content_disposition(filename, inline=True)
    return resp


def export_view(request, pk):
    """Open the exported PDFs in the browser, one by one or all together, with a print button."""
    job = _own_job(request, pk)
    names = _job_pdf_names(job)
    try:
        current = min(max(int(request.GET.get("i", 0)), 0), max(len(names) - 1, 0))
    except ValueError:
        current = 0
    files = [{"idx": i, "name": n.rsplit(".", 1)[0]} for i, n in enumerate(names)]
    return render(request, "documents/viewer.html", {
        "job": job, "files": files, "current": current,
        "src": reverse("export_file", args=[job.pk, current]) if names else "",
        "print_all": reverse("export_print_all", args=[job.pk]) if len(names) > 1 else "",
        "download": reverse("export_download", args=[job.pk]),
        "back": reverse("export_job", args=[job.pk]), "title": f"ไฟล์ส่งออก #{job.pk}",
    })


def export_file(request, pk, idx):
    job = _own_job(request, pk)
    name, data = _job_pdf_bytes(job, idx)
    log(request, AuditAction.DOWNLOAD, job, f"เปิดดูไฟล์ส่งออก {name}")
    return _inline_pdf(data, name)


def export_print_all(request, pk):
    """Every PDF of the export joined into one, to print all people at once."""

    from .services import merge_pdfs

    job = _own_job(request, pk)
    names = _job_pdf_names(job)
    work = Path(job.result_file.path).parent / "print_all.pdf"
    if not work.exists():
        parts = []
        tmp = Path(job.result_file.path).parent / "print_parts"
        tmp.mkdir(exist_ok=True)
        for i in range(len(names)):
            _, data = _job_pdf_bytes(job, i)
            part = tmp / f"{i:05d}.pdf"
            part.write_bytes(data)
            parts.append(part)
        merge_pdfs(parts, work)
        for part in parts:
            part.unlink(missing_ok=True)
        tmp.rmdir()
    log(request, AuditAction.DOWNLOAD, job, f"เปิดพิมพ์ไฟล์ส่งออกทั้งหมด {len(names)} ไฟล์")
    return _inline_pdf(work.read_bytes(), f"พิมพ์ทั้งหมด_{len(names)}ไฟล์.pdf")


def summons_view(request, pk):
    summons = get_object_or_404(Summons.objects.select_related("case"), pk=pk)
    if not perms.can_access_summons(request.user, summons):
        raise PermissionDenied
    back = request.GET.get("back", "")
    from django.utils.http import url_has_allowed_host_and_scheme

    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("summons_list")
    return render(request, "documents/viewer.html", {
        "files": [], "src": reverse("summons_pdf", args=[summons.pk]),
        "download": reverse("summons_pdf", args=[summons.pk]) + "?download=1",
        "word": reverse("summons_docx", args=[summons.pk]), "back": back,
        "title": f"หมายเรียก {summons.case.receipt_no} ครั้งที่ {summons.number}",
    })


def export_jobs(request):
    jobs = ExportJob.objects.filter(created_by=request.user).prefetch_related("summonses")[:50]
    return render(request, "documents/export_jobs.html", {"jobs": jobs})


def issue_from_case(request, pk):
    case = get_object_or_404(Case, pk=pk)
    if not perms.can_issue_for_case(request.user, case):
        raise PermissionDenied
    request.session[SESSION_CASES] = [case.pk]
    for key in (SESSION_OPTS, SESSION_EXCLUDED):
        request.session.pop(key, None)
    return redirect(reverse("issue_form"))

