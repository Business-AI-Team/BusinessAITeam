"""Keep Account (User) ↔ Customer / BackOffice profiles aligned with portal_role (ERD)."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import BackOffice, Customer, PortalRole, User


@receiver(post_save, sender=User)
def sync_er_profiles_for_user(sender, instance: User, **kwargs) -> None:
    role = getattr(instance, "portal_role", None)
    if role == PortalRole.CUSTOMER:
        Customer.objects.update_or_create(
            user=instance,
            defaults={
                "email": instance.email or "",
                "first_name": instance.first_name or "",
                "last_name": instance.last_name or "",
            },
        )
    elif role == PortalRole.BACKOFFICE:
        BackOffice.objects.update_or_create(
            user=instance,
            defaults={
                "email": instance.email or "",
                "first_name": instance.first_name or "",
                "last_name": instance.last_name or "",
            },
        )
