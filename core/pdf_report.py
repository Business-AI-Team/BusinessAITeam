"""
Professional PDF report generation for final loan summary (ReportLab).
"""

from __future__ import annotations

import io
from typing import Any

from django.utils import translation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import LoanRequest


def build_application_pdf(application: LoanRequest) -> bytes:
    """Return PDF bytes for the given application (language-aware labels)."""
    language = application.language or "fr"
    previous = translation.get_language()
    translation.activate(language)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="TitleLW",
        parent=styles["Heading1"],
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=12,
    )
    body = styles["Normal"]

    story = []
    title = (
        "Rapport LoanWise — Éligibilité"
        if language.startswith("fr")
        else "LoanWise Report — Eligibility"
    )
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"<b>Ref:</b> {application.reference}", body))
    story.append(Paragraph(f"<b>Status:</b> {application.get_status_display()}", body))
    story.append(Spacer(1, 0.5 * cm))

    roi: dict[str, Any] = application.roi_summary or {}
    rows = [
        ["Field", "Value"],
        ["Loan type", application.get_loan_type_display()],
        ["Amount", str(application.amount_requested)],
        ["Term (months)", str(application.term_months)],
        ["Annual income", str(application.annual_income)],
        ["Eligibility score", str(application.score or "-")],
    ]
    if roi:
        for k, v in roi.items():
            rows.append([k.replace("_", " ").title(), str(v)])

    t = Table(rows, colWidths=[6 * cm, 10 * cm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))
    impact = application.business_impact or {}
    story.append(
        Paragraph(
            f"<b>Business impact:</b> {impact}" if not language.startswith("fr") else f"<b>Impact:</b> {impact}",
            body,
        )
    )

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    translation.activate(previous)
    return pdf
