"""Option B1 — Tier-2 Pretrained Server Verifier (YOLO HD + MediaPipe FaceLandmarker HD + Optional ONNX Hook).

Designed for the AI Team (folder CNN_IOT/) to customize, tune thresholds, swap pretrained weights,
or plug in a custom multi-class/face-crop ONNX model WITHOUT changing Pi Edge, ESP32 firmware,
or server_ai REST/DB contracts.

Activation in server_ai:
    Set environment variable: TIER2_VERIFIER_MODE=b1
    (Default server mode remains 'auto' -> ONNX if present, else TemporalBaselineVerifier.)

Thread-Safety:
    DeepVerifierEngine runs multiple worker threads (default 2). Every ML backend call
    (MediaPipe, YOLO, cv2.dnn ONNX) is serialized behind dedicated threading.Lock() instances.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

CNN_IOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CNN_IOT_DIR.parent
DEFAULT_CONFIG_PATH = CNN_IOT_DIR / "verifier_config.json"

# Standard MediaPipe 468/478 FaceMesh landmark indices for EAR, MAR, and Yaw proxy
LEFT_EYE_IDX = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_IDX = (362, 385, 387, 263, 373, 380)
MOUTH_IDX = (61, 291, 13, 14)       # left corner, right corner, inner top lip, inner bottom lip
NOSE_TIP_IDX = 1
LEFT_CHEEK_IDX = 234
RIGHT_CHEEK_IDX = 454


@dataclass
class B1Verdict:
    """Drop-in compatible with server_ai.server_core.deep_verifier.VerificationVerdict."""
    status: str          # SERVER_CNN_CONFIRMED | SERVER_CNN_REJECTED
    confidence: float
    model_version: str
    latency_ms: float
    detail: str


def load_b1_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _dist2d(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def compute_ear(landmarks: Sequence[Tuple[float, float]]) -> float:
    """Compute mean Eye Aspect Ratio (EAR) from 2D facial landmarks."""
    def _eye(idx: Tuple[int, int, int, int, int, int]) -> float:
        p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in idx)
        horiz = _dist2d(p1, p4)
        if horiz <= 1e-6:
            return 0.35
        return (_dist2d(p2, p6) + _dist2d(p3, p5)) / (2.0 * horiz)
    return 0.5 * (_eye(LEFT_EYE_IDX) + _eye(RIGHT_EYE_IDX))


def compute_mar(landmarks: Sequence[Tuple[float, float]]) -> float:
    """Compute Mouth Aspect Ratio (MAR = vertical inner lip / horizontal mouth width)."""
    left, right, top, bot = (landmarks[i] for i in MOUTH_IDX)
    width = _dist2d(left, right)
    if width <= 1e-6:
        return 0.0
    return _dist2d(top, bot) / width


def compute_yaw_asymmetry(landmarks: Sequence[Tuple[float, float]]) -> float:
    """Compute roll-invariant normalized horizontal nose-offset ratio [0.0=frontal .. ~0.5+=turned side]."""
    nose = landmarks[NOSE_TIP_IDX]
    l_cheek = landmarks[LEFT_CHEEK_IDX]
    r_cheek = landmarks[RIGHT_CHEEK_IDX]
    dx = r_cheek[0] - l_cheek[0]
    dy = r_cheek[1] - l_cheek[1]
    face_w = math.hypot(dx, dy)
    if face_w <= 1e-6:
        return 0.0
    mid_x = 0.5 * (l_cheek[0] + r_cheek[0])
    mid_y = 0.5 * (l_cheek[1] + r_cheek[1])
    # Project nose offset onto cheek-to-cheek unit axis (roll-invariant!)
    proj = ((nose[0] - mid_x) * dx + (nose[1] - mid_y) * dy) / face_w
    return abs(proj) / face_w


def compute_pitch_ratio(landmarks: Sequence[Tuple[float, float]]) -> float:
    """Compute normalized vertical nose-offset deviation from eye-mouth midpoint (detects looking down/up)."""
    nose = landmarks[NOSE_TIP_IDX]
    l_eye = landmarks[LEFT_EYE_IDX[0]]
    r_eye = landmarks[RIGHT_EYE_IDX[0]]
    mouth_top = landmarks[MOUTH_IDX[2]]
    mouth_bot = landmarks[MOUTH_IDX[3]]
    eye_mid_y = 0.5 * (l_eye[1] + r_eye[1])
    mouth_mid_y = 0.5 * (mouth_top[1] + mouth_bot[1])
    span = abs(mouth_mid_y - eye_mid_y)
    if span <= 1e-6:
        return 0.0
    # Frontal nose sits around ~0.55 of eye->mouth vertical span; looking down/up shifts ratio significantly
    return abs(((nose[1] - eye_mid_y) / span) - 0.55)


def _resolve_first_existing(candidates: Sequence[str]) -> Optional[Path]:
    for raw in candidates:
        p = Path(raw)
        if not p.is_absolute():
            p_repo = REPO_ROOT / p
            if p_repo.exists():
                return p_repo
            p_local = CNN_IOT_DIR / p
            if p_local.exists():
                return p_local
        elif p.exists():
            return p
    return None


def _normalize_alert_family(alert_type: str) -> str:
    raw = str(alert_type or "").strip().upper()
    if raw in ("DROWSY", "DROWSY_ALERT", "EYE_CLOSED", "MICROSLEEP"):
        return "DROWSY"
    if raw in ("YAWNING", "YAWNING_ALERT", "MOUTH_OPEN", "YAWNING_FATIGUE"):
        return "YAWNING"
    if raw in ("DISTRACTED", "DISTRACTED_ALERT", "DISTRACTED HEAD"):
        return "DISTRACTED"
    if raw in ("PHONE", "PHONE_DISTRACTION", "CELL PHONE"):
        return "PHONE"
    if raw in ("SMOKING", "CIGARETTE"):
        return "SMOKING"
    return raw


class _MediaPipeFaceBackend:
    """Thread-safe MediaPipe FaceLandmarker wrapper (lazy-loaded)."""

    def __init__(self, task_path: Optional[Path]) -> None:
        self._task_path = task_path
        self._lock = threading.Lock()
        self._landmarker = None
        self._init_failed = False

    @property
    def available(self) -> bool:
        return self._task_path is not None and self._task_path.exists() and not self._init_failed

    def _ensure_loaded(self) -> bool:
        if self._landmarker is not None:
            return True
        if self._init_failed or not self._task_path or not self._task_path.exists():
            return False
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            opts = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(self._task_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_faces=1,
            )
            self._landmarker = mp_vision.FaceLandmarker.create_from_options(opts)
            return True
        except Exception:
            self._init_failed = True
            return False

    def analyze_frame_bgr(self, img_bgr: Any) -> Optional[Dict[str, float]]:
        with self._lock:
            if not self._ensure_loaded():
                return None
            import cv2
            import mediapipe as mp

            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            res = self._landmarker.detect(mp_img)
            if not res.face_landmarks:
                return None
            pts = [(lm.x, lm.y) for lm in res.face_landmarks[0]]
            if len(pts) <= RIGHT_CHEEK_IDX:
                return None
            return {
                "ear": round(compute_ear(pts), 4),
                "mar": round(compute_mar(pts), 4),
                "yaw_ratio": round(compute_yaw_asymmetry(pts), 4),
                "pitch_ratio": round(compute_pitch_ratio(pts), 4),
            }


class _YoloHdBackend:
    """Thread-safe Ultralytics YOLO HD wrapper (imgsz=640 on Server PC vs 320 on Pi 4)."""

    def __init__(self, weights_path: Optional[Path], imgsz: int = 640) -> None:
        self._weights_path = weights_path
        self._imgsz = int(imgsz)
        self._lock = threading.Lock()
        self._model = None
        self._init_failed = False

    @property
    def available(self) -> bool:
        return self._weights_path is not None and self._weights_path.exists() and not self._init_failed

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._init_failed or not self._weights_path or not self._weights_path.exists():
            return False
        try:
            from ultralytics import YOLO
            self._model = YOLO(str(self._weights_path))
            return True
        except Exception:
            self._init_failed = True
            return False

    def detect_frame_bgr(self, img_bgr: Any) -> List[Tuple[str, float]]:
        with self._lock:
            if not self._ensure_loaded():
                return []
            results = self._model.predict(img_bgr, imgsz=self._imgsz, verbose=False)
            out: List[Tuple[str, float]] = []
            for r in results:
                names = r.names or {}
                boxes = getattr(r, "boxes", None)
                if boxes is None:
                    continue
                for cls_t, conf_t in zip(boxes.cls.tolist(), boxes.conf.tolist()):
                    label = str(names.get(int(cls_t), str(int(cls_t)))).strip().lower()
                    out.append((label, float(conf_t)))
            return out


class PretrainedServerVerifier:
    """Option B1 Tier-2 Verifier: per-alert_type verification using HD FaceLandmarker + YOLO HD.

    Teammate AI engineers can:
      1. Tune thresholds in CNN_IOT/verifier_config.json
      2. Drop stronger server weights into CNN_IOT/models/server_yolo.pt (e.g. YOLOv8m/l)
      3. Inject custom face_analyzer / yolo_detector / onnx_scorer in unit tests or custom pipelines
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        face_analyzer: Optional[Callable[[Any], Optional[Dict[str, float]]]] = None,
        yolo_detector: Optional[Callable[[Any], List[Tuple[str, float]]]] = None,
        onnx_scorer: Optional[Callable[[List[Any], str], Optional[float]]] = None,
    ) -> None:
        self.cfg = load_b1_config(config_path)
        models_cfg = self.cfg.get("models") or {}
        self._face_backend = None
        self._yolo_backend = None

        if face_analyzer is not None:
            self._face_fn = face_analyzer
            face_tag = "face-injected"
        else:
            task_path = _resolve_first_existing(models_cfg.get("face_landmarker_task") or [])
            self._face_backend = _MediaPipeFaceBackend(task_path)
            self._face_fn = self._face_backend.analyze_frame_bgr if self._face_backend.available else None
            face_tag = task_path.name if task_path else "no-face-model"

        if yolo_detector is not None:
            self._yolo_fn = yolo_detector
            yolo_tag = "yolo-injected"
        else:
            yolo_path = _resolve_first_existing(models_cfg.get("yolo_weights") or [])
            imgsz = int(models_cfg.get("yolo_imgsz") or 640)
            self._yolo_backend = _YoloHdBackend(yolo_path, imgsz=imgsz)
            self._yolo_fn = self._yolo_backend.detect_frame_bgr if self._yolo_backend.available else None
            yolo_tag = f"{yolo_path.name}@{imgsz}" if yolo_path else "no-yolo-model"

        self._onnx_fn = onnx_scorer
        self._non_penalty = {str(x).strip().upper() for x in (self.cfg.get("non_penalty_alert_types") or [])}
        self.model_version = f"b1-pretrained:{yolo_tag}+{face_tag}"

    def _make_verdict(self, status: str, conf: float, t0: float, detail: str) -> Any:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        try:
            from server_ai.server_core.deep_verifier import VerificationVerdict
            return VerificationVerdict(
                status=status,
                confidence=round(max(0.0, min(1.0, float(conf))), 3),
                model_version=self.model_version,
                latency_ms=latency_ms,
                detail=detail,
            )
        except Exception:
            return B1Verdict(
                status=status,
                confidence=round(max(0.0, min(1.0, float(conf))), 3),
                model_version=self.model_version,
                latency_ms=latency_ms,
                detail=detail,
            )

    def _load_valid_frames(self, frame_paths: List[Path]) -> Tuple[List[Any], int, int]:
        import cv2
        import numpy as np

        max_f = int(self.cfg.get("max_frames_per_burst") or 16)
        min_luma = float(self.cfg.get("min_luma_non_black") or 8.0)
        # Uniformly sample across the entire 10s burst instead of slicing only the first [:16] frames (fixes F-018)
        if len(frame_paths) > max_f > 0:
            step = (len(frame_paths) - 1) / float(max(1, max_f - 1))
            selected = [frame_paths[int(round(i * step))] for i in range(max_f)]
        else:
            selected = list(frame_paths)
        imgs: List[Any] = []
        valid_count = 0
        non_black = 0
        for fp in selected:
            if not fp.exists() or fp.stat().st_size < 100:
                continue
            img = cv2.imread(str(fp))
            if img is None or img.size == 0:
                continue
            valid_count += 1
            if float(np.mean(img)) > min_luma:
                non_black += 1
                imgs.append(img)
        return imgs, valid_count, non_black

    def verify_burst(self, frame_paths: List[Path], meta: Dict[str, Any]) -> Any:
        t0 = time.perf_counter()
        raw_alert = str(meta.get("alert_type") or "").strip().upper()
        family = _normalize_alert_family(raw_alert)
        duration_s = float(meta.get("duration_seconds") or 0.0)
        edge_conf = float(meta.get("confidence") or 0.75)

        # 1) Advisory / Emergency / Hardware events: passthrough (Server NEVER deducts safety_score!)
        if raw_alert in self._non_penalty:
            return self._make_verdict(
                "SERVER_CNN_CONFIRMED",
                max(0.85, edge_conf),
                t0,
                f"Advisory/non-penalty event ({raw_alert}) verified without driver penalty",
            )

        # 2) Signal quality & temporal sanity gate
        min_dur = float(self.cfg.get("min_temporal_duration_s") or 0.35)
        if duration_s < min_dur and family in ("DROWSY", "YAWNING"):
            return self._make_verdict(
                "SERVER_CNN_REJECTED",
                0.20,
                t0,
                f"Transient spike rejected ({duration_s:.2f}s < {min_dur:.2f}s)",
            )

        imgs, valid_count, non_black = self._load_valid_frames(frame_paths)
        if len(frame_paths) > 0 and valid_count == 0:
            return self._make_verdict("SERVER_CNN_REJECTED", 0.05, t0, "All burst frames corrupt/unreadable")
        if len(frame_paths) > 0 and non_black == 0:
            return self._make_verdict("SERVER_CNN_REJECTED", 0.10, t0, "All burst frames black/occluded")
        if not imgs:
            return self._make_verdict(
                "SERVER_CNN_CONFIRMED",
                round(max(0.55, edge_conf), 3),
                t0,
                f"Metadata-only event ({family}) accepted (0 frames attached)",
            )

        th = self.cfg.get("thresholds") or {}
        aliases = self.cfg.get("class_aliases") or {}

        # 3) Optional custom ONNX hook (if teammate enabled custom_tier2.onnx)
        if self._onnx_fn is not None:
            prob = self._onnx_fn(imgs, family)
            if prob is not None:
                p_min = float(th.get("custom_onnx_prob_min") or 0.50)
                st = "SERVER_CNN_CONFIRMED" if prob >= p_min else "SERVER_CNN_REJECTED"
                return self._make_verdict(st, prob, t0, f"Custom ONNX scorer ({family}): prob={prob:.3f} (th={p_min})")

        # 4) Face-geometry behaviors (DROWSY / YAWNING / DISTRACTED) -> MediaPipe FaceLandmarker HD
        if family in ("DROWSY", "YAWNING", "DISTRACTED") and self._face_fn is not None:
            metrics = [m for m in (self._face_fn(im) for im in imgs) if m is not None]
            if metrics:
                n_face = len(metrics)
                if family == "DROWSY":
                    ear_max = float(th.get("drowsy_ear_max") or 0.21)
                    ratio_min = float(th.get("drowsy_min_frame_ratio") or 0.45)
                    hits = sum(1 for m in metrics if m["ear"] <= ear_max)
                    ratio = hits / n_face
                    mean_ear = sum(m["ear"] for m in metrics) / n_face
                    ok = ratio >= ratio_min
                    conf = (0.55 + 0.44 * ratio) if ok else max(0.08, 0.45 * ratio)
                    return self._make_verdict(
                        "SERVER_CNN_CONFIRMED" if ok else "SERVER_CNN_REJECTED",
                        conf,
                        t0,
                        f"B1 FaceHD DROWSY: EAR_mean={mean_ear:.3f} <= {ear_max} on {hits}/{n_face} frames (ratio={ratio:.2f})",
                    )
                if family == "YAWNING":
                    mar_min = float(th.get("yawning_mar_min") or 0.55)
                    ratio_min = float(th.get("yawning_min_frame_ratio") or 0.35)
                    hits = sum(1 for m in metrics if m["mar"] >= mar_min)
                    ratio = hits / n_face
                    mean_mar = sum(m["mar"] for m in metrics) / n_face
                    ok = ratio >= ratio_min
                    conf = (0.55 + 0.44 * ratio) if ok else max(0.08, 0.45 * ratio)
                    return self._make_verdict(
                        "SERVER_CNN_CONFIRMED" if ok else "SERVER_CNN_REJECTED",
                        conf,
                        t0,
                        f"B1 FaceHD YAWNING: MAR_mean={mean_mar:.3f} >= {mar_min} on {hits}/{n_face} frames (ratio={ratio:.2f})",
                    )
                if family == "DISTRACTED":
                    yaw_min = float(th.get("distracted_yaw_ratio_min") or 0.34)
                    pitch_min = float(th.get("distracted_pitch_ratio_min") or 0.32)
                    ratio_min = float(th.get("distracted_min_frame_ratio") or 0.45)
                    hits = sum(
                        1
                        for m in metrics
                        if (m["yaw_ratio"] >= yaw_min or float(m.get("pitch_ratio", 0.0)) >= pitch_min)
                    )
                    ratio = hits / n_face
                    mean_yaw = sum(m["yaw_ratio"] for m in metrics) / n_face
                    mean_pitch = sum(float(m.get("pitch_ratio", 0.0)) for m in metrics) / n_face
                    ok = ratio >= ratio_min
                    conf = (0.55 + 0.44 * ratio) if ok else max(0.08, 0.45 * ratio)
                    return self._make_verdict(
                        "SERVER_CNN_CONFIRMED" if ok else "SERVER_CNN_REJECTED",
                        conf,
                        t0,
                        f"B1 FaceHD DISTRACTED: yaw={mean_yaw:.3f}/pitch={mean_pitch:.3f} on {hits}/{n_face} frames (ratio={ratio:.2f})",
                    )

        # 5) Object/behavior detection (PHONE / SMOKING or fallback when face landmarks unavailable) -> YOLO HD
        if self._yolo_fn is not None and family in aliases:
            target_labels = {str(x).strip().lower() for x in aliases[family]}
            conf_min = float(th.get(f"{family.lower()}_conf_min") or 0.40)
            ratio_min = float(th.get(f"{family.lower()}_min_frame_ratio") or 0.25)
            hits = 0
            best_confs: List[float] = []
            for im in imgs:
                dets = self._yolo_fn(im)
                matched = [c for lbl, c in dets if lbl in target_labels and c >= conf_min]
                if matched:
                    hits += 1
                    best_confs.append(max(matched))
            ratio = hits / max(1, len(imgs))
            ok = ratio >= ratio_min
            mean_c = (sum(best_confs) / len(best_confs)) if best_confs else 0.12
            conf = min(0.99, 0.5 * mean_c + 0.5 * ratio) if ok else max(0.08, 0.4 * ratio)
            return self._make_verdict(
                "SERVER_CNN_CONFIRMED" if ok else "SERVER_CNN_REJECTED",
                conf,
                t0,
                f"B1 YoloHD {family}: matched {hits}/{len(imgs)} frames (ratio={ratio:.2f}, mean_conf={mean_c:.2f})",
            )

        # 6) Safe fallback when ML backends unavailable on host
        frame_ratio = non_black / max(1, len(frame_paths))
        conf = round(min(0.99, max(0.55, 0.6 * edge_conf + 0.4 * frame_ratio)), 3)
        return self._make_verdict(
            "SERVER_CNN_CONFIRMED",
            conf,
            t0,
            f"B1 fallback temporal pass ({non_black}/{len(frame_paths)} valid frames, duration={duration_s:.2f}s)",
        )
