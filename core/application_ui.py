"""
UI helpers: derive pipeline progress (0–100) from persisted application state.
"""

from __future__ import annotations

from core.models import LoanRequest, LoanRequestStatus


def application_pipeline_progress_percent(application: LoanRequest) -> int:
    """
    Approximate progress for the progress bar (matches chat/doc/orchestration reality).

    100% only when scoring has run (score set) or terminal workflow state.
    """
    if application.score is not None:
        return 100
    st = application.status
    if st in (LoanRequestStatus.VALIDATED, LoanRequestStatus.REJECTED):
        return 100

    n_msg = application.chat_messages.count()
    n_doc = application.documents.count()

    p = 8
    p += min(40, n_msg * 4)
    p += min(42, n_doc * 9)

    step = application.current_step or ""
    if step in ("review", "done"):
        p = max(p, 78)
    if st == LoanRequestStatus.PENDING:
        p = max(p, 92)

    return min(99, p)
