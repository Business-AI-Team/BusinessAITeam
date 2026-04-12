"""
DRF serializers for LoanWise API (API-first contract).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.models import ApplicationDocument, DocumentRequirement, LoanApplication, PortalRole

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "email_verified",
            "preferred_language",
            "theme_preference",
            "portal_role",
        )
        read_only_fields = ("id", "email_verified", "portal_role")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    preferred_language = serializers.ChoiceField(choices=["fr", "en"], default="fr")

    class Meta:
        model = User
        fields = ("email", "username", "password", "first_name", "last_name", "preferred_language")

    def create(self, validated_data):
        password = validated_data.pop("password")
        email = validated_data["email"].lower()
        validated_data["email"] = email
        validated_data["username"] = validated_data.get("username") or email.split("@")[0]
        user = User(**validated_data)
        user.set_password(password)
        user.email_verified = False
        user.portal_role = PortalRole.CUSTOMER
        user.save()
        return user


class DocumentRequirementSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = DocumentRequirement
        fields = (
            "id",
            "code",
            "label",
            "description",
            "applies_to_loan_types",
            "is_required",
            "min_files",
            "sort_order",
        )

    def get_label(self, obj: DocumentRequirement) -> str:
        lang = self.context.get("language") or "en"
        return obj.label_fr if lang.startswith("fr") else obj.label_en

    def get_description(self, obj: DocumentRequirement) -> str:
        lang = self.context.get("language") or "en"
        return obj.description_fr if lang.startswith("fr") else obj.description_en


class LoanApplicationSerializer(serializers.ModelSerializer):
    applicant_email = serializers.SerializerMethodField()

    class Meta:
        model = LoanApplication
        fields = (
            "id",
            "reference",
            "loan_type",
            "status",
            "language",
            "amount_requested",
            "term_months",
            "annual_income",
            "purpose",
            "current_step",
            "eligibility_score",
            "roi_summary",
            "business_impact",
            "orchestration_log",
            "created_at",
            "updated_at",
            "submitted_at",
            "due_date",
            "customer",
            "applicant_email",
        )
        read_only_fields = (
            "id",
            "reference",
            "status",
            "eligibility_score",
            "roi_summary",
            "business_impact",
            "orchestration_log",
            "created_at",
            "updated_at",
            "submitted_at",
            "due_date",
            "customer",
            "applicant_email",
        )

    def get_applicant_email(self, obj: LoanApplication) -> str | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if getattr(request.user, "portal_role", None) != PortalRole.BACKOFFICE and not getattr(
            request.user, "is_superuser", False
        ):
            return None
        if getattr(obj, "customer_id", None):
            return obj.customer.email or getattr(obj.user, "email", None)
        if obj.user_id:
            return getattr(obj.user, "email", None)
        return None


class LoanApplicationWriteSerializer(serializers.ModelSerializer):
    """Create/update loan application from API (formulaire web, pas de chat)."""

    class Meta:
        model = LoanApplication
        fields = (
            "loan_type",
            "language",
            "amount_requested",
            "term_months",
            "annual_income",
            "purpose",
            "due_date",
            "current_step",
        )
        extra_kwargs = {
            "language": {"required": False},
            "due_date": {"required": False},
            "current_step": {"required": False},
        }

    def validate_amount_requested(self, value: Decimal) -> Decimal:
        if value < 0:
            raise serializers.ValidationError(_("Amount must be positive or zero."))
        return value

    def validate_annual_income(self, value: Decimal) -> Decimal:
        if value < 0:
            raise serializers.ValidationError(_("Annual income cannot be negative."))
        return value


class ApplicationDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationDocument
        fields = (
            "id",
            "requirement",
            "kind",
            "original_filename",
            "content_type",
            "sha256_hex",
            "file_size",
            "analysis_result",
            "analyzed_at",
            "created_at",
        )
        read_only_fields = fields


class UserPreferencesSerializer(serializers.Serializer):
    preferred_language = serializers.ChoiceField(choices=["fr", "en"], required=False)
    theme_preference = serializers.ChoiceField(choices=["system", "light", "dark"], required=False)
