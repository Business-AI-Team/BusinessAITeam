"""
Django admin: user management (including deletion), applications, and API tokens.

Suppression d’un utilisateur supprime en cascade ses demandes de prêt, documents,
messages et jetons (FK `CASCADE`).
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from rest_framework.authtoken.models import Token

from django.utils import timezone

from core.models import (
    ApplicationDocument,
    BackOffice,
    ChatMessage,
    Customer,
    DocumentRequirement,
    EmailVerificationToken,
    EligibilityKnowledgeSource,
    IntegrationSettings,
    LoanApplication,
    Notification,
    User,
)
from core.rag_eligibility import (
    delete_eligibility_source_index,
    extract_text_from_file,
    index_eligibility_source,
)

admin.site.site_header = _("LoanWise administration")
admin.site.site_title = _("LoanWise admin")
admin.site.index_title = _("Applications & users")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Liste + fiche utilisateur : suppression possible (action ou bouton « Supprimer »)."""

    ordering = ("-date_joined",)
    list_per_page = 50
    show_full_result_count = True

    list_display = (
        "email",
        "username",
        "first_name",
        "last_name",
        "portal_role",
        "email_verified",
        "preferred_language",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
        "last_login",
    )
    list_filter = (
        "portal_role",
        "is_staff",
        "is_superuser",
        "is_active",
        "email_verified",
        "preferred_language",
    )
    search_fields = ("email", "username", "first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (_("Preferences"), {"fields": ("preferred_language", "theme_preference", "email_verified", "portal_role")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2", "portal_role"),
            },
        ),
    )

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        # Empêcher un non-superuser de supprimer un compte superuser
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        # Éviter la suppression accidentelle de son propre compte depuis l’admin
        if obj is not None and obj.pk == request.user.pk:
            return False
        return True


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    """Jetons API REST ; supprimés en cascade si l’utilisateur est effacé."""

    list_display = ("user", "created")
    search_fields = ("user__email", "user__username", "key")
    raw_id_fields = ("user",)
    ordering = ("-created",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "id_card", "user", "created_at")
    search_fields = ("email", "first_name", "last_name", "id_card", "phone")
    raw_id_fields = ("user",)


@admin.register(BackOffice)
class BackOfficeAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "cin_number", "user", "created_at")
    search_fields = ("email", "first_name", "last_name", "cin_number")
    raw_id_fields = ("user",)


@admin.register(LoanApplication)
class LoanApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "customer", "loan_type", "status", "eligibility_score", "created_at")
    list_filter = ("status", "loan_type")
    search_fields = ("reference", "user__email", "customer__email")
    raw_id_fields = ("user", "customer")
    date_hierarchy = "created_at"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("loan_application", "read", "created_at")
    list_filter = ("read",)
    raw_id_fields = ("loan_application",)


@admin.register(ApplicationDocument)
class ApplicationDocumentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "application", "sha256_hex", "analyzed_at", "deleted_at")
    raw_id_fields = ("application", "requirement")


@admin.register(DocumentRequirement)
class DocumentRequirementAdmin(admin.ModelAdmin):
    list_display = ("code", "label_en", "is_required", "min_files", "sort_order", "active")
    list_filter = ("active", "is_required")
    search_fields = ("code", "label_en", "label_fr")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("application", "role", "created_at")
    list_filter = ("role",)
    raw_id_fields = ("application",)
    search_fields = ("content",)


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "created_at", "consumed_at")
    raw_id_fields = ("user",)
    search_fields = ("token", "code", "user__email")


@admin.register(EligibilityKnowledgeSource)
class EligibilityKnowledgeSourceAdmin(admin.ModelAdmin):
    """
    PDF / images (JPEG, PNG, WebP) + optional manual text → extracted, chunked, indexed for RAG.
    """

    list_display = ("title", "is_active", "last_indexed_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "manual_text", "extracted_text")
    readonly_fields = ("extracted_text", "last_indexed_at", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("title", "file", "manual_text", "is_active")}),
        (_("Indexed content"), {"fields": ("extracted_text", "last_indexed_at", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        re_extract = not change or "file" in form.changed_data
        if re_extract and obj.file:
            extracted = extract_text_from_file(obj.file.path)
            EligibilityKnowledgeSource.objects.filter(pk=obj.pk).update(extracted_text=extracted)
            obj.refresh_from_db()
        full = obj.searchable_blob()
        if obj.is_active and full.strip():
            index_eligibility_source(obj.pk, obj.title or "", full)
            EligibilityKnowledgeSource.objects.filter(pk=obj.pk).update(last_indexed_at=timezone.now())
        else:
            delete_eligibility_source_index(obj.pk)

    def delete_model(self, request, obj):
        delete_eligibility_source_index(obj.pk)
        super().delete_model(request, obj)


class IntegrationSettingsForm(forms.ModelForm):
    class Meta:
        model = IntegrationSettings
        fields = ("openai_api_key",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["openai_api_key"].widget = forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "off", "size": "72"},
        )
        self.fields["openai_api_key"].required = False
        self.fields["openai_api_key"].label = _("OpenAI API key")
        self.fields["openai_api_key"].help_text = _(
            "Used for chat, RAG, and document vision. Leave blank when saving to keep the current key. "
            "Environment variable OPENAI_API_KEY overrides this value when set."
        )

    def clean_openai_api_key(self):
        val = (self.cleaned_data.get("openai_api_key") or "").strip()
        if not val and self.instance.pk and getattr(self.instance, "openai_api_key", None):
            return self.instance.openai_api_key
        return val


@admin.register(IntegrationSettings)
class IntegrationSettingsAdmin(admin.ModelAdmin):
    """Singleton: one row for API keys (superuser only)."""

    form = IntegrationSettingsForm
    list_display = ("__str__", "updated_at")
    readonly_fields = ("updated_at",)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        if not request.user.is_superuser:
            return False
        return not IntegrationSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False
