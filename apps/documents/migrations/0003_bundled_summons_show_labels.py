from django.db import migrations


def show_labels(apps, schema_editor):
    """The agency prints the form on blank A4 with its labels: show hidden text for bundled templates."""
    DocTemplate = apps.get_model("documents", "DocTemplate")
    DocTemplate.objects.filter(kind="summons", uploaded_by__isnull=True).update(show_hidden_text=True)


class Migration(migrations.Migration):
    dependencies = [("documents", "0002_doctemplate_show_hidden_text")]
    operations = [migrations.RunPython(show_labels, migrations.RunPython.noop)]
