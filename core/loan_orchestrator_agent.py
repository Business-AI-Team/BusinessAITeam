"""
Orchestrator: coordinates document analysis (vision/OCR path) and eligibility scoring.

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
from core.document_requirement_service import (
    document_counts_by_requirement_code,
    missing_required_codes,
    requirements_for_application,
)
from core.loan_engine import run_eligibility_for_application
from core.models import ApplicationDocument, LoanApplication, LoanApplicationStatus
from core.security_utils import secure_delete_file

logger = logging.getLogger(__name__)


def _is_video_or_legacy_face_upload(doc: ApplicationDocument) -> bool:
    name_l = (doc.original_filename or "").lower()
    if name_l.endswith((".webm", ".mp4", ".mov", ".mkv", ".avi")):
        return True
    k = doc.kind or ""
    return k in ("face_selfie", "liveness_video")


def run_orchestration(application: LoanApplication) -> LoanApplication:
    """
    Full pipeline: validate docs → analyze images/PDFs → score.

    Video / legacy face uploads are skipped (facial recognition disabled). Deletes local files after analysis when configured.
    """
    language = application.language or "fr"
    run_log: list[dict[str, Any]] = []

    def log(step: str, detail: dict[str, Any]) -> None:
        run_log.append({"step": step, "detail": detail})

    log("start", {"language": language})

    counts = document_counts_by_requirement_code(application)
    missing = missing_required_codes(application, counts)
    if missing:
        log("blocked", {"missing_documents": missing})
        application.orchestration_log = run_log
        application.status = LoanApplicationStatus.IN_PROGRESS
        application.save(update_fields=["orchestration_log", "status", "updated_at"])
        return application

    media_root = Path(settings.MEDIA_ROOT)
    delete_after = getattr(settings, "LOANWISE_DELETE_FILES_AFTER_ANALYSIS", True)

    for doc in list(application.documents.filter(deleted_at__isnull=True)):
        rel = doc.storage_path
        path = media_root / rel if rel else None
        if _is_video_or_legacy_face_upload(doc):
            prev = doc.analysis_result or {}
            doc.analysis_result = {
                **prev,
                "skipped": True,
                "note": "Video or legacy face-related upload — not analyzed (facial recognition disabled).",
            }
            doc.analyzed_at = timezone.now()
            doc.save(update_fields=["analysis_result", "analyzed_at"])
            log("document_skipped", {"document_id": doc.id, "reason": "facial_recognition_disabled"})
            continue
        if path and path.is_file():
            analysis = analyze_document_image(path, language=language)
            doc.analysis_result = analysis
            doc.analyzed_at = timezone.now()
            doc.save(update_fields=["analysis_result", "analyzed_at"])
            log("document_analyzed", {"document_id": doc.id, "engine": analysis.get("engine")})

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
        application.status = LoanApplicationStatus.VALIDATED
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
