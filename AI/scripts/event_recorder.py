"""Asynchronous event assembly for later temporal-CNN verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import threading
import uuid
from typing import Any, Optional

from AI.scripts.cnn_frame_buffer import CnnFrame, CnnFrameBuffer
from AI.scripts.event_storage import EventStorage
from AI.scripts.upload_worker import UploadWorker


@dataclass
class _ActiveEvent:
    event_id: str
    event_type: str
    event_start_ns: int
    event_start_time: str
    confirmed_at_ns: Optional[int] = None
    confirmed_at_time: Optional[str] = None
    frames: list[CnnFrame] = field(default_factory=list)
    confirmed: bool = False
    recording_end_ns: Optional[int] = None
    local_status: str = "WATCHING"


class EventRecorder:
    """Collect event windows without running inference or blocking the pipeline."""

    def __init__(
        self,
        storage_dir: str,
        buffer_seconds: float = 5.0,
        sample_fps: float = 5.0,
        pre_event_seconds: float = 0.5,
        post_detection_seconds: float = 0.5,
        dataset_mode: bool = False,
    ):
        self.buffer = CnnFrameBuffer(buffer_seconds, sample_fps)
        self.pre_event_seconds = float(pre_event_seconds)
        self.post_detection_seconds = float(post_detection_seconds)
        self.dataset_mode = bool(dataset_mode)
        self.storage = EventStorage(storage_dir)
        self.upload_worker = UploadWorker(self.storage)
        self._active: dict[str, _ActiveEvent] = {}
        self._frame_id = 0
        self._last_sampled: Optional[CnnFrame] = None
        self._lock = threading.RLock()

    def append_frame(
        self,
        image,
        capture_timestamp_ns: Optional[int] = None,
        capture_time_utc: Optional[str] = None,
        face_valid: Optional[bool] = None,
        metadata: Optional[dict[str, Any]] = None,
        frame_id: Optional[int] = None,
    ) -> Optional[CnnFrame]:
        with self._lock:
            if capture_timestamp_ns is None:
                capture_timestamp_ns = time.monotonic_ns()
            if frame_id is None:
                self._frame_id += 1
                frame_id = self._frame_id
            else:
                self._frame_id = max(self._frame_id, int(frame_id))
            force_boundary = any(
                event.recording_end_ns is not None
                and capture_timestamp_ns >= event.recording_end_ns
                for event in self._active.values()
            )
            sampled = self.buffer.append(
                frame_id=frame_id,
                capture_timestamp_ns=capture_timestamp_ns,
                image=image,
                capture_time_utc=capture_time_utc or _utc_now(),
                face_valid=face_valid,
                metadata=metadata,
                force=force_boundary,
            )
            if sampled is not None:
                self._last_sampled = sampled
                for event in self._active.values():
                    event.frames.append(sampled)
                self._finish_due_events(sampled.capture_timestamp_ns)
            return sampled

    def start_event(
        self,
        event_type: str,
        event_start_timestamp_ns: int,
        event_start_time: Optional[str] = None,
    ) -> None:
        with self._lock:
            event_type = event_type.upper()
            if event_type not in {"DROWSY", "YAWNING"}:
                raise ValueError("event_type must be DROWSY or YAWNING")
            if event_type in self._active:
                return
            frames = self.buffer.before(event_start_timestamp_ns, self.pre_event_seconds)
            self._active[event_type] = _ActiveEvent(
                event_id=f"{event_type.lower()}-{uuid.uuid4().hex}",
                event_type=event_type,
                event_start_ns=event_start_timestamp_ns,
                event_start_time=_as_utc(event_start_time),
                frames=frames,
            )

    def annotate_frame(self, frame_id: Optional[int], metadata: dict[str, Any]) -> None:
        with self._lock:
            if frame_id is None:
                return
            frame = self.buffer.get(frame_id)
            if frame is None:
                return
            if "face_valid" in metadata:
                frame.face_valid = metadata["face_valid"]
            frame.metadata.update(metadata)

    def update_event(
        self,
        event_type: str,
        timestamp_ns: int,
        local_status: str,
        confirmed: bool,
        metadata: Optional[dict[str, Any]] = None,
        confirmed_at_ns: Optional[int] = None,
        confirmed_at_time: Optional[str] = None,
    ) -> None:
        with self._lock:
            event = self._active.get(event_type.upper())
            if event is None:
                return
            event.local_status = str(local_status).upper()
            if confirmed and not event.confirmed:
                event.confirmed_at_ns = confirmed_at_ns or timestamp_ns
                event.confirmed_at_time = _as_utc(confirmed_at_time)
            event.confirmed = event.confirmed or bool(confirmed)
            if event.confirmed and event.recording_end_ns is None:
                event.recording_end_ns = timestamp_ns + int(self.post_detection_seconds * 1e9)
            if metadata:
                self.annotate_frame(metadata.get("frame_id"), metadata)
            if event.recording_end_ns is not None and timestamp_ns >= event.recording_end_ns:
                self._finish_event(event.event_type)

    def end_detection(self, event_type: str, timestamp_ns: int) -> None:
        with self._lock:
            event = self._active.get(event_type.upper())
            if event is None:
                return
            if not event.confirmed:
                if not self.dataset_mode:
                    self._active.pop(event.event_type, None)
                return
            if event.recording_end_ns is None:
                event.recording_end_ns = timestamp_ns + int(self.post_detection_seconds * 1e9)
            self._finish_due_events(timestamp_ns)

    def close(self) -> None:
        with self._lock:
            for event_type in list(self._active):
                event = self._active[event_type]
                if event.confirmed:
                    self._finish_event(event_type, complete=False)
                else:
                    self._active.pop(event_type, None)
        self.upload_worker.close()
        self.storage.close()

    def _finish_due_events(self, timestamp_ns: int) -> None:
        for event_type, event in list(self._active.items()):
            if event.recording_end_ns is not None and timestamp_ns >= event.recording_end_ns:
                self._finish_event(event_type)

    def _finish_event(self, event_type: str, complete: bool = True) -> None:
        event = self._active.pop(event_type, None)
        if event is None or not event.confirmed:
            return
        actual_first_ns = event.frames[0].capture_timestamp_ns if event.frames else None
        actual_last_ns = event.frames[-1].capture_timestamp_ns if event.frames else None
        confirmed_ns = event.confirmed_at_ns
        target_end_ns = (
            confirmed_ns + int(self.post_detection_seconds * 1e9)
            if confirmed_ns is not None else None
        )
        actual_duration = (
            max(0.0, (actual_last_ns - actual_first_ns) / 1e9)
            if actual_first_ns is not None and actual_last_ns is not None else 0.0
        )
        sample_fps_actual = (
            (len(event.frames) - 1) / actual_duration
            if actual_duration > 0 and len(event.frames) > 1 else 0.0
        )
        required_first_ns = event.event_start_ns - int(self.pre_event_seconds * 1e9)
        has_pre = actual_first_ns is not None and actual_first_ns <= required_first_ns
        has_post = (
            target_end_ns is not None and actual_last_ns is not None
            and actual_last_ns >= target_end_ns
        )
        metadata = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "event_start_time": event.event_start_time,
            "event_start_ns": event.event_start_ns,
            "confirmed_at_ns": confirmed_ns,
            "confirmed_at_time": event.confirmed_at_time,
            "target_end_ns": target_end_ns,
            "recording_end_time": _utc_now(),
            "frame_count": len(event.frames),
            "sample_fps_target": self.buffer.sample_fps,
            "sample_fps_actual": round(sample_fps_actual, 3),
            "actual_first_frame_ns": actual_first_ns,
            "actual_last_frame_ns": actual_last_ns,
            "duration_actual_seconds": round(actual_duration, 6),
            "pre_event_coverage_seconds": round(
                max(0.0, (event.event_start_ns - actual_first_ns) / 1e9)
                if actual_first_ns is not None else 0.0, 6
            ),
            "post_confirmation_coverage_seconds": round(
                max(0.0, (actual_last_ns - confirmed_ns) / 1e9)
                if confirmed_ns is not None and actual_last_ns is not None else 0.0, 6
            ),
            "sequence_complete": bool(complete and has_pre and has_post),
            "image_format": "BGR",
            "image_scope": "full_camera_frame_resized",
            "face_crop_applied": False,
            "local_alert_state": event.local_status,
            "verification_status": "PENDING",
        }
        self.upload_worker.submit_event(metadata, event.frames)
        self.upload_worker.wake()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_utc(value: Optional[str]) -> str:
    if not value:
        return _utc_now()
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except ValueError:
        return _utc_now()
