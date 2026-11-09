"""Data migrations do what they say on a database that has the data they look for."""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

BEFORE = [("registry", "0006_case_is_test"), ("documents", "0005_summons_ems"), ("calls", "0003_shared_call_pool")]
AFTER = [("registry", "0007_remove_test_cases")]


@pytest.mark.django_db(transaction=True)
def test_removing_the_test_data_feature_deletes_only_test_cases():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps
    Case, CallAssignment, CallAttempt = (old.get_model("registry", "Case"), old.get_model("calls", "CallAssignment"),
                                         old.get_model("calls", "CallAttempt"))
    test = Case.objects.create(receipt_no="TEST0001", victim_name="ทดลอง", victim_phone="0812345678", is_test=True)
    real = Case.objects.create(receipt_no="C660000001", victim_name="ผู้เสียหายจริง")
    for case in (test, real):
        CallAssignment.objects.create(case=case)
        CallAttempt.objects.create(case=case, number=1, outcome="off", called_at=timezone.now())

    executor = MigrationExecutor(connection)  # reload the graph after migrating
    executor.migrate(AFTER)
    new = executor.loader.project_state(AFTER).apps
    Case, CallAttempt = new.get_model("registry", "Case"), new.get_model("calls", "CallAttempt")
    assert list(Case.objects.values_list("receipt_no", flat=True)) == ["C660000001"]
    assert list(CallAttempt.objects.values_list("case__receipt_no", flat=True)) == ["C660000001"]
    assert "is_test" not in {f.name for f in Case._meta.get_fields()}
