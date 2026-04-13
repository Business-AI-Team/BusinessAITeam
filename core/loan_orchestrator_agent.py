"""
Orchestrator: coordinates document analysis, face verification, and eligibility scoring.

Logs structured steps into `LoanApplication.orchestration_log` for audit and UI progress.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from core.document_processor import analyze_document_image
from core.document_requirement_service import missing_mandatory, requirements_for_application
from core.face_verification import verify_faces
from core.liveness_service import run_liveness_check
from core.loan_engine import run_eligibility_for_application
from core.models import Document, DocumentType, LoanRequest, LoanRequestStatus
from core.security_utils import secure_delete_file

logger = logging.getLogger(__name__)


def _find_identity_doc(loan_request: LoanRequest) -> Document | None:
    """Latest document tagged as ID Card."""
    return (
        loan_request.documents.filter(deleted_at__isnull=True, document_type=DocumentType.ID_CARD)
        .order_by("-created_at")
        .first()
    )


def _find_liveness_video_doc(loan_request: LoanRequest) -> Document | None:
    for doc in loan_request.documents.filter(deleted_at__isnull=True):
        if doc.document_type == DocumentType.LIVENESS_VIDEO:
            return doc
    for doc in loan_request.documents.filter(deleted_at__isnull=True).order_by("-created_at"):
        name = (doc.original_filename or "").lower()
        if not name.endswith((".webm", ".mp4", ".mov", ".mkv", ".avi")):
            continue
        if doc.document_type == DocumentType.FACE_SELFIE:
            return doc
    return None


def run_orchestration(loan_request: LoanRequest) -> LoanRequest:
    """
    Full pipeline: validate docs → analyze images → optional face match → score.

    Deletes local files after successful analysis and verification (configurable).
    """
    language = loan_request.language or "fr"
    run_log: list[dict[str, Any]] = []

    def log(step: str, detail: dict[str, Any]) -> None:
        run_log.append({"step": step, "detail": detail})

    log("start", {"language": language})

    uploaded_ids: set[int] = {
        doc.requirement_id
        for doc in loan_request.documents.filter(deleted_at__isnull=True)
        if doc.requirement_id
    }

    missing = missing_mandatory(loan_request, uploaded_ids)
    if missing:
        log("blocked", {"missing_documents": [r.name for r in missing]})
        loan_request.orchestration_log = run_log
        loan_request.status = LoanRequestStatus.PENDING
        loan_request.save(update_fields=["orchestration_log", "status", "modification_date"])
        return loan_request

    media_root = Path(settings.MEDIA_ROOT)
    delete_after = getattr(settings, "LOANWISE_DELETE_FILES_AFTER_ANALYSIS", True)

    # Phase 1: analyze each file; skip raw video (liveness handled later)
    for doc in list(loan_request.documents.filter(deleted_at__isnull=True)):
        rel = doc.storage_path
        path = media_root / rel if rel else None
        if doc.document_type == DocumentType.LIVENESS_VIDEO:
            prev = doc.analysis_result or {}
            doc.analysis_result = {
                **prev,
                "type": "liveness_video",
                "note": "Analyzed in liveness phase (OpenCV / MediaPipe + face match).",
            }
            doc.analyzed_at = timezone.now()
            doc.save(update_fields=["analysis_result", "analyzed_at"])
            log("document_analyzed", {"document_id": doc.id, "engine": "liveness_video"})
            continue
        name_l = (doc.original_filename or "").lower()
        if name_l.endswith((".webm", ".mp4", ".mov", ".mkv", ".avi")) and (
            doc.document_type == DocumentType.FACE_SELFIE
        ):
            prev = doc.analysis_result or {}
            doc.analysis_result = {
                **prev,
                "type": "liveness_video",
                "note": "Video tagged as face_selfie (legacy upload); analyzed in liveness phase.",
            }
            doc.analyzed_at = timezone.now()
            doc.save(update_fields=["analysis_result", "analyzed_at"])
            log("document_analyzed", {"document_id": doc.id, "engine": "liveness_video"})
            continue
        if path and path.is_file():
            analysis = analyze_document_image(path, language=language)
            doc.analysis_result = analysis
            doc.analyzed_at = timezone.now()
            doc.save(update_fields=["analysis_result", "analyzed_at"])
            log("document_analyzed", {"document_id": doc.id, "engine": analysis.get("engine")})

    # Phase 2: face verification — prefer liveness video + ID; else static selfie + ID
    id_doc = _find_identity_doc(loan_request)
    liveness_doc = _find_liveness_video_doc(loan_request)
    selfie = None
    for d in loan_request.documents.filter(deleted_at__isnull=True).order_by("-created_at"):
        if liveness_doc and d.pk == liveness_doc.pk:
            continue
        if d.document_type == DocumentType.FACE_SELFIE:
            selfie = d
            break

    debug_ai = getattr(settings, "DEBUG", False)

    if id_doc and liveness_doc:
        id_path = media_root / id_doc.storage_path if id_doc.storage_path else None
        vid_path = media_root / liveness_doc.storage_path if liveness_doc.storage_path else None
        if id_path and vid_path and id_path.is_file() and vid_path.is_file():
            client_done = bool((liveness_doc.analysis_result or {}).get("client_sequence_completed"))
            lv = run_liveness_check(
                id_path,
                vid_path,
                language=language,
                debug=debug_ai,
                client_sequence_completed=client_done,
            )
            loan_request.liveness_verification = lv
            fv = {
                "source": "liveness_video",
                "verified": bool(lv.get("face_match") and lv.get("liveness_passed")),
                "liveness_passed": lv.get("liveness_passed"),
                "blink_detected": lv.get("blink_detected"),
                "face_match": lv.get("face_match"),
                "skipped": bool(lv.get("error")),
                "summary": lv.get("summary"),
                "match_similarity_percent": lv.get("face_match_similarity_percent"),
            }
            loan_request.face_verification = fv
            loan_request.save(update_fields=["liveness_verification", "face_verification", "modification_date"])
            log(
                "liveness",
                {
                    "liveness_passed": lv.get("liveness_passed"),
                    "face_match": lv.get("face_match"),
                    "error": lv.get("error"),
                },
            )
    elif id_doc and selfie:
        id_path = media_root / id_doc.storage_path if id_doc.storage_path else None
        selfie_path = media_root / selfie.storage_path if selfie.storage_path else None
        if id_path and selfie_path and id_path.is_file() and selfie_path.is_file():
            loan_request.liveness_verification = {
                "skipped": True,
                "reason": "static_selfie_only",
                "note": "No liveness video on file; DeepFace compares ID photo to static selfie only.",
            }
            fv = verify_faces(id_path, selfie_path)
            loan_request.face_verification = fv
            loan_request.save(
                update_fields=["liveness_verification", "face_verification", "modification_date"]
            )
            log("face_verification", {"verified": fv.get("verified"), "skipped": fv.get("skipped")})

    # Phase 3: secure deletion
    if delete_after:
        for doc in list(loan_request.documents.filter(deleted_at__isnull=True)):
            rel = doc.storage_path
            path = media_root / rel if rel else None
            secure_delete_file(path)
            doc.storage_path = ""
            doc.deleted_at = timezone.now()
            doc.save(update_fields=["storage_path", "deleted_at"])

    loan_request.status = LoanRequestStatus.PENDING
    loan_request.save(update_fields=["status", "modification_date"])
    log("scoring", {})

    if not loan_request.submitted_at:
        loan_request.submitted_at = timezone.now()
        loan_request.save(update_fields=["submitted_at", "modification_date"])
    run_eligibility_for_application(loan_request)
    threshold = float(getattr(settings, "LOANWISE_APPROVAL_THRESHOLD", 0.55))
    if loan_request.score is not None and loan_request.score >= threshold:
        loan_request.status = LoanRequestStatus.VALIDATED
    else:
        loan_request.status = LoanRequestStatus.REJECTED
    loan_request.save(update_fields=["status", "modification_date"])
    log("complete", {"score": str(loan_request.score), "status": str(loan_request.status)})
    loan_request.orchestration_log = run_log
    loan_request.save(update_fields=["orchestration_log", "modification_date"])
    return loan_request


def orchestration_progress(loan_request: LoanRequest) -> dict[str, Any]:
    """Expose progress for UI (steps count, current phase)."""
    total_req = len(requirements_for_application(loan_request))
    processed = loan_request.documents.filter(deleted_at__isnull=False).count()
    log_len = len(loan_request.orchestration_log or [])
    return {
        "total_requirements": total_req,
        "documents_archived": processed,
        "log_steps": log_len,
        "status": loan_request.status,
    }
