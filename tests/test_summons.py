import datetime as dt
import re
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.documents import docx_engine
from apps.documents.models import ExportJob, ExportMode, JobStatus, Summons
from apps.documents.placeholders import default_appointment_date
from apps.documents.services import IssueOptions, issue, plan_issue, run_export
from apps.registry.models import AddressOverride, Case

pytestmark = pytest.mark.django_db

ISSUE = dt.date(2026, 2, 9)


def opts(**kw):
    base = dict(issue_date=ISSUE, appointment_date=default_appointment_date(ISSUE))
    base.update(kw)
    return IssueOptions(**base)


def issuable_cases():
    return [c for c in Case.objects.not_final().select_related("complainant", "unit", "status",
                                                               "investigator_alias__investigator")
            if not c.issue_blockers()]


def pdf_text(path):
    """pdftotext (poppler) extracts Thai correctly; pypdf mangles some glyph clusters."""
    import subprocess

    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                          check=True).stdout


def test_default_appointment():
    assert default_appointment_date(dt.date(2026, 2, 9)) == dt.date(2026, 2, 23)  # Monday
    assert default_appointment_date(dt.date(2026, 2, 7)) == dt.date(2026, 2, 23)  # Sat -> Mon
    assert default_appointment_date(dt.date(2026, 2, 8)) == dt.date(2026, 2, 23)  # Sun+14 = Sun -> Mon


def test_blockers(setup_signers):
    open_cases = Case.objects.not_final()
    assert len(issuable_cases()) == 26  # 32 open - 6 incomplete addresses
    incomplete = open_cases.address_incomplete().first()
    assert any("ที่อยู่ไม่ครบ" in b for b in incomplete.issue_blockers())


def test_issue_one(setup_signers, admin_user, tmp_path):
    case = issuable_cases()[0]
    plan = plan_issue(admin_user, [case], opts())
    assert not plan.blocked_items, plan.blocked_items[0].problems if plan.blocked_items else ""
    [summons] = issue(admin_user, plan, opts())
    assert summons.number == 1
    docx_text = docx_engine.document_text(summons.docx_file.path)
    assert "{" not in docx_text and "}" not in docx_text and "+14" not in docx_text
    assert "กุมภาพันธ์" in docx_text and "๖๙" in docx_text and "๒๓" in docx_text
    assert not re.search(r"25(68|69)", docx_text)  # dates use Thai digits
    from apps.documents.services import ensure_pdf

    pdf = ensure_pdf(summons)
    text = pdf_text(pdf)
    assert "{" not in text and "}" not in text
    assert "กุมภาพันธ์" in text
    assert AuditLog.objects.filter(action="issue", target_id=str(summons.pk)).exists()
    # next one is ครั้งที่ 2
    plan2 = plan_issue(admin_user, [case], opts())
    assert next(plan2.items).number == 2


def test_legacy_template_also_works(setup_signers, admin_user):
    from apps.documents.models import DocTemplate

    v1 = DocTemplate.objects.get(kind="summons", version=1)
    case = issuable_cases()[0]
    plan = plan_issue(admin_user, [case], opts(template_id=v1.pk))
    assert not plan.blocked_items, plan.blocked_items[0].problems
    [summons] = issue(admin_user, plan, opts(template_id=v1.pk))
    text = docx_engine.document_text(summons.docx_file.path)
    assert "+14" not in text and "{" not in text and "๒๓" in text and "กุมภาพันธ์" in text


def test_issue_32_merged_pdf(setup_signers, admin_user):
    # fix the 6 incomplete addresses with overrides so all 32 open cases can be issued
    for case in Case.objects.not_final().address_incomplete():
        c = case.complainant
        AddressOverride.objects.create(
            receipt_no=case.receipt_no, house_no=c.house_no or "99", moo=c.moo, subdistrict=c.subdistrict or "สุเทพ",
            district=c.district or "เมืองเชียงใหม่", province=c.province or "เชียงใหม่", postcode="50200",
            source_snapshot=c.address().as_dict(),
        )
    cases = list(Case.objects.not_final())
    assert len(cases) == 32
    plan = plan_issue(admin_user, cases, opts(stagger=True))
    assert not plan.blocked_items, [i.problems for i in plan.blocked_items]
    created = issue(admin_user, plan, opts(stagger=True))
    assert len(created) == 32
    job = ExportJob.objects.create(format="pdf", mode=ExportMode.MERGED, created_by=admin_user)
    job.summonses.set(created)
    final = run_export(job)
    job.refresh_from_db()
    assert job.status == JobStatus.DONE
    assert final.suffix == ".pdf"
    text = pdf_text(final)
    assert "{" not in text
    for s in created:
        assert s.recipient_name.split()[0][-4:] in text.replace("\n", " ")
    pages = len(PdfReader(str(final)).pages)
    single = len(PdfReader(str(__import__("apps.documents.services", fromlist=["x"]).ensure_pdf(created[0]))).pages)
    assert pages == 32 * single
    # stagger: times inside a signer group go up by 30 minutes
    times = sorted({s.appointment_at for s in created if s.issuer_id == created[0].issuer_id})
    assert len(times) > 1 and times[1] - times[0] == dt.timedelta(minutes=30)


