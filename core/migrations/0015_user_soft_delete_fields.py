# User closure from admin: deleted_at + previous_email (frees real email for re-registration).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_remove_user_username"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Set when the account is closed from the admin; login email is freed for a new registration.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="previous_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="Last login email before closure (audit).",
                max_length=254,
            ),
        ),
    ]
