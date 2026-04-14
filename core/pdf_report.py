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

from core.currency_fx import convert_amount
from core.models import LoanApplication

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

    # Revenu converti dans la même devise que le montant pour affichage cohérent
    income_raw = application.annual_income
    try:
        from decimal import Decimal as _D
        income_display = convert_amount(_D(str(income_raw)), inc_ccy, amt_ccy) if income_raw else None
    except Exception:
        income_display = income_raw

    if is_fr:
        loan_rows = [
            ["Champ", "Valeur"],
            ["Client", customer_name or "—"],
            ["Type de prêt", application.get_loan_type_display()],
            ["Montant demandé", _fmt_amount(application.amount_requested, amt_ccy)],
            ["Durée", f"{application.term_months} mois" if application.term_months else "—"],
            ["Revenu annuel déclaré", _fmt_amount(income_display, amt_ccy)],
            ["Score d'éligibilité", _fmt_score(score)],
            ["Seuil d'approbation", f"{threshold:.0f} / 100"],
        ]
    else:
        loan_rows = [
            ["Field", "Value"],
            ["Customer", customer_name or "—"],
            ["Loan type", application.get_loan_type_display()],
            ["Amount requested", _fmt_amount(application.amount_requested, amt_ccy)],
            ["Term", f"{application.term_months} months" if application.term_months else "—"],
            ["Declared annual income", _fmt_amount(income_display, amt_ccy)],
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

    # ── Financial analysis ─────────────────────────────────────────────────
    fin_bullets: list[str] = list(
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

    # ── Policy notes (min/max amount, income floor) ────────────────────────
    policy_bullets: list[str] = list(
        detail.get("policy_bullets_fr" if is_fr else "policy_bullets_en") or []
    )
    if policy_bullets:
        story.append(Paragraph(
            "Politique de crédit" if is_fr else "Credit policy",
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
