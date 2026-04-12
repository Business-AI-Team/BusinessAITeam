"""
Intelligent document analysis using microsoft/Florence-2-large (when available).

The processor loads the vision-language model via Hugging Face transformers + torch,
with a deterministic fallback (OCR-less metadata) when the model or GPU is unavailable.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from django.conf import settings

from core.openai_config import get_openai_api_key

logger = logging.getLogger(__name__)

FLORENCE_MODEL_ID = "microsoft/Florence-2-large"


def _florence_available() -> bool:
    if not getattr(settings, "LOANWISE_ENABLE_FLORENCE", False):
        return False
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoProcessor  # noqa: F401

        return True
    except ImportError:
        return False


_florence_model = None
_florence_processor = None
_florence_device = None


def _get_florence():
    """Lazy singleton for Florence-2 to avoid loading on every request."""
    global _florence_model, _florence_processor, _florence_device
    if _florence_model is not None:
        return _florence_model, _florence_processor, _florence_device
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    logger.info("Loading Florence-2 on %s (%s)", device, FLORENCE_MODEL_ID)
    processor = AutoProcessor.from_pretrained(FLORENCE_MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        FLORENCE_MODEL_ID,
        trust_remote_code=True,
        torch_dtype=dtype,
    ).to(device)
    model.eval()
    _florence_model = model
    _florence_processor = processor
    _florence_device = device
    return _florence_model, _florence_processor, _florence_device


def _resolve_document_backend() -> str:
    """auto | openai | florence | fallback"""
    raw = getattr(settings, "LOANWISE_DOCUMENT_ANALYSIS_BACKEND", "auto") or "auto"
    raw = str(raw).strip().lower()
    if raw in ("openai", "florence", "fallback"):
        return raw
    # auto
    if getattr(settings, "OPENAI_API_KEY", ""):
        return "openai"
    if _florence_available():
        return "florence"
    return "fallback"


def _analyze_with_openai_vision(path: Path, language: str) -> dict[str, Any]:
    """Describe document / ID image via OpenAI Vision API."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback_result("openai_no_api_key", language, filename=path.name)
    try:
        import base64

        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; pip install openai")
        return _fallback_result("openai_not_installed", language, filename=path.name)

    try:
        data = path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mime = "image/jpeg"
        data_url = f"data:{mime};base64,{b64}"

        client = OpenAI(api_key=api_key)
        model = getattr(settings, "LOANWISE_OPENAI_VISION_MODEL", "gpt-4o-mini")
        if language.startswith("fr"):
            prompt = (
                "Tu analyses un document pour un dossier de prêt (CIN, justificatif, etc.). "
                "Réponds en JSON compact avec les clés: document_type (court), visible_text_summary (résumé du texte visible), "
                "language_detected, confidence (high/medium/low). Pas de markdown, JSON seul."
            )
        else:
            prompt = (
                "Analyze this image for a loan application (ID card, payslip, etc.). "
                "Reply with compact JSON only, keys: document_type, visible_text_summary, language_detected, "
                "confidence (high/medium/low). No markdown."
            )

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
    except Exception as e:
        logger.exception("OpenAI vision analysis failed: %s", e)
        return _fallback_result("openai_error", language, error=str(e), filename=path.name)


def analyze_document_image(
    image_path: str | Path,
    *,
    task_prompt: str = "<CAPTION>",
    language: str = "en",
) -> dict[str, Any]:
    """
    Analyze an image: OpenAI Vision (if configured), else Florence-2, else deterministic fallback.

    `language` affects prompts and fallback text (UI/i18n).
    """
    path = Path(image_path)
    if not path.is_file():
        return _fallback_result("file_not_found", language)

    backend = _resolve_document_backend()
    if backend == "openai":
        return _analyze_with_openai_vision(path, language)
    if backend == "fallback":
        logger.info("Document analysis backend is fallback (explicit).")
        return _fallback_result("model_unavailable", language, filename=path.name)

    if not _florence_available():
        logger.info("Florence/transformers not available; using fallback analysis.")
        return _fallback_result("model_unavailable", language, filename=path.name)

    try:
        from PIL import Image
        import torch

        model, processor, device = _get_florence()
        image = Image.open(path).convert("RGB")
        inputs = processor(
            text=task_prompt,
            images=image,
            return_tensors="pt",
        ).to(device, torch.float16 if device == "cuda" else torch.float32)
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=256,
            num_beams=3,
        )
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return {
            "engine": "florence-2-large",
            "caption": text.strip(),
            "task_prompt": task_prompt,
            "filename": path.name,
            "ok": True,
        }
    except Exception as e:
        logger.exception("Florence analysis failed: %s", e)
        return _fallback_result("inference_error", language, error=str(e), filename=path.name)


def _fallback_result(
    reason: str,
    language: str,
    *,
    filename: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Human-readable placeholders for demo / CI without heavy models."""
    if language.startswith("fr"):
        summary = (
            "Analyse locale (démonstration): le modèle Florence-2 n'est pas chargé "
            "ou une erreur s'est produite. Les métadonnées du fichier sont enregistrées."
        )
    else:
        summary = (
            "Local analysis (demo): Florence-2 is not loaded or an error occurred. "
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
