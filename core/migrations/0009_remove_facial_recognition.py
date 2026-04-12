# Remove facial recognition fields and legacy document kinds.

from django.db import migrations, models


def deactivate_face_requirements_and_remap_docs(apps, schema_editor):
    DocumentRequirement = apps.get_model("core", "DocumentRequirement")
    ApplicationDocument = apps.get_model("core", "ApplicationDocument")
    DocumentRequirement.objects.filter(code="face_selfie").update(is_required=False, active=False)
    ApplicationDocument.objects.filter(kind__in=("face_selfie", "liveness_video")).update(kind="generic")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_loanapplication_customer_required"),
    ]

    operations = [
        migrations.RunPython(deactivate_face_requirements_and_remap_docs, noop),
        migrations.RemoveField(
            model_name="loanapplication",
            name="face_verification",
        ),
        migrations.RemoveField(
            model_name="loanapplication",
            name="liveness_verification",
        ),
        migrations.AlterField(
            model_name="applicationdocument",
            name="kind",
            field=models.CharField(
                choices=[
                    ("generic", "Generic"),
                    ("identity", "Identity"),
                    ("income", "Income proof"),
                ],
                default="generic",
                max_length=32,
            ),
        ),
    ]
