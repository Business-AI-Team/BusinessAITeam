import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        # 1. Add the two new fields (nullable/defaulted so existing rows are fine)
        migrations.AddField(
            model_name="documentrequirement",
            name="name",
            field=models.CharField(
                default="",
                max_length=255,
                verbose_name="Name",
                help_text="e.g. Payslip, ID Card",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documentrequirement",
            name="is_mandatory",
            field=models.BooleanField(
                default=True,
                verbose_name="Is Mandatory",
                help_text="If true, the loan request is blocked until this document is uploaded.",
            ),
        ),
        migrations.AddField(
            model_name="documentrequirement",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        # 2. Remove the old fields
        migrations.RemoveField(model_name="documentrequirement", name="code"),
        migrations.RemoveField(model_name="documentrequirement", name="label_fr"),
        migrations.RemoveField(model_name="documentrequirement", name="label_en"),
        migrations.RemoveField(model_name="documentrequirement", name="description_fr"),
        migrations.RemoveField(model_name="documentrequirement", name="description_en"),
        migrations.RemoveField(model_name="documentrequirement", name="applies_to_loan_types"),
        migrations.RemoveField(model_name="documentrequirement", name="is_required"),
        migrations.RemoveField(model_name="documentrequirement", name="min_files"),
        migrations.RemoveField(model_name="documentrequirement", name="sort_order"),
        migrations.RemoveField(model_name="documentrequirement", name="active"),
        # 3. Update ordering
        migrations.AlterModelOptions(
            name="documentrequirement",
            options={
                "ordering": ["name"],
                "verbose_name": "Document Requirement",
                "verbose_name_plural": "Document Requirements",
            },
        ),
    ]
