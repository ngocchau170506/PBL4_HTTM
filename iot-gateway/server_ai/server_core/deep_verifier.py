"""Tier-2 Deep AI Verification Engine on Server of AI (Async CNN Worker + Hard-Negative Archive).

Pi 4 performs Tier-1 Edge Safety Reflex (immediate speaker alert <5ms) and sends
a lightweight 10-20 JPEG keyframe burst. This worker verifies the burst asynchronously:
  - Decodes burst images & runs pluggable DeepVerifier (TemporalBaselineVerifier or trained ONNX/PyTorch CNN)
  - Updates violations.cnn_status -> SERVER_CNN_CONFIRMED or SERVER_CNN_REJECTED
  - Applies Safety Score penalty ONLY when SERVER_CNN_CONFIRMED
  - Archives SERVER_CNN_REJECTED bursts to data/hard_negatives/ for retraining Pi's best.pt
  - Stitches confirmed bursts into MP4 on Server (0% CPU cost on Pi 4!)
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_ai.server_core import analytics, config, database, media
from server_ai.server_core.pbl4_bridge import pbl4_bridge
from server_ai.server_core.sse_broker import broker


@dataclass
class VerificationVerdict:
    status: str          # SERVER_CNN_CONFIRMED | SERVER_CNN_REJECTED | ERROR
    confidence: float
    model_version: str
    latency_ms: float
    detail: str


class BaseDeepVerifier:
    model_version: str = "base"

    def verify_burst(self, frame_paths: List[Path], meta: Dict[str, Any]) -> VerificationVerdict:
        raise NotImplementedError


class TemporalBaselineVerifier(BaseDeepVerifier):
    """Multi-frame image quality & temporal consistency verifier (baseline until custom CNN weights loaded)."""

    model_version = "temporal-baseline-v1"

    def verify_burst(self, frame_paths: List[Path], meta: Dict[str, Any]) -> VerificationVerdict:
        t0 = time.perf_counter()
        valid_frames = 0
        non_black_frames = 0
        mean_lumas: List[float] = []

        try:
            import cv2
            import numpy as np
            has_cv2 = True
        except Exception:
            has_cv2 = False

        for fp in frame_paths:
            if not fp.exists() or fp.stat().st_size < 100:
                continue
            if has_cv2:
                img = cv2.imread(str(fp))
                if img is None or img.size == 0:
                    continue
                valid_frames += 1
                luma = float(np.mean(img))
                mean_lumas.append(luma)
                if luma > 8.0:
                    non_black_frames += 1
            else:
                valid_frames += 1
                non_black_frames += 1

        duration_s = float(meta.get("duration_seconds") or 0.0)
        alert_type = str(meta.get("alert_type") or "").upper()
        edge_conf = float(meta.get("confidence") or 0.75)

        # Reject obvious false-positive artifacts: completely dark/corrupt burst or ultra-short glitch
        if len(frame_paths) > 0 and valid_frames == 0:
            verdict_status = "SERVER_CNN_REJECTED"
            conf = 0.05
            detail = "All burst frames unreadable or corrupt"
        elif len(frame_paths) > 0 and non_black_frames == 0:
            verdict_status = "SERVER_CNN_REJECTED"
            conf = 0.10
            detail = "All burst frames black/occluded (mean luma <= 8.0)"
        elif duration_s < 0.35 and alert_type in ("DROWSY", "DROWSY_ALERT", "YAWNING", "YAWNING_ALERT"):
            verdict_status = "SERVER_CNN_REJECTED"
            conf = 0.25
            detail = f"Transient edge spike ({duration_s:.2f}s < 0.35s minimum temporal span)"
        else:
            frame_ratio = (non_black_frames / max(1, len(frame_paths))) if frame_paths else 1.0
            conf = round(min(0.99, max(0.55, 0.6 * edge_conf + 0.4 * frame_ratio)), 3)
            verdict_status = "SERVER_CNN_CONFIRMED"
            detail = (
                f"Temporal burst verified ({non_black_frames}/{max(1, len(frame_paths))} valid frames, "
                f"duration={duration_s:.2f}s)"
            )

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return VerificationVerdict(
            status=verdict_status,
            confidence=conf,
            model_version=self.model_version,
            latency_ms=latency_ms,
            detail=detail,
        )


class OnnxCnnVerifier(BaseDeepVerifier):
    """Pluggable ONNX CNN classifier loader (activated automatically when DEEP_CNN_MODEL_PATH points to .onnx)."""

    def __init__(self, model_path: str):
        import cv2
        self._net = cv2.dnn.readNetFromONNX(model_path)
        self.model_version = f"onnx-cnn:{Path(model_path).name}"
        self._fallback = TemporalBaselineVerifier()

    def verify_burst(self, frame_paths: List[Path], meta: Dict[str, Any]) -> VerificationVerdict:
        t0 = time.perf_counter()
        import cv2
        import numpy as np

        probs: List[float] = []
        for fp in frame_paths[:16]:
            img = cv2.imread(str(fp))
            if img is None:
                continue
            blob = cv2.dnn.blobFromImage(img, 1.0 / 255.0, (224, 224), swapRB=True, crop=False)
            self._net.setInput(blob)
            out = self._net.forward().flatten()
            if out.size >= 1:
                score = float(1.0 / (1.0 + np.exp(-out[-1])))
                probs.append(score)
        if not probs:
            return self._fallback.verify_burst(frame_paths, meta)
        mean_p = float(sum(probs) / len(probs))
        status = "SERVER_CNN_CONFIRMED" if mean_p >= 0.5 else "SERVER_CNN_REJECTED"
        return VerificationVerdict(
            status=status,
            confidence=round(mean_p, 3),
            model_version=self.model_version,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            detail=f"ONNX CNN mean_prob={mean_p:.3f} across {len(probs)} frames",
        )


def build_verifier() -> BaseDeepVerifier:
    mp = config.DEEP_CNN_MODEL_PATH
    if mp and Path(mp).exists() and mp.lower().endswith(".onnx"):
        try:
            return OnnxCnnVerifier(mp)
        except Exception:
            pass
    return TemporalBaselineVerifier()


class DeepVerifierEngine:
    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._verifier: BaseDeepVerifier = build_verifier()
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop.clear()
            self._verifier = build_verifier()
            for i in range(max(1, config.DEEP_VERIFIER_WORKERS)):
                t = threading.Thread(target=self._worker, name=f"deep-cnn-{i}", daemon=True)
                t.start()
                self._threads.append(t)
            self._started = True

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            for _ in self._threads:
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass
            for t in self._threads:
                t.join(timeout=2.0)
            self._threads.clear()
            self._started = False

    def enqueue(self, event_id: str, vehicle_id: str, trip_id: Optional[str], burst_dir: Optional[str], meta: Dict[str, Any]) -> bool:
        job = {
            "event_id": event_id,
            "vehicle_id": vehicle_id,
            "trip_id": trip_id,
            "burst_dir": burst_dir,
            "meta": meta,
        }
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            return False

    def process_job_sync(self, job: Dict[str, Any]) -> VerificationVerdict:
        event_id = job["event_id"]
        vehicle_id = job["vehicle_id"]
        trip_id = job.get("trip_id")
        burst_dir = job.get("burst_dir")
        meta = job.get("meta") or {}

        frame_paths: List[Path] = []
        if burst_dir:
            bpath = config.EVIDENCE_DIR / burst_dir
            if bpath.exists():
                frame_paths = sorted(bpath.glob("*.jpg"))

        verdict = self._verifier.verify_burst(frame_paths, meta)

        video_url = None
        stitched_name = None
        if verdict.status == "SERVER_CNN_CONFIRMED" and burst_dir and len(frame_paths) >= 2:
            stitch_fps = max(1.0, min(15.0, float(meta.get("burst_fps") or 4.0)))
            stitched = media.stitch_burst_to_mp4(burst_dir, vehicle_id, event_id, fps=stitch_fps)
            if stitched:
                stitched_name = stitched
                video_url = "/evidence/" + stitched
        elif verdict.status == "SERVER_CNN_REJECTED" and burst_dir:
            self._archive_hard_negative(event_id, burst_dir, meta, verdict)

        score = None
        with database.tx() as conn:
            row = conn.execute(
                "SELECT penalty_applied, alert_type, trip_id FROM violations WHERE event_id=?", (event_id,)
            ).fetchone()
            already_penalized = bool(row["penalty_applied"]) if row else True
            eff_trip_id = trip_id or (row["trip_id"] if row else None)
            alert_type = meta.get("alert_type") or (row["alert_type"] if row else "")
            conn.execute(
                """UPDATE violations
                   SET cnn_status=?, cnn_confidence=?, cnn_model_version=?, cnn_latency_ms=?, cnn_detail=?,
                       video_url=COALESCE(?, video_url), penalty_applied=?
                   WHERE event_id=?""",
                (
                    verdict.status,
                    verdict.confidence,
                    verdict.model_version,
                    verdict.latency_ms,
                    verdict.detail,
                    video_url,
                    1 if (verdict.status == "SERVER_CNN_CONFIRMED" or already_penalized) else 0,
                    event_id,
                ),
            )

        if verdict.status == "SERVER_CNN_CONFIRMED" and not already_penalized:
            score = analytics.apply_violation_penalty(eff_trip_id, alert_type)
        if verdict.status == "SERVER_CNN_CONFIRMED":
            pbl4_bridge.forward_violation(
                event_id=event_id,
                idempotency_key=str(meta.get("idempotency_key") or event_id),
                vehicle_id=vehicle_id,
                alert_type=alert_type,
                confidence=verdict.confidence,
                detail=verdict.detail,
                video_rel_name=stitched_name,
            )

        broker.publish(
            "cnn_verdict",
            {
                "event_id": event_id,
                "vehicle_id": vehicle_id,
                "cnn_status": verdict.status,
                "cnn_confidence": verdict.confidence,
                "model_version": verdict.model_version,
                "latency_ms": verdict.latency_ms,
                "video_url": video_url,
                "safety_score": score,
            },
        )
        return verdict

    def _archive_hard_negative(self, event_id: str, burst_dir: str, meta: Dict[str, Any], verdict: VerificationVerdict) -> None:
        try:
            src = config.EVIDENCE_DIR / burst_dir
            dst = config.HARD_NEGATIVES_DIR / burst_dir
            if src.exists() and not dst.exists():
                shutil.copytree(src, dst)
            (dst / "verdict.json").write_text(
                json.dumps({"event_id": event_id, "meta": meta, "verdict": verdict.__dict__}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                self._queue.task_done()
                break
            try:
                self.process_job_sync(job)
            except Exception:
                pass
            finally:
                self._queue.task_done()


verifier_engine = DeepVerifierEngine()
