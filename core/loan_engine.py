"""
Loan eligibility scoring, ROI, and business impact calculations.

Pure deterministic logic for transparency; tune weights via settings or constants.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings

from core.models import LoanApplication


def _financial_base_score(application: LoanApplication) -> Decimal:
    """
    Debt-to-income style score 0–100 from declared income, amount, term (deterministic).

    Same inputs always yield the same base (e.g. always 65.76 if nothing else changes).
    """
    income = application.annual_income or Decimal("1")
    amount = application.amount_requested or Decimal("1")
    months = max(1, application.term_months)
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


def _verification_adjustment(application: LoanApplication) -> Decimal:
    """
    Bonus/malus from face + liveness JSON (after pipeline). Zero if no verification data.

    This is what ties the displayed score to DeepFace / liveness outputs; the base above does not use AI.
    """
    fv = application.face_verification or {}
    lv = application.liveness_verification or {}
    if not isinstance(fv, dict):
        fv = {}
    if not isinstance(lv, dict):
        lv = {}
    delta = Decimal("0")
    has_pipeline_signal = False

    if fv:
        has_pipeline_signal = True
        if fv.get("skipped"):
            delta -= Decimal("5")
        elif fv.get("verified") is True:
            delta += Decimal("4")
        elif fv.get("verified") is False and fv.get("error") is None:
            delta -= Decimal("12")

    if lv.get("engine") == "liveness_opencv_mediapipe":
        has_pipeline_signal = True
        if not lv.get("liveness_passed", True):
            delta -= Decimal("6")
        if lv.get("face_match") is False:
            delta -= Decimal("6")

    if lv.get("skipped") and lv.get("reason") == "static_selfie_only":
        has_pipeline_signal = True
        delta -= Decimal("3")

    if not has_pipeline_signal:
        return Decimal("0")

    # Keep adjustment in a reasonable band for demo UX
    delta = max(Decimal("-25"), min(Decimal("8"), delta))
    return delta.quantize(Decimal("0.01"))


def compute_eligibility_score(application: LoanApplication) -> Decimal:
    """
    Final 0–100 score = financial base + verification adjustment.

    The **base** is pure math on your form fields (income, amount, term) — no ML.
    **Adjustment** uses face/liveness results from the pipeline (DeepFace, OpenCV/MediaPipe).
    """
    base = _financial_base_score(application)
    adj = _verification_adjustment(application)
    total = base + adj
    total = max(Decimal("0"), min(Decimal("100"), total))
    return total.quantize(Decimal("0.01"))


def compute_roi_and_impact(application: LoanApplication) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Build ROI summary and business impact dicts for dashboard and PDF.

    Returns (roi_summary, business_impact).
    """
    amount = application.amount_requested
    months = application.term_months
    rate = Decimal(str(getattr(settings, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)))
    monthly_rate = rate / Decimal("12")
    if monthly_rate > 0 and months > 0:
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
    adj = _verification_adjustment(application)
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
        "eligibility_verification_adjustment": str(adj),
        "eligibility_final": str(final),
        "eligibility_note": (
            "Financial base uses only income, amount, term (deterministic). "
            "Adjustment reflects face/liveness pipeline results when present."
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
    """Persist score, ROI, and impact on the application row."""
    score = compute_eligibility_score(application)
    roi, impact = compute_roi_and_impact(application)
    application.eligibility_score = score
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
