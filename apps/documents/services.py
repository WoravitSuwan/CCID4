"""Issuing summonses and exporting documents."""
from __future__ import annotations

import copy
import io
import datetime as dt
import logging
import shutil
import tempfile
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core import permissions as perms
from apps.core.audit import log
from apps.core.models import AuditAction
from apps.registry.defaults import SUMMONS_STATUS_NAME
from apps.registry.models import Case, Status

from . import docx_engine
from .models import DocTemplate, ExportJob, ExportMode, JobStatus, Summons, TemplateKind
from .pdf import convert_many
from .placeholders import build_values, check_section_53

logger = logging.getLogger(__name__)
SLOT_MINUTES = 30


# --------------------------------------------------------------------------- templates
class TemplateMissing(Exception):
    pass


BUNDLED_TEMPLATES = [
    # kind, version, name, file in templates_docx/, make active
    (TemplateKind.SUMMONS, 1, "แบบฟอร์มเดิม (วันนัด +14)", "summons_template_sanitized.docx", False),
    (TemplateKind.SUMMONS, 2, "แบบฟอร์มเวอร์ชันใหม่ (วัน/เดือน/ปีนัด ครบ)", "summons_template_v2.docx", True),
    (TemplateKind.ENVELOPE, 1, "ใบจ่าหน้าซอง A4 แนวตั้ง (แบบง่าย)", "envelope_template.docx", False),
    (TemplateKind.ENVELOPE, 2, "ใบจ่าหน้าซองราชการ A4 แนวนอน (ครุฑ + กรอบฝากส่ง)", "envelope_official.docx", True),
]


def ensure_default_templates(user=None) -> list[DocTemplate]:
    """Register the bundled templates (templates_docx/) and keep them up to date.

    Bundled rows (uploaded_by is empty) are system-managed: when the file in
    templates_docx/ changes, the stored copy is replaced. Templates uploaded by
    staff are never touched.
    """
    created = []
    for kind, version, name, filename, make_active in BUNDLED_TEMPLATES:
        src = Path(settings.TEMPLATES_DOCX_DIR) / filename
        if not src.exists():
            continue
        content = src.read_bytes()
        tpl = DocTemplate.objects.filter(kind=kind, version=version).first()
        if tpl is None:
            tpl = DocTemplate(kind=kind, version=version, name=name, uploaded_by=user,
                              show_hidden_text=kind == TemplateKind.SUMMONS)
            tpl.file.save(filename, File(io.BytesIO(content)), save=False)
            tpl.placeholders = docx_engine.scan_placeholders(tpl.file.path)["placeholders"]
            tpl.save()
            created.append(tpl)
        elif tpl.uploaded_by_id is None:
            path = Path(tpl.file.path) if tpl.file else None
            if path is None or not path.exists() or path.read_bytes() != content:
                if tpl.file:
                    tpl.file.delete(save=False)
                tpl.file.save(filename, File(io.BytesIO(content)), save=False)
                tpl.placeholders = docx_engine.scan_placeholders(tpl.file.path)["placeholders"]
                tpl.name = name
                tpl.save()
                _template_cache.clear()
        if make_active:
            current = DocTemplate.active(kind)
            if current is None or (current.uploaded_by_id is None and current.version < version):
                tpl.activate()
    return created


def get_template(kind, template_id=None) -> DocTemplate:
    tpl = DocTemplate.objects.filter(pk=template_id, kind=kind).first() if template_id else DocTemplate.active(kind)
    if tpl is None:
        ensure_default_templates()
        tpl = DocTemplate.active(kind)
    if tpl is None:
        raise TemplateMissing(f"ยังไม่มีแบบฟอร์ม{TemplateKind(kind).label}ที่ใช้งานอยู่")
    return tpl


_template_cache: dict = {}


def template_info(tpl: DocTemplate) -> dict:
    key = (tpl.pk, tpl.file.name)
    if key not in _template_cache:
        info = docx_engine.scan_placeholders(tpl.file.path)
        info["text"] = docx_engine.document_text(tpl.file.path)
        _template_cache[key] = info
    return _template_cache[key]


# --------------------------------------------------------------------------- planning
@dataclass
class IssueOptions:
    issue_date: dt.date
    appointment_date: dt.date
    start_time: dt.time = dt.time(9, 0)
    stagger: bool = False
    reason: str = "มาให้การต่อพนักงานสอบสวนตามประเด็นที่เกี่ยวข้องกับคดี"
    change_status: bool = False
    thai_digits: bool = True
    template_id: int | None = None


