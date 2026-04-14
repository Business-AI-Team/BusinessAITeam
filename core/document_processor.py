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

from core.openai_config import get_openai_api_key
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


def _loan_doc_json_prompt(language: str) -> str:
    """Prompt JSON unique pour vision / PDF Responses / raster — inclut champs pour cohérence formulaire / pièces."""
    if language.startswith("fr"):
        return (
            "Tu analyses ce document pour un dossier de prêt (CIN, fiche de paie, justificatif de domicile, etc.). "
            "Réponds en JSON STRICT uniquement (pas de markdown, pas de texte hors JSON). "
            "Clés obligatoires: "
            "document_type (court, ex. fiche_de_paie, piece_identite, justificatif_domicile), "
            "visible_text_summary (résumé factuel du texte visible), "
            "language_detected, confidence (high/medium/low). "
            "Clé obligatoire extracted_fields (objet) avec les valeurs null si absent ou illisible: "
            "annual_income_amount (nombre: revenu annuel NET indiqué sur le document si présent), "
            "monthly_net_amount (nombre: salaire net MENSUEL si indiqué sur une fiche de paie), "
            "address_on_document (chaîne: adresse complète lue sur le document — domicile, employeur, ou cadre prévu), "
            "person_full_name (nom complet titulaire/salarié si visible), "
            "national_id (n° CIN/carte si visible), "
            "employer_name (nom employeur si bulletin de salaire). "
            "Pour une fiche de paie, extrais avec précision les montants nets et l’adresse affichée. "
            "Si seul le net mensuel est visible, laisse annual_income_amount à null et renseigne monthly_net_amount. "
            "document_currency: une parmi MGA, EUR, MUR selon le symbole sur le document. "
            "pay_period_year_month: période du bulletin au format YYYY-MM (ex. 2025-02) si visible. "
            "R\u00e8gle m\u00e9tier importante : SEUL le justificatif de domicile (attestation de r\u00e9sidence, facture, etc.) "
            "doit avoir une adresse correspondant \u00e0 l'adresse de r\u00e9sidence d\u00e9clar\u00e9e dans le dossier de pr\u00eat. "
            "La CIN (carte d'identit\u00e9 nationale) et le passeport peuvent l\u00e9gitimement afficher une adresse diff\u00e9rente "
            "(adresse de naissance, ancienne adresse, adresse parentale) \u2014 ce n'est PAS une anomalie \u00e0 signaler. "
            "Si l'adresse sur un justificatif de domicile ne correspond pas \u00e0 l'adresse d\u00e9clar\u00e9e, "
            "signale-le dans visible_text_summary."
        )
    return (
        "Analyze this document for a loan application (ID, payslip, proof of address, etc.). "
        "Reply with STRICT JSON only (no markdown). Required keys: "
        "document_type (short slug, e.g. payslip, id_card, proof_of_address), "
        "visible_text_summary (factual summary of visible text), language_detected, confidence (high/medium/low). "
        "Required key extracted_fields (object), use null if missing/unreadable: "
        "annual_income_amount (number: NET annual salary printed on the document), "
        "monthly_net_amount (number: NET monthly salary on a payslip), "
        "address_on_document (full address string read on the document), "
        "person_full_name, national_id, employer_name (for payslips). "
        "On payslips, extract net amounts and any printed address accurately. "
        "If only monthly net is shown, set annual_income_amount to null and set monthly_net_amount. "
        "document_currency: one of MGA, EUR, MUR based on symbols on the document. "
        "pay_period_year_month: payslip period as YYYY-MM if visible. "
        "Important business rule: ONLY the proof-of-address document (certificate of residence, utility bill, etc.) "
        "is required to match the applicant's declared home address. "
        "National ID cards (CIN) and passports may legitimately show a different address - this is NOT an anomaly. "
        "If the address on a proof-of-address document does not match the declared home, note it in visible_text_summary."
    )


