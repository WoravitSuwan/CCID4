"""นำเข้า PDF ทะเบียนราษฎร: upload -> check what was read against the case -> save the address -> issue."""
from __future__ import annotations

import hashlib
import re

from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import permissions as perms
from apps.core.audit import log
from apps.core.models import AuditAction
from apps.registry.address import Address, clean_text
from apps.registry.forms import StyledForm
from apps.registry.models import AddressOverride, Case, complainant_address_snapshot

from . import dopa
from .dopa_match import cases_for, id_agrees, match, may_use
from .models import DopaRecord, DopaState

MAX_FILES = 20
MAX_MB = 15
ADDRESS_INPUTS = ["house_no", "moo", "building", "alley", "soi", "road", "subdistrict", "district", "province",
                  "postcode"]
ADDRESS_LABELS = {"house_no": "บ้านเลขที่", "moo": "หมู่ที่", "building": "อาคาร/หมู่บ้าน", "alley": "ตรอก",
                  "soi": "ซอย", "road": "ถนน", "subdistrict": "ตำบล/แขวง", "district": "อำเภอ/เขต",
                  "province": "จังหวัด", "postcode": "รหัสไปรษณีย์"}


class ApplyForm(StyledForm):
    receipt_no = forms.CharField(label="เลขรับแจ้งของคดี", max_length=100)
    recipient_name = forms.CharField(label="ชื่อผู้รับหมาย", max_length=300, required=False,
                                     help_text="ตามทะเบียนราษฎร (แก้ได้) ใช้เป็นชื่อในหมายเรียกและหน้าซอง")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ADDRESS_INPUTS:
            self.fields[name] = forms.CharField(label=ADDRESS_LABELS[name], required=False, max_length=200)
        self._style()

    def clean_postcode(self):
        value = re.sub(r"\D", "", self.cleaned_data.get("postcode", ""))
        if value and len(value) != 5:
            raise forms.ValidationError("รหัสไปรษณีย์ต้องเป็นตัวเลข 5 หลัก")
        return value

    def address(self) -> Address:
        return Address(**{k: clean_text(self.cleaned_data.get(k, "")) for k in ADDRESS_INPUTS})


def _records(user):
    qs = DopaRecord.objects.select_related("case", "uploaded_by")
    return qs if perms.is_admin(user) else qs.filter(uploaded_by=user)


def _chosen_case(request) -> Case | None:
    raw = request.POST.get("case") or request.GET.get("case") or ""
    case = Case.objects.filter(pk=raw).first() if raw.isdigit() else None
    return case if case is not None and may_use(request.user, case) else None


def mask_ids(text: str) -> str:
    """Hide ID numbers in the text shown on the page (x-xxxx-xxxxx-12-3)."""
    def hide(m):
        d = re.sub(r"\D", "", m.group(0))
        return f"x-xxxx-xxxxx-{d[10:12]}-{d[12]}" if len(d) == 13 else m.group(0)

    return re.sub(r"(?<!\d)\d[\s-]?\d{4}[\s-]?\d{5}[\s-]?\d{2}[\s-]?\d(?!\d)", hide, text or "")