@dataclass
class PlannedSummons:
    case: Case
    number: int
    appointment_at: dt.datetime
    place: str
    values: dict
    problems: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.problems


@dataclass
class IssuePlan:
    template: DocTemplate
    groups: "OrderedDict[object, list[PlannedSummons]]" = field(default_factory=OrderedDict)

    @property
    def items(self):
        for items in self.groups.values():
            yield from items

    @property
    def ok_items(self):
        return [i for i in self.items if i.ok]

    @property
    def blocked_items(self):
        return [i for i in self.items if not i.ok]


def next_number(case: Case) -> int:
    return (case.summonses.aggregate(m=Max("number"))["m"] or 0) + 1


def plan_issue(user, cases, opts: IssueOptions) -> IssuePlan:
    tpl = get_template(TemplateKind.SUMMONS, opts.template_id)
    info = template_info(tpl)
    plan = IssuePlan(template=tpl)
    cases = sorted(cases, key=lambda c: (str(c.investigator or "~"), c.receipt_no))
    slot_by_group: dict = {}
    for case in cases:
        inv = case.investigator
        group = plan.groups.setdefault(inv, [])
        slot = slot_by_group.get(inv, 0)
        start = dt.datetime.combine(opts.appointment_date, opts.start_time)
        if opts.stagger:
            start += dt.timedelta(minutes=SLOT_MINUTES * slot)
            slot_by_group[inv] = slot + 1
        appointment_at = timezone.make_aware(start)
        place = case.unit.appointment_place if case.unit else ""
        number = next_number(case)
        values = build_values(case=case, issue_date=opts.issue_date, appointment_at=appointment_at,
                              place=place, reason=opts.reason, issuer=inv, number=number,
                              thai_digits=opts.thai_digits)
        problems = []
        if not perms.can_issue_for_case(user, case):
            problems.append("ไม่มีสิทธิ์ออกหมายในคดีนี้ (ออกได้เฉพาะคดีของตนเอง)")
        blockers = case.issue_blockers()
        problems += blockers
        if not opts.reason.strip():
            problems.append("ไม่ได้ระบุเหตุที่เรียก")
        if opts.appointment_date < opts.issue_date:
            problems.append("วันนัดต้องไม่ก่อนวันออกหมาย")
        unknown = [n for n in info["placeholders"] if n not in values]
        if unknown:
            problems.append("แบบฟอร์มมีตัวแทนที่ระบบไม่รู้จัก: " + ", ".join(f"{{{n}}}" for n in unknown))
        if not blockers:  # otherwise the same gaps are already explained above
            problems += check_section_53(values, info, info["text"])
            missing = [n for n in info["placeholders"] if n in values and not str(values[n]).strip()]
            if missing:
                problems.append("ไม่มีข้อมูลสำหรับ: " + ", ".join(f"{{{n}}}" for n in missing))
        group.append(PlannedSummons(case=case, number=number, appointment_at=appointment_at,
                                    place=place, values=values, problems=_dedupe(problems)))
    return plan


def _dedupe(items):
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# --------------------------------------------------------------------------- rendering
def render_docx(tpl: DocTemplate, values: dict, out_path, optional=frozenset()) -> docx_engine.RenderReport:
    report = docx_engine.render(tpl.file.path, values, out_path, show_hidden=tpl.show_hidden_text,
                                optional=optional)
    if not report.ok:
        raise ValueError("; ".join(report.problems()))
    return report