def _loan_doc_extracted_text_prompt(language: str) -> str:
    """Prompt quand seul le texte extrait (pypdf) est envoyé au Chat."""
    if language.startswith("fr"):
        return (
            "Tu analyses le texte suivant extrait d'un document pour un dossier de prêt. "
            "Réponds en JSON STRICT uniquement (même schéma que pour une image: document_type, visible_text_summary, "
            "language_detected, confidence, et extracted_fields avec "
            "annual_income_amount, monthly_net_amount, address_on_document, person_full_name, national_id, employer_name — null si absent). "
            "Si l’adresse sur la pièce contredit manifestement une adresse de résidence déclarée dans un dossier de prêt, mentionne-le dans visible_text_summary.\n\n---\n"
        )
    return (
        "Analyze the following extracted document text for a loan application. "
        "Strict JSON only, same schema as for images: document_type, visible_text_summary, language_detected, confidence, "
        "extracted_fields {annual_income_amount, monthly_net_amount, address_on_document, person_full_name, national_id, employer_name}. "
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


def _pdf_via_openai_responses(path: Path, language: str, api_key: str) -> dict[str, Any] | None:
    """Envoie le PDF brut via Responses API (input_file) — fonctionne pour scans et PDF texte."""
    try:
        from openai import OpenAI
    except ImportError:
        return None

    data = path.read_bytes()
    if len(data) > _MAX_PDF_BYTES_OPENAI:
        logger.warning("PDF too large for OpenAI file input (%s bytes): %s", len(data), path.name)
        return None

    model = _resolve_pdf_model()
    prompt = _loan_doc_json_prompt(language)
    b64 = base64.standard_b64encode(data).decode("ascii")
    file_data = f"data:application/pdf;base64,{b64}"
    filename = path.name or "document.pdf"

    try:
        client = OpenAI(api_key=api_key)
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


def _pdf_via_raster_vision(path: Path, language: str, api_key: str) -> dict[str, Any] | None:
    """PyMuPDF: pages → PNG → Chat Vision multi-images."""
    try:
        import fitz  # PyMuPDF
        from openai import BadRequestError, OpenAI
    except ImportError:
        return None

    try:
        doc = fitz.open(path)
    except Exception as e:
        logger.debug("PyMuPDF could not open %s: %s", path.name, e)
        return None

    prompt = _loan_doc_json_prompt(language)
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
        client = OpenAI(api_key=api_key)
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


def _pdf_via_extracted_text_chat(path: Path, language: str, api_key: str) -> dict[str, Any] | None:
    """Dernier repli : pypdf + Chat sur le texte."""
    text = extract_text_from_file(path)
    text = (text or "").strip()
    if not text:
        return None

    text = text[:14000]
    model = getattr(settings, "LOANWISE_OPENAI_MODEL", "gpt-4o-mini")
    prompt = _loan_doc_extracted_text_prompt(language)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
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


def _analyze_pdf_with_openai(path: Path, language: str) -> dict[str, Any]:
    """PDF : Responses (fichier natif) → raster Vision → texte extrait + Chat."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback_result("openai_no_api_key", language, filename=path.name)
    try:
        import openai  # noqa: F401 — vérifie que le package est là
    except ImportError:
        logger.warning("openai package not installed; pip install openai")
        return _fallback_result("openai_not_installed", language, filename=path.name)

    out = _pdf_via_openai_responses(path, language, api_key)
    if out is not None:
        return out

    out = _pdf_via_raster_vision(path, language, api_key)
    if out is not None:
        return out

    out = _pdf_via_extracted_text_chat(path, language, api_key)
    if out is not None:
        return out

    msg_fr = (
        "Impossible d’analyser ce PDF via OpenAI (réponse vide ou format non pris en charge). "
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


def _analyze_with_openai_vision(path: Path, language: str) -> dict[str, Any]:
    """Raster images only — data URL must be png/jpeg/gif/webp."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback_result("openai_no_api_key", language, filename=path.name)
    try:
        from openai import BadRequestError, OpenAI
    except ImportError:
        logger.warning("openai package not installed; pip install openai")
        return _fallback_result("openai_not_installed", language, filename=path.name)

    data_url = _build_data_url_for_vision(path)
    if not data_url:
        return _fallback_result("unsupported_image_format", language, filename=path.name)

    try:
        client = OpenAI(api_key=api_key)
        model = getattr(settings, "LOANWISE_OPENAI_VISION_MODEL", "gpt-4o-mini")
        prompt = _loan_doc_json_prompt(language)

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
) -> dict[str, Any]:
    """
    Analyze a document file: PDF → OpenAI Responses (input_file) puis repli ;
    images → Vision.

    ``language`` affects prompts and fallback text (UI/i18n).
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
        return _analyze_pdf_with_openai(path, language)

    return _analyze_with_openai_vision(path, language)


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
            "Analyse locale (démonstration) : aucune clé OpenAI ou erreur d’appel. "
            "Configurez la clé (variable d’environnement ou Admin → Paramètres d’intégration). "
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
