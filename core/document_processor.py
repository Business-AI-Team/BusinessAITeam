"""
Document analysis via OpenAI: Vision for images (png/jpeg/gif/webp), Responses API for PDF
(input_file, incl. scans), with fallbacks (raster + Vision, puis texte extrait + Chat).

API key: env ``OPENAI_API_KEY`` or Admin → Integration settings (``get_openai_api_key``).
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

from django.conf import settings

from core.openai_config import get_openai_api_key, get_openai_client
from core.rag_eligibility import extract_text_from_file

logger = logging.getLogger(__name__)

VISION_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
# Limite fichier côté OpenAI ~50 MiB — marge de sécurité
_MAX_PDF_BYTES_OPENAI = 48 * 1024 * 1024
_MAX_RASTER_PAGES = 10


def _resolve_document_backend() -> str:
    """auto | openai | fallback — auto uses OpenAI when a key is configured (env or Admin)."""
    raw = getattr(settings, "LOANWISE_DOCUMENT_ANALYSIS_BACKEND", "auto") or "auto"
    raw = str(raw).strip().lower()
    if raw in ("openai", "fallback"):
        return raw
    if get_openai_api_key():
        return "openai"
    return "fallback"


def _build_data_url_for_vision(path: Path) -> str | None:
    """
    Build a data URL acceptable by OpenAI Vision (png, jpeg, gif, webp only).
    Other raster formats (bmp, tiff, …) are converted to PNG via Pillow when possible.
    """
    mime = mimetypes.guess_type(str(path))[0]
    raw = path.read_bytes()
    if mime in VISION_MIMES:
        b64 = base64.standard_b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    try:
        from PIL import Image

        img = Image.open(path).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        logger.debug("Could not open/convert image %s: %s", path.name, e)
        return None


def _valid_payslip_months(count: int = 3, reference_date=None) -> list[str]:
    """Retourne les N mois calendaires précédant ``reference_date`` (ou aujourd'hui si None) au format YYYY-MM."""
    from datetime import date as _date
    ref = reference_date or _date.today()
    if hasattr(ref, "date"):
        ref = ref.date()
    y, m = ref.year, ref.month
    months: list[str] = []
    for _ in range(count):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")
    return months


def _loan_doc_json_prompt(language: str, reference_date=None) -> str:
    """Prompt JSON unique pour vision / PDF Responses / raster — inclut champs pour cohérence formulaire / pièces."""
    valid_months = _valid_payslip_months(3, reference_date)
    valid_months_str = ", ".join(valid_months)
    ref_label = valid_months[0][:7] if valid_months else "?"

    if language.startswith("fr"):
        return (
            "Tu analyses ce document pour un dossier de prêt (CIN, fiche de paie, justificatif de domicile, etc.). "
            "Réponds en JSON STRICT uniquement (pas de markdown, pas de texte hors JSON). "
            "Clés obligatoires: "
            "document_type (court, ex. fiche_de_paie, piece_identite, justificatif_domicile), "
            "visible_text_summary (résumé factuel du texte visible), "
            "language_detected, confidence (high/medium/low). "
            "Clé obligatoire extracted_fields (objet) avec les valeurs null si absent ou illisible: "
            "annual_income_amount (nombre: revenu annuel NET indiqué sur le document si présent — "
            "IMPORTANT : le revenu n'est JAMAIS saisi par l'utilisateur, il est uniquement extrait par l'IA depuis les documents fournis), "
            "monthly_net_amount (nombre: salaire net MENSUEL si indiqué sur une fiche de paie — "
            "si seul le net mensuel est visible, renseigne ce champ et laisse annual_income_amount à null ; l'IA calculera l'équivalent annuel), "
            "address_on_document (chaîne: adresse complète lue sur le document — domicile, employeur, ou cadre prévu), "
            "person_full_name (nom complet titulaire/salarié si visible — champ générique), "
            "payslip_employee_name (nom complet du SALARIÉ tel qu'écrit sur la fiche de paie — null si ce n'est pas une fiche de paie), "
            "id_holder_name (nom complet du TITULAIRE tel qu'écrit sur la CIN ou le passeport — null si ce n'est pas une pièce d'identité), "
            "national_id (n° CIN/carte si visible), "
            "employer_name (nom employeur si bulletin de salaire), "
            "document_currency (une parmi MGA, EUR, MUR selon le symbole sur le document), "
            "pay_period_year_month (période du bulletin au format YYYY-MM, ex. 2025-02 — "
            "extrait exactement telle qu'écrite sur le document ; null si pas une fiche de paie ou date illisible), "
            f"payslip_period_valid (RÈGLE OBLIGATOIRE pour toute fiche de paie — "
            f"les 3 mois autorisés à la date de création du dossier sont : {valid_months_str} ; "
            "mettre true si pay_period_year_month est dans cette liste, false sinon, null si pas une fiche de paie). "
            "IMPORTANT : si payslip_period_valid est false, le mentionner EXPLICITEMENT dans visible_text_summary. "
            "Pour une fiche de paie, extrais avec précision les montants nets et l'adresse affichée. "
            "Si seul le net mensuel est visible, laisse annual_income_amount à null et renseigne monthly_net_amount. "
            "Cohérence du salaire : si le montant net mensuel de cette fiche de paie semble anormalement incohérent "
            "(ex. : montant dérisoire de quelques dizaines d'unités alors qu'on attendrait des milliers, ou inversement — "
            "indépendamment de la devise), signale-le EXPLICITEMENT dans visible_text_summary avec le montant exact et le mois concerné. "
            "Règle métier adresse : SEUL le justificatif de domicile (attestation de résidence, facture, etc.) "
            "doit avoir une adresse correspondant à l'adresse de résidence déclarée dans le dossier de prêt. "
            "La CIN (carte d'identité nationale) et le passeport peuvent légitimement afficher une adresse différente "
            "(adresse de naissance, ancienne adresse, adresse parentale) — ce n'est PAS une anomalie à signaler. "
            "Si l'adresse sur un justificatif de domicile ne correspond pas à l'adresse déclarée, "
            "signale-le dans visible_text_summary."
        )
    return (
        "Analyze this document for a loan application (ID, payslip, proof of address, etc.). "
        "Reply with STRICT JSON only (no markdown). Required keys: "
        "document_type (short slug, e.g. payslip, id_card, proof_of_address), "
        "visible_text_summary (factual summary of visible text), language_detected, confidence (high/medium/low). "
        "Required key extracted_fields (object), use null if missing/unreadable: "
        "annual_income_amount (number: NET annual salary printed on the document — "
        "IMPORTANT: income is NEVER entered by the user; it is extracted by the AI from the uploaded documents only), "
        "monthly_net_amount (number: NET monthly salary on a payslip — "
        "if only monthly net is visible, set this field and leave annual_income_amount null; the AI will compute the annual equivalent), "
        "address_on_document (full address string read on the document), "
        "person_full_name (generic: full name of holder/employee if visible), "
        "payslip_employee_name (full name of the EMPLOYEE as printed on the payslip — null if not a payslip), "
        "id_holder_name (full name of the ID/passport HOLDER as printed — null if not an identity document), "
        "national_id, employer_name (for payslips), "
        "document_currency (one of MGA, EUR, MUR based on symbols on the document), "
        "pay_period_year_month (payslip period as YYYY-MM, e.g. 2025-02 — "
        "extracted exactly as written on the document; null if not a payslip or date unreadable), "
        f"payslip_period_valid (MANDATORY for any payslip — "
        f"the 3 authorized months at the time the application was created are: {valid_months_str} ; "
        "set true if pay_period_year_month is in that list, false otherwise, null if not a payslip). "
        "IMPORTANT: if payslip_period_valid is false, mention it explicitly in visible_text_summary. "
        "On payslips, extract net amounts and any printed address accurately. "
        "If only monthly net is shown, set annual_income_amount to null and set monthly_net_amount. "
        "Salary consistency: if the net monthly amount on this payslip appears abnormally inconsistent "
        "(e.g. an absurdly low amount of a few tens of units when thousands would be expected, or vice versa — "
        "regardless of currency), flag it EXPLICITLY in visible_text_summary with the exact amount and pay period. "
        "Important business rule: ONLY the proof-of-address document (certificate of residence, utility bill, etc.) "
        "is required to match the applicant's declared home address. "
        "National ID cards (CIN) and passports may legitimately show a different address - this is NOT an anomaly. "
        "If the address on a proof-of-address document does not match the declared home, note it in visible_text_summary."
    )


def _loan_doc_extracted_text_prompt(language: str, reference_date=None) -> str:
    """Prompt quand seul le texte extrait (pypdf) est envoyé au Chat."""
    valid_months = _valid_payslip_months(3, reference_date)
    valid_months_str = ", ".join(valid_months)

    if language.startswith("fr"):
        return (
            "Tu analyses le texte suivant extrait d'un document pour un dossier de prêt. "
            "Réponds en JSON STRICT uniquement (même schéma que pour une image: document_type, visible_text_summary, "
            "language_detected, confidence, et extracted_fields avec "
            "annual_income_amount (NET annuel lu sur le doc — jamais saisi par l'user), "
            "monthly_net_amount (net mensuel — si annuel absent, renseigne ce champ), "
            "address_on_document, "
            "person_full_name, payslip_employee_name, id_holder_name, "
            "national_id, employer_name, document_currency, "
            "pay_period_year_month (YYYY-MM, extrait exactement tel qu'écrit sur le document), "
            f"payslip_period_valid (mois autorisés à la date de création du dossier : {valid_months_str} ; "
            "true/false/null) — null si absent). "
            "Si payslip_period_valid est false, le mentionner dans visible_text_summary. "
            "Cohérence du salaire : si le montant net mensuel de cette fiche semble anormalement incohérent "
            "(ex. quelques dizaines d'unités alors qu'on attendrait des milliers, ou inversement), "
            "le signaler explicitement dans visible_text_summary avec le montant et le mois. "
            "Si l'adresse sur la pièce contredit manifestement une adresse de résidence déclarée dans un dossier de prêt, mentionne-le dans visible_text_summary.\n\n---\n"
        )
    return (
        "Analyze the following extracted document text for a loan application. "
        "Strict JSON only, same schema as for images: document_type, visible_text_summary, language_detected, confidence, "
        "extracted_fields {{annual_income_amount (NET annual — never entered by user, extracted from doc only), "
        "monthly_net_amount (NET monthly — set if annual absent), address_on_document, "
        "person_full_name, payslip_employee_name, id_holder_name, "
        "national_id, employer_name, document_currency, "
        "pay_period_year_month (YYYY-MM, extracted exactly as written on the document), "
        f"payslip_period_valid (authorized months at application creation: {valid_months_str} ; true/false/null)}}. "
        "If payslip_period_valid is false, mention it in visible_text_summary. "
        "Salary consistency: if the net monthly amount appears abnormally inconsistent "
        "(e.g. absurdly low tens of units when thousands would be expected, or vice versa), "
        "flag it explicitly in visible_text_summary with the exact amount and pay period. "
        "Rule: only the proof-of-address document is checked against the declared home. CIN/passport address may differ — not an issue. "
        "If the proof-of-address document address conflicts with the declared address, note it in visible_text_summary.\n\n---\n"
    )
    return (
        "Analyze the following extracted document text for a loan application. "
        "Strict JSON only, same schema as for images: document_type, visible_text_summary, language_detected, confidence, "
        "extracted_fields {{annual_income_amount (NET annual — never entered by user, extracted from doc only), "
        "monthly_net_amount (NET monthly — set if annual absent), address_on_document, "
        "person_full_name, payslip_employee_name, id_holder_name, "
        "national_id, employer_name, document_currency, "
        f"pay_period_year_month (YYYY-MM), "
        f"payslip_period_valid (true/false/null — Today: {today_str}, valid months: {valid_months_str})}}. "
        "If payslip_period_valid is false, mention it in visible_text_summary. "
        "Rule: only the proof-of-address document is checked against the declared home. CIN/passport address may differ — not an issue. "
        "If the proof-of-address document address conflicts with the declared address, note it in visible_text_summary.\n\n---\n"
    )


def _resolve_pdf_model() -> str:
    m = getattr(settings, "LOANWISE_OPENAI_PDF_MODEL", None)
    if isinstance(m, str) and m.strip():
        return m.strip()
    return getattr(settings, "LOANWISE_OPENAI_VISION_MODEL", "gpt-4o-mini")


def _responses_output_text(resp: Any) -> str:
    t = getattr(resp, "output_text", None)
    if t is not None and str(t).strip():
        return str(t).strip()
    out = getattr(resp, "output", None) or []
    parts: list[str] = []
    for item in out:
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", None) == "output_text":
                    tx = getattr(block, "text", None)
                    if tx:
                        parts.append(str(tx))
    return "".join(parts).strip()


def _pdf_via_openai_responses(path: Path, language: str, api_key: str, reference_date=None) -> dict[str, Any] | None:
    """Envoie le PDF brut via Responses API (input_file) — fonctionne pour scans et PDF texte."""
    try:
        from openai import OpenAI  # noqa: F401 — vérifie que le package est installé
    except ImportError:
        return None

    data = path.read_bytes()
    if len(data) > _MAX_PDF_BYTES_OPENAI:
        logger.warning("PDF too large for OpenAI file input (%s bytes): %s", len(data), path.name)
        return None

    model = _resolve_pdf_model()
    prompt = _loan_doc_json_prompt(language, reference_date)
    b64 = base64.standard_b64encode(data).decode("ascii")
    file_data = f"data:application/pdf;base64,{b64}"
    filename = path.name or "document.pdf"

    try:
        client = get_openai_client()
        if client is None:
            return None
        resp = client.responses.create(
            model=model,
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": file_data,
                        },
                    ],
                }
            ],
            temperature=0.2,
            max_output_tokens=3072,
        )
    except Exception as e:
        logger.warning("OpenAI Responses PDF failed (%s): %s", path.name, e)
        return None

    err = getattr(resp, "error", None)
    if err is not None:
        logger.warning("OpenAI Responses returned error for PDF %s: %s", path.name, err)
        return None
    if getattr(resp, "status", None) == "failed":
        logger.warning("OpenAI Responses status=failed for PDF %s", path.name)
        return None

    text_out = _responses_output_text(resp)
    if not text_out:
        logger.warning("OpenAI Responses empty output for PDF %s", path.name)
        return None

    return {
        "engine": "openai-pdf",
        "caption": text_out,
        "model": model,
        "filename": path.name,
        "ok": True,
    }


