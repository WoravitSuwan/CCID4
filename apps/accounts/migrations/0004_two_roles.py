from django.db import migrations, models


def merge_roles(apps, schema_editor):
    """Only two roles remain: former หัวหน้า / ธุรการ / พนักงานสอบสวน accounts become administrators."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(role__in=["head", "clerk", "investigator"]).update(role="admin", is_staff=True)
    User.objects.filter(role="admin").update(is_staff=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_alter_user_role")]
    operations = [
        migrations.RunPython(merge_roles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("admin", "ผู้ดูแลระบบ"), ("outsource", "เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource)")],
                default="outsource", max_length=20, verbose_name="บทบาท",
            ),
        ),
    ]
