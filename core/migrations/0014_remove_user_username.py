# Login identifier: email (+ CIN on Customer profile). No separate username column.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_alter_customer_annual_income"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="username",
        ),
    ]
