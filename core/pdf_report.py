"""
PDF report – clean, professional layout for loan eligibility results.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

from django.utils import translation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.currency_fx import convert_amount, format_money
from core.models import LoanApplication


def _fmt_rag(amount_val: Any, rag_ccy: str, display_ccy: str) -> str:
    """
    Format a RAG-sourced monetary value for PDF display.

    Primary value is always in ``display_ccy`` (the user's loan currency).
    When ``rag_ccy`` differs, the original RAG amount is appended in parentheses.

    Example: 4 800 Ar  →  _fmt_rag(4800, "MGA", "EUR")  →  "1.02 EUR (4 800.00 MGA)"
    """
    from decimal import Decimal
    try:
        d = Decimal(str(amount_val))
        if rag_ccy.upper() == display_ccy.upper():
            return format_money(d, display_ccy)
        converted = convert_amount(d, rag_ccy, display_ccy)
        return f"{format_money(converted, display_ccy)} ({format_money(d, rag_ccy)})"
    except Exception:
        return str(amount_val)

# ── Brand colours ──────────────────────────────────────────────────────────
C_PRIMARY   = colors.HexColor("#0284c7")   # sky-600
C_DARK      = colors.HexColor("#0f172a")   # slate-900
C_SLATE     = colors.HexColor("#1e293b")   # slate-800
C_MUTED     = colors.HexColor("#64748b")   # slate-500
C_BG_ALT    = colors.HexColor("#f1f5f9")   # slate-100
C_WHITE     = colors.white
C_GREEN     = colors.HexColor("#059669")   # emerald-600
C_RED       = colors.HexColor("#dc2626")   # red-600
C_BORDER    = colors.HexColor("#e2e8f0")   # slate-200


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "LW_Title", parent=base["Normal"],
            fontSize=22, fontName="Helvetica-Bold",
            textColor=C_DARK, spaceAfter=4, leading=26,
        ),
        "subtitle": ParagraphStyle(
            "LW_Sub", parent=base["Normal"],
            fontSize=10, fontName="Helvetica",
            textColor=C_MUTED, spaceAfter=2,
        ),
        "section": ParagraphStyle(
            "LW_Section", parent=base["Normal"],
            fontSize=9, fontName="Helvetica-Bold",
            textColor=C_PRIMARY, spaceBefore=14, spaceAfter=4,
            textTransform="uppercase", letterSpacing=0.8,
        ),
        "body": ParagraphStyle(
            "LW_Body", parent=base["Normal"],
            fontSize=10, fontName="Helvetica",
            textColor=C_SLATE, leading=14,
        ),
        "small": ParagraphStyle(
            "LW_Small", parent=base["Normal"],
            fontSize=8, fontName="Helvetica",
            textColor=C_MUTED, leading=12,
        ),
        "verdict_ok": ParagraphStyle(
            "LW_VerdictOk", parent=base["Normal"],
            fontSize=14, fontName="Helvetica-Bold",
            textColor=C_GREEN,
        ),
        "verdict_ko": ParagraphStyle(
            "LW_VerdictKo", parent=base["Normal"],
            fontSize=14, fontName="Helvetica-Bold",
            textColor=C_RED,
        ),
    }


def _table(rows: list, col_widths: list, header: bool = True) -> Table:
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME",  (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",  (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), C_SLATE),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [C_WHITE, C_BG_ALT]),
        ("TOPPADDING",  (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), C_SLATE),
            ("TEXTCOLOR",  (0, 0), (-1, 0), C_WHITE),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, 0), 9),
        ]
    t.setStyle(TableStyle(style))
    return t


def _fmt_amount(amount, currency: str) -> str:
    if amount is None:
        return "—"
    try:
        return f"{float(amount):,.2f} {currency or 'EUR'}"
    except Exception:
        return str(amount)


def _fmt_score(score) -> str:
    if score is None:
        return "—"
    try:
        return f"{float(score):.1f} / 100"
    except Exception:
        return str(score)


def _bullet_para(text: str, style) -> Paragraph:
    safe = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(f"• {safe}", style)


def build_application_pdf(application: LoanApplication, lang: str | None = None) -> bytes:
    lang = (lang or application.language or "fr").lower()
    is_fr = lang.startswith("fr")
    previous = translation.get_language()
    translation.activate(lang)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2.5 * cm,
    )

    S = _styles()
    W = A4[0] - 4 * cm   # usable width
    story: list = []

    # ── Header bar ─────────────────────────────────────────────────────────
    header_label = "Rapport d'éligibilité" if is_fr else "Eligibility Report"
    header_tbl = Table(
        [[Paragraph(f"<b>{header_label}</b>", ParagraphStyle(
            "h", fontSize=14, fontName="Helvetica-Bold",
            textColor=C_WHITE, leading=18,
        ))]],
        colWidths=[W],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 0.4 * cm))

    # ── Reference / date ───────────────────────────────────────────────────
    ref_label  = "Référence" if is_fr else "Reference"
    date_label = "Date" if is_fr else "Date"
    story.append(Paragraph(
        f"<b>{ref_label}:</b> {application.reference}&nbsp;&nbsp;&nbsp;"
        f"<b>{date_label}:</b> {date.today().strftime('%d/%m/%Y')}",
        S["body"],
    ))
    story.append(Spacer(1, 0.4 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 0.3 * cm))

    # ── Extract eligibility_detail stored in roi_summary ───────────────────
    roi: dict[str, Any] = application.roi_summary or {}
    detail: dict[str, Any] = roi.get("eligibility_detail") or {}

    score = application.eligibility_score
    threshold = float(detail.get("threshold") or 55.0)
    eligible = score is not None and float(score) >= threshold
    address_blocker = bool(detail.get("address_blocker") or detail.get("identity_address_block"))
    if address_blocker:
        eligible = False

    # ── Eligibility verdict ────────────────────────────────────────────────
    verdict_style = S["verdict_ok"] if eligible else S["verdict_ko"]
    if score is not None:
        verdict_text = ("✓ Éligible" if eligible else "✗ Non éligible") if is_fr else ("✓ Eligible" if eligible else "✗ Not eligible")
        story.append(Paragraph(verdict_text, verdict_style))
        story.append(Spacer(1, 0.15 * cm))

    # Summary sentence (reason for rejection or validation)
    summary = detail.get("summary_fr") if is_fr else detail.get("summary_en")
    if summary:
        story.append(Paragraph(str(summary), S["body"]))
        story.append(Spacer(1, 0.3 * cm))

    story.append(HRFlowable(width="100%", thickness=0.3, color=C_BORDER))
    story.append(Spacer(1, 0.3 * cm))

    # ── Loan details ───────────────────────────────────────────────────────
    story.append(Paragraph("Détails du prêt" if is_fr else "Loan details", S["section"]))
    cust = getattr(application, "customer", None)
    customer_name = ""
    if cust and hasattr(cust, "user"):
        u = cust.user
        customer_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or getattr(u, "email", "")

    # Devise d'affichage = devise du montant demandé (toujours)
    amt_ccy = application.amount_currency or "EUR"
    inc_ccy = getattr(cust, "income_currency", None) or amt_ccy

    # Use effective_annual_income: declared profile income, then payslip estimate.
    from decimal import Decimal as _D
    _declared = (getattr(cust, "annual_income", None) or _D("0")) + (application.annual_income or _D("0"))
    income_raw = application.effective_annual_income
    income_is_estimated = bool(income_raw and income_raw > 0 and _declared <= 0)
    try:
        income_display = convert_amount(_D(str(income_raw)), inc_ccy, amt_ccy) if income_raw else None
    except Exception:
        income_display = income_raw

    if is_fr:
        _income_label = "Revenu annuel estimé (bulletins)" if income_is_estimated else "Revenu annuel déclaré"
        loan_rows = [
            ["Champ", "Valeur"],
            ["Client", customer_name or "—"],
            ["Type de prêt", application.get_loan_type_display()],
            ["Montant demandé", _fmt_amount(application.amount_requested, amt_ccy)],
            ["Durée", f"{application.term_months} mois" if application.term_months else "—"],
            [_income_label, _fmt_amount(income_display, amt_ccy)],
            ["Score d'éligibilité", _fmt_score(score)],
            ["Seuil d'approbation", f"{threshold:.0f} / 100"],
        ]
    else:
        _income_label_en = "Estimated annual income (payslips)" if income_is_estimated else "Declared annual income"
        loan_rows = [
            ["Field", "Value"],
            ["Customer", customer_name or "—"],
            ["Loan type", application.get_loan_type_display()],
            ["Amount requested", _fmt_amount(application.amount_requested, amt_ccy)],
            ["Term", f"{application.term_months} months" if application.term_months else "—"],
            [_income_label_en, _fmt_amount(income_display, amt_ccy)],
            ["Eligibility score", _fmt_score(score)],
            ["Approval threshold", f"{threshold:.0f} / 100"],
        ]
    story.append(_table(loan_rows, [7 * cm, W - 7 * cm]))
    story.append(Spacer(1, 0.3 * cm))

    # ── Repayment simulation — recalculée dans amt_ccy ─────────────────────
    try:
        from decimal import Decimal as _D
        _amount = _D(str(application.amount_requested or 0))
        _months = max(1, application.term_months or 12)
        _rate   = _D(str(roi.get("annual_rate_assumed") or "0.05"))
        _mr     = _rate / _D("12")
        if _mr > 0 and _amount > 0:
            _pow     = (_D("1") + _mr) ** _months
            _monthly = _amount * (_mr * _pow) / (_pow - _D("1"))
            _total   = (_monthly * _months).quantize(_D("0.01"))
            _monthly = _monthly.quantize(_D("0.01"))
            _interest = (_total - _amount).quantize(_D("0.01"))
        else:
            _monthly  = _amount / _D(_months)
            _total    = _amount
            _interest = _D("0")
    except Exception:
        _monthly = _total = _interest = None

    if _monthly is not None and application.amount_requested:
        story.append(Paragraph(
            "Simulation de remboursement" if is_fr else "Repayment simulation",
            S["section"],
        ))
        rate_pct = f"{float(roi.get('annual_rate_assumed', 0.05)) * 100:.1f} %"
        if is_fr:
            rep_rows = [
                ["Élément", "Montant"],
                ["Mensualité estimée",  _fmt_amount(_monthly, amt_ccy)],
                ["Total remboursé",     _fmt_amount(_total, amt_ccy)],
                ["Intérêts totaux",     _fmt_amount(_interest, amt_ccy)],
                ["Taux annuel",         rate_pct],
            ]
        else:
            rep_rows = [
                ["Item", "Amount"],
                ["Est. monthly payment", _fmt_amount(_monthly, amt_ccy)],
                ["Total repayment",      _fmt_amount(_total, amt_ccy)],
                ["Total interest",       _fmt_amount(_interest, amt_ccy)],
                ["Annual rate",          rate_pct],
            ]
        story.append(_table(rep_rows, [8 * cm, W - 8 * cm]))
        story.append(Spacer(1, 0.3 * cm))

    # ── Financial analysis — computed dynamically in amt_ccy ───────────────
    fin_bullets: list[str] = []
    try:
        from decimal import Decimal as _D2
        from core.currency_fx import convert_amount as _conv, format_money as _fmt_m
        from django.conf import settings as _st

        _inc_ccy = getattr(cust, "income_currency", None) or amt_ccy
        _income_raw = application.effective_annual_income
        _months2 = max(1, application.term_months or 12)
        _rate2 = _D2(str(roi.get("annual_rate_assumed") or
                         getattr(_st, "LOANWISE_INTEREST_RATE_ANNUAL", 0.05)))
        _score_f = float(application.eligibility_score) if application.eligibility_score is not None else 0.0
        _thr = float(detail.get("threshold") or 55.0)

        if _income_raw and application.amount_requested:
            # Compute payment in inc_ccy (amount converted to inc_ccy), then display in amt_ccy
            _amt_in_inc = _conv(_D2(str(application.amount_requested)), amt_ccy, _inc_ccy)
            _mr2 = _rate2 / _D2("12")
            if _mr2 > 0:
                _p2 = (_D2("1") + _mr2) ** _months2
                _pay_inc = _amt_in_inc * (_mr2 * _p2) / (_p2 - _D2("1"))
            else:
                _pay_inc = _amt_in_inc / _D2(_months2)
            _mon_inc = _income_raw / _D2("12")

            # Convert all display values to amt_ccy
            if _inc_ccy != amt_ccy:
                _income_d = _conv(_income_raw, _inc_ccy, amt_ccy)
                _pay_d = _conv(_pay_inc, _inc_ccy, amt_ccy)
                _mon_d = _conv(_mon_inc, _inc_ccy, amt_ccy)
            else:
                _income_d, _pay_d, _mon_d = _income_raw, _pay_inc, _mon_inc

            _dti_pct = float((_pay_inc / _mon_inc * _D2("100"))) if _mon_inc > 0 else 0.0

            if is_fr:
                fin_bullets = [
                    f"Revenu annuel utilisé : {_fmt_m(_income_d, amt_ccy)} (profil ou estimation bulletins).",
                    f"Mensualité estimée en {amt_ccy} (taux annuel {float(_rate2):.0%}, {_months2} mois) : {_fmt_m(_pay_d, amt_ccy)}.",
                    f"Revenu mensuel ({amt_ccy}) : {_fmt_m(_mon_d, amt_ccy)} — charge / revenu ~{_dti_pct:.1f} %.",
                    f"Score d'éligibilité calculé : {_score_f:.2f} / 100 (seuil interne : {_thr:.0f}).",
                ]
            else:
                fin_bullets = [
                    f"Annual income used: {_fmt_m(_income_d, amt_ccy)} (profile or payslip estimate).",
                    f"Estimated monthly payment in {amt_ccy} (annual rate {float(_rate2):.0%}, {_months2} months): {_fmt_m(_pay_d, amt_ccy)}.",
                    f"Monthly income ({amt_ccy}): {_fmt_m(_mon_d, amt_ccy)} — payment-to-income ~{_dti_pct:.1f}%.",
                    f"Computed eligibility score: {_score_f:.2f} / 100 (internal threshold: {_thr:.0f}).",
                ]
            # Append address blocker note if applicable
            if address_blocker:
                if is_fr:
                    fin_bullets.append(
                        "Incohérence d'adresse : l'adresse déclarée sur le profil ne correspond pas à celle "
                        "lisible sur les pièces. Le dossier n'est pas recevable tant que l'adresse n'est pas alignée."
                    )
                else:
                    fin_bullets.append(
                        "Address mismatch: the address on your profile does not match what is readable on your "
                        "documents. The application cannot be accepted until addresses are consistent."
                    )
    except Exception:
        # Fallback to stored bullets if dynamic computation fails
        fin_bullets = list(
            detail.get("financial_bullets_fr" if is_fr else "financial_bullets_en") or []
        )

    if fin_bullets:
        story.append(Paragraph(
            "Analyse financière" if is_fr else "Financial analysis",
            S["section"],
        ))
        for b in fin_bullets:
            story.append(_bullet_para(b, S["body"]))
            story.append(Spacer(1, 0.1 * cm))
        story.append(Spacer(1, 0.2 * cm))

    # ── Policy notes — regenerated dynamically so all amounts use amt_ccy ────
    # RAG-sourced figures are shown in amt_ccy with the original RAG value in
    # parentheses when the currencies differ  e.g. "23 500 000 MGA (5 000 EUR)".
    policy_bullets: list[str] = []
    try:
        from decimal import Decimal as _D3

        _pol_ccy: str = str(detail.get("policy_currency") or amt_ccy)
        _min_amt = detail.get("policy_guidance_min_amount")
        _max_amt = detail.get("policy_guidance_max_amount")
        _inc_floor = detail.get("rag_income_floor_detected")
        _fin_ok: bool = bool(detail.get("financial_inputs_complete"))
        _inc_ccy_p: str = getattr(cust, "income_currency", None) or amt_ccy

        if _fin_ok and _min_amt is not None and _max_amt is not None:
            try:
                _a = float(application.amount_requested or 0)
                _mn, _mx = float(_min_amt), float(_max_amt)
                if _a < _mn:
                    if is_fr:
                        policy_bullets.append(
                            f"Les documents de politique indiquent un montant minimum d'emprunt "
                            f"d'environ {_fmt_rag(_mn, _pol_ccy, amt_ccy)} — "
                            f"votre demande ({format_money(_D3(str(_a)), amt_ccy)}) est en dessous."
                        )
                    else:
                        policy_bullets.append(
                            f"Policy documents suggest a minimum loan amount around "
                            f"{_fmt_rag(_mn, _pol_ccy, amt_ccy)} — "
                            f"your request ({format_money(_D3(str(_a)), amt_ccy)}) is below that range."
                        )
                elif _a > _mx:
                    if is_fr:
                        policy_bullets.append(
                            f"Les documents de politique indiquent un plafond d'environ "
                            f"{_fmt_rag(_mx, _pol_ccy, amt_ccy)} — "
                            f"votre demande ({format_money(_D3(str(_a)), amt_ccy)}) le depasse."
                        )
                    else:
                        policy_bullets.append(
                            f"Policy documents suggest a maximum around "
                            f"{_fmt_rag(_mx, _pol_ccy, amt_ccy)} — "
                            f"your request ({format_money(_D3(str(_a)), amt_ccy)}) exceeds it."
                        )
                else:
                    if is_fr:
                        policy_bullets.append(
                            f"Le montant demande se situe dans la fourchette indicative "
                            f"({_fmt_rag(_mn, _pol_ccy, amt_ccy)} - {_fmt_rag(_mx, _pol_ccy, amt_ccy)})."
                        )
                    else:
                        policy_bullets.append(
                            f"The requested amount is within the indicative range from policy "
                            f"({_fmt_rag(_mn, _pol_ccy, amt_ccy)} - {_fmt_rag(_mx, _pol_ccy, amt_ccy)})."
                        )
            except (TypeError, ValueError):
                pass

        if _fin_ok and _inc_floor is not None:
            try:
                _income_val = application.effective_annual_income or _D3("0")
                # Convert income to policy_ccy for comparison (apples-to-apples)
                _income_in_pol = float(
                    convert_amount(_D3(str(_income_val)), _inc_ccy_p, _pol_ccy)
                    if _inc_ccy_p != _pol_ccy else _D3(str(_income_val))
                )
                _floor_int = int(_inc_floor)
                # Display income in amt_ccy
                _income_disp = (
                    convert_amount(_D3(str(_income_val)), _inc_ccy_p, amt_ccy)
                    if _inc_ccy_p != amt_ccy else _D3(str(_income_val))
                )
                if _income_in_pol < _floor_int:
                    if is_fr:
                        policy_bullets.append(
                            f"D'apres les extraits de politique indexes, un revenu annuel d'au moins "
                            f"environ {_fmt_rag(_floor_int, _pol_ccy, amt_ccy)} est mentionne — "
                            f"le revenu utilise ({format_money(_income_disp, amt_ccy)}) est inferieur."
                        )
                    else:
                        policy_bullets.append(
                            f"Indexed policy excerpts mention an annual income of at least about "
                            f"{_fmt_rag(_floor_int, _pol_ccy, amt_ccy)} — "
                            f"income used ({format_money(_income_disp, amt_ccy)}) is below that benchmark."
                        )
                else:
                    if is_fr:
                        policy_bullets.append(
                            f"Par rapport aux extraits de politique (repere de revenu annuel d'environ "
                            f"{_fmt_rag(_floor_int, _pol_ccy, amt_ccy)}), "
                            f"le revenu utilise ({format_money(_income_disp, amt_ccy)}) atteint ce niveau."
                        )
                    else:
                        policy_bullets.append(
                            f"Compared to policy excerpts (annual income benchmark around "
                            f"{_fmt_rag(_floor_int, _pol_ccy, amt_ccy)}), "
                            f"income used ({format_money(_income_disp, amt_ccy)}) meets or exceeds it."
                        )
            except Exception:
                pass
    except Exception:
        # Fallback to stored bullets
        policy_bullets = list(
            detail.get("policy_bullets_fr" if is_fr else "policy_bullets_en") or []
        )

    if policy_bullets:
        story.append(Paragraph(
            "Politique de credit" if is_fr else "Credit policy",
            S["section"],
        ))
        for b in policy_bullets:
            story.append(_bullet_para(b, S["body"]))
            story.append(Spacer(1, 0.1 * cm))
        story.append(Spacer(1, 0.2 * cm))

    # ── User advice (especially on address mismatch or rejection) ──────────
    advice = detail.get("user_advice_fr") if is_fr else detail.get("user_advice_en")
    if not advice and not eligible:
        advice = (
            "Veuillez contacter votre conseiller LoanWise pour régulariser votre dossier."
            if is_fr else
            "Please contact your LoanWise advisor to regularise your application."
        )
    if advice:
        story.append(Paragraph(
            "Recommandations" if is_fr else "Recommendations",
            S["section"],
        ))
        story.append(Paragraph(str(advice), S["body"]))
        story.append(Spacer(1, 0.3 * cm))

    # ── LLM narrative (if available) ──────────────────────────────────────
    llm_out = detail.get("llm") or {}
    if isinstance(llm_out, dict) and not llm_out.get("unavailable"):
        narrative = llm_out.get("narrative_fr" if is_fr else "narrative_en") or llm_out.get("narrative")
        if narrative and isinstance(narrative, str) and len(narrative) > 20:
            story.append(Paragraph(
                "Analyse détaillée" if is_fr else "Detailed analysis",
                S["section"],
            ))
            story.append(Paragraph(narrative[:1200], S["body"]))
            story.append(Spacer(1, 0.3 * cm))

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 0.15 * cm))
    footer_txt = (
        f"Généré par LoanWise — Smart Loan Eligibility Checker · {date.today().strftime('%d/%m/%Y')}"
        if is_fr else
        f"Generated by LoanWise — Smart Loan Eligibility Checker · {date.today().strftime('%d/%m/%Y')}"
    )
    story.append(Paragraph(footer_txt, S["small"]))

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    translation.activate(previous)
    return pdf
