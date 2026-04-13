"""
REST API views (API-first). Web UI consumes the same endpoints where relevant.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.document_requirement_service import label_for, seed_default_requirements
from core.email_verification_service import send_registration_verification_email, verify_email_with_code, verify_email_with_token
from core.loan_chatbot_agent import run_chat_turn
from core.loan_orchestrator_agent import run_orchestration
from core.models import (
    Document,
    ChatMessage,
    DocumentType,
    DocumentRequirement,
    EmailVerificationToken,
    LoanRequest,
)
from core.pdf_report import build_application_pdf
from core.security_utils import sha256_file
from core.serializers import (
    DocumentSerializer,
    ChatMessageSerializer,
    ChatSendSerializer,
    DocumentRequirementSerializer,
    LoanRequestSerializer,
    LoanRequestWriteSerializer,
    RegisterSerializer,
    UserPreferencesSerializer,
    UserSerializer,
)

# Backward-compat aliases used below
ApplicationDocumentSerializer = DocumentSerializer
LoanApplicationSerializer = LoanRequestSerializer
LoanApplicationWriteSerializer = LoanRequestWriteSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


@extend_schema(tags=["auth"])
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        if getattr(settings, "LOANWISE_AUTO_VERIFY_EMAIL_IN_DEBUG", False) and settings.DEBUG:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        evt = EmailVerificationToken.create_for_user(user)
        verify_url = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/api/auth/verify/?token={evt.token}"
        try:
            send_registration_verification_email(user, evt)
        except Exception as e:
            logger.warning("Could not send verification email: %s", e)
        payload: dict = {"detail": "registered", "code": "registered"}
        if settings.DEBUG:
            payload["verify_url_hint"] = verify_url
        return Response(payload, status=status.HTTP_201_CREATED)


@extend_schema(tags=["auth"])
class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        token = request.query_params.get("token")
        ok, detail = verify_email_with_token(token)
        if not ok:
            return Response({"detail": detail, "code": detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": detail, "code": detail})

    def post(self, request):
        """Verify email with numeric code: ``{"email": "...", "code": "123456"}``."""
        ok, detail = verify_email_with_code(request.data.get("email"), request.data.get("code"))
        if not ok:
            return Response({"detail": detail, "code": detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": detail, "code": detail})


@extend_schema(tags=["auth"])
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


@extend_schema(tags=["auth"])
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


@extend_schema_view(
    list=extend_schema(tags=["applications"]),
    retrieve=extend_schema(tags=["applications"]),
    create=extend_schema(tags=["applications"]),
)
class LoanRequestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options", "patch"]

    def get_queryset(self):
        return LoanRequest.objects.filter(account=self.request.user)

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return LoanRequestWriteSerializer
        return LoanRequestSerializer

    def perform_create(self, serializer):
        lang = serializer.validated_data.get("language") or self.request.user.preferred_language or "fr"
        serializer.save(account=self.request.user, language=lang)

    def create(self, request, *args, **kwargs):
        """Return full loan request payload (incl. id, reference) after create — needed by the web UI."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        assert isinstance(instance, LoanRequest)
        out = LoanRequestSerializer(instance, context=self.get_serializer_context())
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"])
    def chat(self, request, pk=None):
        loan_req = self.get_object()
        ser = ChatSendSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reply = run_chat_turn(loan_req, ser.validated_data["message"])
        return Response({"reply": reply})

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        loan_req = self.get_object()
        qs = ChatMessage.objects.filter(loan_request=loan_req)
        return Response(ChatMessageSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def orchestrate(self, request, pk=None):
        loan_req = self.get_object()
        require = getattr(settings, "LOANWISE_REQUIRE_EMAIL_VERIFICATION", True)
        if require and not request.user.email_verified:
            return Response({"detail": "email not verified", "code": "email_not_verified"}, status=403)
        try:
            loan_req = run_orchestration(loan_req)
        except Exception as e:
            logger.exception("Orchestration failed for loan request %s", loan_req.pk)
            payload = {
                "detail": "Orchestration failed; see server logs or retry later.",
                "code": "orchestration_failed",
            }
            if settings.DEBUG:
                payload["exception"] = type(e).__name__
                payload["message"] = str(e)
            return Response(payload, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        loan_req.refresh_from_db()
        steps = loan_req.orchestration_log or []
        last = steps[-1] if steps else {}
        step_name = last.get("step")
        lang = loan_req.language or "fr"
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
                "application": LoanRequestSerializer(loan_req).data,
                "pipeline": pipeline,
            }
        )

    @action(detail=True, methods=["get"])
    def export_pdf(self, request, pk=None):
        loan_req = self.get_object()
        pdf_bytes = build_application_pdf(loan_req)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="loanwise-{loan_req.reference}.pdf"'
        return resp


