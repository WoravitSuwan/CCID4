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
    if request.POST.get("all_matching"):
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
        "signer_forms": _signer_forms(plan) if plan else {},
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

    if not perms.can_issue_any(request.user):
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
def summons_list(request):
    qs = Summons.objects.select_related("case", "issuer", "created_by")
    result = request.GET.get("result")
    if result in SummonsResult.values:
        qs = qs.filter(result=result)
    issuer = request.GET.get("issuer")
    if issuer and issuer.isdigit():
        qs = qs.filter(issuer_id=issuer)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(case__receipt_no__icontains=q.replace(" ", "")) | qs.filter(recipient_name__icontains=q)
    date_from = request.GET.get("from")
    if date_from:
        qs = qs.filter(issue_date__gte=date_from)
    date_to = request.GET.get("to")
    if date_to:
        qs = qs.filter(issue_date__lte=date_to)
    page = Paginator(qs.order_by("-created_at"), 50).get_page(request.GET.get("page"))
    return render(request, "documents/summons_list.html", {
        "page": page, "results": SummonsResult.choices, "issuers": Investigator.objects.all(),
    })


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
    return file_download(path, filename, inline=kind == "pdf")


def summons_docx(request, pk):
    return _summons_file(request, pk, "docx")


def summons_pdf(request, pk):
    return _summons_file(request, pk, "pdf")


# --------------------------------------------------------------------------- export
class ExportForm(StyledForm):
    format = forms.ChoiceField(label="รูปแบบไฟล์", choices=ExportFormat.choices, widget=forms.RadioSelect,
                               initial=ExportFormat.PDF)
    mode = forms.ChoiceField(label="การรวมไฟล์", choices=ExportMode.choices, widget=forms.RadioSelect,
                             initial=ExportMode.MERGED)
    include_summons = forms.BooleanField(label="หมายเรียก", required=False, initial=True)
    include_envelope = forms.BooleanField(label="ใบจ่าหน้าซอง", required=False, initial=True)

    def __init__(self, *args, pdf=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not pdf:  # no LibreOffice here: only Word can be produced
            self.fields["format"].choices = [(ExportFormat.DOCX, ExportFormat.DOCX.label)]
            self.fields["format"].initial = ExportFormat.DOCX


def _export_queryset(request):
    qs = Summons.objects.select_related("case", "issuer")
    return qs


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

