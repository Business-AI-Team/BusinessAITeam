"""
LoanWise domain models — aligned with ERD data model diagram.

Entity names match the ERD exactly:
  Customer, Account (login), BackOffice, LoanRequest, Document, Notification,
  EligibilityCondition (formerly EligibilityKnowledgeSource).

Sensitive document bytes are never stored long-term: files are hashed (SHA-256),
analyzed, then removed per security policy.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ── Enumerations ──────────────────────────────────────────────────────────────

class Language(models.TextChoices):
    """Supported UI and AI agent languages."""

    FRENCH = "fr", _("French")
    ENGLISH = "en", _("English")


class ThemePreference(models.TextChoices):
    """Client-side theme; persisted for cross-device consistency."""

    SYSTEM = "system", _("System")
    LIGHT = "light", _("Light")
    DARK = "dark", _("Dark")


class AccountType(models.TextChoices):
    """ERD Account.Type picklist: role of the account holder."""

    CUSTOMER = "customer", _("Customer")
    BACKOFFICE = "backoffice", _("Back-Office")


class LoanRequestStatus(models.TextChoices):
    """ERD LoanRequest.Status picklist."""

    PENDING = "pending", _("Pending")
    VALIDATED = "validated", _("Validated")
    REJECTED = "rejected", _("Rejected")
    CANCELED = "canceled", _("Canceled")
    CLOSED = "closed", _("Closed")


class LoanType(models.TextChoices):
    PERSONAL = "personal", _("Personal loan")
    MORTGAGE = "mortgage", _("Mortgage")
    BUSINESS = "business", _("Business loan")


class DocumentType(models.TextChoices):
    """ERD Document.Type picklist."""

    ID_CARD = "id_card", _("ID Card")
    PAY_STUB = "pay_stub", _("PayStub")
    TAX_RETURN = "tax_return", _("TaxReturn")
    FACE_SELFIE = "face_selfie", _("Face selfie")
    LIVENESS_VIDEO = "liveness_video", _("Liveness video")
    OTHER = "other", _("Other")


class ChatRole(models.TextChoices):
    USER = "user", _("User")
    ASSISTANT = "assistant", _("Assistant")
    SYSTEM = "system", _("System")


# ── Core profiles ─────────────────────────────────────────────────────────────

class Customer(models.Model):
    """
    ERD: Customer — the individual who submits a loan request.
    Linked 1:1 to an Account when Type = 'Customer'.
    """

    id_card = models.CharField(
        max_length=128,
        blank=True,
        verbose_name=_("ID Card"),
        help_text=_("Official identity card number."),
    )
    first_name = models.CharField(max_length=150, blank=True, verbose_name=_("First Name"))
    last_name = models.CharField(max_length=150, blank=True, verbose_name=_("Last Name"))
    email = models.EmailField(blank=True, verbose_name=_("Email Address"))
    phone = models.CharField(max_length=32, blank=True, verbose_name=_("Phone Number"))
    address = models.TextField(blank=True, verbose_name=_("Address"))
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
    ERD: Back-Office — bank staff responsible for managing and validating loan requests.
    Linked 1:1 to an Account when Type = 'Back-Office'.
    """

    cin_number = models.CharField(max_length=64, blank=True, verbose_name=_("CIN Number"))
    first_name = models.CharField(max_length=150, blank=True, verbose_name=_("First Name"))
    last_name = models.CharField(max_length=150, blank=True, verbose_name=_("Last Name"))
    email = models.EmailField(blank=True, verbose_name=_("Email Address"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Back-Office")
        verbose_name_plural = _("Back-Office")

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or (self.email or str(self.pk))


# ── Account (login) ───────────────────────────────────────────────────────────

class Account(AbstractUser):
    """
    ERD: Account — login credentials and role for a user (Customer or Back-Office).

    Fields:
      - email        : login identifier (Email Address)
      - password     : hashed password (inherited from AbstractUser)
      - account_type : Type picklist ('customer' | 'backoffice')
      - customer     : CustomerId FK — set when account_type = 'customer'
      - back_office  : BackOfficeId FK — set when account_type = 'backoffice'
    """

    email = models.EmailField(_("Email Address"), unique=True)
    email_verified = models.BooleanField(default=False)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.CUSTOMER,
        db_index=True,
        verbose_name=_("Type"),
        help_text=_("Customer: own loan requests only. Back-Office: manage all requests."),
    )
    customer = models.OneToOneField(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account",
        verbose_name=_("CustomerId"),
        help_text=_("Linked Customer profile (set when Type = Customer)."),
    )
    back_office = models.OneToOneField(
        BackOffice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account",
        verbose_name=_("BackOfficeId"),
        help_text=_("Linked Back-Office profile (set when Type = Back-Office)."),
    )
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
        verbose_name = _("Account")
        verbose_name_plural = _("Accounts")

    def __str__(self) -> str:
        return self.email

    # Keep backward-compat property so existing code using `portal_role` still works
    @property
    def portal_role(self) -> str:
        return self.account_type


# ── Email verification ────────────────────────────────────────────────────────

class EmailVerificationToken(models.Model):
    """Single-use token for email verification after signup."""

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
    def create_for_user(cls, user: Account) -> EmailVerificationToken:
        return cls.objects.create(
            user=user,
            token=secrets.token_urlsafe(48),
            code=cls._new_unique_code(),
        )

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])