def _pdf_via_raster_vision(path: Path, language: str, api_key: str, reference_date=None) -> dict[str, Any] | None:
    """PyMuPDF: pages → PNG → Chat Vision multi-images."""
    try:
        import fitz  # PyMuPDF
        from openai import BadRequestError  # noqa: F401
    except ImportError:
        return None

    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.debug("PyMuPDF could not open %s: %s", path.name, e)
        return None

    prompt = _loan_doc_json_prompt(language, reference_date)
    model = getattr(settings, "LOANWISE_OPENAI_VISION_MODEL", "gpt-4o-mini")
    mat = fitz.Matrix(1.5, 1.5)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

    try:
        n = min(doc.page_count, _MAX_RASTER_PAGES)
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png = pix.tobytes("png")
            b64 = base64.standard_b64encode(png).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
    finally:
        doc.close()

    if len(content) <= 1:
        return None

    try:
        client = get_openai_client()
        if client is None:
            return None
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=1600,
            temperature=0.2,
        )
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            return None
        return {
            "engine": "openai-pdf-raster",
            "caption": out,
            "model": model,
            "filename": path.name,
            "ok": True,
        }
    except BadRequestError as e:
        logger.warning("OpenAI vision (raster PDF) rejected %s: %s", path.name, e)
        return None
    except Exception as e:
        logger.warning("OpenAI vision (raster PDF) failed %s: %s", path.name, e)
        return None