# Backward-compat alias for URL routing
LoanApplicationViewSet = LoanRequestViewSet


@extend_schema(tags=["documents"])
class DocumentUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, application_id: int):
        app = get_object_or_404(LoanRequest, pk=application_id, account=request.user)
        requirement_id = request.POST.get("requirement_id")
        kind = request.POST.get("kind") or "generic"
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file required"}, status=400)
        req = None
        if requirement_id:
            req = DocumentRequirement.objects.filter(pk=requirement_id).first()
        if (kind == "liveness_video" or kind == "face_selfie") and req is None:
            req = DocumentRequirement.objects.filter(code="face_selfie", active=True).first()
        if kind == "liveness_video":
            ext = Path(file.name).suffix.lower()
            if ext not in (".webm", ".mp4", ".mov", ".mkv", ".avi"):
                ext = ".webm"
            stored_name = f"liveness_{uuid.uuid4().hex}{ext}"
        else:
            stored_name = file.name
        rel_path = f"loanwise/{app.id}/{stored_name}"
        from django.core.files.storage import default_storage

        path = default_storage.save(rel_path, file)
        full = default_storage.path(path)
        digest = sha256_file(full)
        valid_types = {c[0] for c in DocumentType.choices}
        analysis_result: dict = {}
        if kind == "liveness_video":
            # Set by the web UI only after the guided MediaPipe sequence completes (recording stops then).
            raw_client = (request.POST.get("client_liveness_completed") or "").strip().lower()
            analysis_result["client_sequence_completed"] = raw_client in ("1", "true", "yes", "on")
        doc = Document.objects.create(
            loan_request=app,
            requirement=req,
            document_type=kind if kind in valid_types else DocumentType.OTHER,
            original_filename=file.name,
            content_type=file.content_type or "",
            sha256_hex=digest,
            file_size=file.size,
            storage_path=path,
            analysis_result=analysis_result,
        )
        # Liveness must win over requirement=face_selfie (otherwise webm is mis-tagged as FACE_SELFIE).
        if kind == "liveness_video":
            doc.document_type = DocumentType.LIVENESS_VIDEO
            doc.save(update_fields=["document_type"])
        elif kind == "face_selfie" or (req and req.code == "face_selfie"):
            doc.document_type = DocumentType.FACE_SELFIE
            doc.save(update_fields=["document_type"])
        return Response(DocumentSerializer(doc).data, status=201)


@extend_schema(tags=["requirements"])
class DocumentRequirementListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        seed_default_requirements()
        lang = request.query_params.get("lang") or getattr(request.user, "preferred_language", "fr")
        qs = DocumentRequirement.objects.filter(active=True).order_by("sort_order")
        ser = DocumentRequirementSerializer(qs, many=True, context={"language": lang})
        return Response(ser.data)


@extend_schema(tags=["system"])
class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "LoanWise"})


@extend_schema(tags=["system"])
class SeedDemoView(APIView):
    """Populate default document requirements (hackathon convenience)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        n = seed_default_requirements()
        return Response({"created": n})
