"""
Email verification: send numeric code after signup, confirm via code (or legacy URL token).
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

VERIFICATION_TOKEN_MAX_AGE = timedelta(days=7)


def normalize_verification_code(raw: str | None) -> str:
    """Keep digits only so users may paste '123 456' or '123-456'."""
    if not raw:
        return ""
    return "".join(c for c in raw.strip() if c.isdigit())


def send_registration_verification_email(user: AbstractBaseUser, evt) -> None:
    """Send plain-text email with numeric code and optional link (legacy)."""
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    verify_url = f"{base}/api/auth/verify/?token={evt.token}"
    verify_page_fr = f"{base}/verify-email/"
    verify_page_en = f"{base}/en/verify-email/"
    code = evt.code or ""
    fr = (user.preferred_language or "fr").startswith("fr")
    if fr:
        subject = "LoanWise — votre code de vérification"
        message = (
            f"Votre code de vérification LoanWise : {code}\n\n"
            f"Saisissez ce code sur la page de vérification :\n"
            f"  {verify_page_fr}\n"
            f"  (English UI: {verify_page_en})\n\n"
            f"Ce code expire dans 7 jours.\n\n"
            f"Ou ouvrez ce lien (alternative) :\n{verify_url}\n"
        )
    else:
        subject = "LoanWise — your verification code"
        message = (
            f"Your LoanWise verification code: {code}\n\n"
            f"Enter this code on the verification page:\n"
            f"  {verify_page_en}\n"
            f"  (interface FR : {verify_page_fr})\n\n"
            f"This code expires in 7 days.\n\n"
            f"Or use this link (alternative):\n{verify_url}\n"
        )
    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@localhost"),
        recipient_list=[user.email],
        fail_silently=False,
    )


def verify_email_with_code(email: str | None, raw_code: str | None) -> tuple[bool, str]:
    """
    Validate email + numeric code. Returns (success, detail).
    On success, sets user.email_verified and consumes the token.
    """
    from django.contrib.auth import get_user_model

    from core.models import EmailVerificationToken

    User = get_user_model()
    if not email or not raw_code:
        return False, "email_and_code_required"
    email_norm = email.strip().lower()
    code = normalize_verification_code(raw_code)
    if len(code) != EmailVerificationToken.CODE_LENGTH:
        return False, "invalid_code"

    user = User.objects.filter(email__iexact=email_norm).first()
    if not user:
        return False, "invalid_code_or_email"

    since = timezone.now() - VERIFICATION_TOKEN_MAX_AGE
    candidates = EmailVerificationToken.objects.filter(
        user=user,
        consumed_at__isnull=True,
        created_at__gte=since,
    ).exclude(code="")

    evt = None
    for c in candidates:
        if secrets.compare_digest(c.code, code):
            evt = c
            break
    if evt is None:
        return False, "invalid_code_or_email"

    if user.email_verified:
        evt.consume()
        return True, "already_verified"

    user.email_verified = True
    user.save(update_fields=["email_verified"])
    evt.consume()
    return True, "verified"


def verify_email_with_token(token: str | None) -> tuple[bool, str]:
    """Legacy GET ?token= flow."""
    from core.models import EmailVerificationToken

    if not token:
        return False, "missing_token"
    since = timezone.now() - VERIFICATION_TOKEN_MAX_AGE
    evt = EmailVerificationToken.objects.filter(
        token=token,
        consumed_at__isnull=True,
        created_at__gte=since,
    ).first()
    if not evt:
        return False, "invalid_token"
    user = evt.user
    if user.email_verified:
        evt.consume()
        return True, "already_verified"
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    evt.consume()
    return True, "verified"