def _pdf_via_extracted_text_chat(path: Path, language: str, api_key: str, reference_date=None) -> dict[str, Any] | None:
    """Dernier repli : pypdf + Chat sur le texte."""
    text = extract_text_from_file(path)
    text = (text or "").strip()
    if not text:
        return None

    text = text[:14000]
    model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
    prompt = _loan_doc_extracted_text_prompt(language, reference_date)

    try:
        client = get_openai_client()
        if client is None:
            return None
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt + text}],
            max_tokens=1400,
            temperature=0.2,
        )
        out = (resp.choices[0].message.content or "").strip()
        if not out:
            return None
        return {
            "engine": "openai-pdf-text",
            "caption": out,
            "model": model,
            "filename": path.name,
            "ok": True,
        }
    except Exception as e:
        logger.warning("OpenAI PDF text analysis failed: %s", e)
        return None


def _analyze_pdf_with_openai(path: Path, language: str, reference_date=None) -> dict[str, Any]:
    """PDF : Responses (fichier natif) → raster Vision → texte extrait + Chat."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback_result("openai_no_api_key", language, filename=path.name)
    try:
        import openai  # noqa: F401 — vérifie que le package est là
    except ImportError:
        logger.warning("openai package not installed; pip install openai")
        return _fallback_result("openai_not_installed", language, filename=path.name)

    out = _pdf_via_openai_responses(path, language, api_key, reference_date)
    if out is not None:
        return out

    out = _pdf_via_raster_vision(path, language, api_key, reference_date)
    if out is not None:
        return out

    out = _pdf_via_extracted_text_chat(path, language, api_key, reference_date)
    if out is not None:
        return out

    msg_fr = (
        "Impossible d'analyser ce PDF via OpenAI (réponse vide ou format non pris en charge). "
        "Vérifiez la clé API, le modèle (LOANWISE_OPENAI_PDF_MODEL), ou téléversez une image (JPG/PNG)."
    )
    msg_en = (
        "Could not analyze this PDF via OpenAI (empty response or unsupported input). "
        "Check your API key, model (LOANWISE_OPENAI_PDF_MODEL), or upload an image (JPG/PNG)."
    )
    return {
        "engine": "openai-pdf",
        "caption": msg_fr if language.startswith("fr") else msg_en,
        "filename": path.name,
        "ok": False,
        "reason": "pdf_openai_failed",
    }


def _analyze_with_openai_vision(path: Path, language: str, reference_date=None) -> dict[str, Any]:
    """Raster images only — data URL must be png/jpeg/gif/webp."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback_result("openai_no_api_key", language, filename=path.name)
    try:
        from openai import BadRequestError  # noqa: F401
    except ImportError:
        logger.warning("openai package not installed; pip install openai")
        return _fallback_result("openai_not_installed", language, filename=path.name)

    data_url = _build_data_url_for_vision(path)
    if not data_url:
        return _fallback_result("unsupported_image_format", language, filename=path.name)

    try:
        client = get_openai_client()
        if client is None:
            return _fallback_result("openai_no_api_key", language, filename=path.name)
        model = getattr(settings, "LOANWISE_OPENAI_VISION_MODEL", "gpt-4o-mini")
        prompt = _loan_doc_json_prompt(language, reference_date)

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=1600,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {
            "engine": "openai-vision",
            "caption": text,
            "model": model,
            "filename": path.name,
            "ok": True,
        }
    except BadRequestError as e:
        logger.warning("OpenAI vision rejected file %s: %s", path.name, e)
        return _fallback_result("openai_error", language, error=str(e), filename=path.name)
    except Exception as e:
        logger.exception("OpenAI vision analysis failed: %s", e)
        return _fallback_result("openai_error", language, error=str(e), filename=path.name)


