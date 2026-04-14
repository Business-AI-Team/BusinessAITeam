"""
Django admin: user management (including deletion), applications, and API tokens.

Suppression d’un utilisateur : DELETE réel en base ; les objets liés en CASCADE
(demandes, documents, jetons, etc.) sont supprimés avec le compte.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


from core.models import (
    ApplicationDocument,
    AssistantGuideSource,
    BackOffice,
    Customer,
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
    """Liste + fiche utilisateur : suppression = effacement réel (CASCADE)."""

    ordering = ("-date_joined",)
    list_per_page = 50
    show_full_result_count = True

    list_display = (
        "email",
        "first_name",
        "last_name",
        "portal_role",
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
        "preferred_language",
    )
    search_fields = ("email", "first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (_("Preferences"), {"fields": ("preferred_language", "theme_preference", "portal_role")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "portal_role"),
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



@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "id_card", "user", "created_at")
    search_fields = ("email", "first_name", "last_name", "id_card", "phone")
    raw_id_fields = ("user",)


@admin.register(BackOffice)
class BackOfficeAdmin(admin.ModelAdmin):
    list_display = ("user", "email", "first_name", "last_name", "created_at")
    search_fields = ("email", "first_name", "last_name", "user__email")
    fields = ("user",)

    def save_model(self, request, obj, form, change):
        """Promote the selected user to backoffice and copy their info automatically."""
        from core.models import Customer, PortalRole
        user = obj.user
        if user:
            user.portal_role = PortalRole.BACKOFFICE
            user.save(update_fields=["portal_role"])
            # Remove customer profile so they no longer appear in the Customer section
            Customer.objects.filter(user=user).delete()
            # Use update_or_create to avoid UNIQUE constraint on user_id
            BackOffice.objects.update_or_create(
                user=user,
                defaults={
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                },
            )


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


@admin.register(EligibilityKnowledgeSource)
class EligibilityKnowledgeSourceAdmin(admin.ModelAdmin):
    """
    PDF / images (JPEG, PNG, WebP) + optional manual text → extracted, chunked, indexed for RAG.

    LangChain RAG pipeline: Load (PyMuPDFLoader / PyPDFLoader / TextLoader)
      → Split (RecursiveCharacterTextSplitter) → Store (Chroma) → Retrieve (similarity_search).
    """

    list_display = ("title", "is_active", "has_text", "last_indexed_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "manual_text", "extracted_text")
    readonly_fields = ("extracted_text", "last_indexed_at", "created_at", "updated_at")
    actions = ["reindex_sources"]
    fieldsets = (
        (None, {"fields": ("title", "file", "manual_text", "is_active")}),
        (_("Indexed content"), {"fields": ("extracted_text", "last_indexed_at", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description=_("Has text"))
    def has_text(self, obj: EligibilityKnowledgeSource) -> bool:
        """True when the source has indexable text (manual_text or extracted_text)."""
        return bool(obj.searchable_blob())

    @admin.action(description=_("Re-index selected sources (extract text + rebuild Chroma)"))
    def reindex_sources(self, request, queryset):
        """
        Admin bulk action: re-runs the full LangChain Load → Split → Store pipeline
        for every selected source. Use this when:
          - A source was saved before the OpenAI API key was configured.
          - The file changed on disk but was not re-uploaded.
          - extracted_text is empty and you want to retry extraction.
        """
        count = 0
        for obj in queryset:
            if obj.file:
                extracted = extract_text_from_file(obj.file.path)
                EligibilityKnowledgeSource.objects.filter(pk=obj.pk).update(
                    extracted_text=extracted
                )
                obj.refresh_from_db()
            full = obj.searchable_blob()
            file_path = obj.file.path if obj.file else None
            if obj.is_active:
                index_eligibility_source(
                    obj.pk, obj.title or "", full, file_path=file_path
                )
                EligibilityKnowledgeSource.objects.filter(pk=obj.pk).update(
                    last_indexed_at=timezone.now()
                )
                count += 1
            else:
                delete_eligibility_source_index(obj.pk)
        self.message_user(
            request,
            _("%(count)d source(s) re-indexed successfully.") % {"count": count},
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        re_extract = not change or "file" in form.changed_data
        if re_extract and obj.file:
            extracted = extract_text_from_file(obj.file.path)
            EligibilityKnowledgeSource.objects.filter(pk=obj.pk).update(extracted_text=extracted)
            obj.refresh_from_db()
        full = obj.searchable_blob()
        if obj.is_active:
            # Pass file_path so index_eligibility_source can use LangChain loaders
            # even when extracted_text / manual_text are both empty (e.g. scanned PDF).
            file_path = obj.file.path if obj.file else None
            index_eligibility_source(obj.pk, obj.title or "", full, file_path=file_path)
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
        self.fields["openai_api_key"].widget = forms.TextInput(
            attrs={"autocomplete": "off", "size": "72", "style": "font-family: monospace;"},
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


@admin.register(AssistantGuideSource)
class AssistantGuideSourceAdmin(admin.ModelAdmin):
    """
    Usage guides for the AI assistant — uploaded as PDF/TXT/MD per role and page.
    Text is extracted automatically on save and injected into the assistant prompt.
    """

    list_display = ("title", "role", "page_context", "active", "updated_at")
    list_filter = ("role", "page_context", "active")
    search_fields = ("title", "manual_text", "extracted_text")
    readonly_fields = ("extracted_text", "updated_at", "created_at")
    fieldsets = (
        (None, {
            "fields": ("title", "role", "page_context", "active"),
        }),
        (_("Content"), {
            "fields": ("source_file", "manual_text", "extracted_text"),
            "description": _(
                "Upload a PDF/TXT/MD file OR type the guide directly in Manual text. "
                "Manual text takes priority over the extracted file text."
            ),
        }),
        (_("Timestamps"), {
            "fields": ("updated_at", "created_at"),
            "classes": ("collapse",),
        }),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.source_file and (not change or "source_file" in form.changed_data):
            try:
                extracted = extract_text_from_file(obj.source_file.path)
                AssistantGuideSource.objects.filter(pk=obj.pk).update(extracted_text=extracted)
                obj.refresh_from_db()
            except Exception as exc:
                self.message_user(
                    request,
                    f"Text extraction failed: {exc}",
                    level="warning",
                )


# ── Masquer du panel Setup les sections inutiles ───────────────────────────
# core/admin.py est chargé APRÈS rest_framework.authtoken (ordre INSTALLED_APPS)
# donc Token est déjà enregistré quand ces lignes s'exécutent.
from django.contrib.auth.models import Group  # noqa: E402

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

try:
    from rest_framework.authtoken.admin import TokenAdmin as _TokenAdmin  # noqa: F401
    from rest_framework.authtoken.models import TokenProxy as _TokenProxy
    admin.site.unregister(_TokenProxy)
except (admin.sites.NotRegistered, ImportError):
    pass
