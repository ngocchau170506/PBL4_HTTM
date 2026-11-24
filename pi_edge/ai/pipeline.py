"""Lean, Async Edge AI Pipeline for Raspberry Pi 4 (Tier-1 Safety Reflex).

Key optimizations over IOTv5:
  - Unified CPU thread budget (torch=2, cv2=1, never oversubscribes 3 pinned cores)
  - Event-driven 1-slot async worker (no 10Hz idle spin)
  - Bounded Tier-2 tilt pass (max 1 roll-compensated pass, eliminates 750ms triple-YOLO stall)
  - Raw clean frames preserved for JPEG burst (ZERO HUD overlays burned into evidence!)
  - Thread-safe live behavior permissions (allow_smoking / allow_phone / conf thresholds)
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from pi_edge.ai.face_geometry import (
    BlazeFaceGeometry,
    HeadPoseEstimate,
    evaluate_mouth_yawn_geometry,
    rotate_roi_replicate,
    unrotate_bbox_in_roi,
)
from pi_edge.ai.sensor_fusion import FusionDecision, SensorFusion
from pi_edge.core.config import EdgeConfig
from pi_edge.core.telemetry import ThrottlePlan

# Per-class minimum confidence thresholds (fixes F-008 weak conf=0.15 false alarms!)
CLASS_MIN_CONF: Dict[str, float] = {
    "Eye_Closed": 0.35,
    "Eye_Open": 0.25,
    "Mouth_Open": 0.35,
    "Distracted Head": 0.35,
    "Normal": 0.25,
}
FACE_RELATED_CLASSES = frozenset({"Eye_Closed", "Eye_Open", "Mouth_Open", "Distracted Head", "Normal"})


@dataclass
class InferenceOutput:
    ts_mono: float
    detected_classes: List[str] = field(default_factory=list)
    detections: List[Tuple[str, float, Tuple[int, int, int, int]]] = field(default_factory=list)
    face_detected: bool = False
    pose: Optional[HeadPoseEstimate] = None
    eye_closed: Optional[bool] = None
    mouth_yawn: Optional[bool] = None
    distracted: Optional[bool] = None
    smoking: Optional[bool] = None
    phone: Optional[bool] = None
    channel_confidences: Dict[str, float] = field(default_factory=dict)
    behavior_detail: str = ""
    latency_ms: float = 0.0
    verification_mode: str = "SINGLE_MODEL_YOLO"


class EdgeAiPipeline:
    def __init__(self, cfg: EdgeConfig, yolo_model: Any = None, face_geo: Optional[BlazeFaceGeometry] = None):
        self.cfg = cfg
        self._perm_lock = threading.Lock()
        self.allow_smoking = cfg.allow_smoking
        self.allow_phone = cfg.allow_phone
        self.smoking_conf = cfg.smoking_conf
        self.phone_conf = cfg.phone_conf

        self.fusion = SensorFusion(
            eye_threshold_s=cfg.eye_threshold_s,
            yawn_threshold_s=cfg.yawn_threshold_s,
            distracted_threshold_s=cfg.distracted_threshold_s,
            max_blind_hold_s=cfg.blind_hold_max_s,
        )

        # Unified thread budget on Pi 4
        try:
            cv2.setNumThreads(cfg.opencv_threads)
        except Exception:
            pass
        try:
            import torch
            torch.set_num_threads(cfg.torch_threads)
        except Exception:
            pass

        self._yolo = yolo_model
        if self._yolo is None and not cfg.disable_ai:
            if os.path.exists(cfg.yolo_model_path):
                try:
                    from ultralytics import YOLO
                    self._yolo = YOLO(cfg.yolo_model_path)
                except Exception as exc:
                    import sys
                    print(f"[PI_AI AI CRITICAL] Failed to load YOLO model {cfg.yolo_model_path}: {exc}", file=sys.stderr)
                    self._yolo = None
            else:
                import sys
                msg = f"[PI_AI AI CRITICAL] YOLO model NOT FOUND at {cfg.yolo_model_path} — Tier-1 AI is blind!"
                print(msg, file=sys.stderr)
                if os.environ.get("FAIL_ON_MISSING_MODEL", "0").strip().lower() in ("1", "true", "yes"):
                    raise RuntimeError(msg)
        self.model_loaded = (self._yolo is not None)

        self._geo = face_geo if face_geo is not None else BlazeFaceGeometry(cfg.landmarker_path)

        # Event-driven 1-slot async inference worker
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._pending_task: Optional[Tuple[np.ndarray, ThrottlePlan]] = None
        self._latest_infer: Optional[InferenceOutput] = None
        self._last_consumed_infer_ts: float = 0.0
        self._last_submit_mono = 0.0
        self.worker_errors = 0
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="ai-infer-worker", daemon=True)
        self._worker.start()

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None

    def set_behavior_permissions(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._perm_lock:
            if "allow_smoking" in patch:
                self.allow_smoking = bool(patch["allow_smoking"])
            if "allow_phone" in patch:
                self.allow_phone = bool(patch["allow_phone"])
            if "smoking_conf_threshold" in patch:
                self.smoking_conf = max(0.1, min(0.95, float(patch["smoking_conf_threshold"])))
            if "phone_conf_threshold" in patch:
                self.phone_conf = max(0.1, min(0.95, float(patch["phone_conf_threshold"])))
            return {
                "allow_smoking": self.allow_smoking,
                "allow_phone": self.allow_phone,
                "smoking_conf_threshold": self.smoking_conf,
                "phone_conf_threshold": self.phone_conf,
            }

    def get_behavior_permissions(self) -> Dict[str, Any]:
        with self._perm_lock:
            return {
                "allow_smoking": self.allow_smoking,
                "allow_phone": self.allow_phone,
                "smoking_conf_threshold": self.smoking_conf,
                "phone_conf_threshold": self.phone_conf,
            }

    def submit_if_due(self, frame_bgr: np.ndarray, plan: ThrottlePlan, now_mono: float) -> bool:
        if self.cfg.disable_ai or self._yolo is None:
            return False
        if (now_mono - self._last_submit_mono) < plan.ai_min_interval_s:
            return False
        with self._cond:
            if self._pending_task is not None:
                return False
            self._pending_task = (frame_bgr, plan)
            self._last_submit_mono = now_mono
            self._cond.notify()
            return True

    def _run_inference(self, frame_bgr: np.ndarray, plan: ThrottlePlan) -> InferenceOutput:
        t0 = time.perf_counter()
        perms = self.get_behavior_permissions()
        pose: Optional[HeadPoseEstimate] = None
        if plan.allow_face_geometry and self._geo.available:
            pose = self._geo.detect_pose(frame_bgr)

        dets: List[Tuple[str, float, Tuple[int, int, int, int]]] = []
        if self._yolo is not None:
            res = self._yolo.predict(
                frame_bgr,
                imgsz=plan.yolo_imgsz,
                conf=0.15,
                max_det=self.cfg.yolo_max_det,
                verbose=False,
            )
            if res and len(res) > 0 and res[0].boxes is not None:
                names = res[0].names or {}
                for b in res[0].boxes:
                    cls_id = int(b.cls[0])
                    label = str(names.get(cls_id, cls_id))
                    conf = float(b.conf[0])
                    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                    if label.lower() == "smoking":
                        if not perms["allow_smoking"] or conf < perms["smoking_conf_threshold"]:
                            continue
                    elif label.lower() == "phone":
                        if not perms["allow_phone"] or conf < perms["phone_conf_threshold"]:
                            continue
                    else:
                        min_c = CLASS_MIN_CONF.get(label, 0.25)
                        if conf < min_c:
                            continue
                    dets.append((label, conf, (x1, y1, x2, y2)))

            # Bounded Tier-2: MAX 1 roll-compensated pass only when BlazeFace confirms tilted head (>18 deg) and 0 eyes detected
            has_eye = any(lbl in ("Eye_Closed", "Eye_Open") for lbl, _, _ in dets)
            if not has_eye and pose is not None and pose.valid and abs(pose.roll_deg) >= 18.0 and pose.face_box:
                fx1, fy1, fx2, fy2 = pose.face_box
                roi = frame_bgr[max(0, fy1):max(1, fy2), max(0, fx1):max(1, fx2)]
                if roi.size > 0 and roi.shape[0] >= 24 and roi.shape[1] >= 24:
                    # OpenCV positive angle rotates CCW, compensating CW (+roll_deg) head tilt (fixes BUG-11!)
                    rot = rotate_roi_replicate(roi, +pose.roll_deg)
                    r2 = self._yolo.predict(rot, imgsz=256, conf=0.25, max_det=4, verbose=False)
                    if r2 and len(r2) > 0 and r2[0].boxes is not None:
                        names2 = r2[0].names or {}
                        roi_h, roi_w = roi.shape[:2]
                        for b in r2[0].boxes:
                            lbl2 = str(names2.get(int(b.cls[0]), ""))
                            c2 = float(b.conf[0])
                            if c2 < CLASS_MIN_CONF.get(lbl2, 0.30):
                                continue
                            if lbl2 in ("Eye_Closed", "Eye_Open"):
                                dets.append((lbl2, c2, pose.face_box))
                            elif lbl2 == "Mouth_Open":
                                # Inverse-rotate mouth box back into unrotated face coordinates (fixes F-017!)
                                mx1, my1, mx2, my2 = [int(v) for v in b.xyxy[0].tolist()]
                                ux1, uy1, ux2, uy2 = unrotate_bbox_in_roi((mx1, my1, mx2, my2), roi_w, roi_h, +pose.roll_deg)
                                dets.append((lbl2, c2, (fx1 + ux1, fy1 + uy1, fx1 + ux2, fy1 + uy2)))

        labels = [d[0] for d in dets]
        face_detected = bool((pose and pose.valid) or any(l in FACE_RELATED_CLASSES for l in labels))

        # Conflict resolution by max confidence: weak Eye_Closed must NOT override strong Eye_Open (fixes F-008!)
        closed_confs = [c for lbl, c, _ in dets if lbl == "Eye_Closed"]
        open_confs = [c for lbl, c, _ in dets if lbl in ("Eye_Open", "Normal")]
        eye_closed: Optional[bool] = None
        eye_conf = 0.0
        if closed_confs and open_confs:
            if max(closed_confs) >= max(open_confs):
                eye_closed = True
                eye_conf = max(closed_confs)
            else:
                eye_closed = False
                eye_conf = max(open_confs)
        elif closed_confs:
            eye_closed = True
            eye_conf = max(closed_confs)
        elif open_confs:
            eye_closed = False
            eye_conf = max(open_confs)

        mouth_yawn: Optional[bool] = None
        mouth_detail = ""
        mouth_conf = 0.0
        mouth_dets = sorted([d for d in dets if d[0] == "Mouth_Open"], key=lambda x: x[1], reverse=True)
        if mouth_dets:
            mouth_conf = float(mouth_dets[0][1])
            is_yawn, mouth_detail = evaluate_mouth_yawn_geometry(
                mouth_dets[0][2],
                pose.face_box if (pose and pose.valid) else None,
                yaw_deg=pose.yaw_deg if (pose and pose.valid) else 0.0,
            )
            mouth_yawn = is_yawn
        elif face_detected:
            mouth_yawn = False

        dist_confs = [c for lbl, c, _ in dets if lbl == "Distracted Head"]
        pose_dist = bool(pose and pose.valid and (abs(pose.yaw_deg) >= 32.0 or abs(pose.pitch_deg) >= 26.0))
        distracted: Optional[bool] = None
        dist_conf = 0.0
        if dist_confs or pose_dist:
            distracted = True
            dist_conf = max(dist_confs) if dist_confs else float(pose.score if pose else 0.70)
        elif face_detected:
            distracted = False

        smoke_confs = [c for lbl, c, _ in dets if lbl.lower() == "smoking"]
        phone_confs = [c for lbl, c, _ in dets if lbl.lower() == "phone"]
        # Require a detected driver face before triggering PHONE / SMOKING (blocks empty-cabin background hallucinations!)
        smoking = True if (face_detected and smoke_confs) else (False if face_detected else False)
        phone = True if (face_detected and phone_confs) else (False if face_detected else False)
        ver_mode = "YOLO_BLAZEFACE_SEQ" if (pose and pose.valid) else "SINGLE_MODEL_YOLO"

        ch_confs = {
            "DROWSY": round(eye_conf, 3),
            "YAWNING": round(mouth_conf, 3),
            "DISTRACTED": round(dist_conf, 3),
            "SMOKING": round(max(smoke_confs), 3) if smoke_confs else 0.0,
            "PHONE": round(max(phone_confs), 3) if phone_confs else 0.0,
        }

        return InferenceOutput(
            ts_mono=time.monotonic(),
            detected_classes=labels,
            detections=dets,
            face_detected=face_detected,
            pose=pose,
            eye_closed=eye_closed,
            mouth_yawn=mouth_yawn,
            distracted=distracted,
            smoking=smoking,
            phone=phone,
            channel_confidences=ch_confs,
            behavior_detail=mouth_detail,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            verification_mode=ver_mode,
        )

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while self._pending_task is None and not self._stop.is_set():
                    self._cond.wait(timeout=1.0)
                if self._stop.is_set():
                    break
                task = self._pending_task
                self._pending_task = None
            if task is not None:
                frame, plan = task
                try:
                    out = self._run_inference(frame, plan)
                    with self._lock:
                        self._latest_infer = out
                except Exception as exc:
                    self.worker_errors += 1
                    import sys
                    print(f"[PI_AI AI WORKER ERR #{self.worker_errors}] {exc}", file=sys.stderr)

    def get_latest_overlay(self) -> Dict[str, Any]:
        """Thread-safe snapshot of latest AI boxes/pose for on-demand live camera HUD (0 cost if unused)."""
        with self._lock:
            inf = self._latest_infer
        if inf is None:
            return {"valid": False, "detections": [], "pose": None, "face_detected": False}
        pose = inf.pose
        return {
            "valid": True,
            "ts_mono": float(inf.ts_mono),
            "face_detected": bool(inf.face_detected),
            "detections": list(inf.detections),
            "pose": (
                {
                    "valid": bool(pose.valid),
                    "yaw_deg": float(pose.yaw_deg),
                    "pitch_deg": float(pose.pitch_deg),
                    "roll_deg": float(pose.roll_deg),
                    "face_box": pose.face_box,
                }
                if pose is not None
                else None
            ),
        }

    def evaluate_tick(
        self,
        frame_bgr: Optional[np.ndarray],
        plan: ThrottlePlan,
        now_mono: Optional[float] = None,
        is_stale: bool = False,
    ) -> Dict[str, Any]:
        """Non-blocking 30Hz tick: feeds each InferenceOutput ONCE to SensorFusion (enforces LCK-06 <= 0.8s & 2-hit guard!)."""
        now_m = now_mono if now_mono is not None else time.monotonic()
        if frame_bgr is not None and not is_stale:
            self.submit_if_due(frame_bgr, plan, now_m)
        with self._lock:
            inf = self._latest_infer

        fresh_display = inf is not None and (now_m - inf.ts_mono) <= self.cfg.blind_hold_max_s
        is_new_infer = fresh_display and (inf.ts_mono > self._last_consumed_infer_ts)

        if is_new_infer:
            self._last_consumed_infer_ts = inf.ts_mono
            obs = {
                "eye": inf.eye_closed,
                "mouth": inf.mouth_yawn,
                "distracted": inf.distracted,
                "smoking": inf.smoking,
                "phone": inf.phone,
            }
        else:
            # Between real inferences, pass None so ChannelTimer is the SINGLE blind-hold authority (<= 0.8s, no fake hits!)
            obs = {"eye": None, "mouth": None, "distracted": None, "smoking": None, "phone": None}

        if fresh_display:
            ver_mode = inf.verification_mode
            face_detected = inf.face_detected
            classes = list(inf.detected_classes)
            detail = inf.behavior_detail
            lat_ms = inf.latency_ms
        else:
            ver_mode = "SINGLE_MODEL_YOLO"
            face_detected = False
            classes = []
            detail = ""
            lat_ms = 0.0

        primary, all_dec = self.fusion.update(obs, now_m, verification_mode=ver_mode)
        perms = self.get_behavior_permissions()
        real_conf = 0.0
        if inf is not None and primary.alert_type != "NORMAL":
            real_conf = float((inf.channel_confidences or {}).get(primary.alert_type, 0.0))
        return {
            "alert_state": primary.alert_state,
            "alert_type": primary.alert_type,
            "verification_mode": primary.verification_mode,
            "alert_reason": primary.reason,
            "duration_seconds": primary.duration_s,
            "confidence": round(real_conf, 3),
            "model_loaded": self.model_loaded,
            "face_detected": face_detected,
            "detected_classes": classes,
            "behavior_detail": detail,
            "ai_latency_ms": lat_ms,
            "infer_ts_mono": float(inf.ts_mono) if inf is not None else 0.0,
            "allow_smoking": perms["allow_smoking"],
            "allow_phone": perms["allow_phone"],
            "channels": {k: {"state": d.alert_state, "dur": d.duration_s} for k, d in all_dec.items()},
        }
