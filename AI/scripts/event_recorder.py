"""Bộ ghi video vi phạm liên tục 10 giây (-3s -> 0s -> 2s -> 3..6s -> đủ 10s tự kết thúc & cuốn chiếu đoạn mới).

Thiết kế chống xung đột luồng & chống chèn frame trên Raspberry Pi 4:
1. Một luồng cấp frame duy nhất theo thứ tự đơn điệu (Single-Thread Monotonic Feed từ DrowsinessPipeline.process)
   -> loại bỏ hoàn toàn xung đột đua dữ liệu giữa CameraFrameSource và Pipeline.
2. Cô lập 3 tầng bộ đệm:
   - Tầng 1 (CnnFrameBuffer): Bộ đệm vòng RAM giữ liên tục 5.0s gần nhất (đủ lấy -3.0s tiền sự kiện).
   - Tầng 2 (_ActiveEvent): Khi có dấu hiệu vi phạm tại mốc 0s, nhân bản độc lập (clone_for_segment)
     các frame [-3s, 0s] và khóa thứ tự (frame_id > last_frame_id, ts_ns > last_timestamp_ns) để frame mới
     không bao giờ chèn đè vào frame đang lưu.
   - Tầng 3 (UploadWorker / EventStorage): Khi đủ 10s (hoặc người lái đã tỉnh sau 3-6s hậu cảnh báo),
     đóng băng danh sách frame (tuple) đẩy sang luồng nền kết xuất .mp4 (Wi-Fi ổn định -> gửi Server;
     mất mạng/Wi-Fi yếu -> lưu trực tiếp thẻ nhớ SD). Nếu sau 10s người vi phạm vẫn chưa tỉnh,
     tự động đóng đoạn 10s hiện tại và mở ngay đoạn 10s mới (Segment #2, #3...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any, Optional
import uuid

from AI.scripts.cnn_frame_buffer import CnnFrame, CnnFrameBuffer
from AI.scripts.event_storage import EventStorage
from AI.scripts.upload_worker import UploadWorker

VALID_EVENT_TYPES = {"DROWSY", "YAWNING", "DISTRACTED"}


@dataclass
class _ActiveEvent:
    event_id: str
    event_type: str
    event_start_ns: int
    event_start_time: str
    clip_start_ns: int
    max_clip_end_ns: int
    segment_index: int = 1
    continued_from: Optional[str] = None
    confirmed_at_ns: Optional[int] = None
    confirmed_at_time: Optional[str] = None
    frames: list[CnnFrame] = field(default_factory=list)
    confirmed: bool = False
    currently_active: bool = True
    recording_end_ns: Optional[int] = None
    local_status: str = "WATCHING"
    last_frame_id: int = -1
    last_timestamp_ns: int = -1


class EventRecorder:
    """Collect 10-second continuous violation video windows (-3s -> 0s -> 2s -> 10s) without blocking AI."""

    def __init__(
        self,
        storage_dir: str,
        buffer_seconds: Optional[float] = None,
        sample_fps: Optional[float] = None,
        pre_event_seconds: Optional[float] = None,
        post_detection_seconds: Optional[float] = None,
        max_clip_seconds: Optional[float] = None,
        dataset_mode: bool = False,
        cloud_sync: Optional[Any] = None,
    ):
        self.pre_event_seconds = float(
            pre_event_seconds
            if pre_event_seconds is not None
            else os.getenv("CLIP_PRE_EVENT_S", "3.0")
        )
        self.post_detection_seconds = float(
            post_detection_seconds
            if post_detection_seconds is not None
            else os.getenv("CLIP_POST_CONFIRM_S", "5.0")
        )
        self.max_clip_seconds = float(
            max_clip_seconds
            if max_clip_seconds is not None
            else os.getenv("CLIP_MAX_DURATION_S", "10.0")
        )
        eff_sample_fps = float(
            sample_fps
            if sample_fps is not None
            else os.getenv("CLIP_SAMPLE_FPS", "10.0")
        )
        eff_buf_seconds = float(
            buffer_seconds
            if buffer_seconds is not None
            else max(5.0, self.pre_event_seconds + 2.0)
        )
        clip_w = int(os.getenv("CLIP_WIDTH", "320"))
        clip_h = int(os.getenv("CLIP_HEIGHT", "240"))

        self.buffer = CnnFrameBuffer(
            buffer_seconds=eff_buf_seconds,
            sample_fps=eff_sample_fps,
            image_size=(clip_w, clip_h),
        )
        self.dataset_mode = bool(dataset_mode)
        self.storage = EventStorage(storage_dir)
        self.upload_worker = UploadWorker(self.storage, cloud_sync=cloud_sync)
        self._active: dict[str, _ActiveEvent] = {}
        self._frame_id = 0
        self._last_sampled: Optional[CnnFrame] = None
        self._completed_segments_count: int = 0
        self._rollover_segments_count: int = 0
        self._last_finished_metadata: Optional[dict[str, Any]] = None
        self._lock = threading.RLock()

    def set_cloud_sync(self, cloud_sync: Any) -> None:
        self.upload_worker.set_cloud_sync(cloud_sync)

    @staticmethod
    def _append_ordered(event: _ActiveEvent, frame: CnnFrame) -> None:
        """Chỉ nối frame mới nếu có frame_id và timestamp lớn hơn hoàn toàn frame trước đó."""
        if frame.frame_id > event.last_frame_id and frame.capture_timestamp_ns > event.last_timestamp_ns:
            cloned = frame.clone_for_segment()
            event.frames.append(cloned)
            event.last_frame_id = cloned.frame_id
            event.last_timestamp_ns = cloned.capture_timestamp_ns

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
                if int(frame_id) <= self._frame_id:
                    frame_id = self._frame_id + 1
                self._frame_id = int(frame_id)

            force_boundary = any(
                (
                    event.recording_end_ns is not None
                    and capture_timestamp_ns >= event.recording_end_ns
                )
                or (
                    event.confirmed
                    and capture_timestamp_ns >= event.max_clip_end_ns
                )
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
                    self._append_ordered(event, sampled)
                self._finish_due_events(sampled.capture_timestamp_ns)
            return sampled

    def start_event(
        self,
        event_type: str,
        event_start_timestamp_ns: int,
        event_start_time: Optional[str] = None,
        segment_index: int = 1,
        continued_from: Optional[str] = None,
        pre_confirmed: bool = False,
    ) -> None:
        with self._lock:
            event_type = event_type.upper()
            if event_type not in VALID_EVENT_TYPES:
                raise ValueError(f"event_type must be one of {sorted(VALID_EVENT_TYPES)}")
            if event_type in self._active:
                return
            pre_frames = self.buffer.before(event_start_timestamp_ns, self.pre_event_seconds)
            clip_start_ns = (
                pre_frames[0].capture_timestamp_ns
                if pre_frames
                else event_start_timestamp_ns
            )
            max_clip_end_ns = clip_start_ns + int(self.max_clip_seconds * 1e9)
            last_fid = pre_frames[-1].frame_id if pre_frames else -1
            last_ts = pre_frames[-1].capture_timestamp_ns if pre_frames else -1
            utc_start = _as_utc(event_start_time)

            active_ev = _ActiveEvent(
                event_id=f"{event_type.lower()}-seg{segment_index}-{uuid.uuid4().hex[:10]}",
                event_type=event_type,
                event_start_ns=event_start_timestamp_ns,
                event_start_time=utc_start,
                clip_start_ns=clip_start_ns,
                max_clip_end_ns=max_clip_end_ns,
                segment_index=int(segment_index),
                continued_from=continued_from,
                confirmed_at_ns=event_start_timestamp_ns if pre_confirmed else None,
                confirmed_at_time=utc_start if pre_confirmed else None,
                frames=pre_frames,
                confirmed=bool(pre_confirmed),
                currently_active=True,
                recording_end_ns=max_clip_end_ns if pre_confirmed else None,
                local_status="CONFIRMED" if pre_confirmed else "VERIFYING",
                last_frame_id=last_fid,
                last_timestamp_ns=last_ts,
            )
            self._active[event_type] = active_ev

    def annotate_frame(self, frame_id: Optional[int], metadata: dict[str, Any]) -> None:
        with self._lock:
            if frame_id is None:
                return
            fid = int(frame_id)
            for event in self._active.values():
                for f in reversed(event.frames[-5:]):
                    if f.frame_id == fid:
                        if "face_valid" in metadata:
                            f.face_valid = metadata["face_valid"]
                        f.metadata.update(metadata)
                        break

    def update_event(
        self,
        event_type: str,
        timestamp_ns: int,
        local_status: str,
        confirmed: bool,
        metadata: Optional[dict[str, Any]] = None,
        confirmed_at_ns: Optional[int] = None,
        confirmed_at_time: Optional[str] = None,
        currently_active: bool = True,
    ) -> None:
        with self._lock:
            ev_key = event_type.upper()
            event = self._active.get(ev_key)
            if event is None:
                if currently_active:
                    self.start_event(
                        ev_key,
                        timestamp_ns,
                        confirmed_at_time,
                        segment_index=1,
                        pre_confirmed=confirmed,
                    )
                    event = self._active.get(ev_key)
                if event is None:
                    return

            event.currently_active = bool(currently_active)
            event.local_status = str(local_status).upper()
            if confirmed and not event.confirmed:
                event.confirmed_at_ns = confirmed_at_ns or timestamp_ns
                event.confirmed_at_time = _as_utc(confirmed_at_time)
            event.confirmed = event.confirmed or bool(confirmed)
            if event.confirmed and event.recording_end_ns is None:
                # Mốc 2s xác nhận vi phạm: tiếp tục ghi thêm post_detection_seconds (mặc định 5s)
                # để tổng đoạn clip từ -3s đến +7s đạt đúng 10.0s
                post_end_ns = timestamp_ns + int(self.post_detection_seconds * 1e9)
                event.recording_end_ns = min(event.max_clip_end_ns, post_end_ns)
            if metadata:
                self.annotate_frame(metadata.get("frame_id"), metadata)
            self._finish_due_events(timestamp_ns)

    def end_detection(self, event_type: str, timestamp_ns: int) -> None:
        with self._lock:
            event = self._active.get(event_type.upper())
            if event is None:
                return
            event.currently_active = False
            if not event.confirmed:
                # Dấu hiệu dưới 2s (chớp mắt bình thường, chưa thành vi phạm) -> hủy ngay trong RAM
                if not self.dataset_mode:
                    self._active.pop(event.event_type, None)
                return
            if event.recording_end_ns is None:
                post_end_ns = timestamp_ns + int(self.post_detection_seconds * 1e9)
                event.recording_end_ns = min(event.max_clip_end_ns, post_end_ns)
            self._finish_due_events(timestamp_ns)

    def interrupt(self) -> None:
        """Flush incomplete windows on camera gaps without closing the worker."""
        with self._lock:
            for event_type in list(self._active):
                event = self._active[event_type]
                if event.confirmed:
                    self._finish_event(event_type, complete=False, allow_rollover=False)
                else:
                    self._active.pop(event_type, None)

    def close(self) -> None:
        self.interrupt()
        self.upload_worker.close()
        self.storage.close()

    def get_status_snapshot(self, now_ns: Optional[int] = None) -> dict[str, Any]:
        """Trả về trạng thái thời gian thực của luồng ghi video 10s cho AI Pipeline & Dashboard."""
        with self._lock:
            cur_ns = now_ns if now_ns is not None else time.monotonic_ns()
            ring_frames = len(self.buffer)
            active_info = {}
            for ev_type, ev in self._active.items():
                elapsed_clip_s = max(0.0, (cur_ns - ev.clip_start_ns) / 1e9)
                active_info[ev_type] = {
                    "event_id": ev.event_id,
                    "segment_index": ev.segment_index,
                    "confirmed": ev.confirmed,
                    "currently_active": ev.currently_active,
                    "frames_collected": len(ev.frames),
                    "elapsed_clip_s": round(elapsed_clip_s, 2),
                    "max_clip_s": self.max_clip_seconds,
                }
            if not active_info:
                mode = "IDLE_RING_BUFFER (-3s)"
            elif any(v["segment_index"] > 1 for v in active_info.values()):
                mode = "ROLLOVER_NEW_SEGMENT (10s+)"
            elif any(v["confirmed"] for v in active_info.values()):
                mode = "CONFIRMED_RECORDING (2s->10s)"
            else:
                mode = "PRE_CONFIRM (0s->2s)"

            return {
                "mode": mode,
                "pre_event_seconds": self.pre_event_seconds,
                "post_detection_seconds": self.post_detection_seconds,
                "max_clip_seconds": self.max_clip_seconds,
                "sample_fps": self.buffer.sample_fps,
                "ring_buffer_frames": ring_frames,
                "active_events": active_info,
                "completed_segments": self._completed_segments_count,
                "rollover_segments": self._rollover_segments_count,
                "total_videos_saved_sd": self.storage.total_videos_saved_sd,
                "total_uploaded_server": self.upload_worker.total_uploaded_server,
                "wifi_stable": self.upload_worker.wifi_stable,
                "last_route_reason": self.upload_worker.last_route_reason,
                "last_saved_video_path": self.storage.last_saved_video_path,
                "last_finished_event": self._last_finished_metadata,
            }

    def _finish_due_events(self, timestamp_ns: int) -> None:
        for event_type, event in list(self._active.items()):
            due_by_post = (
                event.recording_end_ns is not None
                and timestamp_ns >= event.recording_end_ns
            )
            due_by_max_10s = (
                event.confirmed
                and timestamp_ns >= event.max_clip_end_ns
            )
            if due_by_post or due_by_max_10s:
                self._finish_event(
                    event_type,
                    complete=True,
                    allow_rollover=True,
                    current_timestamp_ns=timestamp_ns,
                )

    def _finish_event(
        self,
        event_type: str,
        complete: bool = True,
        allow_rollover: bool = True,
        current_timestamp_ns: Optional[int] = None,
    ) -> None:
        event = self._active.pop(event_type, None)
        if event is None or not event.confirmed:
            return
        frozen_frames = tuple(event.frames)
        actual_first_ns = frozen_frames[0].capture_timestamp_ns if frozen_frames else None
        actual_last_ns = frozen_frames[-1].capture_timestamp_ns if frozen_frames else None
        confirmed_ns = event.confirmed_at_ns
        target_end_ns = event.recording_end_ns
        actual_duration = (
            max(0.0, (actual_last_ns - actual_first_ns) / 1e9)
            if actual_first_ns is not None and actual_last_ns is not None else 0.0
        )
        sample_fps_actual = (
            (len(frozen_frames) - 1) / actual_duration
            if actual_duration > 0 and len(frozen_frames) > 1 else 0.0
        )
        required_first_ns = event.event_start_ns - int(self.pre_event_seconds * 1e9)
        has_pre = actual_first_ns is not None and actual_first_ns <= required_first_ns + int(0.35 * 1e9)
        has_post = (
            target_end_ns is not None and actual_last_ns is not None
            and actual_last_ns >= target_end_ns - int(0.25 * 1e9)
        )
        will_rollover = bool(allow_rollover and complete and event.currently_active)
        metadata = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "segment_index": event.segment_index,
            "continued_from": event.continued_from,
            "auto_rollover_next": will_rollover,
            "event_start_time": event.event_start_time,
            "event_start_ns": event.event_start_ns,
            "confirmed_at_ns": confirmed_ns,
            "confirmed_at_time": event.confirmed_at_time,
            "target_end_ns": target_end_ns,
            "recording_end_time": _utc_now(),
            "frame_count": len(frozen_frames),
            "sample_fps_target": self.buffer.sample_fps,
            "sample_fps_actual": round(sample_fps_actual, 3),
            "actual_first_frame_ns": actual_first_ns,
            "actual_last_frame_ns": actual_last_ns,
            "duration_actual_seconds": round(actual_duration, 3),
            "pre_event_coverage_seconds": round(
                max(0.0, (event.event_start_ns - actual_first_ns) / 1e9)
                if actual_first_ns is not None else 0.0, 3
            ),
            "post_confirmation_coverage_seconds": round(
                max(0.0, (actual_last_ns - confirmed_ns) / 1e9)
                if confirmed_ns is not None and actual_last_ns is not None else 0.0, 3
            ),
            "sequence_complete": bool(complete and has_pre and has_post),
            "image_format": "BGR_MP4",
            "image_scope": "full_camera_frame_resized",
            "face_crop_applied": False,
            "local_alert_state": event.local_status,
            "verification_status": "PENDING",
        }
        self._completed_segments_count += 1
        self._last_finished_metadata = dict(metadata)
        self.upload_worker.submit_event(metadata, list(frozen_frames))
        self.upload_worker.wake()

        # Nếu sau đủ 10s người vi phạm vẫn chưa tỉnh (event.currently_active == True):
        # tự động kết thúc đoạn 10s vừa rồi và lập tức mở đoạn 10s mới (Segment #N+1)!
        if will_rollover:
            self._rollover_segments_count += 1
            roll_ts = current_timestamp_ns or actual_last_ns or time.monotonic_ns()
            self.start_event(
                event_type=event.event_type,
                event_start_timestamp_ns=roll_ts,
                event_start_time=_utc_now(),
                segment_index=event.segment_index + 1,
                continued_from=event.event_id,
                pre_confirmed=True,
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_utc(value: Optional[str]) -> str:
    if not value:
        return _utc_now()
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except ValueError:
        return _utc_now()
