from django.conf import settings
from django.db import migrations


def create_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Cashier")
    Group.objects.get_or_create(name="Owner")


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("pos", "0009_auditlog"),
    ]

    operations = [
        migrations.RunPython(create_roles, migrations.RunPython.noop),
    ]