def preview_pdf(planned: list[PlannedSummons], tpl: DocTemplate) -> bytes:
    """PDF of the given planned summonses (not saved, nothing recorded)."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for idx, item in enumerate(planned):
            out = Path(tmp) / f"preview_{idx:04d}.docx"
            docx_engine.render(tpl.file.path, item.values, out, show_hidden=tpl.show_hidden_text)
            paths.append(out)
        merged = Path(tmp) / "preview.docx"
        merge_docx(paths, merged)
        pdf = convert_many([merged], tmp)[0]
        return pdf.read_bytes()


def _media_path(rel: str) -> Path:
    path = Path(settings.MEDIA_ROOT) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_docx(summons: Summons) -> Path:
    """Return the summons .docx, re-creating it from the stored values if it was cleaned up."""
    if summons.docx_file and Path(summons.docx_file.path).exists():
        return Path(summons.docx_file.path)
    rel = f"summons/{summons.issue_date:%Y%m}/{summons.pk}_{summons.base_filename()}.docx"
    out = _media_path(rel)
    render_docx(summons.template, complete_context(summons), out)
    summons.docx_file.name = rel
    summons.save(update_fields=["docx_file"])
    return out


def ensure_pdf(summons: Summons) -> Path:
    if summons.pdf_file and Path(summons.pdf_file.path).exists():
        return Path(summons.pdf_file.path)
    docx = ensure_docx(summons)
    pdf = convert_many([docx], docx.parent)[0]
    summons.pdf_file.name = str(pdf.relative_to(settings.MEDIA_ROOT))
    summons.save(update_fields=["pdf_file"])
    return pdf


# Envelope fields that may legitimately be empty (no postcode found, no permit box text, ...)
ENVELOPE_OPTIONAL = frozenset({"รหัสไปรษณีย์", "ใบอนุญาตฝากส่ง", "เลขคดีอาญา", "ที่อยู่หน่วย"})


def complete_context(summons: Summons) -> dict:
    """Stored values + placeholders added after the summons was issued (older summonses)."""
    from apps.core.thai_format import to_thai_digits
    from apps.registry.models import Case

    values = dict(summons.context)
    needed = {"ที่อยู่หลายบรรทัด", "ใบอนุญาตฝากส่ง", "ที่อยู่หลังบ้านเลขที่"}
    if needed <= values.keys():
        return values
    case = Case.objects.select_related("unit", "complainant").get(pk=summons.case_id)
    addr = case.effective_address()
    digits = to_thai_digits if summons.thai_digits else (lambda x: x or "")
    values.setdefault("ที่อยู่หลายบรรทัด", digits("\n".join(addr.lines()) if addr else ""))
    values.setdefault("ใบอนุญาตฝากส่ง", digits(case.unit.postage_permit if case.unit else ""))
    values.setdefault("ที่อยู่หลังบ้านเลขที่", digits(addr.format(house_label=False) if addr else ""))
    return values


def envelope_values(summons: Summons) -> dict:
    """Envelope text uses Arabic digits, like the agency's printed envelopes."""
    from apps.core.thai_format import to_arabic_digits

    return {k: to_arabic_digits(v) for k, v in complete_context(summons).items()}


def render_envelope(summons: Summons, out_path, tpl: DocTemplate | None = None) -> Path:
    tpl = tpl or get_template(TemplateKind.ENVELOPE)
    render_docx(tpl, envelope_values(summons), out_path, optional=ENVELOPE_OPTIONAL)
    return Path(out_path)


# --------------------------------------------------------------------------- issuing
@transaction.atomic
def issue(user, plan: IssuePlan, opts: IssueOptions, request=None) -> list[Summons]:
    items = plan.ok_items
    if not items:
        return []
    created = []
    status = None
    if opts.change_status:
        status, _ = Status.objects.get_or_create(name=SUMMONS_STATUS_NAME, defaults={"is_final": False})
    for item in items:
        case = Case.objects.select_for_update().get(pk=item.case.pk)
        number = next_number(case)
        values = dict(item.values)
        if number != item.number:
            values["ครั้งที่"] = str(number)
        addr = case.effective_address()
        summons = Summons.objects.create(
            case=case, number=number, issue_date=opts.issue_date, appointment_at=item.appointment_at,
            place=item.place, reason=opts.reason, issuer=item.case.investigator, template=plan.template,
            template_version=plan.template.version, thai_digits=opts.thai_digits, context=values,
            recipient_name=case.recipient_name(), recipient_address=addr.format() if addr else "",
            created_by=user,
        )
        ensure_docx(summons)
        if status and case.status_id != status.pk:
            old = case.status.name if case.status_id else "-"
            case.set_status(status, user)
            log(request or user, AuditAction.EDIT, case, f"เปลี่ยนสถานะ {old} → {status.name} (ออกหมายเรียก)")
        log(request or user, AuditAction.ISSUE, summons,
            f"ออกหมายเรียก {case.receipt_no} ครั้งที่ {number} ผู้ลงนาม {summons.issuer} "
            f"(กดออกโดย {getattr(user, 'username', '-')})",
            receipt_no=case.receipt_no, signer_id=summons.issuer_id)
        created.append(summons)
    return created


