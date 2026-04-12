"""
LoanWise domain models: users, loan applications, documents, and AI chat history.

All monetary amounts use Decimal; sensitive document bytes are never stored long-term:
files are hashed (SHA-256), analyzed, then removed per security policy.
"""

from __future__ import annotations

import secrets
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Language(models.TextChoices):
    """Supported UI and AI agent languages."""

    FRENCH = "fr", _("French")
    ENGLISH = "en", _("English")


class ThemePreference(models.TextChoices):
    """Client-side theme; persisted for cross-device consistency."""

    SYSTEM = "system", _("System")
    LIGHT = "light", _("Light")
    DARK = "dark", _("Dark")


class User(AbstractUser):
    """
    Extended user with email-as-username style uniqueness, verification flag,
    and preferences for i18n and theming (used by templates and API serializers).
    """

    email = models.EmailField(_("email address"), unique=True)
    email_verified = models.BooleanField(default=False)
    preferred_language = models.CharField(
        max_length=5,
        choices=Language.choices,
        default=Language.FRENCH,
    )
    theme_preference = models.CharField(
        max_length=10,
        choices=ThemePreference.choices,
        default=ThemePreference.DARK,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.email


class EmailVerificationToken(models.Model):
    """
    Single-use token for email verification after signup.
    """

    CODE_LENGTH = 6

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    code = models.CharField(
        max_length=12,
        blank=True,
        default="",
        db_index=True,
        help_text="Numeric code sent by email (empty for legacy rows).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def _new_unique_code(cls) -> str:
        for _ in range(100):
            c = "".join(secrets.choice("0123456789") for _ in range(cls.CODE_LENGTH))
            if not cls.objects.filter(consumed_at__isnull=True, code=c).exists():
                return c
        raise RuntimeError("Could not allocate a unique verification code")

    @classmethod
    def create_for_user(cls, user: User) -> EmailVerificationToken:
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(48),
            code=cls._new_unique_code(),
        )

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])


class LoanType(models.TextChoices):
    PERSONAL = "personal", _("Personal loan")
    MORTGAGE = "mortgage", _("Mortgage")
    BUSINESS = "business", _("Business loan")


class LoanApplicationStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    IN_PROGRESS = "in_progress", _("In progress")
    UNDER_REVIEW = "under_review", _("Under review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    COMPLETED = "completed", _("Completed")


class LoanApplication(models.Model):
    """
    Main loan request: eligibility inputs, workflow step, aggregated AI outputs,
    ROI / business impact (JSON for flexible dashboard and PDF export).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_applications",
    )
    reference = models.CharField(max_length=32, unique=True, db_index=True)
    loan_type = models.CharField(
        max_length=20,
        choices=LoanType.choices,
        default=LoanType.PERSONAL,
    )
    status = models.CharField(
        max_length=20,
        choices=LoanApplicationStatus.choices,
        default=LoanApplicationStatus.DRAFT,
    )
    language = models.CharField(
        max_length=5,
        choices=Language.choices,
        default=Language.FRENCH,
        help_text=_("Language for this application (UI + AI agents)."),
    )
    amount_requested = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )
    term_months = models.PositiveIntegerField(default=12)
    annual_income = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
    )
    purpose = models.TextField(blank=True)
    current_step = models.CharField(max_length=64, default="welcome")
    # Aggregated scoring and narrative from loan_engine / orchestrator
    eligibility_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    roi_summary = models.JSONField(default=dict, blank=True)
    business_impact = models.JSONField(default=dict, blank=True)
    face_verification = models.JSONField(default=dict, blank=True)
    liveness_verification = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Liveness video analysis: head turns, face match vs ID, debug payloads."),
    )
    orchestration_log = models.JSONField(default=list, blank=True)
    final_report_html = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["reference"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            # Short unique reference for support and PDF headers (LW- + random)
            self.reference = f"LW-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference} ({self.get_status_display()})"


class DocumentRequirement(models.Model):
    """
    Configurable document types required per loan category.
    Extend by adding rows; `applies_to_loan_types` lists LoanType values.
    """

    code = models.SlugField(unique=True, max_length=64)
    label_fr = models.CharField(max_length=255)
    label_en = models.CharField(max_length=255)
    description_fr = models.TextField(blank=True)
    description_en = models.TextField(blank=True)
    applies_to_loan_types = models.JSONField(
        default=list,
        help_text=_('List of loan type codes, e.g. ["personal", "business"].'),
    )
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = _("document requirement")
        verbose_name_plural = _("document requirements")

    def __str__(self) -> str:
        return self.code


class DocumentKind(models.TextChoices):
    GENERIC = "generic", _("Generic")
    IDENTITY = "identity", _("Identity")
    INCOME = "income", _("Income proof")
    FACE_SELFIE = "face_selfie", _("Face selfie")
    LIVENESS_VIDEO = "liveness_video", _("Liveness video")


class ApplicationDocument(models.Model):
    """
    Uploaded artifact for an application: stored temporarily, hashed, analyzed, then deleted.
    """

    application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    requirement = models.ForeignKey(
        DocumentRequirement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    kind = models.CharField(
        max_length=32,
        choices=DocumentKind.choices,
        default=DocumentKind.GENERIC,
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128, blank=True)
    sha256_hex = models.CharField(max_length=64, blank=True, db_index=True)
    file_size = models.PositiveIntegerField(default=0)
    storage_path = models.CharField(max_length=512, blank=True)
    analysis_result = models.JSONField(default=dict, blank=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.application.reference})"


class EligibilityKnowledgeSource(models.Model):
    """
    Admin-managed RAG sources (PDF / images) defining internal eligibility rules.
    Text is extracted, chunked, and indexed for retrieval (see core.rag_eligibility).
    """

    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(
        upload_to="eligibility_kb/%Y/%m/",
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "png", "jpg", "jpeg", "webp"],
            )
        ],
    )
    manual_text = models.TextField(
        blank=True,
        help_text=_("Optional: paste rules here if automatic extraction from the file fails."),
    )
    extracted_text = models.TextField(blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    last_indexed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("eligibility knowledge source")
        verbose_name_plural = _("eligibility knowledge sources")

    def __str__(self) -> str:
        return self.title or self.file.name

    def searchable_blob(self) -> str:
        parts: list[str] = []
        if self.manual_text:
            parts.append(self.manual_text)
        if self.extracted_text:
            parts.append(self.extracted_text)
        raw = "\n\n".join(parts)
        # Do not feed extraction error placeholders into RAG / summaries
        paras = raw.split("\n\n")
        kept: list[str] = []
        for p in paras:
            t = p.strip()
            if t.startswith("[PDF extraction failed") or t.startswith("[Image OCR unavailable"):
                continue
            kept.append(p)
        return "\n\n".join(kept).strip()


class ChatRole(models.TextChoices):
    USER = "user", _("User")
    ASSISTANT = "assistant", _("Assistant")
    SYSTEM = "system", _("System")


class ChatMessage(models.Model):
    """
    Persisted conversation for the LangGraph-style assistant (audit + resume).
    """

    application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    role = models.CharField(max_length=16, choices=ChatRole.choices)
    content = models.TextField()
    step_hint = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:50]}..."
