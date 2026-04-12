# Manual migration: customer FK is required (ERD LoanRequest.CustomerId); DB already populated by 0007.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_erd_customer_loanrequest_notification"),
    ]

    operations = [
        migrations.AlterField(
            model_name="loanapplication",
            name="customer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="loan_requests",
                to="core.customer",
            ),
        ),
    ]
