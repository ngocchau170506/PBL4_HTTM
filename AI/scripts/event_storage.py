"""Atomic on-disk 10-second video clip storage and durable SD card / upload queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any, Iterable, Optional

import cv2


class EventStorage:
    """Lưu trữ nguyên tử đoạn video vi phạm 10s (-3s -> 0s -> 2s -> 10s) ra thẻ nhớ SD (.mp4)
    và quản lý hàng đợi đồng bộ SQLite có giới hạn dung lượng (trần 512MB)."""

    def __init__(
        self,
        root_dir: str | Path,
        max_storage_mb: int = 512,
        evidence_dir: Optional[str | Path] = None,
        save_individual_frames: bool = False,
    ):
        self.root_dir = Path(root_dir)
        self.pending_dir = self.root_dir / "pending_events"
        self.db_path = self.root_dir / "event_queue.db"
        self.evidence_dir = Path(
            evidence_dir
            or os.getenv("EVIDENCE_DIR", str(self.root_dir.parent / "evidence"))
        )
        self.save_individual_frames = bool(save_individual_frames)
        self.max_storage_bytes = int(max_storage_mb * 1024 * 1024)
        self._lock = threading.RLock()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS event_queue (
                event_id TEXT PRIMARY KEY,
                event_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        self._connection.commit()
        self.last_saved_video_path: Optional[str] = None
        self.total_videos_saved_sd: int = 0

    @staticmethod
    def _write_mp4_clip(video_path: Path, frame_list: list[Any], fps: float) -> Path:
        """Ghi chuỗi khung hình liên tục thành file video .mp4 trên luồng nền (không chặn AI)."""
        if not frame_list:
            raise ValueError("Danh sách khung hình rỗng, không thể tạo video 10s")
        first_img = frame_list[0].image
        height, width = first_img.shape[:2]
        safe_fps = max(1.0, min(30.0, float(fps or 10.0)))

        # Thử mở codec mp4v chuẩn nhúng (hoặc MJPG .avi nếu hệ thống thiếu mp4v)
        for codec, suffix in (("mp4v", ".mp4"), ("avc1", ".mp4"), ("MJPG", ".avi")):
            candidate_path = video_path.with_suffix(suffix)
            fourcc = cv2.VideoWriter_fourcc(*codec)
            writer = cv2.VideoWriter(str(candidate_path), fourcc, safe_fps, (width, height))
            if writer.isOpened():
                try:
                    for item in frame_list:
                        img = item.image
                        if img.shape[:2] != (height, width):
                            img = cv2.resize(img, (width, height), interpolation=cv2.INTER_LINEAR)
                        writer.write(img)
                finally:
                    writer.release()
                if candidate_path.exists() and candidate_path.stat().st_size > 0:
                    return candidate_path
        raise OSError(f"Không thể khởi tạo cv2.VideoWriter cho {video_path}")

    def save_event(self, event: dict[str, Any], frames: Iterable[Any]) -> Path:
        frame_list = list(frames)
        if not frame_list:
            raise ValueError("Không có khung hình nào để lưu video 10s")

        # Kiểm tra khóa thứ tự frame nghiêm ngặt: loại bỏ mọi nguy cơ trùng lặp hoặc lùi thứ tự
        ordered_frames = []
        last_fid = -1
        last_ts = -1
        for f in frame_list:
            if f.frame_id > last_fid and f.capture_timestamp_ns > last_ts:
                ordered_frames.append(f)
                last_fid = f.frame_id
                last_ts = f.capture_timestamp_ns
        if not ordered_frames:
            ordered_frames = frame_list

        event_id = str(event["event_id"])
        target = self.pending_dir / event_id
        temporary = self.pending_dir / f".{event_id}.tmp"
        with self._lock:
            if target.exists():
                return target
            self.enforce_limit()
            if self._disk_usage() >= self.max_storage_bytes:
                raise OSError("Event storage quota exceeded")
            if temporary.exists():
                self._remove_tree(temporary)
            temporary.mkdir(parents=True, exist_ok=False)

            frame_metadata = []
            try:
                target_fps = float(event.get("sample_fps_target") or event.get("sample_fps_actual") or 10.0)
                video_file_tmp = self._write_mp4_clip(
                    temporary / "video.mp4",
                    ordered_frames,
                    fps=target_fps,
                )

                # Chọn khung hình tại mốc xác nhận vi phạm (2s) làm thumbnail đại diện
                confirmed_ns = event.get("confirmed_at_ns")
                thumb_frame = ordered_frames[len(ordered_frames) // 2]
                if confirmed_ns is not None:
                    best_f = min(
                        ordered_frames,
                        key=lambda x: abs(x.capture_timestamp_ns - int(confirmed_ns)),
                    )
                    thumb_frame = best_f
                thumb_path = temporary / "thumbnail.jpg"
                cv2.imwrite(str(thumb_path), thumb_frame.image)

                if self.save_individual_frames:
                    frames_dir = temporary / "frames"
                    frames_dir.mkdir(exist_ok=True)
                    for index, frame in enumerate(ordered_frames):
                        filename = f"{index:04d}.jpg"
                        path = frames_dir / filename
                        if not cv2.imwrite(str(path), frame.image):
                            raise OSError(f"Could not write event frame: {path}")

                for index, frame in enumerate(ordered_frames):
                    frame_metadata.append({
                        "index": index,
                        "frame_id": frame.frame_id,
                        "capture_timestamp_ns": frame.capture_timestamp_ns,
                        "capture_time_utc": frame.capture_time_utc,
                        "face_valid": frame.face_valid,
                        "metadata": frame.metadata,
                    })

                seg_idx = int(event.get("segment_index", 1))
                ev_type = str(event.get("event_type", "VIOLATION")).upper()
                safe_ts = (
                    str(event.get("confirmed_at_time") or event.get("event_start_time") or "")
                    .replace(":", "")
                    .replace("-", "")
                    .replace(".", "_")
                    .replace("+", "_")
                )[:22] or event_id[:16]
                sd_video_name = f"{ev_type}_{safe_ts}_seg{seg_idx}_10s{video_file_tmp.suffix}"
                sd_video_path = self.evidence_dir / sd_video_name
                shutil.copy2(str(video_file_tmp), str(sd_video_path))

                payload = dict(event)
                payload["video_filename"] = video_file_tmp.name
                payload["sd_video_path"] = str(sd_video_path)
                payload["thumbnail_filename"] = "thumbnail.jpg"
                payload["frame_count"] = len(ordered_frames)
                payload["frames"] = frame_metadata

                metadata_path = temporary / "metadata.json"
                metadata_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                with metadata_path.open("r+b") as metadata_file:
                    os.fsync(metadata_file.fileno())
                temporary.replace(target)

                # Ghi nhận vào nhật ký sự kiện trên thẻ nhớ SD (events.jsonl)
                events_jsonl = self.evidence_dir / "events.jsonl"
                summary_row = {
                    "event_id": event_id,
                    "event_type": ev_type,
                    "segment_index": seg_idx,
                    "continued_from": event.get("continued_from"),
                    "event_start_time": event.get("event_start_time"),
                    "confirmed_at_time": event.get("confirmed_at_time"),
                    "recording_end_time": event.get("recording_end_time"),
                    "pre_event_coverage_seconds": event.get("pre_event_coverage_seconds"),
                    "post_confirmation_coverage_seconds": event.get("post_confirmation_coverage_seconds"),
                    "duration_actual_seconds": event.get("duration_actual_seconds"),
                    "frame_count": len(ordered_frames),
                    "evidence_file": sd_video_name,
                    "sd_video_path": str(sd_video_path),
                    "storage_mode": "VIDEO_10S_MP4",
                }
                with events_jsonl.open("a", encoding="utf-8") as jf:
                    jf.write(json.dumps(summary_row, ensure_ascii=False) + "\n")

                try:
                    self._connection.execute(
                        "INSERT INTO event_queue(event_id, event_dir, created_at) VALUES (?, ?, ?)",
                        (event_id, str(target), str(event["recording_end_time"])),
                    )
                    self._connection.commit()
                except Exception:
                    self._remove_tree(target)
                    raise

                self.last_saved_video_path = str(sd_video_path)
                self.total_videos_saved_sd += 1
            except Exception:
                if temporary.exists():
                    self._remove_tree(temporary)
                raise
        self.enforce_limit()
        return target

    def pending(self) -> list[tuple[str, Path, int]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id, event_dir, attempts FROM event_queue "
                "WHERE status = 'PENDING' AND next_attempt_at <= strftime('%s','now') "
                "ORDER BY created_at"
            ).fetchall()
            return [(event_id, Path(event_dir), attempts) for event_id, event_dir, attempts in rows]

    def mark_retry(self, event_id: str, attempts: int, next_attempt_at: float) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE event_queue SET attempts = ?, next_attempt_at = ? WHERE event_id = ?",
                (attempts, next_attempt_at, event_id),
            )
            self._connection.commit()

    def mark_uploaded(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE event_queue SET status = 'UPLOADED' WHERE event_id = ?",
                (event_id,),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enforce_limit(self) -> None:
        with self._lock:
            size = self._disk_usage()
            if size <= self.max_storage_bytes:
                return
            rows = self._connection.execute(
                "SELECT event_id, event_dir FROM event_queue ORDER BY CASE WHEN status = 'UPLOADED' THEN 0 ELSE 1 END, created_at"
            ).fetchall()
            for event_id, event_dir in rows:
                self._remove_tree(Path(event_dir))
                self._connection.execute("DELETE FROM event_queue WHERE event_id = ?", (event_id,))
                print(f"[VIDEO 10s STORAGE] Quota: removed oldest event {event_id}")
                size = self._disk_usage()
                if size <= self.max_storage_bytes:
                    break
            self._connection.commit()

    def _disk_usage(self):
        return sum(path.stat().st_size for path in self.root_dir.rglob("*") if path.is_file())

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
