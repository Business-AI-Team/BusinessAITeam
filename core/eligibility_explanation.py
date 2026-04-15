"""
Structured eligibility explanations for back-office and API.

Combines deterministic scoring (income, amount, term) with RAG policy excerpts
(admin PDFs) so refus/validation can be justified with numbers and document hints.

Public API
----------
build_eligibility_detail(application, language, *, consistency) -> dict
    Orchestrates three focused helpers and the LLM final pass:
      _analyse_finances      — financial bullets + decision summary
      _align_with_policy     — comparison against RAG policy constraints
      _assess_document_issues — LLM-generated client advice for doc problems
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from django.conf import settings

from core.currency_fx import convert_amount, format_money
from core.document_consistency import check_application_document_consistency
from core.lang_utils import resolve_language
from core.llm_eligibility_analysis import invoke_full_context_eligibility_llm
from core.models import LoanApplication
from core.rag_eligibility import get_all_active_knowledge_text, get_eligibility_guidance_from_rag


# ── Monetary formatting helper ─────────────────────────────────────────────────

def _fmt_rag(amount_val: Any, rag_ccy: str, display_ccy: str) -> str:
    """
    Format a RAG-sourced monetary value.

    Primary display is in ``display_ccy`` (the user's loan currency).
    When ``rag_ccy`` differs, the original RAG amount is appended in parentheses.

    Example: _fmt_rag(4800, "MGA", "EUR") -> "1.02 EUR (4 800.00 MGA)"
    """
    try:
        d = Decimal(str(amount_val))
        if rag_ccy.upper() == display_ccy.upper():
            return format_money(d, display_ccy)
        converted = convert_amount(d, rag_ccy, display_ccy)
        return f"{format_money(converted, display_ccy)} ({format_money(d, rag_ccy)})"
    except Exception:
        return str(amount_val)


# ── Internal computation helpers ───────────────────────────────────────────────

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
    """Extraits courts pour l'UI back-office à partir du corpus politique complet."""
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


# ── LLM user advice ────────────────────────────────────────────────────────────

_ISSUE_LABELS_FR: dict[str, str] = {
    "address_mismatch": "Adresse incohérente : l'adresse de votre profil ne correspond pas à celle de votre justificatif de domicile. Corrigez votre profil ou fournissez un justificatif récent à votre nom.",
    "income_mismatch": "Revenu incohérent : le montant extrait de vos bulletins de salaire diffère significativement des données de votre profil. Vérifiez que vos bulletins vous appartiennent bien.",
    "payslip_months_outside_window": "Bulletins trop anciens : un ou plusieurs bulletins de salaire sont hors de la fenêtre des 3 derniers mois calendaires autorisés. Téléversez des bulletins récents.",
    "payslip_months_not_distinct": "Bulletins en doublon : plusieurs bulletins couvrent le même mois. Chaque mois doit être représenté par un seul bulletin distinct.",
    "payslip_dates_incomplete": "Dates illisibles : la période de certains bulletins est illisible ou absente. Fournissez des bulletins dont la date de période est clairement visible.",
    "identity_name_mismatch": "Identité non concordante : le nom sur votre pièce d'identité (CIN/passeport) ne correspond pas au nom déclaré dans votre profil. Vérifiez que la pièce vous appartient bien.",
    "payslip_name_mismatch": "Titulaire du bulletin incorrect : le nom du salarié sur votre bulletin de salaire ne correspond pas à votre nom de profil. Le bulletin doit être au nom du demandeur.",
}

_ISSUE_LABELS_EN: dict[str, str] = {
    "address_mismatch": "Address mismatch: your profile address does not match your proof of address document. Update your profile or provide a recent document in your name.",
    "income_mismatch": "Income mismatch: the amount extracted from your payslips differs significantly from your profile data. Make sure your payslips belong to you.",
    "payslip_months_outside_window": "Payslips too old: one or more payslips fall outside the last 3 authorized calendar months. Please upload recent payslips.",
    "payslip_months_not_distinct": "Duplicate payslip months: multiple payslips cover the same calendar month. Each month must be represented by a single distinct payslip.",
    "payslip_dates_incomplete": "Unreadable dates: the period on some payslips is missing or unreadable. Provide payslips where the pay period is clearly visible.",
    "identity_name_mismatch": "Identity mismatch: the name on your ID document (CIN/passport) does not match your declared profile name. Make sure the document belongs to you.",
    "payslip_name_mismatch": "Wrong payslip holder: the employee name on your payslip does not match your profile name. Payslips must be in the applicant's name.",
}


def _normalize_advice_html(value: Any) -> str:
    """
    Convertit la valeur retournée par le LLM en HTML propre quel que soit le format :
    - str HTML (déjà formaté) → retourné tel quel
    - list[str]               → <ul><li>…</li></ul>
    - str texte brut          → <ul> avec un <li> par ligne/point/numéro
    """
    import html as _html
    if not value:
        return ""
    if isinstance(value, list):
        items = "".join(
            f"<li>{_html.escape(str(item).strip())}</li>"
            for item in value
            if str(item).strip()
        )
        return f'<ul class="list-disc space-y-1.5 pl-5">{items}</ul>'
    if isinstance(value, str):
        # Already contains HTML tags → trust it
        if "<li" in value or "<ul" in value or "<p" in value:
            return value
        # Plain text: split on newlines or numbered items
        lines = [ln.strip() for ln in value.strip().splitlines() if ln.strip()]
        if len(lines) <= 1:
            return f"<p>{_html.escape(value)}</p>"
        items = "".join(
            f"<li>{_html.escape(ln.lstrip('•-– ').lstrip('0123456789.) '))}</li>"
            for ln in lines
        )
        return f'<ul class="list-disc space-y-1.5 pl-5">{items}</ul>'
    return str(value)


def _generate_user_advice(
    issues_list: list[dict],
    consistency: dict,
    application: Any,
    combined_policy: str | None = None,
) -> tuple[str, str, str, str]:
    """
    Call OpenAI to generate a personalised client-facing action message based on
    ALL detected document inconsistencies (address, name, income, etc.).

    ``combined_policy`` : corpus complet des règles institutionnelles (si disponible) injecté
    dans le prompt pour que le LLM puisse contextualiser ses recommandations par rapport à la
    politique réelle de l'établissement.

    Returns (advice_fr, advice_en, title_fr, title_en).
    Falls back to a safe generic message if the LLM is unavailable.
    """
    import json as _json

    from core.openai_config import get_openai_api_key

    # ── Build a plain-text summary of every detected issue ──────────────────
    lines: list[str] = []
    for issue in issues_list:
        code = issue.get("code") or "?"
        detail = issue.get("detail_fr") or issue.get("detail_en") or ""
        lines.append(f"- [{code}] {detail}")
    issues_text = "\n".join(lines) if lines else "- (aucune précision disponible)"

    cust = getattr(application, "customer", None)
    customer_name = (
        f"{getattr(cust, 'first_name', '')} {getattr(cust, 'last_name', '')}".strip()
        if cust
        else "le client"
    )

    # ── Application financial context ────────────────────────────────────────
    amt = getattr(application, "amount_requested", None)
    amt_ccy = getattr(application, "amount_currency", None) or "EUR"
    months = getattr(application, "term_months", None)
    income = getattr(application, "effective_annual_income", None)
    income_ccy = (getattr(cust, "income_currency", None) or "EUR") if cust else "EUR"
    application_block = (
        f"\nDonnées de la demande :\n"
        f"- Montant demandé : {amt} {amt_ccy}\n"
        f"- Durée : {months} mois\n"
        f"- Revenu annuel (extrait des documents) : {income} {income_ccy}\n"
    )

    # ── Generic fallbacks (used when LLM is unavailable) ────────────────────
    def _fallback() -> tuple[str, str, str, str]:
        n = len(issues_list)
        title_fr = f"{n} point{'s' if n > 1 else ''} à corriger dans votre dossier" if n > 1 else "Action requise sur votre dossier"
        title_en = f"{n} issue{'s' if n > 1 else ''} to fix in your application" if n > 1 else "Action required on your application"
        items_fr: list[str] = []
        items_en: list[str] = []
        for issue in issues_list:
            code = issue.get("code") or ""
            d_fr = issue.get("detail_fr") or _ISSUE_LABELS_FR.get(code, f"Problème détecté : {code}.")
            d_en = issue.get("detail_en") or _ISSUE_LABELS_EN.get(code, f"Issue detected: {code}.")
            items_fr.append(f"<li>{d_fr}</li>")
            items_en.append(f"<li>{d_en}</li>")
        fr = '<ul class="list-disc space-y-1.5 pl-5">' + "".join(items_fr) + "</ul>"
        en = '<ul class="list-disc space-y-1.5 pl-5">' + "".join(items_en) + "</ul>"
        return fr, en, title_fr, title_en

    api_key = get_openai_api_key()
    if not api_key:
        return _fallback()

    policy_block = ""
    if combined_policy and combined_policy.strip():
        policy_snippet = combined_policy.strip()[:8000]
        policy_block = f"""
Politique institutionnelle (extrait) :
{policy_snippet}
"""

    prompt = f"""Tu es un assistant bancaire bienveillant. Le client « {customer_name} » a soumis une demande de prêt.
{policy_block}
{application_block}
L'analyse automatique a détecté les incohérences suivantes dans ses documents :
{issues_text}

Règle adresse : L'adresse figurant sur une CIN ou un passeport n'est JAMAIS comparée à l'adresse du profil. Seul un justificatif de domicile dédié (facture, relevé bancaire, quittance) est vérifié.
Règle revenu : Le revenu n'est jamais saisi par le client — il est extrait automatiquement des bulletins de salaire.

Ta mission : générer un message destiné au CLIENT qui liste EXPLICITEMENT ET EXHAUSTIVEMENT TOUS les problèmes détectés ci-dessus, sans en omettre un seul.
Pour CHAQUE problème détecté, tu dois :
1. Nommer le problème clairement (ex. bulletin hors fenêtre de 3 mois, doublon de mois, nom différent sur le bulletin, adresse incohérente, etc.)
2. Expliquer concrètement ce que le client doit faire pour le corriger

Si la politique institutionnelle est disponible, vérifie également si le montant demandé, la durée ou le revenu sont conformes aux critères (montant min/max, durée max, revenu minimum requis) et signale tout écart dans le message.

IMPORTANT : Ne résume PAS en une phrase générale. Une entrée par problème.
Si plusieurs problèmes sont détectés, tous doivent apparaître dans le message — même les moins graves.
Respecte les exigences de la politique institutionnelle si disponible.

Génère aussi un titre court (5-8 mots max) qui résume l'action requise (ex. "3 points à corriger dans votre dossier").

Réponds UNIQUEMENT en JSON valide avec les clés : "title_fr", "title_en", "advice_fr", "advice_en".
"advice_fr" et "advice_en" doivent être des chaînes HTML contenant une liste <ul><li>...</li></ul> avec un <li> par problème.
Chaque <li> doit commencer par un titre court en <strong> suivi de la description. Pas de markdown, pas de texte hors du JSON."""

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE).strip()
        data = _json.loads(text)
        return (
            _normalize_advice_html(data.get("advice_fr")) or _fallback()[0],
            _normalize_advice_html(data.get("advice_en")) or _fallback()[1],
            data.get("title_fr") or _fallback()[2],
            data.get("title_en") or _fallback()[3],
        )
    except Exception:
        return _fallback()


