"""
LoanWise domain models: users, loan applications, documents, and optional chat history (legacy).

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
    """Supported UI languages for the application."""

    FRENCH = "fr", _("French")
    ENGLISH = "en", _("English")


class ThemePreference(models.TextChoices):
    """Client-side theme; persisted for cross-device consistency."""

    SYSTEM = "system", _("System")
    LIGHT = "light", _("Light")
    DARK = "dark", _("Dark")


class PortalRole(models.TextChoices):
    """Account type (ERD: Account.Type): customer workspace vs bank backoffice. Maps to Customer vs BackOffice profile."""

    CUSTOMER = "customer", _("Customer")
    BACKOFFICE = "backoffice", _("Backoffice")


class Customer(models.Model):
    """
    Applicant profile (ERD: Customer). Linked 1:1 to the login account (`User`) when Type = customer.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_profile",
        null=True,
        blank=True,
    )
    id_card = models.CharField(
        max_length=128,
        blank=True,
        help_text=_("National ID / CIN / ID card number."),
    )
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(
        blank=True,
        help_text=_("Profile copy; canonical login email is on Account (User)."),
    )
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or (self.email or str(self.pk))


class BackOffice(models.Model):
    """
    Bank staff profile (ERD: Back-Office). Linked 1:1 to `User` when Type = backoffice.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="backoffice_profile",
        null=True,
        blank=True,
    )
    cin_number = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Back-Office")
        verbose_name_plural = _("Back-Office")

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or (self.email or str(self.pk))


class User(AbstractUser):
    """
    Login account (ERD: Account: email, password, Type via `portal_role`).
    Extended with verification, i18n, theming; 1:1 Customer or BackOffice profile when applicable.
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
    portal_role = models.CharField(
        max_length=20,
        choices=PortalRole.choices,
        default=PortalRole.CUSTOMER,
        db_index=True,
        help_text=_("Customer: own applications only. Backoffice: list and open all applications."),
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
    """ERD LoanRequest status (extended with in_review pipeline states)."""

    PENDING = "pending", _("Pending")
    IN_PROGRESS = "in_progress", _("In progress")
    UNDER_REVIEW = "under_review", _("Under review")
    VALIDATED = "validated", _("Validated")
    REJECTED = "rejected", _("Rejected")
    CANCELED = "canceled", _("Canceled")
    CLOSED = "closed", _("Closed")


class LoanApplication(models.Model):
    """
    Loan request (ERD: LoanRequest): owned by a Customer profile; `user` duplicates Customer.user for auth queries.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_applications",
        help_text=_("Account (login user); same as customer.user when set."),
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="loan_requests",
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
        default=LoanApplicationStatus.PENDING,
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
    orchestration_log = models.JSONField(default=list, blank=True)
    final_report_html = models.TextField(blank=True)
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Optional target decision or offer expiry date (ERD)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["customer", "-created_at"]),
            models.Index(fields=["reference"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            # Short unique reference for support and PDF headers (LW- + random)
            self.reference = f"LW-{secrets.token_hex(4).upper()}"
        if self.user_id and not self.customer_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            user = User.objects.filter(pk=self.user_id).first()
            if user:
                profile = getattr(user, "customer_profile", None)
                if profile is not None:
                    self.customer_id = profile.pk
                elif getattr(user, "portal_role", None) != PortalRole.BACKOFFICE:
                    c, _ = Customer.objects.update_or_create(
                        user_id=user.pk,
                        defaults={
                            "email": user.email or "",
                            "first_name": user.first_name or "",
                            "last_name": user.last_name or "",
                        },
                    )
                    self.customer_id = c.pk
        super().save(*args, **kwargs)

    def has_complete_financial_profile(self) -> bool:
        """Declared income and loan amount are set (> 0) so the deterministic score is meaningful."""
        if self.annual_income is None or self.amount_requested is None:
            return False
        if self.annual_income <= 0 or self.amount_requested <= 0:
            return False
        if self.term_months is None or self.term_months < 1:
            return False
        return True

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
    min_files = models.PositiveIntegerField(
        default=1,
        help_text=_("Minimum uploads linked to this requirement (e.g. 3 payslips)."),
    )
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
    ADDRESS = "address", _("Proof of address")


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
    validation_rule = models.TextField(
        blank=True,
        help_text=_("Optional rule text or validator id for this document type (ERD)."),
    )
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


class Notification(models.Model):
    """In-app notification tied to a loan request (ERD)."""

    loan_application = models.ForeignKey(
        LoanApplication,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Notification #{self.pk} ({self.loan_application.reference})"


class EligibilityKnowledgeSource(models.Model):
    """
    Admin-managed RAG sources (PDF / images) defining internal eligibility rules.
    Text is extracted, split with LangChain (RecursiveCharacterTextSplitter), embedded
    with OpenAI via LangChain (OpenAIEmbeddings), stored in Chroma, and retrieved with similarity_search (see core.rag_eligibility).
    ERD name: Eligibility Condition (file).
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
        verbose_name = _("Eligibility condition")
        verbose_name_plural = _("Eligibility conditions")

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


class IntegrationSettings(models.Model):
    """
    Singleton (pk=1): OpenAI and other API credentials editable in Admin.

    Environment variable OPENAI_API_KEY overrides the stored value when set.
    """

    openai_api_key = models.TextField(
        blank=True,
        help_text=_(
            "Used for chat, RAG extraction, and document vision. "
            "You can also set OPENAI_API_KEY in the environment (it takes precedence)."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Integration settings (OpenAI)")
        verbose_name_plural = _("Integration settings (OpenAI)")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    def __str__(self) -> str:
        if self.openai_api_key:
            return str(_("OpenAI integration"))
        return str(_("OpenAI integration (not configured)"))


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
