"""
UI helpers: derive pipeline progress (0–100) from persisted application state.
"""

from __future__ import annotations

from core.models import LoanApplication, LoanApplicationStatus


def application_pipeline_progress_percent(application: LoanApplication) -> int:
    """
    Approximate progress for the progress bar (formulaire + documents + orchestration).

    100% only when scoring has run (eligibility_score set) or terminal workflow state.
    """
    if application.eligibility_score is not None:
        return 100
    st = application.status
    if st in (LoanApplicationStatus.VALIDATED, LoanApplicationStatus.REJECTED):
        return 100

    n_doc = application.documents.count()

    p = 15
    if application.has_complete_financial_profile():
        p = 42
    p += min(42, n_doc * 9)

    step = application.current_step or ""
    if step in ("review", "done"):
        p = max(p, 78)
    if st == LoanApplicationStatus.UNDER_REVIEW:
        p = max(p, 92)

    return min(99, p)