# ── Focused sub-builders called by build_eligibility_detail ───────────────────

def _analyse_finances(
    application: LoanApplication,
    score_f: float,
    threshold: float,
    validated: bool,
    has_address_mismatch: bool,
) -> dict[str, Any]:
    """
    Compute financial bullets and the decision summary.

    Handles two scenarios transparently:
      • incomplete profile  — short bullets asking the user to fill in missing data
      • complete profile    — full DTI breakdown with converted amounts
    Returns: profile_ok, financial_bullets_fr/en, summary_fr/en.
    """
    currency = getattr(settings, "LOANWISE_CURRENCY", "EUR")
    cust = getattr(application, "customer", None)
    income_ccy = (getattr(cust, "income_currency", None) or currency) if cust else currency
    amt_ccy = getattr(application, "amount_currency", None) or currency
    income = application.effective_annual_income
    amount = application.amount_requested if application.amount_requested is not None else Decimal("0")
    months = max(1, application.term_months or 12)
    rate_annual = getattr(settings, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)
    pmt_dti = _payment_and_dti(application)
    profile_ok = application.has_complete_financial_profile() and pmt_dti is not None

    if not profile_ok:
        try:
            income_disp = convert_amount(income, income_ccy, amt_ccy) if income_ccy != amt_ccy else income
        except Exception:
            income_disp = income
        financial_bullets_fr = [
            f"Revenu annuel utilisé pour le dossier : {format_money(income_disp, amt_ccy)} ; montant demandé : {format_money(amount, amt_ccy)} ; durée : {months} mois.",
            "Le score reste à 0 si le montant est absent ou si aucun revenu n'est disponible (profil ou estimation depuis les bulletins après analyse).",
            "Indiquez le montant du prêt, téléversez les bulletins de salaire pour l'estimation du revenu, puis relancez le pipeline. "
            "Le score est une formule déterministe (capacité de remboursement), indépendante des appels OpenAI (analyse documents / RAG).",
            f"Score calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Annual income used for the application: {format_money(income_disp, amt_ccy)}; requested amount: {format_money(amount, amt_ccy)}; term: {months} months.",
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
        payment, monthly_income, dti = pmt_dti  # type: ignore[misc]
        dti_pct = float(dti * Decimal("100"))

        # Display all amounts in the loan's currency; DTI is unitless.
        disp_ccy = amt_ccy
        if income_ccy != amt_ccy:
            try:
                income_disp = convert_amount(income, income_ccy, amt_ccy)
                payment_disp = convert_amount(payment, income_ccy, amt_ccy)
                monthly_income_disp = convert_amount(monthly_income, income_ccy, amt_ccy)
            except Exception:
                income_disp, payment_disp, monthly_income_disp = income, payment, monthly_income
                disp_ccy = income_ccy
        else:
            income_disp, payment_disp, monthly_income_disp = income, payment, monthly_income

        financial_bullets_fr = [
            f"Revenu annuel utilisé : {format_money(income_disp, disp_ccy)} (profil ou estimation bulletins).",
            f"Mensualité estimée en {disp_ccy} (taux annuel {rate_annual}, {months} mois) : {format_money(payment_disp, disp_ccy)}.",
            f"Revenu mensuel ({disp_ccy}) : {format_money(monthly_income_disp, disp_ccy)} — charge / revenu ≈ {dti_pct:.1f} %.",
            f"Score d'éligibilité calculé : {score_f:.2f} / 100 (seuil interne : {threshold:.0f}).",
        ]
        financial_bullets_en = [
            f"Annual income used: {format_money(income_disp, disp_ccy)} (profile or payslip estimate).",
            f"Estimated monthly payment in {disp_ccy} (annual rate {rate_annual}, {months} months): {format_money(payment_disp, disp_ccy)}.",
            f"Monthly income ({disp_ccy}): {format_money(monthly_income_disp, disp_ccy)} — payment-to-income ≈ {dti_pct:.1f}%.",
            f"Computed eligibility score: {score_f:.2f} / 100 (internal threshold: {threshold:.0f}).",
        ]

        if has_address_mismatch:
            financial_bullets_fr.append(
                "Incohérence d'adresse : l'adresse déclarée sur le profil ne correspond pas de façon suffisante à celle lisible sur les pièces "
                "(justificatif de domicile, bulletin, etc.). Même si les indicateurs financiers sont favorables, le dossier n'est pas recevable tant que l'adresse n'est pas alignée."
            )
            financial_bullets_en.append(
                "Address mismatch: the address on your profile does not match closely enough what is readable on your documents "
                "(proof of address, payslip, etc.). Even if financial ratios look good, the application cannot be accepted until addresses are consistent."
            )

        if has_address_mismatch:
            summary_fr = (
                f"Décision indicative : non éligible — incohérence entre l'adresse du profil et les pièces "
                f"(score numérique {score_f:.2f}, seuil {threshold:.0f}). "
                "Mettez à jour votre adresse dans le profil ou fournissez un justificatif de domicile et une pièce d'identité cohérents avec cette adresse."
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

    return {
        "profile_ok": profile_ok,
        "financial_bullets_fr": financial_bullets_fr,
        "financial_bullets_en": financial_bullets_en,
        "summary_fr": summary_fr,
        "summary_en": summary_en,
    }


def _align_with_policy(
    application: LoanApplication,
    guidance: dict,
    income_floor: int | None,
    *,
    profile_ok: bool,
) -> dict[str, Any]:
    """
    Compare the application's amount and income against RAG-derived policy constraints.

    Only runs when the financial profile is complete and policy guidance is available.
    Returns: policy_bullets_fr/en.
    """
    policy_bullets_fr: list[str] = []
    policy_bullets_en: list[str] = []

    if not profile_ok or not guidance.get("available"):
        return {"policy_bullets_fr": policy_bullets_fr, "policy_bullets_en": policy_bullets_en}

    currency = getattr(settings, "LOANWISE_CURRENCY", "EUR")
    cust = getattr(application, "customer", None)
    income_ccy = (getattr(cust, "income_currency", None) or currency) if cust else currency
    amt_ccy = getattr(application, "amount_currency", None) or currency
    policy_cur = guidance.get("currency") or currency
    income = application.effective_annual_income
    amount = application.amount_requested if application.amount_requested is not None else Decimal("0")
    min_amt = guidance.get("min_amount")
    max_amt = guidance.get("max_amount")

    if min_amt is not None and max_amt is not None:
        try:
            a = float(amount)
            mn, mx = float(min_amt), float(max_amt)
            req_fmt = format_money(application.amount_requested, amt_ccy)
            if a < mn:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un montant minimum d'emprunt d'environ "
                    f"{_fmt_rag(mn, policy_cur, amt_ccy)} — votre demande ({req_fmt}) est en dessous."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a minimum loan amount around "
                    f"{_fmt_rag(mn, policy_cur, amt_ccy)} — your request ({req_fmt}) is below that range."
                )
            elif a > mx:
                policy_bullets_fr.append(
                    f"Les documents de politique indiquent un plafond d'environ "
                    f"{_fmt_rag(mx, policy_cur, amt_ccy)} — votre demande ({req_fmt}) le dépasse."
                )
                policy_bullets_en.append(
                    f"Policy documents suggest a maximum around "
                    f"{_fmt_rag(mx, policy_cur, amt_ccy)} — your request ({req_fmt}) exceeds it."
                )
            else:
                policy_bullets_fr.append(
                    f"Le montant demandé se situe dans la fourchette indicative issue des documents "
                    f"({_fmt_rag(mn, policy_cur, amt_ccy)} – {_fmt_rag(mx, policy_cur, amt_ccy)})."
                )
                policy_bullets_en.append(
                    f"The requested amount falls within the indicative range from policy documents "
                    f"({_fmt_rag(mn, policy_cur, amt_ccy)} – {_fmt_rag(mx, policy_cur, amt_ccy)})."
                )
        except (TypeError, ValueError):
            pass

    if income_floor is not None:
        try:
            income_in_policy_ccy = (
                convert_amount(income, income_ccy, policy_cur)
                if income_ccy != policy_cur else income
            )
            income_disp = (
                convert_amount(income, income_ccy, amt_ccy)
                if income_ccy != amt_ccy else income
            )
            income_disp_fmt = format_money(income_disp, amt_ccy)
            ai = float(income_in_policy_ccy)
            if ai < income_floor:
                policy_bullets_fr.append(
                    f"D'après les extraits de politique indexés, un revenu annuel d'au moins environ "
                    f"{_fmt_rag(income_floor, policy_cur, amt_ccy)} est mentionné — "
                    f"le revenu utilisé pour le dossier ({income_disp_fmt}) est inférieur à ce repère."
                )
                policy_bullets_en.append(
                    f"Indexed policy excerpts mention an annual income of at least about "
                    f"{_fmt_rag(income_floor, policy_cur, amt_ccy)} — "
                    f"income used for the application ({income_disp_fmt}) is below that benchmark."
                )
            else:
                policy_bullets_fr.append(
                    f"Par rapport aux extraits de politique (repère de revenu annuel d'environ "
                    f"{_fmt_rag(income_floor, policy_cur, amt_ccy)}), "
                    f"le revenu utilisé ({income_disp_fmt}) atteint ou dépasse ce niveau."
                )
                policy_bullets_en.append(
                    f"Compared to policy excerpts (annual income benchmark around "
                    f"{_fmt_rag(income_floor, policy_cur, amt_ccy)}), "
                    f"income used ({income_disp_fmt}) meets or exceeds that level."
                )
        except Exception:
            pass

    return {"policy_bullets_fr": policy_bullets_fr, "policy_bullets_en": policy_bullets_en}


def _assess_document_issues(
    issues_list: list[dict],
    consistency: dict,
    application: LoanApplication,
    combined_policy: str,
) -> dict[str, Any]:
    """
    Generate LLM-powered client-facing advice for ALL document inconsistencies.

    Returns: has_doc_issues, user_advice_fr/en, user_advice_title_fr/en.
    """
    has_doc_issues = bool(issues_list)
    if has_doc_issues:
        advice_fr, advice_en, title_fr, title_en = _generate_user_advice(
            issues_list, consistency, application, combined_policy=combined_policy
        )
    else:
        advice_fr = advice_en = title_fr = title_en = ""

    return {
        "has_doc_issues": has_doc_issues,
        "user_advice_fr": advice_fr,
        "user_advice_en": advice_en,
        "user_advice_title_fr": title_fr,
        "user_advice_title_en": title_en,
    }


# ── Public entry point ─────────────────────────────────────────────────────────

def build_eligibility_detail(
    application: LoanApplication,
    language: str | None = None,
    *,
    consistency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full structured explanation: financial reasoning + policy alignment + document advice.

    Stored under roi_summary['eligibility_detail'] after scoring.

    Parameters
    ----------
    application:
        The loan application to evaluate.
    language:
        Explicit language override (e.g. "fr", "en").  When omitted the language
        is resolved from the application record via resolve_language().
    consistency:
        Pre-computed result of check_application_document_consistency().
        Pass it when already available to avoid a redundant computation.
    """
    lang = language.lower() if language else resolve_language(application=application)
    if consistency is None:
        consistency = check_application_document_consistency(application)

    currency = getattr(settings, "LOANWISE_CURRENCY", "EUR")
    threshold = float(getattr(settings, "LOANWISE_APPROVAL_THRESHOLD", 55))
    score_f = float(application.eligibility_score) if application.eligibility_score is not None else 0.0
    issues_list = consistency.get("issues") or []
    has_address_mismatch = any(i.get("code") == "address_mismatch" for i in issues_list)
    validated = (score_f >= threshold) and not has_address_mismatch

    # Load policy corpus once; pass it down to sub-builders to avoid redundant I/O.
    policy_bundle = get_all_active_knowledge_text()
    combined_policy = (policy_bundle.get("text") or "").strip()
    guidance = get_eligibility_guidance_from_rag(
        application.loan_type or "personal",
        lang,
        preloaded_policy_bundle=policy_bundle,
    )
    income_floor = _extract_income_floor_from_text(combined_policy)

    fin = _analyse_finances(application, score_f, threshold, validated, has_address_mismatch)
    pol = _align_with_policy(application, guidance, income_floor, profile_ok=fin["profile_ok"])
    doc = _assess_document_issues(issues_list, consistency, application, combined_policy)

    detail: dict[str, Any] = {
        "decision": "validated" if validated else "rejected",
        "financial_inputs_complete": fin["profile_ok"],
        "threshold": threshold,
        "score": round(score_f, 2),
        "currency": currency,
        "financial_bullets_fr": fin["financial_bullets_fr"],
        "financial_bullets_en": fin["financial_bullets_en"],
        "policy_bullets_fr": pol["policy_bullets_fr"],
        "policy_bullets_en": pol["policy_bullets_en"],
        "summary_fr": fin["summary_fr"],
        "summary_en": fin["summary_en"],
        "rag_income_floor_detected": income_floor,
        "policy_guidance_min_amount": guidance.get("min_amount"),
        "policy_guidance_max_amount": guidance.get("max_amount"),
        "policy_currency": guidance.get("currency") or currency,
        "rag_snippets": _rag_snippets_from_full_policy(combined_policy, n=4),
        "policy_full_text_chars": len(combined_policy),
        "policy_truncated": bool(policy_bundle.get("truncated")),
        "document_consistency": consistency,
        "address_blocker": has_address_mismatch,
        **doc,
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
