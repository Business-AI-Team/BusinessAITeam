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
from core.document_requirement_service import missing_required_codes, requirements_for_application
from core.face_verification import verify_faces
from core.liveness_service import run_liveness_check
from core.loan_engine import run_eligibility_for_application
from core.models import ApplicationDocument, DocumentKind, LoanApplication, LoanApplicationStatus
from core.security_utils import secure_delete_file

logger = logging.getLogger(__name__)


def _find_doc_by_code(application: LoanApplication, code: str) -> ApplicationDocument | None:
    for doc in application.documents.filter(deleted_at__isnull=True).select_related("requirement"):
        if doc.requirement and doc.requirement.code == code:
            return doc
    return None


def _find_identity_doc(application: LoanApplication) -> ApplicationDocument | None:
    """Prefer explicit identity_card requirement; else latest document tagged as identity."""
    d = _find_doc_by_code(application, "identity_card")
    if d:
        return d
    return (
        application.documents.filter(deleted_at__isnull=True, kind=DocumentKind.IDENTITY)
        .order_by("-created_at")
        .first()
    )


def _find_liveness_video_doc(application: LoanApplication) -> ApplicationDocument | None:
    for doc in application.documents.filter(deleted_at__isnull=True):
        if doc.kind == DocumentKind.LIVENESS_VIDEO:
            return doc
    # Older uploads: liveness webm was saved as FACE_SELFIE because face_selfie requirement won over kind.
    for doc in application.documents.filter(deleted_at__isnull=True).order_by("-created_at"):
        name = (doc.original_filename or "").lower()
        if not name.endswith((".webm", ".mp4", ".mov", ".mkv", ".avi")):
            continue
        if doc.kind == DocumentKind.FACE_SELFIE or (
            doc.requirement and doc.requirement.code == "face_selfie"
        ):
            return doc
    return None


def run_orchestration(application: LoanApplication) -> LoanApplication:
    """
    Full pipeline: validate docs → analyze images → optional face match → score.

    Deletes local files after successful analysis and verification (configurable).
    """
    language = application.language or "fr"
    run_log: list[dict[str, Any]] = []

    def log(step: str, detail: dict[str, Any]) -> None:
        run_log.append({"step": step, "detail": detail})

    log("start", {"language": language})

    uploaded: set[str] = set()
    for doc in application.documents.filter(deleted_at__isnull=True):
        if doc.requirement:
            uploaded.add(doc.requirement.code)

    missing = missing_required_codes(application, uploaded)
    if missing:
        log("blocked", {"missing_documents": missing})
        application.orchestration_log = run_log
        application.status = LoanApplicationStatus.IN_PROGRESS
        application.save(update_fields=["orchestration_log", "status", "updated_at"])
        return application

    media_root = Path(settings.MEDIA_ROOT)
    delete_after = getattr(settings, "LOANWISE_DELETE_FILES_AFTER_ANALYSIS", True)

    # Phase 1: analyze each file (keep paths for face step); skip raw video (liveness handled later)
    for doc in list(application.documents.filter(deleted_at__isnull=True)):
        rel = doc.storage_path
        path = media_root / rel if rel else None
        if doc.kind == DocumentKind.LIVENESS_VIDEO:
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
            doc.kind == DocumentKind.FACE_SELFIE
            or (doc.requirement and doc.requirement.code == "face_selfie")
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
    id_doc = _find_identity_doc(application)
    liveness_doc = _find_liveness_video_doc(application)
    selfie = None
    for d in application.documents.filter(deleted_at__isnull=True).order_by("-created_at"):
        if liveness_doc and d.pk == liveness_doc.pk:
            continue
        if d.kind == DocumentKind.FACE_SELFIE or (d.requirement and d.requirement.code == "face_selfie"):
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
            application.liveness_verification = lv
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
            application.face_verification = fv
            application.save(update_fields=["liveness_verification", "face_verification", "updated_at"])
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
            application.liveness_verification = {
                "skipped": True,
                "reason": "static_selfie_only",
                "note": "No liveness video on file; DeepFace compares ID photo to static selfie only.",
            }
            fv = verify_faces(id_path, selfie_path)
            application.face_verification = fv
            application.save(
                update_fields=["liveness_verification", "face_verification", "updated_at"]
            )
            log("face_verification", {"verified": fv.get("verified"), "skipped": fv.get("skipped")})

    # Phase 3: secure deletion
    if delete_after:
        for doc in list(application.documents.filter(deleted_at__isnull=True)):
            rel = doc.storage_path
            path = media_root / rel if rel else None
            secure_delete_file(path)
            doc.storage_path = ""
            doc.deleted_at = timezone.now()
            doc.save(update_fields=["storage_path", "deleted_at"])

    application.status = LoanApplicationStatus.UNDER_REVIEW
    application.save(update_fields=["status", "updated_at"])
    log("scoring", {})

    if not application.submitted_at:
        application.submitted_at = timezone.now()
        application.save(update_fields=["submitted_at", "updated_at"])
    run_eligibility_for_application(application)
    threshold = Decimal(str(getattr(settings, "LOANWISE_APPROVAL_THRESHOLD", 55)))
    if application.eligibility_score is not None and application.eligibility_score >= threshold:
        application.status = LoanApplicationStatus.APPROVED
    else:
        application.status = LoanApplicationStatus.REJECTED
    application.save(update_fields=["status", "updated_at"])
    log("complete", {"score": str(application.eligibility_score), "status": str(application.status)})
    application.orchestration_log = run_log
    application.save(update_fields=["orchestration_log", "updated_at"])
    return application


def orchestration_progress(application: LoanApplication) -> dict[str, Any]:
    """Expose progress for UI (steps count, current phase)."""
    total_req = len(requirements_for_application(application))
    processed = application.documents.filter(deleted_at__isnull=False).count()
    log_len = len(application.orchestration_log or [])
    return {
        "total_requirements": total_req,
        "documents_archived": processed,
        "log_steps": log_len,
        "status": application.status,
    }