# --------------------------------------------------------------------------- merging / export
def _ensure_section_break_at_end(doc):
    """Turn the last body paragraph into a 'next page' section break.

    Every appended summons then starts on a new page without adding an extra
    (possibly overflowing) paragraph.
    """
    from docx.oxml.ns import qn

    body = doc.element.body
    sect = body.find(qn("w:sectPr"))
    paragraphs = body.findall(qn("w:p"))
    if sect is None or not paragraphs:
        doc.add_page_break()
        return
    last_p = paragraphs[-1]
    ppr = last_p.get_or_add_pPr()
    existing = ppr.find(qn("w:sectPr"))
    if existing is not None:
        ppr.remove(existing)
    new_sect = copy.deepcopy(sect)
    for el in new_sect.findall(qn("w:type")):
        new_sect.remove(el)
    type_el = new_sect.makeelement(qn("w:type"), {qn("w:val"): "nextPage"})
    new_sect.insert(0, type_el)
    ppr.append(new_sect)


def merge_docx(paths, out_path) -> Path:
    """Concatenate .docx files (each starting on a new page) with docxcompose."""
    from docx import Document
    from docxcompose.composer import Composer

    paths = [Path(p) for p in paths]
    if len(paths) == 1:
        shutil.copyfile(paths[0], out_path)
        return Path(out_path)
    master = Document(str(paths[0]))
    composer = Composer(master)
    for p in paths[1:]:
        _ensure_section_break_at_end(composer.doc)
        composer.append(Document(str(p)))
    composer.save(str(out_path))
    return Path(out_path)


def run_export(job: ExportJob):
    job.status = JobStatus.RUNNING
    job.done = 0
    summonses = list(job.summonses.select_related("case", "issuer", "template")
                     .order_by("issuer__sign_name", "case__receipt_no", "number"))
    want_pdf = job.format in ("pdf", "both")
    want_docx = job.format in ("docx", "both")
    steps = len(summonses) * (int(job.include_summons) + int(job.include_envelope))
    job.total = steps + (2 if want_pdf else 1)
    job.save(update_fields=["status", "done", "total"])
    work = Path(settings.MEDIA_ROOT) / "exports" / f"job_{job.pk}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    env_tpl = get_template(TemplateKind.ENVELOPE) if job.include_envelope else None

    def tick(n=1):
        job.done += n
        ExportJob.objects.filter(pk=job.pk).update(done=job.done)

    summons_files, envelope_files = [], []
    for s in summonses:
        base = s.base_filename()
        if job.include_summons:
            dst = work / "docx" / f"{base}.docx"
            dst.parent.mkdir(exist_ok=True)
            shutil.copyfile(ensure_docx(s), dst)
            summons_files.append(dst)
            tick()
        if job.include_envelope:
            dst = work / "docx" / f"{base}_ซอง.docx"
            dst.parent.mkdir(exist_ok=True)
            render_envelope(s, dst, env_tpl)
            envelope_files.append(dst)
            tick()

    outputs: list[Path] = []
    if job.mode == ExportMode.MERGED:
        stamp = timezone.localtime().strftime("%Y%m%d_%H%M")
        merged = []
        if summons_files:
            merged.append(merge_docx(summons_files, work / f"หมายเรียก_{len(summons_files)}ฉบับ_{stamp}.docx"))
        if envelope_files:
            merged.append(merge_docx(envelope_files, work / f"ใบจ่าหน้าซอง_{len(envelope_files)}ฉบับ_{stamp}.docx"))
        tick()
        if want_pdf:
            outputs += convert_many(merged, work)
            tick()
        if want_docx:
            outputs += merged
    else:
        files = summons_files + envelope_files
        tick()
        if want_pdf:
            outputs += convert_many(files, work / "pdf")
            tick()
        if want_docx:
            outputs += files

    if len(outputs) == 1:
        final = outputs[0]
    else:
        final = work / f"ส่งออกหมายเรียก_{timezone.localtime():%Y%m%d_%H%M}.zip"
        with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in outputs:
                zf.write(f, arcname=f.name)  # non-ASCII names are stored as UTF-8
    job.result_file.name = str(final.relative_to(settings.MEDIA_ROOT))
    job.status = JobStatus.DONE
    job.done = job.total
    job.finished_at = timezone.now()
    job.message = f"เสร็จแล้ว {len(summonses)} ราย"
    job.save()
    return final


def start_export(job: ExportJob):
    """Run small exports inline, large ones in the background (Celery or thread)."""
    from apps.core.background import run_background

    from .tasks import export_job

    if job.summonses.count() > settings.EXPORT_ASYNC_THRESHOLD:
        run_background(export_job, job.pk)
    else:
        export_job(job.pk)
