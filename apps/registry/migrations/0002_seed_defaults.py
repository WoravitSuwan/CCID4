from django.db import migrations


def seed(apps, schema_editor):
    from apps.registry.defaults import DEFAULT_STATUSES, DEFAULT_UNITS

    Status = apps.get_model("registry", "Status")
    Unit = apps.get_model("registry", "Unit")
    for order, (name, final) in enumerate(DEFAULT_STATUSES):
        Status.objects.get_or_create(name=name, defaults={"is_final": final, "sort_order": order})
    for unit in DEFAULT_UNITS:
        Unit.objects.get_or_create(name=unit["name"], defaults=unit)


class Migration(migrations.Migration):
    dependencies = [("registry", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
