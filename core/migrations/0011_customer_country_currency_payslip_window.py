# Generated manually for LoanWise — customer profile, loan currency, payslip window

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_documentrequirement_min_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="annual_income",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Annual net income declared at registration (same currency as income_currency).",
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="country",
            field=models.CharField(
                blank=True,
                default="",
                help_text="ISO 3166-1 alpha-2 country code.",
                max_length=2,
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="income_currency",
            field=models.CharField(
                choices=[("MGA", "MGA (Ariary)"), ("EUR", "EUR (Euro)"), ("MUR", "MUR (Mauritius rupee)")],
                default="EUR",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="loanapplication",
            name="amount_currency",
            field=models.CharField(
                choices=[("MGA", "MGA (Ariary)"), ("EUR", "EUR (Euro)"), ("MUR", "MUR (Mauritius rupee)")],
                default="EUR",
                help_text="Currency of the requested loan amount.",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="documentrequirement",
            name="payslip_distinct_months_window",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="If set (e.g. 3), payslips must cover that many distinct calendar months within the recent window.",
                null=True,
            ),
        ),
    ]
