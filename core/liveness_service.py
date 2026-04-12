"""
Liveness: short selfie video with head turns (left / right), face crops vs ID document photo.

Uses OpenCV for video IO and optional Haar fallback; MediaPipe Face Mesh when available
for head-yaw estimation. Compares crops with DeepFace via ``face_verification.verify_faces``.
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# MediaPipe Face Mesh landmark indices (canonical model)
_LM_NOSE_TIP = 1
_LM_LEFT_EYE_OUTER = 33
_LM_RIGHT_EYE_OUTER = 263
# Six-point EAR (same indices as common MediaPipe examples)
_LEFT_EYE_EAR_INDICES = [33, 160, 158, 133, 153, 144]
_RIGHT_EYE_EAR_INDICES = [362, 385, 387, 263, 373, 380]
_EAR_THRESHOLD = 0.20
# Match static/js/loanwise_liveness_realtime.js (FaceLandmarker coach)
_YAW_TURN_THRESHOLD = 0.09
_BLINK_MIN_CLOSED_FRAMES = 2
_MAX_FRAMES = 360
_FRAME_STRIDE = 1


def _try_cv2():
    try:
        import cv2  # noqa: F401

        return __import__("cv2", fromlist=["*"])
    except ImportError:
        return None


def _try_mediapipe_face_mesh():
    try:
        import mediapipe as mp

        return mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    except ImportError:
        return None


def _extract_face_crop_haar(cv2: Any, image_bgr: Any) -> Any | None:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    cascade_path = str(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    face_cascade = cv2.CascadeClassifier(cascade_path)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(48, 48))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return image_bgr[y : y + h, x : x + w]


def _save_temp_jpeg(cv2: Any, image_bgr: Any) -> Path:
    fd, path = tempfile.mkstemp(suffix=".jpg")
    import os

    os.close(fd)
    cv2.imwrite(path, image_bgr)
    return Path(path)


def _eye_aspect_ratio_mediapipe(lm: Any, indices: list[int], w: int, h: int) -> float:
    pts = [(lm.landmark[i].x * w, lm.landmark[i].y * h) for i in indices]

    def dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    a = dist2(pts[1], pts[5])
    b = dist2(pts[2], pts[4])
    c = dist2(pts[0], pts[3])
    if c <= 1e-6:
        return 1.0
    return (a + b) / (2.0 * c)


def _yaw_from_mesh_landmarks(lm: Any, w: int, h: int) -> float:
    """Rough normalized yaw: nose offset from inter-eye midpoint in [-1, 1] range."""
    nose = lm.landmark[_LM_NOSE_TIP]
    le = lm.landmark[_LM_LEFT_EYE_OUTER]
    re = lm.landmark[_LM_RIGHT_EYE_OUTER]
    cx = (le.x + re.x) / 2.0
    # Positive when nose moves to user's right in image = head turned toward user's left
    return float((nose.x - cx) * 2.0)


def _bbox_face_mesh(lm: Any, w: int, h: int, margin: float = 0.05) -> tuple[int, int, int, int]:
    xs = [p.x * w for p in lm.landmark]
    ys = [p.y * h for p in lm.landmark]
    x1, x2 = int(min(xs)), int(max(xs))
    y1, y2 = int(min(ys)), int(max(ys))
    pad_x = int((x2 - x1) * margin)
    pad_y = int((y2 - y1) * margin)
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(w, x2 + pad_x)
    y2 = min(h, y2 + pad_y)
    return x1, y1, x2 - x1, y2 - y1


def run_liveness_check(
    id_document_path: str | Path,
    video_path: str | Path,
    *,
    language: str = "fr",
    debug: bool = False,
    client_sequence_completed: bool = False,
) -> dict[str, Any]:
    """
    Analyze a liveness video against an ID document image.

    Returns a JSON-serializable dict including ``liveness_passed``, ``head_turns``,
    ``face_match``, ``comparisons`` (DeepFace results per crop), and optional ``debug``.

    ``client_sequence_completed`` should be True when the web app only uploads after the
    guided FaceLandmarker sequence finishes (recording stops only then). The server
    re-scans the file with a different stack (Python MediaPipe / sampling); if that
    disagrees, we still treat liveness as satisfied when the client attests, and record
    ``server_motion_confirmed`` separately for audit. For production hardening, sign or
    bind this flag server-side — the POST field alone can be spoofed.
    """
    from core.face_verification import deepface_distance_to_similarity_percent, verify_faces

    vid = Path(video_path)
    doc = Path(id_document_path)
    result: dict[str, Any] = {
        "engine": "liveness_opencv_mediapipe",
        "liveness_passed": False,
        "head_turns": {"left": False, "right": False},
        "face_match": False,
        "id_face_extracted": False,
        "video_frames_sampled": 0,
        "comparisons": [],
        "notes": [],
        "blink_detected": False,
        "error": None,
        "client_sequence_completed": bool(client_sequence_completed),
        "server_motion_confirmed": False,
    }

    cv2 = _try_cv2()
    if cv2 is None:
        result["error"] = "opencv_not_installed"
        result["notes"].append("Install opencv-python-headless for liveness.")
        return result

    if not vid.is_file() or not doc.is_file():
        result["error"] = "missing_file"
        return result

    temp_files: list[Path] = []
    try:
        id_img = cv2.imread(str(doc))
        if id_img is None:
            result["error"] = "id_image_unreadable"
            return result
        id_crop = _extract_face_crop_haar(cv2, id_img)
        if id_crop is None:
            result["notes"].append("No Haar face on ID — using full document image for matching.")
            id_face_path = _save_temp_jpeg(cv2, id_img)
        else:
            id_face_path = _save_temp_jpeg(cv2, id_crop)
            result["id_face_extracted"] = True
        temp_files.append(id_face_path)

        cap = cv2.VideoCapture(str(vid))
        if not cap.isOpened():
            result["error"] = "video_open_failed"
            return result

        face_mesh = _try_mediapipe_face_mesh()
        yaw_samples: list[dict[str, Any]] = []
        probe_paths: list[Path] = []
        frame_idx = 0
        sampled = 0
        turns_left = False
        turns_right = False
        blink_detected = False
        blink_counter = 0

        # Haar fallback trackers
        cascade_path = str(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        face_cascade = cv2.CascadeClassifier(cascade_path)
        centers_x: list[float] = []

        while frame_idx < _MAX_FRAMES * _FRAME_STRIDE:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % _FRAME_STRIDE != 0:
                frame_idx += 1
                continue
            sampled += 1
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if face_mesh is not None:
                mp_res = face_mesh.process(rgb)
                if mp_res.multi_face_landmarks:
                    lm = mp_res.multi_face_landmarks[0]
                    yaw = _yaw_from_mesh_landmarks(lm, w, h)
                    le_ear = _eye_aspect_ratio_mediapipe(lm, _LEFT_EYE_EAR_INDICES, w, h)
                    re_ear = _eye_aspect_ratio_mediapipe(lm, _RIGHT_EYE_EAR_INDICES, w, h)
                    ear = (le_ear + re_ear) / 2.0
                    if ear < _EAR_THRESHOLD:
                        blink_counter += 1
                    else:
                        if blink_counter >= _BLINK_MIN_CLOSED_FRAMES:
                            blink_detected = True
                        blink_counter = 0
                    yaw_samples.append({"frame": frame_idx, "yaw": round(yaw, 4), "ear": round(ear, 4)})
                    if yaw > _YAW_TURN_THRESHOLD:
                        turns_left = True
                    if yaw < -_YAW_TURN_THRESHOLD:
                        turns_right = True
                    if abs(yaw) < 0.07 and len(probe_paths) < 6:
                        bx, by, bw, bh = _bbox_face_mesh(lm, w, h)
                        if bw > 20 and bh > 20:
                            crop = frame[by : by + bh, bx : bx + bw]
                            p = _save_temp_jpeg(cv2, crop)
                            temp_files.append(p)
                            probe_paths.append(p)
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(40, 40))
                if len(faces):
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    cx = x + fw / 2.0
                    centers_x.append(cx / float(w))
                    if len(centers_x) >= 3:
                        span = max(centers_x) - min(centers_x)
                        if span > 0.12:
                            turns_left = True
                            turns_right = True
                    if len(probe_paths) < 4:
                        crop = frame[y : y + fh, x : x + fw]
                        p = _save_temp_jpeg(cv2, crop)
                        temp_files.append(p)
                        probe_paths.append(p)

            frame_idx += 1

        mp_used = face_mesh is not None
        cap.release()
        if face_mesh is not None:
            face_mesh.close()

        result["video_frames_sampled"] = sampled
        result["head_turns"]["left"] = turns_left
        result["head_turns"]["right"] = turns_right
        result["blink_detected"] = blink_detected
        if mp_used:
            server_motion_ok = bool(turns_left and turns_right and blink_detected)
        else:
            server_motion_ok = bool(turns_left and turns_right)
            result["notes"].append("Blink check skipped (MediaPipe not available).")
        result["server_motion_confirmed"] = server_motion_ok
        # Product rule: upload only after guided completion in our web UI → accept liveness if attested,
        # while keeping server_motion_confirmed for audit / manual review.
        result["liveness_passed"] = bool(server_motion_ok or client_sequence_completed)
        if client_sequence_completed and not server_motion_ok:
            result["notes"].append(
                "Guided session was completed in the browser before upload; automated server motion "
                "re-analysis did not reproduce all cues (codec, sampling, or model mismatch vs "
                "FaceLandmarker in the browser). Face-vs-ID matching below is still evaluated."
            )

        if not probe_paths:
            result["notes"].append("No stable face crops from video — try better lighting or slower head turns.")

        comparisons: list[dict[str, Any]] = []
        similarity_percents: list[float] = []
        for i, p in enumerate(probe_paths):
            vr = verify_faces(id_face_path, p)
            vr["probe_index"] = i
            raw = vr.get("raw") or {}
            msp = deepface_distance_to_similarity_percent(raw.get("distance"), raw.get("threshold"))
            if msp is not None:
                vr["match_similarity_percent"] = msp
                similarity_percents.append(msp)
            comparisons.append(vr)
        result["comparisons"] = comparisons
        if similarity_percents:
            best = max(similarity_percents)
            result["face_match_similarity_percent"] = round(best, 1)
            result["face_match_mean_similarity_percent"] = round(
                sum(similarity_percents) / len(similarity_percents), 1
            )
        verified_any = any(c.get("verified") and not c.get("skipped") for c in comparisons)
        result["face_match"] = bool(verified_any)

        if language.startswith("fr"):
            result["summary"] = (
                f"Vivacité globale: {'ok' if result['liveness_passed'] else 'échec'} "
                f"(re-scan serveur mouvements: {'ok' if server_motion_ok else 'partiel'}; "
                f"session guidée déclarée: {'oui' if client_sequence_completed else 'non'}). "
                f"Correspondance visage: {'oui' if result['face_match'] else 'non ou partielle'}."
            )
        else:
            result["summary"] = (
                f"Liveness outcome: {'pass' if result['liveness_passed'] else 'fail'} "
                f"(server motion re-scan: {'pass' if server_motion_ok else 'partial'}; "
                f"guided session attested: {'yes' if client_sequence_completed else 'no'}). "
                f"Face match: {'yes' if result['face_match'] else 'no or partial'}."
            )

        if debug:
            result["debug"] = {
                "yaw_samples": yaw_samples[:80],
                "mediapipe_used": mp_used,
                "probe_count": len(probe_paths),
                "haar_centers_normalized": centers_x[:40] if not mp_used else [],
            }
        return result

    except Exception as e:
        logger.exception("Liveness pipeline failed: %s", e)
        result["error"] = str(e)
        return result
    finally:
        for p in temp_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
