import io
from pathlib import Path
from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Font

from apps.core import permissions as perms
from apps.core.audit import log
from apps.core.models import AuditAction
from apps.registry.forms import StyledForm

from . import tasks
from .models import BatchStatus, ImportBatch, ImportKind
from .schema import COLUMNS, missing_required
from .services import prepare_batch, remember_mapping, workbook_info


class UploadForm(StyledForm):
    kind = forms.ChoiceField(label="ประเภทไฟล์", choices=ImportKind.choices, widget=forms.RadioSelect)
    file = forms.FileField(label="ไฟล์ Excel (.xlsx)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("รองรับเฉพาะไฟล์ .xlsx")
        return f


def _batch(pk):
    return get_object_or_404(ImportBatch, pk=pk)


@perms.require(perms.can_import)
def import_home(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        batch = ImportBatch(kind=form.cleaned_data["kind"], original_filename=upload.name, created_by=request.user)
        batch.file.save(Path(upload.name).name, upload, save=True)
        try:
            prepare_batch(batch)
        except Exception as exc:  # unreadable workbook
            batch.status = BatchStatus.FAILED
            batch.error = str(exc)
            batch.save()
            messages.error(request, f"อ่านไฟล์ไม่ได้: {exc}")
            return redirect("import_home")
        return redirect("import_sheets", pk=batch.pk)
    history = Paginator(ImportBatch.objects.select_related("created_by"), 20).get_page(request.GET.get("page"))
    return render(request, "imports/home.html", {"form": form, "history": history})


@perms.require(perms.can_import)
def import_sheets(request, pk):
    batch = _batch(pk)
    if request.method == "POST":
        chosen = [s for s in request.POST.getlist("sheets") if s in batch.available_sheets]
        if not chosen:
            messages.error(request, "กรุณาเลือกอย่างน้อย 1 ชีต")
        else:
            batch.sheets = chosen
            batch.save(update_fields=["sheets"])
            return redirect("import_mapping", pk=pk)
    return render(request, "imports/sheets.html", {"batch": batch, "step": 2})


@perms.require(perms.can_import)
def import_mapping(request, pk):
    batch = _batch(pk)
    info = {s["name"]: s["headers"] for s in workbook_info(batch.file.path)}
    headers = []
    for sheet in batch.sheets:
        for h in info.get(sheet, []):
            if h not in headers:
                headers.append(h)
    columns = COLUMNS[batch.kind]
    if request.method == "POST":
        mapping = {}
        for field, _header, _req in columns:
            value = request.POST.get(f"map_{field}", "")
            if value in headers:
                mapping[field] = value
        missing = missing_required(batch.kind, mapping)
        if missing:
            messages.error(request, "ยังไม่ได้จับคู่คอลัมน์ที่จำเป็น: " + ", ".join(missing))
            batch.mapping = mapping
        else:
            batch.mapping = mapping
            batch.status = BatchStatus.ANALYZING
            batch.progress = 0
            batch.save()
            remember_mapping(batch.kind, mapping)
            tasks.run(tasks.analyze_batch, batch.pk)
            return redirect("import_preview", pk=pk)
    sheet_headers = {s: info.get(s, []) for s in batch.sheets}
    differing = [s for s, hs in sheet_headers.items() if set(hs) != set(headers)]
    rows = [{"field": f, "label": h, "required": r, "selected": batch.mapping.get(f, ""),
             "auto": batch.mapping.get(f) == h} for f, h, r in columns]
    return render(request, "imports/mapping.html", {
        "batch": batch, "rows": rows, "headers": headers, "step": 3, "differing": differing,
        "all_auto": all(r["selected"] for r in rows if r["required"]),
    })


@perms.require(perms.can_import)
def import_preview(request, pk):
    batch = _batch(pk)
    if request.headers.get("HX-Request"):
        if not batch.is_running:
            response = HttpResponse("")
            response["HX-Refresh"] = "true"
            return response
        return render(request, "imports/_progress.html", {"batch": batch})
    template = "imports/preview.html"
    issues = batch.issues.all()[:200] if batch.status in (BatchStatus.ANALYZED, BatchStatus.DONE) else []
    return render(request, template, {"batch": batch, "step": 4, "issues": issues})


@require_POST
@perms.require(perms.can_import)
def import_confirm(request, pk):
    batch = _batch(pk)
    if batch.status != BatchStatus.ANALYZED:
        messages.error(request, "ไฟล์นี้ยังไม่พร้อมนำเข้า")
        return redirect("import_preview", pk=pk)
    batch.status = BatchStatus.COMMITTING
    batch.progress = 0
    batch.save(update_fields=["status", "progress"])
    tasks.run(tasks.commit_batch, batch.pk, request.user.pk)
    return redirect("import_preview", pk=pk)


@require_POST
@perms.require(perms.can_import)
def import_cancel(request, pk):
    batch = _batch(pk)
    if batch.status in (BatchStatus.UPLOADED, BatchStatus.ANALYZED, BatchStatus.FAILED):
        batch.status = BatchStatus.CANCELLED
        batch.save(update_fields=["status"])
        if batch.file:
            batch.file.delete(save=True)
        messages.info(request, "ยกเลิกการนำเข้าแล้ว")
    return redirect("import_home")


@perms.require(perms.can_import)
def import_issues_download(request, pk):
    batch = _batch(pk)
    wb = Workbook()
    ws = wb.active
    ws.title = "รายการปัญหา"
    ws.append(["ชีต", "แถวในไฟล์", "เลขรับแจ้ง", "ปัญหา", "รายละเอียด"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for issue in batch.issues.all().iterator():
        ws.append([issue.sheet, issue.row_number, issue.receipt_no, issue.get_issue_type_display(), issue.message])
    for col, width in zip("ABCDE", (18, 10, 16, 32, 60)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    log(request, AuditAction.DOWNLOAD, batch, f"ดาวน์โหลดรายการปัญหาการนำเข้า #{batch.pk}")
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'ปัญหาการนำเข้า_{batch.pk}.xlsx')}"
    return resp
