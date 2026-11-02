import pytest

from apps.imports.models import ImportKind, IssueType
from apps.registry.models import Case, Complainant, Status

from .conftest import SAMPLE, make_batch, run_import

pytestmark = pytest.mark.django_db


def test_counts_and_matching(imported):
    assert Complainant.objects.count() == 50
    assert Case.objects.count() == 40
    assert Case.objects.filter(source_sheet="ปี 2568 (2025)").count() == 15
    assert Case.objects.filter(source_sheet="ปี 2569 (2026)").count() == 25
    assert Case.objects.unmatched().count() == 0
    assert Complainant.objects.filter(case__isnull=True).count() == 10


def test_non_final_and_address_flags(imported):
    open_cases = Case.objects.not_final()
    assert open_cases.count() == 32
    assert open_cases.filter(complainant__flag_missing_house=True).count() == 5
    assert open_cases.filter(complainant__flag_missing_province=True).count() == 2
    assert open_cases.filter(complainant__flag_missing_house=True,
                             complainant__flag_missing_province=True).count() == 1
    assert open_cases.address_incomplete().count() == 6
    assert Case.objects.filter(complainant__flag_missing_house=True).count() == 6
    assert Case.objects.filter(complainant__flag_missing_province=True).count() == 3


def test_reimport_adds_nothing(imported):
    reg = run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    comp = run_import(ImportKind.COMPLAINANTS, SAMPLE / "mock_complainants.xlsx")
    assert Case.objects.count() == 40
    assert Complainant.objects.count() == 50
    assert reg.new_count == 0 and reg.updated_count == 0 and reg.unchanged_count == 40
    assert comp.new_count == 0 and comp.updated_count == 0


def test_status_change_summary(imported):
    case = Case.objects.not_final().first()
    case.status = Status.objects.get(name="สิ้นสุด")
    case.save()
    batch = make_batch(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    from apps.imports.services import analyze, summary_dict

    summary = summary_dict(analyze(batch))
    assert summary["updated"] == 1
    assert "สถานะเปลี่ยน 1 คดี" in summary["changes"]


def test_text_cleaning_and_types(imported):
    c = Complainant.objects.exclude(national_id="").first()
    assert c.national_id.isdigit()
    assert all(x.phone.startswith("0") for x in Complainant.objects.all())
    assert not Complainant.objects.filter(case_type__endswith=" ").exists()
    assert not Complainant.objects.filter(last_name__endswith=" ").exists()
    case = Case.objects.exclude(daily_log="").first()
    assert case.daily_log_at is not None
    assert Case.objects.filter(report_date__isnull=True).count() == 0
    assert Case.objects.filter(report_year=2025).count() == 15


def test_issues_flagged(imported):
    comp = imported["complainants"]
    types = set(comp.issues.values_list("issue_type", flat=True))
    assert IssueType.MISSING_HOUSE in types
    assert IssueType.BAD_ID in types
    assert Complainant.objects.filter(flag_bad_id=True).count() == 1


def test_register_before_complainants_reports_unmatched(db):
    reg = run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    assert reg.unmatched_count == 40
    assert Case.objects.unmatched().count() == 40
    run_import(ImportKind.COMPLAINANTS, SAMPLE / "mock_complainants.xlsx")
    assert Case.objects.unmatched().count() == 0  # auto re-linked


def test_new_statuses_created_non_final(db):
    run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx")
    assert Status.objects.get(name="สิ้นสุด").is_final
    assert not Status.objects.get(name="อายัดบัญชี").is_final


def test_postcode_autofill(imported):
    filled = Complainant.objects.filter(postcode="").exclude(postcode_auto="")
    assert filled.count() >= 30
    c = Complainant.objects.filter(subdistrict="ดอนแก้ว", district="แม่ริม").first()
    if c and not c.postcode:
        assert c.postcode_auto == "50180"


def test_sheet_selection(db):
    reg = run_import(ImportKind.REGISTER, SAMPLE / "mock_case_register.xlsx", sheets=["ปี 2569 (2026)"])
    assert reg.total_rows == 25
    assert Case.objects.count() == 25
