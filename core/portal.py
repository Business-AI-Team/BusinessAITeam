"""Portal access: customer vs backoffice vs Django admin (is_staff)."""

from __future__ import annotations

from core.models import LoanApplication, PortalRole


def is_backoffice_user(user) -> bool:
    """Bank staff portal (UI: /backoffice/). Not the same as Django Admin (is_staff)."""
    if not user.is_authenticated:
        return False
    return getattr(user, "portal_role", None) == PortalRole.BACKOFFICE


def can_access_all_applications(user) -> bool:
    """List/open any application (API + workspace): backoffice role or superuser."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, "portal_role", None) == PortalRole.BACKOFFICE


def can_view_application(user, application: LoanApplication) -> bool:
    if not user.is_authenticated:
        return False
    if application.user_id == user.id:
        return True
    return can_access_all_applications(user)


def get_loan_application_for_portal(user, pk: int) -> LoanApplication | None:
    """Return application if the user may open it (owner or backoffice/superuser)."""
    app = (
        LoanApplication.objects.filter(pk=pk)
        .select_related("user", "customer")
        .prefetch_related("documents__requirement")
        .first()
    )
    if not app:
        return None
    if can_view_application(user, app):
        return app
    return None