@perms.require(perms.can_issue_any)
def dopa_home(request):
    """Upload one or many PDFs; the list of what is waiting to be checked."""
    chosen = _chosen_case(request)
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "เลือกไฟล์ PDF อย่างน้อย 1 ไฟล์")
            return redirect(request.get_full_path())
        if len(files) > MAX_FILES:
            messages.error(request, f"อัปโหลดได้ครั้งละไม่เกิน {MAX_FILES} ไฟล์")
            return redirect(request.get_full_path())
        created, problems = [], []
        for f in files:
            if f.size > MAX_MB * 1024 * 1024:
                problems.append(f"{f.name}: ใหญ่เกิน {MAX_MB} MB")
                continue
            data = f.read()
            if not data.startswith(b"%PDF"):
                problems.append(f"{f.name}: ไม่ใช่ไฟล์ PDF")
                continue
            people, how, notes, text = dopa.read_pdf(data)
            if not people:  # nothing could be read: the staff type it in on the review page
                people = [dopa.Person(warnings=["อ่านข้อมูลจากไฟล์ไม่ได้ กรอกเองจากเอกสาร"])]
            for person in people:
                use = chosen if len(files) == 1 and len(people) == 1 else None
                case, how_match, _ = match(person, f.name, request.user, use)
                created.append(DopaRecord.objects.create(
                    file_name=f.name[:300], file_hash=hashlib.sha256(data).hexdigest(), read_by=how,
                    notes=notes, page_text=text[:20000], data=person.as_dict(), national_id=person.national_id,
                    case=case, match_by=how_match, uploaded_by=request.user))
        for p in problems:
            messages.error(request, p)
        if created:
            matched = sum(1 for r in created if r.case_id)
            log(request, AuditAction.IMPORT, None, f"อ่าน PDF ทะเบียนราษฎร {len(files)} ไฟล์ ได้ {len(created)} รายการ "
                                                   f"(จับคู่คดีได้ {matched})")
            messages.success(request, f"อ่านแล้ว {len(created)} รายการ จับคู่คดีได้ {matched} รายการ ตรวจทีละรายการก่อนบันทึก")
        if len(created) == 1:
            return redirect("dopa_review", pk=created[0].pk)
        return redirect("dopa_home")
    records = _records(request.user)
    return render(request, "imports/dopa_home.html", {
        "chosen": chosen, "pending": records.filter(state=DopaState.PENDING)[:200],
        "done": records.exclude(state=DopaState.PENDING)[:20], "max_files": MAX_FILES, "max_mb": MAX_MB,
        "ocr": dopa.ocr_status(), "ocr_help": dopa.OCR_HELP.get(dopa.ocr_status(), ""),
        "linked": getattr(request.user, "investigator", None) if not perms.is_admin(request.user) else None,
    })


def _record(request, pk) -> DopaRecord:
    rec = get_object_or_404(DopaRecord.objects.select_related("case__complainant"), pk=pk)
    if not perms.is_admin(request.user) and rec.uploaded_by_id != request.user.pk:
        raise Http404
    if rec.case_id and not may_use(request.user, rec.case):  # the case was moved to another investigator
        rec.case = None
    return rec


def _initial(rec: DopaRecord, person) -> dict:
    a = person.address
    initial = {k: getattr(a, k) for k in ADDRESS_INPUTS}
    initial["recipient_name"] = (rec.data or {}).get("recipient_name") or person.full_name
    initial["receipt_no"] = rec.case.receipt_no if rec.case else ""
    return initial


@perms.require(perms.can_issue_any)
def dopa_review(request, pk):
    rec = _record(request, pk)
    person = rec.person()
    if request.method == "POST" and rec.state == DopaState.PENDING:
        action = request.POST.get("action", "save")
        if action == "discard":
            rec.state, rec.page_text, rec.done_by, rec.done_at = DopaState.DISCARDED, "", request.user, timezone.now()
            rec.save(update_fields=["state", "page_text", "done_by", "done_at"])
            log(request, AuditAction.EDIT, rec.case, f"ไม่ใช้ข้อมูลจาก PDF ทะเบียนราษฎร {rec.file_name}")
            messages.info(request, "ไม่ใช้รายการนี้แล้ว")
            return _next(request)
        form = ApplyForm(request.POST)
        if form.is_valid():
            case = cases_for(request.user).filter(receipt_no=form.cleaned_data["receipt_no"].strip().upper()).first()
            if case is None:
                form.add_error("receipt_no", "ไม่พบเลขรับแจ้งนี้ในคดีที่คุณดูแล")
            elif action == "set_case":
                rec.case, rec.match_by = case, "เลือกเอง"
                rec.data = {**(rec.data or {}), "address": form.address().as_dict(),
                            "recipient_name": form.cleaned_data["recipient_name"]}
                rec.save(update_fields=["case", "match_by", "data"])
                messages.info(request, f"เปลี่ยนเป็นคดี {case.receipt_no} แล้ว ตรวจที่อยู่อีกครั้งก่อนบันทึก")
                return redirect("dopa_review", pk=rec.pk)
            else:
                override = apply_to_case(request, rec, case, form)
                blockers = case.issue_blockers()
                if action == "save_issue" and not blockers and perms.can_issue_for_case(request.user, case):
                    return redirect("case_issue", pk=case.pk)
                if blockers:
                    messages.warning(request, "ยังออกหมายไม่ได้: " + " · ".join(blockers))
                else:
                    messages.success(request, f"คดี {case.receipt_no} พร้อมออกหมายเรียก: {override.address().format()}")
                return _next(request)
    else:
        form = ApplyForm(initial=_initial(rec, person))
    case = rec.case
    current = case.effective_address() if case else None
    new = Address(**{k: (form.data.get(k) if form.is_bound else form.initial.get(k, "")) or "" for k in ADDRESS_INPUTS})
    _, _, candidates = match(person, rec.file_name, request.user)
    rows = [{"name": k, "label": ADDRESS_LABELS[k], "field": form[k],
             "now": getattr(current, k, "") if current else "",
             "changed": current is not None and clean_text(getattr(current, k, "")) != clean_text(getattr(new, k, ""))}
            for k in ADDRESS_INPUTS]
    return render(request, "imports/dopa_review.html", {
        "rec": rec, "person": person, "form": form, "case": case, "current": current, "new": new, "rows": rows,
        "id_agrees": id_agrees(case, person), "candidates": [c for c in candidates if c != case][:8],
        "page_text": mask_ids(rec.page_text), "blockers": case.issue_blockers() if case else [],
        "details": [(dopa.FIELD_LABELS[f], getattr(person, f)) for f in dopa.PERSON_FIELDS
                    if f not in ("national_id", "prefix", "first_name", "last_name")],
        "postcode_from": person.postcode_from, "pending_left": _records(request.user).filter(
            state=DopaState.PENDING).exclude(pk=rec.pk).count(),
    })


