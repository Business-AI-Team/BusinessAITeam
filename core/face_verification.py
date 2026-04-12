"""
Face verification using DeepFace when available.

Falls back to a structured "skipped" result if DeepFace or dependencies are missing,
so the API and UI remain functional in hackathon/demo environments without GPU libs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _json_scalar(value: Any) -> Any:
    """DeepFace often returns numpy scalars; JSONField must get plain Python types."""
    if value is None or isinstance(value, (bool, str, int, float)):
        return value
    conv = getattr(value, "item", None)
    if callable(conv):
        try:
            return conv()
        except Exception:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def deepface_distance_to_similarity_percent(distance: Any, threshold: Any) -> float | None:
    """
    Map DeepFace distance + model threshold to a 0–100% *display* score (demo UX).

    Uses a linear scale: distance 0 → 100%, distance at threshold → ~50%,
    distance ≥ 2×threshold → 0%. This is illustrative, not a certified metric.
    """
    try:
        d = float(distance)
        t = float(threshold)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    pct = 100.0 * (1.0 - d / (2.0 * t))
    return round(max(0.0, min(100.0, pct)), 1)


def _try_import_deepface():
    try:
        from deepface import DeepFace  # type: ignore

        return DeepFace
    except ImportError:
        return None


def verify_faces(
    reference_image_path: str | Path,
    probe_image_path: str | Path,
    *,
    model_name: str = "VGG-Face",
    detector_backend: str = "opencv",
) -> dict[str, Any]:
    """
    Compare two face images (e.g. ID document crop vs selfie).

    Returns a dict with keys:
      - verified: bool
      - distance: float | None
      - model: str
      - error: str | None (if verification could not run)
    """
    ref = Path(reference_image_path)
    probe = Path(probe_image_path)
    DeepFace = _try_import_deepface()
    if DeepFace is None:
        logger.warning("DeepFace not installed; face verification skipped.")
        return {
            "verified": False,
            "distance": None,
            "model": model_name,
            "error": "deepface_not_installed",
            "skipped": True,
        }
    if not ref.is_file() or not probe.is_file():
        return {
            "verified": False,
            "distance": None,
            "model": model_name,
            "error": "missing_file",
            "skipped": True,
        }
    try:
        result = DeepFace.verify(
            img1_path=str(ref),
            img2_path=str(probe),
            model_name=model_name,
            detector_backend=detector_backend,
            enforce_detection=False,
        )
        verified = bool(result.get("verified"))
        distance = result.get("distance")
        raw = {
            k: _json_scalar(v)
            for k, v in result.items()
            if k in ("verified", "distance", "threshold")
        }
        msp = deepface_distance_to_similarity_percent(
            raw.get("distance"),
            raw.get("threshold"),
        )
        out = {
            "verified": verified,
            "distance": float(distance) if distance is not None else None,
            "model": model_name,
            "error": None,
            "skipped": False,
            "raw": raw,
        }
        if msp is not None:
            out["match_similarity_percent"] = msp
        return out
    except Exception as e:
        logger.exception("DeepFace verification failed: %s", e)
        return {
            "verified": False,
            "distance": None,
            "model": model_name,
            "error": str(e),
            "skipped": False,
        }


def analyze_face_attributes(image_path: str | Path) -> dict[str, Any]:
    """
    Optional demographic/analysis hook (not used for lending decisions by default).
    """
    DeepFace = _try_import_deepface()
    if DeepFace is None:
        return {"available": False, "reason": "deepface_not_installed"}
    try:
        # DeepFace.analyze returns list of dicts per detected face
        out = DeepFace.analyze(
            img_path=str(image_path),
            actions=["age", "gender", "race", "emotion"],
            enforce_detection=False,
        )
        return {"available": True, "result": out}
    except Exception as e:
        logger.exception("DeepFace analyze failed: %s", e)
        return {"available": False, "error": str(e)}
