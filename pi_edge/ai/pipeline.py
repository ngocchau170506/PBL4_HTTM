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

from pi_edge.ai.calibration_10s import DriverCalibrator10s, FaceMesh478TFLite
from pi_edge.ai.face_geometry import (
    BlazeFaceGeometry,
    HeadPoseEstimate,
    apply_clahe_lab,
    evaluate_mouth_yawn_geometry,
    expand_square_face_roi,
    inspect_upper_face_glare,
    rotate_roi_replicate,
    unrotate_bbox_in_roi,
)
from pi_edge.ai.sensor_fusion import FusionDecision, SensorFusion
from pi_edge.core.config import EdgeConfig
from pi_edge.core.telemetry import ThrottlePlan

# Per-class minimum confidence thresholds (balanced eye thresholds; Normal separated from Eye_Open)
CLASS_MIN_CONF: Dict[str, float] = {
    "Eye_Closed": 0.28,
    "Eye_Open": 0.28,
    "Mouth_Open": 0.35,
    "Distracted Head": 0.35,
    "Normal": 0.25,
}
FACE_RELATED_CLASSES = frozenset({"Eye_Closed", "Eye_Open", "Mouth_Open", "Distracted Head", "Normal"})


def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = float(iw * ih)
    if inter <= 0.0:
        return 0.0
    area_a = float(max(1, a[2] - a[0]) * max(1, a[3] - a[1]))
    area_b = float(max(1, b[2] - b[0]) * max(1, b[3] - b[1]))
    return inter / max(1.0, area_a + area_b - inter)


