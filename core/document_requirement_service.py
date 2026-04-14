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


def document_counts_by_requirement_code(application: LoanApplication) -> dict[str, int]:
    """Non-deleted uploads grouped by requirement code (for min_files)."""
    counts: dict[str, int] = {}
    for doc in application.documents.filter(deleted_at__isnull=True).select_related("requirement"):
        if doc.requirement_id and doc.requirement:
            code = doc.requirement.code
            counts[code] = counts.get(code, 0) + 1
    return counts


def missing_required_codes(
    application: LoanApplication,
    counts_by_code: dict[str, int] | None = None,
) -> list[str]:
    """
    Requirement codes still below ``min_files`` (or absent).
    """
    if counts_by_code is None:
        counts_by_code = document_counts_by_requirement_code(application)
    needed: list[str] = []
    for req in requirements_for_application(application):
        if not req.is_required:
            continue
        have = counts_by_code.get(req.code, 0)
        min_f = max(1, getattr(req, "min_files", 1) or 1)
        if have < min_f:
            needed.append(req.code)
    return needed


def seed_default_requirements() -> int:
    """
    Idempotent seed for development/demo: returns number of created rows.
    """
    defaults = [
        {
            "code": "identity_card",
            "label_fr": "CNI / Passeport",
            "label_en": "ID card / Passport",
            "description_fr": "Carte nationale d'identité ou passeport valide.",
            "description_en": "Valid national ID card or passport.",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.MORTGAGE, LoanType.BUSINESS],
            "is_required": True,
            "min_files": 1,
            "sort_order": 10,
        },
        {
            "code": "proof_of_address",
            "label_fr": "Justificatif de domicile",
            "label_en": "Proof of address",
            "description_fr": "Facture récente (électricité, eau, téléphone) ou avis d'imposition.",
            "description_en": "Recent utility bill or official proof of residence.",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.MORTGAGE, LoanType.BUSINESS],
            "is_required": True,
            "min_files": 1,
            "sort_order": 20,
        },
        {
            "code": "income_proof",
            "label_fr": "Trois bulletins de salaire",
            "label_en": "Three payslips",
            "description_fr": "Les trois derniers bulletins de salaire (ou équivalent).",
            "description_en": "Your last three payslips (or equivalent).",
            "applies_to_loan_types": [LoanType.PERSONAL, LoanType.MORTGAGE, LoanType.BUSINESS],
            "is_required": True,
            "min_files": 3,
            "payslip_distinct_months_window": 3,
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
            "min_files": 1,
            "sort_order": 40,
        },
    ]
    created = 0
    for row in defaults:
        code = row["code"]
        min_files = row.get("min_files", 1)
        data = {k: v for k, v in row.items() if k != "min_files" and k != "payslip_distinct_months_window"}
        payslip_w = row.get("payslip_distinct_months_window")
        obj, was_created = DocumentRequirement.objects.get_or_create(
            code=code,
            defaults={
                **data,
                "min_files": min_files,
                **({"payslip_distinct_months_window": payslip_w} if payslip_w is not None else {}),
            },
        )
        if was_created:
            created += 1
        else:
            updates: list[str] = []
            for k, v in data.items():
                if getattr(obj, k, None) != v:
                    setattr(obj, k, v)
                    updates.append(k)
            if obj.min_files != min_files:
                obj.min_files = min_files
                updates.append("min_files")
            if payslip_w is not None and getattr(obj, "payslip_distinct_months_window", None) != payslip_w:
                obj.payslip_distinct_months_window = payslip_w
                updates.append("payslip_distinct_months_window")
            if updates:
                obj.save(update_fields=list(set(updates)))
    return created


def default_doc_kind_for_requirement_code(code: str) -> str:
    """Suggested DocumentKind for uploads (UI hint)."""
    if code == "identity_card":
        return "identity"
    if code == "proof_of_address":
        return "address"
    if code == "income_proof":
        return "income"
    return "generic"