# ── Loan Request ──────────────────────────────────────────────────────────────

class LoanRequest(models.Model):
    """
    ERD: LoanRequest — the loan application dossier being processed.
    Owned by a Customer; the AI model computes the Score.
    """

    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_requests",
        verbose_name=_("Account"),
        help_text=_("Login account (Account) that submitted this request."),
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="loan_requests",
        null=True,
        blank=True,
        verbose_name=_("Customer"),
    )
    reference = models.CharField(max_length=32, unique=True, db_index=True)
    loan_type = models.CharField(
        max_length=20,
        choices=LoanType.choices,
        default=LoanType.PERSONAL,
    )
    status = models.CharField(
        max_length=20,
        choices=LoanRequestStatus.choices,
        default=LoanRequestStatus.PENDING,
        verbose_name=_("Status"),
    )
    language = models.CharField(
        max_length=5,
        choices=Language.choices,
        default=Language.FRENCH,
        help_text=_("Language for this request (UI + AI agents)."),
    )
    # ERD fields
    creation_date = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Creation Date"),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Due Date"),
        help_text=_("Deadline or offer expiry date."),
    )
    modification_date = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Modification Date"),
    )
    score = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        verbose_name=_("Score"),
        help_text=_("Eligibility score computed by the AI model (0.0 to 1.0)."),
    )
    # Extended fields for the loan workflow
    amount_requested = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    term_months = models.PositiveIntegerField(default=12)
    annual_income = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    purpose = models.TextField(blank=True)
    current_step = models.CharField(max_length=64, default="welcome")
    face_verification = models.JSONField(default=dict, blank=True)
    liveness_verification = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Liveness video analysis: head turns, face match vs ID, debug payloads."),
    )
    roi_summary = models.JSONField(default=dict, blank=True)
    business_impact = models.JSONField(default=dict, blank=True)
    orchestration_log = models.JSONField(default=list, blank=True)
    final_report_html = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Loan Request")
        verbose_name_plural = _("Loan Requests")
        ordering = ["-creation_date"]
        indexes = [
            models.Index(fields=["account", "-creation_date"]),
            models.Index(fields=["customer", "-creation_date"]),
            models.Index(fields=["reference"]),
        ]

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            self.reference = f"LW-{secrets.token_hex(4).upper()}"
        if self.account_id and not self.customer_id:
            from django.contrib.auth import get_user_model
            AccountModel = get_user_model()
            acc = AccountModel.objects.filter(pk=self.account_id).first()
            if acc:
                profile = getattr(acc, "customer", None)
                if profile is not None:
                    self.customer_id = profile.pk
                elif getattr(acc, "account_type", None) != AccountType.BACKOFFICE:
                    c, _ = Customer.objects.update_or_create(
                        account=acc,
                        defaults={
                            "email": acc.email or "",
                            "first_name": acc.first_name or "",
                            "last_name": acc.last_name or "",
                        },
                    )
                    self.customer_id = c.pk
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference} ({self.get_status_display()})"

    # ── Backward-compat properties ──────────────────────────────────────────
    @property
    def user(self):
        return self.account

    @property
    def created_at(self):
        return self.creation_date

    @property
    def updated_at(self):
        return self.modification_date


# ── Document requirements (config table) ─────────────────────────────────────

