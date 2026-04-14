"""
REST API views (API-first). Web UI consumes the same endpoints where relevant.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError
from django.contrib.auth import authenticate, get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.document_requirement_service import label_for, seed_default_requirements
from core.loan_orchestrator_agent import run_orchestration
from core.portal import can_access_all_applications
from core.models import (
    ApplicationDocument,
    DocumentKind,
    DocumentRequirement,
    LoanApplication,
    LoanApplicationStatus,
)
from core.assistant_chat import build_assistant_reply
from core.pdf_report import build_application_pdf
from core.security_utils import sha256_file
from core.serializers import (
    ApplicationDocumentSerializer,
    DocumentRequirementSerializer,
    LoanApplicationSerializer,
    LoanApplicationWriteSerializer,
    RegisterSerializer,
    UserPreferencesSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            user = ser.save()
        except IntegrityError:
            return Response(
                {
                    "email": [
                        _(
                            "An account already exists with this email address. Please log in instead."
                        )
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"detail": "registered", "code": "registered"}, status=status.HTTP_201_CREATED)



class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        password = request.data.get("password", "")
        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({"detail": "invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        ser = UserPreferencesSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        u = request.user
        for k, v in ser.validated_data.items():
            setattr(u, k, v)
        u.save()
        return Response(UserSerializer(u).data)


class LoanApplicationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options", "patch"]

    def get_queryset(self):
        qs = (
            LoanApplication.objects.all()
            if can_access_all_applications(self.request.user)
            else LoanApplication.objects.filter(user=self.request.user)
        )
        return qs.select_related("user", "customer").order_by("-created_at")

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return LoanApplicationWriteSerializer
        return LoanApplicationSerializer

    def perform_create(self, serializer):
        lang = serializer.validated_data.get("language") or self.request.user.preferred_language or "fr"
        ac = serializer.validated_data.get("amount_currency")
        if not ac:
            prof = getattr(self.request.user, "customer_profile", None)
            ac = getattr(prof, "income_currency", None) if prof else None
        serializer.save(
            user=self.request.user,
            language=lang,
            current_step="documents",
            amount_currency=ac or "EUR",
        )

    def perform_update(self, serializer):
        """Après saisie du montant / devise / durée, étape documents."""
        instance = serializer.instance
        if instance and instance.status in (
            LoanApplicationStatus.VALIDATED,
        ):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                _("This application is validated and can no longer be modified.")
            )
        extra: dict = {"current_step": "documents"}
        if not serializer.validated_data.get("amount_currency"):
            prof = getattr(self.request.user, "customer_profile", None)
            ac = getattr(prof, "income_currency", None) if prof else None
            if ac:
                extra["amount_currency"] = ac
        serializer.save(**extra)

    def create(self, request, *args, **kwargs):
        """Return full application payload (incl. id, reference) after create — needed by the web UI."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        assert isinstance(instance, LoanApplication)
        out = LoanApplicationSerializer(instance, context=self.get_serializer_context())
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"], url_path="force_validate")
    def force_validate(self, request, pk=None):
        """Backoffice only: manually validate any application regardless of score."""
        if not can_access_all_applications(request.user):
            return Response({"detail": _("Backoffice access required.")}, status=status.HTTP_403_FORBIDDEN)
        app = self.get_object()
        note = (request.data.get("note") or "").strip()
        app.status = LoanApplicationStatus.VALIDATED
        roi = app.roi_summary or {}
        roi["manual_validation"] = {
            "validated_by": request.user.email,
            "note": note,
        }
        app.roi_summary = roi
        app.save(update_fields=["status", "roi_summary", "updated_at"])
        return Response(
            {
                "status": app.status,
                "reference": app.reference,
                "validated_by": request.user.email,
            }
        )

    @action(detail=True, methods=["post"])
    def orchestrate(self, request, pk=None):
        app = self.get_object()
        try:
            app = run_orchestration(app)
        except Exception as e:
            logger.exception("Orchestration failed for application %s", app.pk)
            payload = {
                "detail": "Orchestration failed; see server logs or retry later.",
                "code": "orchestration_failed",
            }
            if settings.DEBUG:
                payload["exception"] = type(e).__name__
                payload["message"] = str(e)
            return Response(payload, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        app.refresh_from_db()
        steps = app.orchestration_log or []
        last = steps[-1] if steps else {}
        step_name = last.get("step")
        lang = app.language or "fr"
        missing_codes: list[str] = []
        if step_name == "blocked":
            missing_codes = list(last.get("detail", {}).get("missing_documents") or [])
        missing_labels: list[str] = []
        for code in missing_codes:
            req = DocumentRequirement.objects.filter(code=code).first()
            missing_labels.append(label_for(req, lang) if req else code)

        pipeline = {
            "completed": step_name == "complete",
            "blocked": step_name == "blocked",
            "missing_documents": missing_codes,
            "missing_document_labels": missing_labels,
            "last_step": step_name,
        }
        return Response(
            {
                "application": LoanApplicationSerializer(app).data,
                "pipeline": pipeline,
            }
        )

    @action(detail=True, methods=["get"])
    def export_pdf(self, request, pk=None):
        app = self.get_object()
        lang = request.LANGUAGE_CODE or app.language or "fr"
        pdf_bytes = build_application_pdf(app, lang=lang)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="loanwise-{app.reference}.pdf"'
        return resp


class DocumentUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, application_id: int):
        if can_access_all_applications(request.user):
            app = get_object_or_404(LoanApplication, pk=application_id)
        else:
            app = get_object_or_404(LoanApplication, pk=application_id, user=request.user)
        if app.status == LoanApplicationStatus.VALIDATED:
            return Response(
                {"detail": _("This application is validated and no longer accepts new documents.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        requirement_id = request.POST.get("requirement_id")
        kind = request.POST.get("kind") or "generic"
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file required"}, status=400)
        req = None
        if requirement_id:
            req = DocumentRequirement.objects.filter(pk=requirement_id).first()

        # Enforce max uploads per requirement (min_files acts as the limit)
        if req is not None:
            max_allowed = max(1, getattr(req, "min_files", 1) or 1)
            already_uploaded = app.documents.filter(requirement=req).count()
            if already_uploaded >= max_allowed:
                return Response(
                    {"detail": _(
                        f"Maximum number of files reached for this document type "
                        f"({max_allowed} file(s) allowed)."
                    )},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        stored_name = file.name
        rel_path = f"loanwise/{app.id}/{stored_name}"
        from django.core.files.storage import default_storage

        path = default_storage.save(rel_path, file)
        full = default_storage.path(path)
        digest = sha256_file(full)
        valid_kinds = {c[0] for c in DocumentKind.choices}
        analysis_result: dict = {}
        ad = ApplicationDocument.objects.create(
            application=app,
            requirement=req,
            kind=kind if kind in valid_kinds else DocumentKind.GENERIC,
            original_filename=file.name,
            content_type=file.content_type or "",
            sha256_hex=digest,
            file_size=file.size,
            storage_path=path,
            analysis_result=analysis_result,
        )
        return Response(ApplicationDocumentSerializer(ad).data, status=201)


class DocumentDownloadView(APIView):
    """Backoffice / owner: serve an uploaded document file."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id: int):
        doc = get_object_or_404(ApplicationDocument, pk=document_id)
        app = doc.application
        if app.user_id != request.user.id and not can_access_all_applications(request.user):
            return Response({"detail": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if not doc.storage_path:
            return Response({"detail": "file not found"}, status=404)
        from django.core.files.storage import default_storage
        import mimetypes

        try:
            f = default_storage.open(doc.storage_path, "rb")
            data = f.read()
            f.close()
        except Exception:
            return Response({"detail": "file not found"}, status=404)
        mime = mimetypes.guess_type(doc.original_filename or doc.storage_path)[0] or "application/octet-stream"
        response = HttpResponse(data, content_type=mime)
        safe_name = (doc.original_filename or "document").replace('"', "'")
        response["Content-Disposition"] = f'inline; filename="{safe_name}"'
        return response


class DocumentRequirementListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        seed_default_requirements()
        lang = request.query_params.get("lang") or getattr(request.user, "preferred_language", "fr")
        qs = DocumentRequirement.objects.filter(active=True).order_by("sort_order")
        ser = DocumentRequirementSerializer(qs, many=True, context={"language": lang})
        return Response(ser.data)


class AssistantChatView(APIView):
    """Questions à l’IA sur le dossier (refus, axes d’amélioration)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        msg = (request.data.get("message") or "").strip()
        if not msg:
            return Response({"detail": "message required", "code": "message_required"}, status=status.HTTP_400_BAD_REQUEST)
        app = None
        raw_id = request.data.get("application_id")
        if raw_id is not None and str(raw_id).strip() != "":
            app = get_object_or_404(LoanApplication, pk=int(raw_id))
            if app.user_id != request.user.id and not can_access_all_applications(request.user):
                return Response({"detail": "forbidden"}, status=status.HTTP_403_FORBIDDEN)
        lang = getattr(request.user, "preferred_language", "fr") or "fr"
        actor_role = "backoffice" if can_access_all_applications(request.user) else "customer"
        out = build_assistant_reply(
            user_message=msg,
            language=str(lang),
            user_email=request.user.email,
            application=app,
            actor_role=actor_role,
        )
        if out.get("error"):
            return Response(out, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(out)


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "LoanWise"})


class SeedDemoView(APIView):
    """Populate default document requirements (hackathon convenience)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        n = seed_default_requirements()
        return Response({"created": n})
