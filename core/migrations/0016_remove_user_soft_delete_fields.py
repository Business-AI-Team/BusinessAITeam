# Revert soft-delete columns: admin deletes User rows for real (CASCADE).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_user_soft_delete_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="user",
            name="previous_email",
        ),
    ]
