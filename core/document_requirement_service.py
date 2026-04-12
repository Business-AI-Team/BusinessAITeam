"""
Dynamic document requirements: resolve which documents apply to a loan type,
labels by language, and easy extension via DB rows or future rules engine.
"""

from __future__ import annotations

from core.models import DocumentRequirement, LoanApplication, LoanType


def requirements_for_loan_type(loan_type: str) -> list[DocumentRequirement]:
    """
    Active requirements whose `applies_to_loan_types` includes the given loan type.

    Filtering is done in Python: SQLite does not support JSONField ``__contains`` lookups.
    PostgreSQL would support them, but a single code path keeps dev and prod aligned.
    """
    qs = DocumentRequirement.objects.filter(active=True).order_by("sort_order", "code")
    return [r for r in qs if loan_type in (r.applies_to_loan_types or [])]


def requirements_for_application(application: LoanApplication) -> list[DocumentRequirement]:
    return requirements_for_loan_type(application.loan_type)


def label_for(req: DocumentRequirement, language: str) -> str:
    if language.startswith("fr"):
        return req.label_fr
    return req.label_en


def description_for(req: DocumentRequirement, language: str) -> str:
    if language.startswith("fr"):
        return req.description_fr
    return req.description_en


def missing_required_codes(
    application: LoanApplication,
    uploaded_codes: set[str],
) -> list[str]:
    """
    Return requirement codes that are still missing (required only).
    `uploaded_codes` should be the set of DocumentRequirement.code for attached docs.
    """
    needed: list[str] = []
    for req in requirements_for_application(application):
        if not req.is_required:
            continue
        if req.code not in uploaded_codes:
            needed.append(req.code)
    return needed


def seed_default_requirements() -> int:
    """
    Idempotent seed for development/demo: returns number of created rows.
    """
    defaults = [
        {
            "code": "identity_card",
            "label_fr": "Pièce d'identité",
            "label_en": "Government ID",
            "description_fr": "CNI ou passeport valide.",
            "description_en": "Valid national ID or passport.",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.MORTGAGE, LoanType.BUSINESS],
            "is_required": True,
            "sort_order": 10,
        },
        {
            "code": "income_proof",
            "label_fr": "Justificatif de revenus",
            "label_en": "Income proof",
            "description_fr": "Bulletins de salaire ou avis d'imposition récents.",
            "description_en": "Recent payslips or tax assessment.",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.MORTGAGE, LoanType.BUSINESS],
            "is_required": True,
            "sort_order": 20,
        },
        {
            "code": "face_selfie",
            "label_fr": "Selfie pour vérification",
            "label_en": "Selfie for verification",
            "description_fr": "Photo du visage pour correspondance avec la pièce d'identité.",
            "description_en": "Face photo for matching with ID document.",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.BUSINESS],
            "is_required": True,
            "sort_order": 30,
        },
        {
            "code": "business_plan",
            "label_fr": "Business plan",
            "label_en": "Business plan",
            "description_fr": "Pour les prêts professionnels.",
            "description_en": "For business loans.",
            "applies_to_loan_types": [LoanType.BUSINESS],
            "is_required": True,
            "sort_order": 40,
        },
    ]
    created = 0
    for row in defaults:
        _, was_created = DocumentRequirement.objects.get_or_create(
            code=row["code"],
            defaults=row,
        )
        if was_created:
            created += 1
    return created
