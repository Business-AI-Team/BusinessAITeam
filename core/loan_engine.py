"""
Loan eligibility scoring, ROI, and business impact calculations.

Pure deterministic logic for transparency; tune weights via settings or constants.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

from core.eligibility_explanation import build_eligibility_detail
from core.models import LoanApplication


def _financial_base_score(application: LoanApplication) -> Decimal:
    """
    Debt-to-income style score 0–100 from declared income, amount, term (deterministic).

    Zéro / absent = données manquantes → score 0 (on n’utilise plus de « faux 1 € » qui faussait le ratio).
    """
    if not application.has_complete_financial_profile():
        return Decimal("0")
    income = application.annual_income
    amount = application.amount_requested
    months = max(1, application.term_months or 12)
    rate = Decimal(str(getattr(settings, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)))
    monthly_rate = rate / Decimal("12")
    if monthly_rate > 0:
        pow_term = (Decimal("1") + monthly_rate) ** months
        payment = amount * (monthly_rate * pow_term) / (pow_term - Decimal("1"))
    else:
        payment = amount / Decimal(months)
    monthly_income = income / Decimal("12")
    if monthly_income <= 0:
        return Decimal("0")
    dti = payment / monthly_income
    raw = Decimal("100") - (dti * Decimal("100"))
    score = max(Decimal("0"), min(Decimal("100"), raw))
    return score.quantize(Decimal("0.01"))


def compute_eligibility_score(application: LoanApplication) -> Decimal:
    """
    Final 0–100 score from declared financials (deterministic).

    Facial recognition has been removed; score is based on income, amount, and term only.
    """
    base = _financial_base_score(application)
    return max(Decimal("0"), min(Decimal("100"), base)).quantize(Decimal("0.01"))


def compute_roi_and_impact(application: LoanApplication) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Build ROI summary and business impact dicts for dashboard and PDF.

    Returns (roi_summary, business_impact).
    """
    amount = application.amount_requested or Decimal("0")
    months = application.term_months or 12
    rate = Decimal(str(getattr(settings, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)))
    monthly_rate = rate / Decimal("12")
    if monthly_rate > 0 and months and months > 0:
        pow_term = (Decimal("1") + monthly_rate) ** months
        total_repay = amount * (monthly_rate * pow_term) / (pow_term - Decimal("1")) * Decimal(months)
    else:
        total_repay = amount
    interest_cost = total_repay - amount
    roi_pct = Decimal("0")
    if amount > 0:
        roi_pct = ((application.annual_income * (months / Decimal("12"))) / amount * Decimal("100")).quantize(
            Decimal("0.01")
        )

    base = _financial_base_score(application)
    final = compute_eligibility_score(application)
    roi_summary = {
        "principal": str(amount),
        "term_months": months,
        "annual_rate_assumed": str(rate),
        "estimated_total_repayment": str(total_repay.quantize(Decimal("0.01"))),
        "estimated_interest": str(interest_cost.quantize(Decimal("0.01"))),
        "income_to_loan_ratio_percent": str(roi_pct),
        "currency": getattr(settings, "LOANWISE_CURRENCY", "EUR"),
        "eligibility_financial_base": str(base),
        "eligibility_verification_adjustment": "0",
        "eligibility_final": str(final),
        "eligibility_note": (
            "Score uses only declared income, amount, and term (deterministic). "
            "Facial recognition is not used."
        ),
    }
    business_impact = {
        "time_saved_hours": 4.5,
        "automation_rate_percent": 92,
        "risk_flags_reduced": 3,
        "summary_key": "positive_flow" if final >= Decimal("50") else "review",
    }
    return roi_summary, business_impact


def run_eligibility_for_application(application: LoanApplication) -> LoanApplication:
    """Persist score, ROI, impact, and structured eligibility explanation on the application row."""
    score = compute_eligibility_score(application)
    application.eligibility_score = score
    roi, impact = compute_roi_and_impact(application)
    try:
        roi["eligibility_detail"] = build_eligibility_detail(application)
    except Exception as exc:
        logger.warning("build_eligibility_detail failed: %s", exc)
        roi["eligibility_detail"] = {"error": "explanation_unavailable"}
    application.roi_summary = roi
    application.business_impact = impact
    application.save(
        update_fields=[
            "eligibility_score",
            "roi_summary",
            "business_impact",
            "updated_at",
        ]
    )
    return application
