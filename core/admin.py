"""
Django admin: user management (including deletion), applications, and API tokens.

Suppression d’un utilisateur supprime en cascade ses demandes de prêt, documents,
messages et jetons (FK `CASCADE`).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from rest_framework.authtoken.models import Token

from django.utils import timezone

from core.models import (
    ApplicationDocument,
    ChatMessage,
    DocumentRequirement,
    EmailVerificationToken,
    EligibilityKnowledgeSource,
    LoanApplication,
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
        "email_verified",
        "preferred_language",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
        "last_login",
    )
    list_filter = (
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
        (_("Preferences"), {"fields": ("preferred_language", "theme_preference", "email_verified")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2"),
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


@admin.register(LoanApplication)
class LoanApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "loan_type", "status", "eligibility_score", "created_at")
    list_filter = ("status", "loan_type")
    search_fields = ("reference", "user__email")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"


@admin.register(ApplicationDocument)
class ApplicationDocumentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "application", "sha256_hex", "analyzed_at", "deleted_at")
    raw_id_fields = ("application", "requirement")


@admin.register(DocumentRequirement)
class DocumentRequirementAdmin(admin.ModelAdmin):
    list_display = ("code", "label_en", "is_required", "sort_order", "active")
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
