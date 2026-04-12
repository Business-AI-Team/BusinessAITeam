"""
Intelligent document analysis using microsoft/Florence-2-large (when available).

The processor loads the vision-language model via Hugging Face transformers + torch,
with a deterministic fallback (OCR-less metadata) when the model or GPU is unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from django.conf import settings

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


def analyze_document_image(
    image_path: str | Path,
    *,
    task_prompt: str = "<CAPTION>",
    language: str = "en",
) -> dict[str, Any]:
    """
    Run Florence-2 on an image and return structured analysis for the loan workflow.

    `language` affects only the fallback message text (UI/i18n), not the model weights.
    """
    path = Path(image_path)
    if not path.is_file():
        return _fallback_result("file_not_found", language)

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
