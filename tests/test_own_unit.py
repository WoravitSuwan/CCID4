"""This system is for กก.1 only: other units' cases are not imported, and old ones are taken out."""
import pytest
from django.utils import timezone

from apps.calls.models import CallAttempt, CallOutcome
from apps.calls.services import record_call
from apps.imports.models import ImportKind
from apps.registry.models import Case, is_own_unit, other_unit_cases
from tests.conftest import SAMPLE, make_batch, run_import

pytestmark = pytest.mark.django_db
REGISTER = SAMPLE / "mock_case_register.xlsx"
OTHER = ["C699900001", "C699900002", "C699900003"]  # the sample's กก.2 rows


def test_unit_names_compare_loosely(settings):
    settings.OWN_UNIT = "กก.1 บก.สอท.4"
    assert is_own_unit("กก.1 บก.สอท.4") and is_own_unit(" กก. ๑  บก.สอท.๔ ") and is_own_unit("")
    assert not is_own_unit("กก.2 บก.สอท.4") and not is_own_unit("กก.๒ บก.สอท.๔")


def test_register_import_keeps_only_own_unit(imported):
    s = imported["register"].summary
    assert s["other_units"] == {"กก.2 บก.สอท.4": 3} and s["other_unit_rows"] == 3 and s["total_rows"] == 40
    assert not Case.objects.filter(receipt_no__in=OTHER).exists()
    assert Case.objects.count() == 40 and not other_unit_cases().exists()


def test_reimport_takes_out_old_other_unit_cases_but_never_a_summons(settings, imported, setup_signers, admin_user):
    from apps.documents.models import Summons, TemplateKind
    from apps.documents.services import get_template
    from apps.registry.models import Investigator

    # a database from before the one-unit rule: the กก.2 cases were imported too
    settings.OWN_UNIT = "กก.2 บก.สอท.4"
    run_import(ImportKind.REGISTER, REGISTER)
    c1, c2, c3 = (Case.objects.get(receipt_no=r) for r in OTHER)
    own_before = set(Case.objects.exclude(receipt_no__in=OTHER).values_list("pk", flat=True))
    record_call(c1, admin_user, outcome=CallOutcome.OFF)
    Summons.objects.create(case=c3, number=1, issue_date=timezone.localdate(), appointment_at=timezone.now(),
                           issuer=Investigator.objects.first(), template=get_template(TemplateKind.SUMMONS),
                           template_version=1, created_by=admin_user)
    settings.OWN_UNIT = "กก.1 บก.สอท.4"
    assert other_unit_cases().count() == 3

    from apps.imports.services import analyze, summary_dict

    preview = summary_dict(analyze(make_batch(ImportKind.REGISTER, REGISTER)))  # shown before confirming
    assert (preview["remove_count"], preview["remove_calls"], preview["kept_with_summons"]) == (2, 1, 1)
    done = run_import(ImportKind.REGISTER, REGISTER).summary
    assert done["removed"] == 2
    assert list(Case.objects.filter(receipt_no__in=OTHER).values_list("receipt_no", flat=True)) == [OTHER[2]]
    assert not CallAttempt.objects.filter(case_id=c1.pk).exists()
    # every case the file lists under กก.1 is still there
    assert own_before <= set(Case.objects.values_list("pk", flat=True))


def test_admin_sees_a_notice_while_other_unit_cases_remain(settings, admin_client, imported):
    settings.OWN_UNIT = "กก.2 บก.สอท.4"
    run_import(ImportKind.REGISTER, REGISTER)
    settings.OWN_UNIT = "กก.1 บก.สอท.4"
    page = admin_client.get("/").content.decode()
    assert "ในระบบยังมีคดีของหน่วยอื่น 3 คดี" in page and "ระบบนี้ใช้เฉพาะ กก.1 บก.สอท.4" in page
