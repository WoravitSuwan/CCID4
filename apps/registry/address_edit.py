"""Editing the address used for summonses (AddressOverride) - shared by the case page and the call page."""
from __future__ import annotations

from django.contrib import messages

from apps.core.audit import log
from apps.core.models import AuditAction

from .forms import AddressOverrideForm
from .models import AddressOverride, Case, complainant_address_snapshot

PREFILL_FIELDS = ["house_no", "moo", "building", "soi", "road", "subdistrict", "district", "province"]


def address_form(case: Case, prefix: str | None = None) -> AddressOverrideForm:
    """Unbound form: the saved override, or the complainant's address from the file as a starting point."""
    override = case.get_override()
    form = AddressOverrideForm(instance=override, prefix=prefix)
    if override is None and case.complainant:
        c = case.complainant
        form.initial.update({f: getattr(c, f) for f in PREFILL_FIELDS})
        form.initial["postcode"] = c.effective_postcode
    return form


def save_address(request, case: Case, prefix: str | None = None) -> bool:
    """Save the posted address as the case's AddressOverride (never overwritten by imports)."""
    override = case.get_override() or AddressOverride(receipt_no=case.receipt_no)
    before = override.address().format() if override.pk else "(ที่อยู่จากไฟล์ผู้แจ้ง)"
    form = AddressOverrideForm(request.POST, instance=override, prefix=prefix)
    if not form.is_valid():
        messages.error(request, "ข้อมูลไม่ถูกต้อง: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return False
    obj = form.save(commit=False)
    obj.updated_by = request.user
    obj.source_snapshot = complainant_address_snapshot(case.complainant)
    obj.save()
    log(request, AuditAction.EDIT, case, f"แก้ที่อยู่ผู้กล่าวหา {case.receipt_no}: {before} → {obj.address().format()}")
    messages.success(request, "บันทึกที่อยู่แล้ว ระบบจะใช้ที่อยู่นี้ในการออกหมาย (ไม่ถูกทับเมื่อนำเข้าไฟล์ใหม่)")
    return True