def test_export_zip_with_envelopes(setup_signers, admin_user):
    cases = issuable_cases()[:3]
    plan = plan_issue(admin_user, cases, opts())
    created = issue(admin_user, plan, opts())
    job = ExportJob.objects.create(format="both", mode=ExportMode.ZIP, include_envelope=True, created_by=admin_user)
    job.summonses.set(created)
    final = run_export(job)
    names = zipfile.ZipFile(final).namelist()
    assert len(names) == 6  # 3 people x (docx + pdf), summons + envelope in one file each
    s = created[0]
    stem = f"{s.case.receipt_no}_{' '.join(s.recipient_name.split())}"
    assert f"{stem}.docx" in names and f"{stem}.pdf" in names
    with zipfile.ZipFile(final) as zf:
        zf.extract(f"{stem}.docx", Path(final).parent / "x")
        zf.extract(f"{stem}.pdf", Path(final).parent / "x")
    text = docx_engine.document_text(Path(final).parent / "x" / f"{stem}.docx")
    assert s.case.receipt_no in text or "หมายเรียก" in text
    pages = PdfReader(str(Path(final).parent / "x" / f"{stem}.pdf")).pages
    single = len(PdfReader(str(__import__("apps.documents.services", fromlist=["x"]).ensure_pdf(s))).pages)
    assert len(pages) == single + 1  # summons pages, then the envelope
    first, last = pages[0].mediabox, pages[-1].mediabox
    assert first.height > first.width and last.height > last.width  # summons and envelope both A4 portrait
    assert "กรุณาส่ง" in text and "{" not in text


def test_outsource_can_issue_any_open_case_via_service(setup_signers):
    user = User.objects.create_user("call9", password="x" * 10, role=Role.OUTSOURCE)
    plan = plan_issue(user, issuable_cases()[:2], opts())
    assert len(plan.ok_items) == 2  # shared call list: no assignment needed


def test_unlinked_investigator_blocks(setup_signers, admin_user):
    case = issuable_cases()[0]
    alias = case.investigator_alias
    alias.investigator = None
    alias.save()
    case = Case.objects.get(pk=case.pk)
    plan = plan_issue(admin_user, [case], opts())
    assert plan.blocked_items and any("ยังไม่ได้ผูก" in p for p in plan.blocked_items[0].problems)


def test_unmatched_case_with_override(setup_signers, admin_user):
    case = issuable_cases()[0]
    case.complainant = None
    case.save()
    assert any("จับคู่" in b for b in Case.objects.get(pk=case.pk).issue_blockers())
    AddressOverride.objects.create(receipt_no=case.receipt_no, house_no="1", subdistrict="สุเทพ",
                                   district="เมืองเชียงใหม่", province="เชียงใหม่", postcode="50200")
    case = Case.objects.get(pk=case.pk)
    assert case.issue_blockers() == []
    assert case.recipient_name() == case.victim_name


def test_status_change_option(setup_signers, admin_user):
    case = next(c for c in issuable_cases() if c.status.name != "ออกหมายเรียก")
    issue(admin_user, plan_issue(admin_user, [case], opts()), opts())
    case.refresh_from_db()
    assert case.status.name != "ออกหมายเรียก"
    o = opts(change_status=True)
    issue(admin_user, plan_issue(admin_user, [case], o), o)
    case.refresh_from_db()
    assert case.status.name == "ออกหมายเรียก"
    assert Summons.objects.filter(case=case).count() == 2
