"""
Document requirements: resolve which documents to request from a customer
when they submit a loan request. The list is fully driven by DocumentRequirement
rows created by Back-Office staff.
"""

from __future__ import annotations

from core.models import DocumentRequirement, LoanRequest


def all_requirements() -> list[DocumentRequirement]:
    """Return all document requirements ordered by name."""
    return list(DocumentRequirement.objects.order_by("name"))


def requirements_for_application(application: LoanRequest) -> list[DocumentRequirement]:
    """All requirements apply to every loan request."""
    return all_requirements()


def missing_mandatory(
    application: LoanRequest,
    uploaded_requirement_ids: set[int],
) -> list[DocumentRequirement]:
    """
    Return mandatory requirements not yet covered by uploaded documents.
    `uploaded_requirement_ids` is the set of DocumentRequirement PKs already uploaded.
    """
    return [
        req
        for req in all_requirements()
        if req.is_mandatory and req.pk not in uploaded_requirement_ids
    ]
