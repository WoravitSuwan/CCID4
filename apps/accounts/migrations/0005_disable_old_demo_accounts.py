from django.contrib.auth.hashers import check_password
from django.db import migrations

OLD_DEMO_USERNAMES = ["head", "clerk", "inv", "call2"]
DEMO_PASSWORD = "demo12345"


def disable_old_demo_accounts(apps, schema_editor):
    """Only two accounts are wanted (admin + one outsource caller). Switch off the demo logins
    created by earlier versions of load_demo - only while they still have the demo password, so a
    real account that happens to use one of these names is left alone."""
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(username__in=OLD_DEMO_USERNAMES, is_active=True):
        if check_password(DEMO_PASSWORD, user.password):
            user.is_active = False
            user.save(update_fields=["is_active"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_two_roles")]
    operations = [migrations.RunPython(disable_old_demo_accounts, migrations.RunPython.noop)]
