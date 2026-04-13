"""
DRF serializers for LoanWise API (API-first contract).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from core.models import Document, ChatMessage, DocumentRequirement, LoanRequest

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
        )
        read_only_fields = ("id", "email_verified")


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
        user.save()
        return user


class DocumentRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentRequirement
        fields = ("id", "name", "is_mandatory")


class LoanRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanRequest
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
            "score",
            "roi_summary",
            "business_impact",
            "face_verification",
            "liveness_verification",
            "orchestration_log",
            "creation_date",
            "modification_date",
            "submitted_at",
        )
        read_only_fields = (
            "id",
            "reference",
            "status",
            "score",
            "roi_summary",
            "business_impact",
            "face_verification",
            "liveness_verification",
            "orchestration_log",
            "creation_date",
            "modification_date",
            "submitted_at",
        )


# Backward-compat alias
LoanApplicationSerializer = LoanRequestSerializer


class LoanRequestWriteSerializer(serializers.ModelSerializer):
    """Create/update loan request from API."""

    class Meta:
        model = LoanRequest
        fields = (
            "loan_type",
            "language",
            "amount_requested",
            "term_months",
            "annual_income",
            "purpose",
        )
        extra_kwargs = {"language": {"required": False}}

    def validate_amount_requested(self, value: Decimal) -> Decimal:
        if value < 0:
            raise serializers.ValidationError(_("Amount must be positive."))
        return value


# Backward-compat alias
LoanApplicationWriteSerializer = LoanRequestWriteSerializer


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = (
            "id",
            "requirement",
            "document_type",
            "original_filename",
            "content_type",
            "sha256_hex",
            "file_size",
            "analysis_result",
            "analyzed_at",
            "created_at",
        )
        read_only_fields = fields


# Backward-compat alias
ApplicationDocumentSerializer = DocumentSerializer


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ("id", "role", "content", "step_hint", "metadata", "created_at")


class ChatSendSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=8000)


class UserPreferencesSerializer(serializers.Serializer):
    preferred_language = serializers.ChoiceField(choices=["fr", "en"], required=False)
    theme_preference = serializers.ChoiceField(choices=["system", "light", "dark"], required=False)
