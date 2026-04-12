"""
Human-readable analysis summary for the application workspace (eligibility score).
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

from core.models import LoanApplication, LoanApplicationStatus


def analysis_rows_for_template(app: LoanApplication) -> list[dict[str, Any]]:
    """Build rows for the « Analysis results » card (server-rendered)."""
    rows: list[dict[str, Any]] = []

    if app.eligibility_score is not None:
        roi = app.roi_summary or {}
        base_s = roi.get("eligibility_financial_base")
        if base_s is not None:
            detail = _(
                "Financial base (income, amount, term): %(base)s. Total score: %(score)s."
            ) % {"base": base_s, "score": app.eligibility_score}
        else:
            detail = _("Total eligibility score: %(score)s") % {"score": app.eligibility_score}
        rows.append(
            {
                "title": _("Eligibility score"),
                "ok": app.status == LoanApplicationStatus.VALIDATED,
                "detail": detail,
                "percent": None,
            }
        )

    return rows