class DocumentRequirement(models.Model):
    """
    Document type required from the customer when submitting a loan request.
    Defined by Back-Office staff; all active rows are requested at submission time.
    """

    name = models.CharField(
        max_length=255,
        verbose_name=_("Name"),
        help_text=_("e.g. Payslip, ID Card"),
    )
    is_mandatory = models.BooleanField(
        default=True,
        verbose_name=_("Is Mandatory"),
        help_text=_("If true, the loan request is blocked until this document is uploaded."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Document Requirement")
        verbose_name_plural = _("Document Requirements")

    def __str__(self) -> str:
        return self.name


# ── Document ──────────────────────────────────────────────────────────────────

class Document(models.Model):
    """
    ERD: Document — supporting file attached to a LoanRequest.
    File is stored temporarily, hashed (SHA-256), analyzed, then deleted per security policy.
    """

    loan_request = models.ForeignKey(
        LoanRequest,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name=_("LoanRequestId"),
    )
    requirement = models.ForeignKey(
        DocumentRequirement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    document_type = models.CharField(
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.ID_CARD,
        verbose_name=_("Type"),
        help_text=_("Nature of the document (ERD: Type picklist)."),
    )
    file = models.FileField(
        upload_to="documents/%Y/%m/",
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "png", "jpg", "jpeg", "webp"],
            )
        ],
        verbose_name=_("File"),
        help_text=_("Binary file or path to cloud storage (ERD: File)."),
    )
    validation_rule = models.TextField(
        blank=True,
        verbose_name=_("Validation Rule"),
        help_text=_("Compliance rule applied to this document (e.g. 'Less than 3 months old')."),
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
        verbose_name = _("Document")
        verbose_name_plural = _("Documents")
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.loan_request.reference})"

    # Backward-compat alias
    @property
    def application(self):
        return self.loan_request


# ── Notification ──────────────────────────────────────────────────────────────

class Notification(models.Model):
    """
    ERD: Notification — unique follow-up alert for a LoanRequest (1:1 relation).
    One loan request generates exactly one notification.
    """

    loan_request = models.OneToOneField(
        LoanRequest,
        on_delete=models.CASCADE,
        related_name="notification",
        verbose_name=_("LoanRequestId"),
    )
    date = models.DateTimeField(
        default=timezone.now,
        verbose_name=_("Date"),
        help_text=_("Moment the alert was sent."),
    )
    read = models.BooleanField(
        default=False,
        verbose_name=_("Read"),
        help_text=_("Whether the notification has been read (True/False)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Notification #{self.pk} ({self.loan_request.reference})"


# ── Eligibility Condition ─────────────────────────────────────────────────────

class EligibilityCondition(models.Model):
    """
    ERD: EligibilityCondition — admin-managed PDF reference document containing
    business rules for loan acceptance. Text is extracted, chunked, and indexed
    for retrieval (see core.rag_eligibility).
    """

    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(
        upload_to="eligibility_kb/%Y/%m/",
        verbose_name=_("File (pdf)"),
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
        verbose_name = _("Eligibility Condition")
        verbose_name_plural = _("Eligibility Conditions")

    def __str__(self) -> str:
        return self.title or self.file.name

    def searchable_blob(self) -> str:
        parts: list[str] = []
        if self.manual_text:
            parts.append(self.manual_text)
        if self.extracted_text:
            parts.append(self.extracted_text)
        raw = "\n\n".join(parts)
        paras = raw.split("\n\n")
        kept: list[str] = []
        for p in paras:
            t = p.strip()
            if t.startswith("[PDF extraction failed") or t.startswith("[Image OCR unavailable"):
                continue
            kept.append(p)
        return "\n\n".join(kept).strip()


# Backward-compat alias so existing imports of EligibilityKnowledgeSource still work
EligibilityKnowledgeSource = EligibilityCondition


# ── Integration Settings (singleton) ─────────────────────────────────────────

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


# ── Chat (AI assistant) ───────────────────────────────────────────────────────

class ChatMessage(models.Model):
    """Persisted conversation for the LangGraph-style assistant (audit + resume)."""

    loan_request = models.ForeignKey(
        LoanRequest,
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

    # Backward-compat alias
    @property
    def application(self):
        return self.loan_request


# ── Backward-compat aliases for old class names ───────────────────────────────
# These allow existing code that imports LoanApplication / ApplicationDocument / User
# to continue working without immediate refactoring.

User = Account
LoanApplication = LoanRequest
ApplicationDocument = Document
LoanApplicationStatus = LoanRequestStatus
PortalRole = AccountType
