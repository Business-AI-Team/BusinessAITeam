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

from core.currency_fx import convert_amount, format_money
from core.document_consistency import check_application_document_consistency
from core.llm_eligibility_analysis import invoke_full_context_eligibility_llm
from core.models import LoanApplication
from core.rag_eligibility import get_all_active_knowledge_text, get_eligibility_guidance_from_rag


def _payment_and_dti(application: LoanApplication) -> tuple[Decimal, Decimal, Decimal] | None:
    """Estimated monthly payment, monthly income, DTI ratio — ou None si données insuffisantes."""
    if not application.has_complete_financial_profile():
        return None
    income = application.effective_annual_income
    cust = getattr(application, "customer", None)
    inc_ccy = (getattr(cust, "income_currency", None) or "EUR") if cust else "EUR"
    amt_ccy = getattr(application, "amount_currency", None) or "EUR"
    amount = convert_amount(application.amount_requested, amt_ccy, inc_ccy)
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


def build_eligibility_detail(
    application: LoanApplication,
    language: str | None = None,
    *,
    consistency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full structured explanation: financial reasoning + policy (RAG) alignment.

    Stored under roi_summary['eligibility_detail'] after scoring.
    ``consistency`` : résultat de :func:`check_application_document_consistency` (évite un double calcul si déjà fait).
    """
    lang = (language or application.language or "fr").lower()
    if consistency is None:
        consistency = check_application_document_consistency(application)
    currency = getattr(settings, "LOANWISE_CURRENCY", "EUR")
    threshold = float(getattr(settings, "LOANWISE_APPROVAL_THRESHOLD", 55))
    score_f = float(application.eligibility_score) if application.eligibility_score is not None else 0.0
    issues_list = consistency.get("issues") or []
    has_address_mismatch = any(i.get("code") == "address_mismatch" for i in issues_list)
    validated = (score_f >= threshold) and not has_address_mismatch

    cust = getattr(application, "customer", None)
    income_ccy = getattr(cust, "income_currency", None) or currency if cust else currency
    amt_ccy = getattr(application, "amount_currency", None) or currency

    income = application.effective_annual_income
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
            f"Revenu annuel utilisé pour le dossier : {format_money(income, income_ccy)} ; montant demandé : {format_money(amount, amt_ccy)} ; durée : {months} mois.",
            "Le score reste à 0 si le montant est absent ou si aucun revenu n’est disponible (profil ou estimation depuis les bulletins après analyse).",
            "Indiquez le montant du prêt, téléversez les bulletins de salaire pour l’estimation du revenu, puis relancez le pipeline. "
            "Le score est une formule déterministe (capacité de remboursement), indépendante des appels OpenAI (analyse documents / RAG).",
            f"Score calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Annual income used for the application: {format_money(income, income_ccy)}; requested amount: {format_money(amount, amt_ccy)}; term: {months} months.",
            "The score stays at 0 when the loan amount is missing or no income is available (profile or payslip-based estimate after analysis).",
            "Set the loan amount, upload payslips so income can be estimated, then re-run the pipeline. The score is a deterministic debt-to-income formula, separate from OpenAI (document analysis / RAG).",
            f"Computed score: {score_f:.2f} / 100 (internal threshold: {threshold:.0f}).",
        ]
        summary_fr = (
            f"Données financières insuffisantes (score {score_f:.2f}). "
            "Indiquez un montant de prêt strictement positif et fournissez des bulletins pour estimer le revenu, ou un revenu profil si renseigné."
        )
        summary_en = (
            f"Insufficient financial data (score {score_f:.2f}). "
            "Enter a strictly positive loan amount and upload payslips for income estimation (or set profile income if used)."
        )
    else:
        payment, monthly_income, dti = pmt_dti  # type: ignore[assignment]
        dti_pct = float(dti * Decimal("100"))
        financial_bullets_fr = [
            f"Revenu annuel utilisé : {format_money(income, income_ccy)} (profil ou estimation bulletins).",
            f"Mensualité estimée en {income_ccy} (taux annuel {rate_annual}, {months} mois) : {format_money(payment, income_ccy)}.",
            f"Revenu mensuel ({income_ccy}) : {format_money(monthly_income, income_ccy)} — charge / revenu ≈ {dti_pct:.1f} %.",
            f"Score d'éligibilité calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Annual income used: {format_money(income, income_ccy)} (profile or payslip estimate).",
            f"Estimated monthly payment in {income_ccy} (annual rate {rate_annual}, {months} months): {format_money(payment, income_ccy)}.",
            f"Monthly income ({income_ccy}): {format_money(monthly_income, income_ccy)} — payment-to-income ≈ {dti_pct:.1f}%.",
            f"Computed eligibility score: {score_f:.2f} / 100 (internal threshold: {threshold:.0f}).",
        ]
        if has_address_mismatch:
            financial_bullets_fr.append(
                "Incohérence d’adresse : l’adresse déclarée sur le profil ne correspond pas de façon suffisante à celle lisible sur les pièces "
                "(justificatif de domicile, bulletin, etc.). Même si les indicateurs financiers sont favorables, le dossier n’est pas recevable tant que l’adresse n’est pas alignée."
            )
            financial_bullets_en.append(
                "Address mismatch: the address on your profile does not match closely enough what is readable on your documents "
                "(proof of address, payslip, etc.). Even if financial ratios look good, the application cannot be accepted until addresses are consistent."
            )
        if has_address_mismatch:
            summary_fr = (
                f"Décision indicative : non éligible — incohérence entre l’adresse du profil et les pièces "
                f"(score numérique {score_f:.2f}, seuil {threshold:.0f}). "
                "Mettez à jour votre adresse dans le profil ou fournissez un justificatif de domicile et une pièce d’identité cohérents avec cette adresse."
            )
            summary_en = (
                f"Indicative decision: not eligible — profile address does not match uploaded documents "
                f"(numeric score {score_f:.2f}, threshold {threshold:.0f}). "
                "Update your profile address or upload proof of address and ID documents that match the declared address."
            )
        elif validated:
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
                f"La mensualité estimée pèse fortement sur le revenu mensuel utilisé (ratio élevé), sauf autres éléments positifs."
            )
            summary_en = (
                f"Indicative decision: not eligible (score {score_f:.2f} < threshold {threshold:.0f}). "
                f"The estimated monthly burden is high relative to monthly income used for the application unless other factors apply."
            )

    policy_bullets_fr: list[str] = []
    policy_bullets_en: list[str] = []

    if profile_ok and guidance.get("available") and min_amt is not None and max_amt is not None:
        try:
            a = float(amount)
            mn, mx = float(min_amt), float(max_amt)
            if a < mn:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un montant minimum d’emprunt d’environ {format_money(Decimal(str(mn)), policy_cur)} — "
                    f"votre demande ({format_money(application.amount_requested, amt_ccy)}) est en dessous."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a minimum loan amount around {format_money(Decimal(str(mn)), policy_cur)} — "
                    f"your request ({format_money(application.amount_requested, amt_ccy)}) is below that range."
                )
            elif a > mx:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un plafond d’environ {format_money(Decimal(str(mx)), policy_cur)} — "
                    f"votre demande ({format_money(application.amount_requested, amt_ccy)}) le dépasse."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a maximum around {format_money(Decimal(str(mx)), policy_cur)} — "
                    f"your request ({format_money(application.amount_requested, amt_ccy)}) exceeds it."
                )
            else:
                policy_bullets_fr.append(
                    f"Le montant demandé se situe dans la fourchette indicative issue des documents ({format_money(Decimal(str(mn)), policy_cur)} – {format_money(Decimal(str(mx)), policy_cur)})."
                )
                policy_bullets_en.append(
                    f"The requested amount falls within the indicative range from policy documents ({format_money(Decimal(str(mn)), policy_cur)} – {format_money(Decimal(str(mx)), policy_cur)})."
                )
        except (TypeError, ValueError):
            pass

    if profile_ok and income_floor is not None:
        try:
            ai = int(Decimal(str(income)).quantize(Decimal("1")))
            if ai < income_floor:
                policy_bullets_fr.append(
                    f"D’après les extraits de politique indexés, un revenu annuel d’au moins environ {format_money(Decimal(income_floor), policy_cur)} "
                    f"est mentionné — le revenu utilisé pour le dossier ({format_money(income, income_ccy)}) est inférieur à ce repère."
                )
                policy_bullets_en.append(
                    f"Indexed policy excerpts mention an annual income of at least about {format_money(Decimal(income_floor), policy_cur)} — "
                    f"income used for the application ({format_money(income, income_ccy)}) is below that benchmark."
                )
            else:
                policy_bullets_fr.append(
                    f"Par rapport aux extraits de politique (repère de revenu annuel d’environ {format_money(Decimal(income_floor), policy_cur)}), "
                    f"le revenu utilisé pour le dossier ({format_money(income, income_ccy)}) atteint ou dépasse ce niveau."
                )
                policy_bullets_en.append(
                    f"Compared to policy excerpts (annual income benchmark around {format_money(Decimal(income_floor), policy_cur)}), "
                    f"income used for the application ({format_money(income, income_ccy)}) meets or exceeds that level."
                )
        except Exception:
            pass

    rag_snippets = _rag_snippets_from_full_policy(combined_policy, n=4)

    user_advice_fr = ""
    user_advice_en = ""
    if has_address_mismatch:
        src = (consistency or {}).get("address_mismatch_sources") or {}
        poa = bool(src.get("proof_of_address"))
        oth = bool(src.get("other_document"))
        # Regle : CIN / passeport non verifies contre l'adresse du profil.
        # Seul le justificatif de domicile doit correspondre a l'adresse declaree.
        if poa and not oth:
            user_advice_fr = (
                "Mettez a jour l'adresse de votre profil pour qu'elle corresponde a celle de votre "
                "justificatif de domicile (facture, attestation d'hebergement, quittance de loyer...), "
                "ou fournissez un justificatif recent a votre nom qui reprend l'adresse declaree. "
                "L'adresse sur votre CIN ou passeport n'est pas concernee par cette verification."
            )
            user_advice_en = (
                "Update your profile address to match your proof of address (utility bill, hosting "
                "certificate, rent receipt...), or upload a recent document in your name showing the "
                "address you declared. The address on your CIN or passport is not subject to this check."
            )
        elif oth and not poa:
            user_advice_fr = (
                "L'ecart provient probablement d'une adresse professionnelle (employeur) visible sur "
                "un bulletin de paie. Telechargez un justificatif de domicile recent a votre nom dont "
                "l'adresse correspond a celle de votre profil. "
                "L'adresse sur votre CIN ou passeport n'est pas verifiee ici."
            )
            user_advice_en = (
                "The mismatch likely comes from an employer address visible on a payslip. "
                "Upload a recent proof of address in your name matching the address declared in your profile. "
                "The address on your CIN or passport is not checked here."
            )
        else:
            user_advice_fr = (
                "L'adresse de votre profil ne correspond pas a celle lisible sur le justificatif de "
                "domicile ou une autre piece. Corrigez le profil ou fournissez un justificatif de "
                "domicile a votre nom avec l'adresse correcte. "
                "Rappel : l'adresse sur la CIN ou le passeport n'est pas prise en compte ici."
            )
            user_advice_en = (
                "Your profile address does not match the address on your proof of address or another "
                "document. Correct your profile or provide a proof of address in your name with the "
                "right address. Reminder: the address on a CIN or passport is not used in this check."
            )

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
        "document_consistency": consistency,
        "address_blocker": has_address_mismatch,
        "user_advice_fr": user_advice_fr,
        "user_advice_en": user_advice_en,
        "identity_address_block": has_address_mismatch,
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