def apply_to_case(request, rec: DopaRecord, case: Case, form: ApplyForm) -> AddressOverride:
    """Save the checked address as the case's address for summonses (AddressOverride, never overwritten by an
    Excel import), and mark the record as used. Logged with before -> after."""
    with transaction.atomic():
        override = case.get_override() or AddressOverride(receipt_no=case.receipt_no)
        before = override.address().format() if override.pk else (
            case.complainant.address().format() if case.complainant else "(ไม่มีที่อยู่)")
        address = form.address()
        for k in ADDRESS_INPUTS:
            setattr(override, k, getattr(address, k))
        name = clean_text(form.cleaned_data.get("recipient_name"))
        if name:
            override.recipient_name = "" if case.complainant and name == case.complainant.full_name else name
        stamp = f"จากทะเบียนราษฎร (PDF {rec.file_name}) {timezone.localtime():%d/%m/%Y}"
        override.note = stamp if not override.note else f"{override.note} · {stamp}"[:1000]
        override.updated_by = request.user
        override.source_snapshot = complainant_address_snapshot(case.complainant)
        override.save()
        rec.case, rec.state, rec.page_text = case, DopaState.APPLIED, ""
        rec.done_by, rec.done_at = request.user, timezone.now()
        rec.data = {**(rec.data or {}), "address": override.address().as_dict(), "recipient_name": name}
        rec.save(update_fields=["case", "state", "page_text", "done_by", "done_at", "data"])
    case._override_cache = override
    log(request, AuditAction.EDIT, case, f"ที่อยู่จาก PDF ทะเบียนราษฎร {case.receipt_no}: {before} → "
                                         f"{override.address().format()}")
    return override


def _next(request):
    nxt = _records(request.user).filter(state=DopaState.PENDING).order_by("uploaded_at", "pk").first()
    return redirect("dopa_review", pk=nxt.pk) if nxt else redirect(reverse("dopa_home"))


@require_POST
@perms.require(perms.can_issue_any)
def dopa_discard_all(request):
    """Clear the list: every record still waiting (only the user's own, for call staff)."""
    qs = _records(request.user).filter(state=DopaState.PENDING)
    n = qs.update(state=DopaState.DISCARDED, page_text="", done_by=request.user, done_at=timezone.now())
    log(request, AuditAction.EDIT, None, f"ไม่ใช้ข้อมูลจาก PDF ทะเบียนราษฎรที่ค้างตรวจ {n} รายการ")
    messages.info(request, f"ล้างรายการที่ค้างตรวจ {n} รายการแล้ว")
    return redirect("dopa_home")
