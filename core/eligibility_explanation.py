"""
Structured eligibility explanations for back-office and API.

Combines deterministic scoring (income, amount, term) with RAG policy excerpts
(admin PDFs) so refus/validation can be justified with numbers and document hints.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from django.conf import settings

from core.llm_eligibility_analysis import invoke_full_context_eligibility_llm
from core.models import LoanApplication
from core.rag_eligibility import get_all_active_knowledge_text, get_eligibility_guidance_from_rag


def _money_str(n: Decimal | float | int, currency: str) -> str:
    try:
        d = n if isinstance(n, Decimal) else Decimal(str(n))
    except Exception:
        d = Decimal("0")
    s = f"{d:,.0f}".replace(",", " ")
    return f"{s} {currency}".strip()


def _payment_and_dti(application: LoanApplication) -> tuple[Decimal, Decimal, Decimal] | None:
    """Estimated monthly payment, monthly income, DTI ratio — ou None si données insuffisantes."""
    if not application.has_complete_financial_profile():
        return None
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
    monthly_income = income / Decimal("12")  # type: ignore[operator]
    if monthly_income <= 0:
        return None
    dti = payment / monthly_income
    return payment, monthly_income, dti


def _extract_income_floor_from_text(text: str) -> int | None:
    """
    Heuristic: find a minimum annual income mentioned in policy text (e.g. Ariary).
    Picks the largest plausible candidate under 500M to avoid picking loan amounts.
    """
    if not text:
        return None
    candidates: list[int] = []
    for m in re.finditer(
        r"(?:revenu|salaire|gain)[^\d]{0,40}(?:minimum|min\.?|au moins|≥|>=)\D{0,12}(\d[\d\s\.]{4,})",
        text,
        re.I,
    ):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 5:
            try:
                candidates.append(int(digits))
            except ValueError:
                pass
    for m in re.finditer(
        r"(\d[\d\s\.]{4,})\s*(?:Ar|MGA|MGAR|Ariary)\b",
        text,
        re.I,
    ):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 5:
            try:
                v = int(digits)
                if 50_000 <= v <= 500_000_000:
                    candidates.append(v)
            except ValueError:
                pass
    if not candidates:
        return None
    return max(candidates)


def _rag_snippets_from_full_policy(full_policy: str, n: int = 4, max_len: int = 420) -> list[str]:
    """Extraits courts pour l’UI back-office à partir du corpus politique complet."""
    raw = (full_policy or "").strip()
    if not raw:
        return []
    blocks = re.split(r"\n{2,}===== \[", raw)
    out: list[str] = []
    for b in blocks:
        t = b.strip().replace("\n", " ")
        if len(t) < 25:
            continue
        if len(t) > max_len:
            t = t[: max_len - 1] + "…"
        out.append(t)
        if len(out) >= n:
            break
    if not out:
        t = raw.replace("\n", " ")
        out.append((t[: max_len - 1] + "…") if len(t) > max_len else t)
    return out[:n]


def build_eligibility_detail(application: LoanApplication, language: str | None = None) -> dict[str, Any]:
    """
    Full structured explanation: financial reasoning + policy (RAG) alignment.

    Stored under roi_summary['eligibility_detail'] after scoring.
    """
    lang = (language or application.language or "fr").lower()
    currency = getattr(settings, "LOANWISE_CURRENCY", "EUR")
    threshold = float(getattr(settings, "LOANWISE_APPROVAL_THRESHOLD", 55))
    score_f = float(application.eligibility_score) if application.eligibility_score is not None else 0.0
    validated = score_f >= threshold

    income = application.annual_income if application.annual_income is not None else Decimal("0")
    amount = application.amount_requested if application.amount_requested is not None else Decimal("0")
    months = max(1, application.term_months or 12)
    pmt_dti = _payment_and_dti(application)
    rate_annual = getattr(settings, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)

    loan_type = application.loan_type or "personal"
    policy_bundle = get_all_active_knowledge_text()
    combined_policy = (policy_bundle.get("text") or "").strip()
    guidance = get_eligibility_guidance_from_rag(
        loan_type,
        lang,
        preloaded_policy_bundle=policy_bundle,
    )
    income_floor = _extract_income_floor_from_text(combined_policy)

    min_amt = guidance.get("min_amount")
    max_amt = guidance.get("max_amount")
    policy_cur = guidance.get("currency") or currency

    profile_ok = application.has_complete_financial_profile() and pmt_dti is not None

    if not profile_ok:
        financial_bullets_fr = [
            f"Revenu annuel déclaré : {_money_str(income, currency)} ; montant demandé : {_money_str(amount, currency)} ; durée : {months} mois.",
            "Le score reste à 0 si le revenu ou le montant n’est pas renseigné ou est à zéro (valeurs par défaut du dossier).",
            "Complétez le montant et le revenu dans le formulaire du dossier, puis relancez le pipeline. "
            "Le score est une formule déterministe (capacité de remboursement), indépendante des appels OpenAI (analyse documents / RAG).",
            f"Score calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Declared annual income: {_money_str(income, currency)}; requested amount: {_money_str(amount, currency)}; term: {months} months.",
            "The score stays at 0 when income or amount is missing or zero (application defaults).",
            "Fill in amount and income in the application form, then re-run the pipeline. The score is a deterministic debt-to-income formula, separate from OpenAI (document analysis / RAG).",
            f"Computed score: {score_f:.2f} / 100 (internal threshold: {threshold:.0f}).",
        ]
        summary_fr = (
            f"Données financières insuffisantes (score {score_f:.2f}). "
            "Indiquez un revenu annuel et un montant de prêt strictement positifs pour un score interprétable."
        )
        summary_en = (
            f"Insufficient financial data (score {score_f:.2f}). "
            "Enter strictly positive annual income and loan amount for a meaningful score."
        )
    else:
        payment, monthly_income, dti = pmt_dti  # type: ignore[assignment]
        dti_pct = float(dti * Decimal("100"))
        financial_bullets_fr = [
            f"Revenu annuel déclaré : {_money_str(income, currency)}.",
            f"Mensualité estimée (taux annuel {rate_annual}, {months} mois) : {_money_str(payment, currency)}.",
            f"Revenu mensuel déclaré : {_money_str(monthly_income, currency)} — charge estimée / revenu mensuel ≈ {dti_pct:.1f} %.",
            f"Score d'éligibilité calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Declared annual income: {_money_str(income, currency)}.",
            f"Estimated monthly payment (annual rate {rate_annual}, {months} months): {_money_str(payment, currency)}.",
            f"Declared monthly income: {_money_str(monthly_income, currency)} — estimated payment-to-income ≈ {dti_pct:.1f}%.",
            f"Computed eligibility score: {score_f:.2f} / 100 (internal threshold: {threshold:.0f}).",
        ]
        if validated:
            summary_fr = (
                f"Décision indicative : éligible (score {score_f:.2f} ≥ seuil {threshold:.0f}). "
                f"Les critères financiers (revenu, mensualité, durée) sont cohérents avec le score."
            )
            summary_en = (
                f"Indicative decision: eligible (score {score_f:.2f} ≥ threshold {threshold:.0f}). "
                f"Financial inputs (income, payment, term) align with the computed score."
            )
        else:
            summary_fr = (
                f"Décision indicative : non éligible (score {score_f:.2f} < seuil {threshold:.0f}). "
                f"La mensualité estimée pèse fortement sur le revenu mensuel déclaré (ratio élevé), sauf autres éléments positifs."
            )
            summary_en = (
                f"Indicative decision: not eligible (score {score_f:.2f} < threshold {threshold:.0f}). "
                f"The estimated monthly burden is high relative to declared monthly income unless other factors apply."
            )

    policy_bullets_fr: list[str] = []
    policy_bullets_en: list[str] = []

    if profile_ok and guidance.get("available") and min_amt is not None and max_amt is not None:
        try:
            a = float(amount)
            mn, mx = float(min_amt), float(max_amt)
            if a < mn:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un montant minimum d’emprunt d’environ {_money_str(Decimal(str(mn)), policy_cur)} — "
                    f"votre demande ({_money_str(amount, currency)}) est en dessous."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a minimum loan amount around {_money_str(Decimal(str(mn)), policy_cur)} — "
                    f"your request ({_money_str(amount, currency)}) is below that range."
                )
            elif a > mx:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un plafond d’environ {_money_str(Decimal(str(mx)), policy_cur)} — "
                    f"votre demande ({_money_str(amount, currency)}) le dépasse."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a maximum around {_money_str(Decimal(str(mx)), policy_cur)} — "
                    f"your request ({_money_str(amount, currency)}) exceeds it."
                )
            else:
                policy_bullets_fr.append(
                    f"Le montant demandé se situe dans la fourchette indicative issue des documents ({_money_str(Decimal(str(mn)), policy_cur)} – {_money_str(Decimal(str(mx)), policy_cur)})."
                )
                policy_bullets_en.append(
                    f"The requested amount falls within the indicative range from policy documents ({_money_str(Decimal(str(mn)), policy_cur)} – {_money_str(Decimal(str(mx)), policy_cur)})."
                )
        except (TypeError, ValueError):
            pass

    if profile_ok and income_floor is not None:
        try:
            ai = int(Decimal(str(income)).quantize(Decimal("1")))
            if ai < income_floor:
                policy_bullets_fr.append(
                    f"D’après les extraits de politique indexés, un revenu annuel d’au moins environ {_money_str(Decimal(income_floor), policy_cur)} "
                    f"est mentionné — le revenu déclaré ({_money_str(income, currency)}) est inférieur à ce repère."
                )
                policy_bullets_en.append(
                    f"Indexed policy excerpts mention an annual income of at least about {_money_str(Decimal(income_floor), policy_cur)} — "
                    f"declared income ({_money_str(income, currency)}) is below that benchmark."
                )
            else:
                policy_bullets_fr.append(
                    f"Par rapport aux extraits de politique (repère de revenu annuel d’environ {_money_str(Decimal(income_floor), policy_cur)}), "
                    f"le revenu déclaré ({_money_str(income, currency)}) atteint ou dépasse ce niveau."
                )
                policy_bullets_en.append(
                    f"Compared to policy excerpts (annual income benchmark around {_money_str(Decimal(income_floor), policy_cur)}), "
                    f"declared income ({_money_str(income, currency)}) meets or exceeds that level."
                )
        except Exception:
            pass

    rag_snippets = _rag_snippets_from_full_policy(combined_policy, n=4)

    detail: dict[str, Any] = {
        "decision": "validated" if validated else "rejected",
        "financial_inputs_complete": profile_ok,
        "threshold": threshold,
        "score": round(score_f, 2),
        "currency": currency,
        "financial_bullets_fr": financial_bullets_fr,
        "financial_bullets_en": financial_bullets_en,
        "policy_bullets_fr": policy_bullets_fr,
        "policy_bullets_en": policy_bullets_en,
        "summary_fr": summary_fr,
        "summary_en": summary_en,
        "rag_income_floor_detected": income_floor,
        "policy_guidance_min_amount": min_amt,
        "policy_guidance_max_amount": max_amt,
        "policy_currency": policy_cur,
        "rag_snippets": rag_snippets,
        "policy_full_text_chars": len(combined_policy),
        "policy_truncated": bool(policy_bundle.get("truncated")),
    }

    try:
        llm_out = invoke_full_context_eligibility_llm(
            application,
            policy_full_text=combined_policy,
            deterministic_detail=detail,
        )
        if llm_out:
            detail["llm"] = llm_out
    except Exception as exc:
        detail["llm"] = {"unavailable": True, "error": str(exc)}

    return detail
