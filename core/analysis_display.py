"""
Human-readable analysis summary for the application workspace (OK / not OK + similarity %).

UX rules:
- Liveness = security session (pass/fail only, no confusing server/browser breakdown).
- Percentage = only “ID photo vs face” (DeepFace), never liveness quality.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

from core.face_verification import deepface_distance_to_similarity_percent
from core.models import LoanApplication, LoanApplicationStatus


def analysis_rows_for_template(app: LoanApplication) -> list[dict[str, Any]]:
    """Build rows for the « Analysis results » card (server-rendered). Order: liveness → face match % → eligibility."""
    fv = app.face_verification or {}
    lv = app.liveness_verification or {}
    rows: list[dict[str, Any]] = []

    # --- 1) Liveness (security): binary OK / Not OK, no per-axis server noise for end users ---
    if lv.get("engine") == "liveness_opencv_mediapipe":
        lp = bool(lv.get("liveness_passed"))
        rows.append(
            {
                "title": _("Liveness (session security)"),
                "ok": lp,
                "detail": (
                    _(
                        "The recording is only sent after you complete the on-screen steps. "
                        "This line confirms that the liveness session is accepted for your application."
                    )
                    if lp
                    else _(
                        "The liveness session could not be validated. Please start again and follow the steps until the end."
                    )
                ),
                "percent": None,
            }
        )
    elif lv.get("skipped") and lv.get("reason") == "static_selfie_only":
        rows.append(
            {
                "title": _("Liveness (session security)"),
                "ok": False,
                "detail": _(
                    "No liveness video was provided — only a static photo was compared to the ID. "
                    "For a full check, add a liveness recording from the guided capture."
                ),
                "percent": None,
            }
        )

    # --- 2) Face vs ID: percentage = ONLY similarity between CIN photo and face (video/selfie), not liveness ---
    pct = fv.get("match_similarity_percent")
    raw = fv.get("raw") or {}
    if pct is None and raw:
        pct = deepface_distance_to_similarity_percent(raw.get("distance"), raw.get("threshold"))

    if fv.get("skipped") and fv.get("error") == "deepface_not_installed":
        rows.append(
            {
                "title": _("Similarity: ID photo vs face (DeepFace)"),
                "ok": False,
                "detail": _("DeepFace is not installed. Add optional dependencies (see requirements-ai.txt)."),
                "percent": None,
            }
        )
    elif fv.get("skipped") and fv.get("error") == "missing_file":
        rows.append(
            {
                "title": _("Similarity: ID photo vs face (DeepFace)"),
                "ok": False,
                "detail": _("Could not read the ID or face image file."),
                "percent": None,
            }
        )
    elif pct is not None or fv.get("distance") is not None:
        ok = bool(fv.get("verified"))
        src = fv.get("source") or ""
        parts: list[str] = []
        parts.append(
            _(
                "This percentage estimates how close the face in your recording (or selfie) is to the photo "
                "on your ID. It does not measure head movements or blinks — only facial similarity."
            )
        )
        if pct is not None:
            parts.append(_("Approximate similarity: about %(pct)s%%.") % {"pct": pct})
        if src == "liveness_video":
            parts.append(_("Face crops are taken from your validated liveness video and compared to the ID photo."))
        else:
            parts.append(_("Comparison uses your uploaded selfie image against the ID photo."))
        detail = " ".join(parts) if parts else ""
        rows.append(
            {
                "title": _("Similarity: ID photo vs face (DeepFace)"),
                "ok": ok,
                "detail": detail,
                "percent": float(pct) if pct is not None else None,
            }
        )

    # --- 3) Eligibility (financial + pipeline adjustment) ---
    if app.eligibility_score is not None:
        roi = app.roi_summary or {}
        base_s = roi.get("eligibility_financial_base")
        adj_s = roi.get("eligibility_verification_adjustment")
        if base_s is not None and adj_s is not None:
            detail = _(
                "Financial base (income, amount, term): %(base)s. "
                "Pipeline adjustment (face/liveness): %(adj)s. Total: %(score)s."
            ) % {"base": base_s, "adj": adj_s, "score": app.eligibility_score}
        else:
            detail = _("Total eligibility score: %(score)s") % {"score": app.eligibility_score}
        rows.append(
            {
                "title": _("Eligibility score (pipeline)"),
                "ok": app.status == LoanApplicationStatus.APPROVED,
                "detail": detail,
                "percent": None,
            }
        )

    return rows
