from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import BackOffice


class Command(BaseCommand):
    help = "Create a BackOffice account safely"

    def handle(self, *args, **kwargs):

        # ─────────────────────────────
        # DATA
        # ─────────────────────────────
        email = "admin.backoffice@test.com"
        username = "backoffice_admin"
        password = "test12345"

        cin_number = "BO-0001"
        first_name = "Admin"
        last_name = "LoanWise"

        self.stdout.write("Starting BackOffice creation...")

        User = get_user_model()

        # ─────────────────────────────
        # CREATE / GET BACKOFFICE PROFILE
        # ─────────────────────────────
        backoffice_profile, _ = BackOffice.objects.get_or_create(
            cin_number=cin_number,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
            }
        )

        self.stdout.write("BackOffice profile ready")

        # ─────────────────────────────
        # CHECK IF ALREADY LINKED
        # ─────────────────────────────
        existing_account = User.objects.filter(
            back_office=backoffice_profile
        ).first()

        if existing_account:
            self.stdout.write(self.style.WARNING("BackOffice already linked to account"))
            self.stdout.write(f"Email: {existing_account.email}")
            return

        # ─────────────────────────────
        # CREATE ACCOUNT
        # ─────────────────────────────
        account, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": username,
                "account_type": "backoffice",
                "email_verified": True,
                "back_office": backoffice_profile,
            }
        )

        if created:
            account.set_password(password)
            account.save()
            self.stdout.write(self.style.SUCCESS("BackOffice account created"))
        else:
            self.stdout.write(self.style.WARNING("Account already exists"))

        self.stdout.write(self.style.SUCCESS(f"Email: {email}"))