def analyze_document_image(
    image_path: str | Path,
    *,
    language: str = "en",
    application_created_at=None,
) -> dict[str, Any]:
    """
    Analyze a document file: PDF → OpenAI Responses (input_file) puis repli ;
    images → Vision.

    ``language`` affects prompts and fallback text (UI/i18n).
    ``application_created_at`` est la date de création de la demande de prêt (datetime ou date).
    Les mois valides pour les fiches de paie sont calculés à partir de cette date.
    """
    path = Path(image_path)
    if not path.is_file():
        return _fallback_result("file_not_found", language)

    backend = _resolve_document_backend()
    if backend != "openai":
        logger.info("Document analysis backend is fallback (no OpenAI key or explicit fallback).")
        return _fallback_result("model_unavailable", language, filename=path.name)

    suf = path.suffix.lower()
    mime = mimetypes.guess_type(str(path))[0] or ""
    if suf == ".pdf" or mime == "application/pdf":
        return _analyze_pdf_with_openai(path, language, application_created_at)

    return _analyze_with_openai_vision(path, language, application_created_at)


def _fallback_result(
    reason: str,
    language: str,
    *,
    filename: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Placeholders when OpenAI is unavailable or errors."""
    if language.startswith("fr"):
        summary = (
            "Analyse locale (démonstration) : aucune clé OpenAI ou erreur d'appel. "
            "Configurez la clé (variable d'environnement ou Admin → Paramètres d'intégration). "
            "Les métadonnées du fichier sont enregistrées."
        )
    else:
        summary = (
            "Local analysis (demo): no OpenAI key or API error. "
            "Set the key via environment or Admin → Integration settings. "
            "File metadata is still recorded."
        )
    return {
        "engine": "fallback",
        "caption": summary,
        "reason": reason,
        "filename": filename,
        "error": error,
        "ok": reason == "model_unavailable",
    }
