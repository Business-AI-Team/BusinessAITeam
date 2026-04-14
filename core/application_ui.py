"""
UI helpers: derive pipeline progress (0–100) from persisted application state.
"""

from __future__ import annotations

from decimal import Decimal

from core.currency_fx import convert_amount, primary_currency_for_country
from core.models import Customer, LoanApplication, LoanApplicationStatus


def income_display_for_payslip_estimate(
    customer: Customer | None,
    application: LoanApplication,
) -> dict[str, Decimal | str] | None:
    """
    Montant + devise pour l’affichage du revenu **estimé depuis les bulletins** :
    conversion vers la devise du pays (ex. MG → MGA) lorsque différente de ``income_currency``.
    Les revenus saisis manuellement sur le profil restent affichés en ``income_currency`` (hors de cette fonction).
    """
    if customer is None:
        return None
    if customer.annual_income and customer.annual_income > 0:
        return None
    eff = application.effective_annual_income
    if eff is None or eff <= 0:
        return None
    base_ccy = (customer.income_currency or "EUR").upper().strip()
    nat = primary_currency_for_country(customer.country)
    if nat and nat != base_ccy:
        amt = convert_amount(eff, base_ccy, nat)
        return {"amount": amt, "currency": nat}
    return {"amount": eff, "currency": base_ccy}


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

    # Use len() to leverage Django's prefetch_related cache when available
    n_doc = len(application.documents.all())

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
