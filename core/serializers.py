"""
DRF serializers for LoanWise API (API-first contract).
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.models import ApplicationDocument, Currency, DocumentRequirement, LoanApplication, PortalRole

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "preferred_language",
            "theme_preference",
            "portal_role",
        )
        read_only_fields = ("id", "portal_role")


class RegisterSerializer(serializers.Serializer):
    """Inscription : identité et coordonnées ; revenu issu des bulletins après analyse (pas de saisie ici)."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    preferred_language = serializers.ChoiceField(choices=["fr", "en"], default="fr")
    id_card = serializers.CharField(max_length=128)
    phone = serializers.CharField(max_length=32)
    address = serializers.CharField()
    country = serializers.CharField(min_length=2, max_length=2)

    def validate_email(self, value: str) -> str:
        v = (value or "").strip().lower()
        if User.objects.filter(email__iexact=v).exists():
            raise serializers.ValidationError(
                _(
                    "An account already exists with this email address. Please log in instead."
                )
            )
        return v

    def validate_phone(self, value: str) -> str:
        s = (value or "").strip()
        if not re.match(r"^\+?[\d\s\-\.]{8,24}$", s):
            raise serializers.ValidationError(_("Enter a valid phone number."))
        return s

    def validate_country(self, value: str) -> str:
        return (value or "").strip().upper()[:2]

    @transaction.atomic
    def create(self, validated_data):
        """
        Un seul enregistrement User + profil Customer : le signal ``post_save`` crée déjà
        un Customer minimal ; on complète avec ``update_or_create`` (pas un second ``create``).
        """
        from core.models import Customer

        password = validated_data.pop("password")
        id_card = validated_data.pop("id_card")
        phone = validated_data.pop("phone")
        address = validated_data.pop("address")
        country = validated_data.pop("country")
        email = validated_data["email"].lower().strip()
        validated_data["email"] = email
        user = User(
            email=email,
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            preferred_language=validated_data.get("preferred_language") or "fr",
        )
        user.set_password(password)
        user.portal_role = PortalRole.CUSTOMER
        user.save()
        Customer.objects.update_or_create(
            user=user,
            defaults={
                "id_card": id_card.strip(),
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": email,
                "phone": phone,
                "address": address.strip(),
                "country": country,
                "annual_income": Decimal("0"),
                "income_currency": Currency.EUR,
            },
        )
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
            "amount_currency",
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
    """Création / mise à jour : montant, devise du montant, durée (revenu = profil client)."""

    class Meta:
        model = LoanApplication
        fields = (
            "loan_type",
            "language",
            "amount_requested",
            "amount_currency",
            "term_months",
            "purpose",
            "due_date",
            "current_step",
        )
        extra_kwargs = {
            "language": {"required": False},
            "due_date": {"required": False},
            "current_step": {"required": False},
            "loan_type": {"required": False},
            "purpose": {"required": False},
            "amount_currency": {"required": False},
        }

    def validate_amount_requested(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError(_("Amount must be strictly positive."))
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
