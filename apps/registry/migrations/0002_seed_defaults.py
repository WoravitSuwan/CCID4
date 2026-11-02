from django.db import migrations


def seed(apps, schema_editor):
    from apps.registry.defaults import DEFAULT_STATUSES, DEFAULT_UNITS

    Status = apps.get_model("registry", "Status")
    Unit = apps.get_model("registry", "Unit")
    for order, (name, final) in enumerate(DEFAULT_STATUSES):
        Status.objects.get_or_create(name=name, defaults={"is_final": final, "sort_order": order})
    fields = {f.name for f in Unit._meta.get_fields()}
    for unit in DEFAULT_UNITS:
        values = {k: v for k, v in unit.items() if k in fields}  # later fields are set by later migrations
        Unit.objects.get_or_create(name=values["name"], defaults=values)


class Migration(migrations.Migration):
    dependencies = [("registry", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
