"""The ข้อมูลทดลอง feature is removed: delete any test case still in the database, with what was recorded on
it (summonses, calls, attachments, typed address), then drop the flag. Register cases are not touched."""
from django.db import migrations


def delete_test_cases(apps, schema_editor):
    Case = apps.get_model("registry", "Case")
    Summons = apps.get_model("documents", "Summons")
    CallAttachment = apps.get_model("calls", "CallAttachment")
    AddressOverride = apps.get_model("registry", "AddressOverride")

    tests = Case.objects.filter(is_test=True)
    if not tests.exists():
        return
    receipts = list(tests.values_list("receipt_no", flat=True))
    for summons in Summons.objects.filter(case__in=tests):
        for f in (summons.docx_file, summons.pdf_file):
            if f:
                f.delete(save=False)
        summons.delete()
    for attachment in CallAttachment.objects.filter(case__in=tests):
        if attachment.file:
            attachment.file.delete(save=False)
    AddressOverride.objects.filter(receipt_no__in=receipts).delete()
    tests.delete()  # call assignment, calls and attachments go with the case


class Migration(migrations.Migration):
    dependencies = [
        ("registry", "0006_case_is_test"),
        ("documents", "0005_summons_ems"),
        ("calls", "0003_shared_call_pool"),
    ]

    operations = [
        migrations.RunPython(delete_test_cases, migrations.RunPython.noop),
        migrations.RemoveField(model_name="case", name="is_test"),
    ]