def _merge_roi_detection(
    dets: List[Tuple[str, float, Tuple[int, int, int, int]]],
    new_det: Tuple[str, float, Tuple[int, int, int, int]],
    same_group: frozenset[str],
    iou_thr: float = 0.35,
) -> None:
    """Merge an ROI eye/mouth box into full-frame `dets` without duplicating the same eye/mouth."""
    nlbl, nconf, nbox = new_det
    for idx, (elbl, econf, ebox) in enumerate(dets):
        if elbl in same_group and _bbox_iou(ebox, nbox) >= iou_thr:
            if nconf > econf:
                dets[idx] = new_det
            return
    dets.append(new_det)


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
    def __init__(
        self,
        cfg: EdgeConfig,
        yolo_model: Any = None,
        face_geo: Optional[BlazeFaceGeometry] = None,
        calibrator: Optional[DriverCalibrator10s] = None,
    ):
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
        self._mesh = FaceMesh478TFLite(cfg.landmarker_path)
        self.calibrator = (
            calibrator
            if calibrator is not None
            else DriverCalibrator10s(
                calibration_dir=cfg.calibration_dir,
                face_geo=self._geo,
                face_mesh=self._mesh,
                duration_s=10.0,
                auto_boot=not cfg.disable_ai,
            )
        )
        if self.calibrator.mesh is not None:
            self._mesh = self.calibrator.mesh

        # Event-driven 1-slot async inference worker
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._pending_task: Optional[Tuple[np.ndarray, ThrottlePlan, float]] = None
        self._latest_infer: Optional[InferenceOutput] = None
        self._last_consumed_infer_ts: float = 0.0
        self._last_submit_mono = 0.0
        self._zero_eye_streak: int = 0
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

    def request_calibration(
        self,
        source: str = "CONTROL_CMD",
        lock: bool = True,
        now_mono: Optional[float] = None,
    ) -> Tuple[bool, str]:
        return self.calibrator.request_calibration(source=source, lock=lock, now_mono=now_mono)

    def get_calibration_status(self, now_mono: Optional[float] = None) -> Dict[str, Any]:
        return self.calibrator.get_status_dict(now_mono=now_mono)

    def pop_calibration_event(self) -> Optional[Dict[str, Any]]:
        return self.calibrator.pop_event()

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
        if self.cfg.disable_ai:
            return False
        cal_running = self.calibrator.is_running(now_mono) and plan.allow_face_geometry and self.calibrator.available
        if not cal_running and self._yolo is None:
            return False
        min_interval = min(plan.ai_min_interval_s, 0.08) if cal_running else plan.ai_min_interval_s
        if (now_mono - self._last_submit_mono) < min_interval:
            return False
        with self._cond:
            if self._pending_task is not None:
                return False
            self._pending_task = (frame_bgr, plan, float(now_mono))
            self._last_submit_mono = now_mono
            self._cond.notify()
            return True

    def _run_inference(
        self,
        frame_bgr: np.ndarray,
        plan: ThrottlePlan,
        now_mono: Optional[float] = None,
    ) -> InferenceOutput:
        t0 = time.perf_counter()
        now_m = time.monotonic() if now_mono is None else float(now_mono)

        # 10-second MediaPipe 478-Mesh calibration mode: pause YOLO so Pi 4 hits ~10-12 FPS MediaPipe cleanly
        if self.calibrator.is_running(now_m) and plan.allow_face_geometry and self.calibrator.available:
            self._zero_eye_streak = 0
            pose_cal, mesh_s, cal_st = self.calibrator.step_frame(frame_bgr, now_mono=now_m)
            cal_dets: List[Tuple[str, float, Tuple[int, int, int, int]]] = []
            if mesh_s is not None and mesh_s.valid:
                if mesh_s.left_eye_box is not None:
                    cal_dets.append(("Eye_Open", mesh_s.presence, mesh_s.left_eye_box))
                if mesh_s.right_eye_box is not None:
                    cal_dets.append(("Eye_Open", mesh_s.presence, mesh_s.right_eye_box))
            ver_cal = "YOLO_BLAZEFACE_SEQ" if pose_cal.valid else "SINGLE_MODEL_YOLO"
            return InferenceOutput(
                ts_mono=now_m,
                detected_classes=[d[0] for d in cal_dets],
                detections=cal_dets,
                face_detected=bool(pose_cal.valid),
                pose=pose_cal,
                eye_closed=False if pose_cal.valid else None,
                mouth_yawn=False if pose_cal.valid else None,
                distracted=False if pose_cal.valid else None,
                smoking=False,
                phone=False,
                channel_confidences={"DROWSY": 0.0, "YAWNING": 0.0, "DISTRACTED": 0.0, "SMOKING": 0.0, "PHONE": 0.0},
                behavior_detail=f"CALIB_{cal_st.get('mode') or 'BOOT'}_{cal_st.get('progress_s', 0.0):.1f}s",
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                verification_mode=ver_cal,
            )

        perms = self.get_behavior_permissions()
        pose: Optional[HeadPoseEstimate] = None
        if plan.allow_face_geometry and self._geo.available:
            pose = self._geo.detect_pose(frame_bgr)

        dets: List[Tuple[str, float, Tuple[int, int, int, int]]] = []
        h_img, w_img = frame_bgr.shape[:2]
        glare_active = False
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

            # Smart Face-ROI + CLAHE Glare Compensation Pass (only when Pass 1 misses or has low eye confidence, < 80°C)
            if pose is not None and pose.valid and pose.face_box is not None and plan.thermal_level < 3:
                eye_dets = [d for d in dets if d[0] in ("Eye_Closed", "Eye_Open")]
                max_eye_conf = max((d[1] for d in eye_dets), default=0.0)
                glare_detected, _, _ = inspect_upper_face_glare(frame_bgr, pose.face_box)
                glare_active = glare_detected or self.calibrator.is_glare_mode()

                if len(eye_dets) < 2 or max_eye_conf < 0.52:
                    rx1, ry1, rx2, ry2 = expand_square_face_roi(pose.face_box, w_img, h_img, scale_mult=1.30)
                    roi = frame_bgr[ry1:ry2, rx1:rx2]
                    if roi.size > 0 and roi.shape[0] >= 24 and roi.shape[1] >= 24:
                        did_rotate = abs(pose.roll_deg) >= 15.0
                        roi_proc = rotate_roi_replicate(roi, +pose.roll_deg) if did_rotate else roi
                        if glare_active:
                            roi_proc = apply_clahe_lab(roi_proc, clip_limit=2.2, tile_size=4)
                        roi_imgsz = 256
                        r2 = self._yolo.predict(roi_proc, imgsz=roi_imgsz, conf=0.20, max_det=6, verbose=False)
                        if r2 and len(r2) > 0 and r2[0].boxes is not None:
                            names2 = r2[0].names or {}
                            roi_h, roi_w = roi.shape[:2]
                            eye_group = frozenset({"Eye_Closed", "Eye_Open"})
                            mouth_group = frozenset({"Mouth_Open"})
                            for b in r2[0].boxes:
                                lbl2 = str(names2.get(int(b.cls[0]), ""))
                                if lbl2 not in ("Eye_Closed", "Eye_Open", "Mouth_Open"):
                                    continue
                                c2 = float(b.conf[0])
                                if c2 < CLASS_MIN_CONF.get(lbl2, 0.28):
                                    continue
                                bx1, by1, bx2, by2 = [int(round(float(v))) for v in b.xyxy[0].tolist()]
                                if did_rotate:
                                    ux1, uy1, ux2, uy2 = unrotate_bbox_in_roi(
                                        (bx1, by1, bx2, by2), roi_w, roi_h, +pose.roll_deg
                                    )
                                else:
                                    ux1 = max(0, min(roi_w - 1, bx1))
                                    uy1 = max(0, min(roi_h - 1, by1))
                                    ux2 = max(ux1 + 1, min(roi_w, bx2))
                                    uy2 = max(uy1 + 1, min(roi_h, by2))
                                mapped_box = (rx1 + ux1, ry1 + uy1, rx1 + ux2, ry1 + uy2)
                                grp = eye_group if lbl2 in eye_group else mouth_group
                                _merge_roi_detection(dets, (lbl2, c2, mapped_box), grp)

        labels = [d[0] for d in dets]
        face_detected = bool((pose and pose.valid) or any(l in FACE_RELATED_CLASSES for l in labels))

        neutral_yaw, neutral_pitch, _ = self.calibrator.get_neutral_pose()
        yaw_eff = (pose.yaw_deg - neutral_yaw) if (pose and pose.valid) else 0.0
        pitch_eff = (pose.pitch_deg - neutral_pitch) if (pose and pose.valid) else 0.0

        # Strictly separate "Eye_Open" from full-face "Normal" so "Normal" NEVER suppresses "Eye_Closed"!
        closed_confs = [c for lbl, c, _ in dets if lbl == "Eye_Closed"]
        open_confs = [c for lbl, c, _ in dets if lbl == "Eye_Open"]
        normal_confs = [c for lbl, c, _ in dets if lbl == "Normal"]
        if pose is not None and pose.valid and not closed_confs and not open_confs:
            self._zero_eye_streak += 1
        else:
            self._zero_eye_streak = 0

        eye_closed: Optional[bool] = None
        eye_conf = 0.0
        extra_detail = ""
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
        else:
            # Hybrid 478-Mesh EAR rescue strictly gated on frontal pose (< 75°C) + sustained 0-eye streak or glare
            if (
                pose is not None
                and pose.valid
                and pose.face_box is not None
                and plan.thermal_level <= 1
                and abs(yaw_eff) < 22.0
                and abs(pitch_eff) < 20.0
                and abs(pose.roll_deg) < 20.0
                and (self._zero_eye_streak >= 2 or glare_active)
                and self._mesh is not None
                and self._mesh.available
            ):
                ms = self._mesh.infer_ear_mar(frame_bgr, pose.face_box)
                if ms.valid:
                    ear_thr = self.calibrator.get_ear_closed_threshold(default=0.18)
                    if ms.ear <= ear_thr:
                        eye_closed = True
                        eye_conf = round(min(0.85, max(0.50, 0.50 + (ear_thr - ms.ear) * 2.0)), 3)
                        extra_detail = f"MeshEAR({ms.ear:.3f}<={ear_thr:.3f})"
                        if ms.left_eye_box is not None:
                            dets.append(("Eye_Closed", eye_conf, ms.left_eye_box))
                        if ms.right_eye_box is not None:
                            dets.append(("Eye_Closed", eye_conf, ms.right_eye_box))
                        labels = [d[0] for d in dets]
                    elif ms.ear >= ear_thr + 0.04:
                        eye_closed = False
                        eye_conf = round(min(0.90, max(0.55, ms.presence)), 3)

            if eye_closed is None and normal_confs:
                eye_closed = False
                eye_conf = max(normal_confs)

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
        pose_dist = bool(pose and pose.valid and (abs(yaw_eff) >= 32.0 or abs(pitch_eff) >= 26.0))
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
        beh_detail = f"{mouth_detail} {extra_detail}".strip() if (mouth_detail and extra_detail) else (mouth_detail or extra_detail)

        return InferenceOutput(
            ts_mono=now_m,
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
            behavior_detail=beh_detail,
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
                frame, plan, submit_mono = task
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
        cal = self.calibrator.get_status_dict()
        if inf is None:
            return {"valid": False, "detections": [], "pose": None, "face_detected": False, "calibration": cal}
        pose = inf.pose
        return {
            "valid": True,
            "ts_mono": float(inf.ts_mono),
            "face_detected": bool(inf.face_detected),
            "detections": list(inf.detections),
            "calibration": cal,
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
            self.calibrator.notify_real_camera_frame(now_m)
            self.submit_if_due(frame_bgr, plan, now_m)
        else:
            self.calibrator.tick_time(now_m)

        cal_status = self.calibrator.get_status_dict(now_m)
        with self._lock:
            inf = self._latest_infer

        fresh_display = inf is not None and (now_m - inf.ts_mono) <= self.cfg.blind_hold_max_s
        is_new_infer = fresh_display and (inf.ts_mono > self._last_consumed_infer_ts)

        if cal_status["state"] == "RUNNING":
            # Never emit false violation alerts while running 10s driver calibration
            for ch in self.fusion.channels.values():
                ch.reset()
            if is_new_infer and inf is not None:
                self._last_consumed_infer_ts = inf.ts_mono
            obs = {"eye": False, "mouth": False, "distracted": False, "smoking": False, "phone": False}
        elif is_new_infer and inf is not None:
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

        if fresh_display and inf is not None:
            ver_mode = inf.verification_mode
            face_detected = inf.face_detected
            classes = list(inf.detected_classes)
            detail = inf.behavior_detail
            lat_ms = inf.latency_ms
        else:
            ver_mode = "SINGLE_MODEL_YOLO"
            face_detected = False
            classes = []
            detail = f"CALIB_{cal_status['mode']}_{cal_status['progress_s']:.1f}s" if cal_status["state"] == "RUNNING" else ""
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
            "calibration": cal_status,
            "channels": {k: {"state": d.alert_state, "dur": d.duration_s} for k, d in all_dec.items()},
        }
