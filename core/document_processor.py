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
    """Prompt JSON unique pour vision / PDF Responses / raster."""
    if language.startswith("fr"):
        return (
            "Tu analyses ce document pour un dossier de prêt (CIN, justificatif, etc.). "
            "Réponds en JSON compact avec les clés: document_type (court), visible_text_summary (résumé du texte visible), "
            "language_detected, confidence (high/medium/low). Pas de markdown, JSON seul."
        )
    return (
        "Analyze this document for a loan application (ID card, payslip, etc.). "
        "Reply with compact JSON only, keys: document_type, visible_text_summary, language_detected, "
        "confidence (high/medium/low). No markdown."
    )


def _loan_doc_extracted_text_prompt(language: str) -> str:
    """Prompt quand seul le texte extrait (pypdf) est envoyé au Chat."""
    if language.startswith("fr"):
        return (
            "Tu analyses le texte suivant extrait d'un document pour un dossier de prêt. "
            "Réponds en JSON compact avec les clés: document_type (court), visible_text_summary (résumé), "
            "language_detected, confidence (high/medium/low). Pas de markdown, JSON seul.\n\n---\n"
        )
    return (
        "Analyze the following extracted document text for a loan application. "
        "Reply with compact JSON only, keys: document_type, visible_text_summary, "
        "language_detected, confidence (high/medium/low). No markdown.\n\n---\n"
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
            max_output_tokens=2048,
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
            max_tokens=1200,
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
            max_tokens=900,
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
            max_tokens=800,
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
