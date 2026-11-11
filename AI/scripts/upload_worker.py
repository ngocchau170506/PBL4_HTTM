"""Non-blocking store-and-forward worker for 10-second violation video clips (.mp4).

Chiến lược phân luồng thông minh trên Raspberry Pi 4:
1. Luồng nền ưu tiên thấp (cnn-upload-worker) nhận danh sách frame đóng băng từ EventRecorder
   -> kết xuất thành file video 10s (.mp4) nguyên tử trên thẻ nhớ SD.
2. Kiểm tra độ ổn định của Wi-Fi (wlan0 link/IP) và kết nối tới Server trung tâm:
   - Nếu Wi-Fi ổn định & Server phản hồi: gửi ngay sự kiện kèm đoạn video 10s + metadata về Server.
   - Nếu Wi-Fi yếu/mất mạng hoặc Server ngoại tuyến: giữ trực tiếp đoạn video 10s (.mp4) trong thẻ nhớ SD
     (AI/evidence/ & AI/data/pending_events/), tự động gửi bù ngầm khi Wi-Fi ổn định trở lại.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
from typing import Any, Callable, Optional
import urllib.request

import cv2

from AI.scripts.event_storage import EventStorage


def check_wifi_and_server_stable(server_url: str = "", timeout_s: float = 1.2) -> tuple[bool, str]:
    """Kiểm tra Wi-Fi trên Pi có ổn định và kết nối thông tới Server không."""
    # 1. Kiểm tra trạng thái giao diện Wi-Fi trên Linux (/proc/net/wireless hoặc sysfs)
    wireless_proc = Path("/proc/net/wireless")
    if wireless_proc.exists():
        try:
            content = wireless_proc.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if "wlan0:" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        link_qual = float(parts[2].rstrip("."))
                        if link_qual <= 5.0:
                            return False, f"WIFI_WEAK_LINK({link_qual})"
        except Exception:
            pass

    target_url = (server_url or os.getenv("REMOTE_SERVER_URL", "")).strip().rstrip("/")
    if not target_url or target_url in ("http://127.0.0.1:5000", "http://localhost:5000"):
        if os.getenv("REMOTE_SERVER_ENABLED", "1") != "1":
            return False, "SERVER_DISABLED_SD_ONLY"

    if not target_url:
        return False, "NO_SERVER_URL_SD_ONLY"
    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        target_url = f"http://{target_url}"

    try:
        req = urllib.request.Request(f"{target_url}/api/v1/health", method="GET")
        req.add_header("User-Agent", "DriverMonitorEdge/WifiStabilityCheck")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if 200 <= getattr(resp, "status", 200) < 300:
                return True, "WIFI_AND_SERVER_STABLE"
            return False, f"SERVER_HTTP_{getattr(resp, 'status', 0)}"
    except Exception as exc:
        return False, f"SERVER_UNREACHABLE({type(exc).__name__})"


class UploadWorker:
    def __init__(
        self,
        storage: EventStorage,
        transport: Optional[Callable[[dict[str, Any], str], bool]] = None,
        cloud_sync: Optional[Any] = None,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
    ):
        self.storage = storage
        self.cloud_sync = cloud_sync
        self.transport = transport or self._default_server_or_sd_transport
        self.retry_base_seconds = float(retry_base_seconds)
        self.retry_max_seconds = float(retry_max_seconds)
        self._event_queue: queue.Queue[tuple[dict[str, Any], list[Any]]] = queue.Queue(maxsize=8)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.wifi_stable: bool = False
        self.last_route_reason: str = "INIT_SD_CARD"
        self.total_uploaded_server: int = 0
        self.total_saved_sd_only: int = 0
        self._thread = threading.Thread(target=self._run, name="cnn-upload-worker", daemon=True)
        self._thread.start()

    def set_cloud_sync(self, cloud_sync: Any) -> None:
        self.cloud_sync = cloud_sync

    def submit_event(self, metadata: dict[str, Any], frames: list[Any]) -> None:
        try:
            self._event_queue.put_nowait((metadata, list(frames)))
        except queue.Full:
            print(f"[VIDEO 10s STORAGE] Queue full; dropped event {metadata.get('event_id')}")
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def close(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError("CNN upload worker did not stop before timeout")

    def _run(self) -> None:
        while True:
            self._save_queued_events()
            if self._stop.is_set() and self._event_queue.empty():
                return
            if self._stop.is_set():
                continue
            for event_id, event_dir, attempts in self.storage.pending():
                try:
                    meta_path = event_dir / "metadata.json"
                    if not meta_path.exists():
                        continue
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                    if self.transport(metadata, str(event_dir)):
                        self.storage.mark_uploaded(event_id)
                        self.total_uploaded_server += 1
                    else:
                        self.total_saved_sd_only = max(
                            self.total_saved_sd_only,
                            self.storage.total_videos_saved_sd - self.total_uploaded_server,
                        )
                        self._retry(event_id, attempts)
                except (OSError, ValueError, TypeError) as exc:
                    print(f"[VIDEO 10s UPLOAD] {event_id} deferred on SD card: {exc}")
                    self._retry(event_id, attempts)
            self._wake.wait(1.0)
            self._wake.clear()

    def _save_queued_events(self) -> None:
        remaining = self._event_queue.qsize()
        while remaining > 0:
            remaining -= 1
            try:
                metadata, frames = self._event_queue.get_nowait()
            except queue.Empty:
                return
            try:
                saved_dir = self.storage.save_event(metadata, frames)
                sd_video = metadata.get("sd_video_path") or self.storage.last_saved_video_path
                print(
                    f"[VIDEO 10s SAVED] Da luu video 10s ({metadata.get('event_type')} seg#{metadata.get('segment_index', 1)}, "
                    f"{len(frames)} frames, {metadata.get('duration_actual_seconds', 0):.1f}s) -> {sd_video or saved_dir}"
                )
            except (OSError, ValueError, TypeError) as exc:
                print(f"[VIDEO 10s STORAGE] {metadata.get('event_id')} deferred: {exc}")
            finally:
                self._event_queue.task_done()

    def _retry(self, event_id: str, attempts: int) -> None:
        next_attempts = attempts + 1
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** min(attempts, 6)))
        self.storage.mark_retry(event_id, next_attempts, time.time() + delay)

    def _default_server_or_sd_transport(self, metadata: dict[str, Any], event_dir_str: str) -> bool:
        """Nếu Wi-Fi ổn định và kết nối được Server thì gửi ngay về Server;
        nếu không thì bảo toàn trực tiếp trong thẻ nhớ SD và hẹn gửi bù sau."""
        event_dir = Path(event_dir_str)
        server_url = ""
        if self.cloud_sync is not None:
            server_url = getattr(self.cloud_sync, "server_url", "") or ""

        stable, reason = check_wifi_and_server_stable(server_url=server_url, timeout_s=1.2)
        self.wifi_stable = stable
        self.last_route_reason = reason
        if not stable:
            return False

        # Đọc ảnh đại diện lúc 2s (thumbnail.jpg) và thông tin file video 10s (.mp4)
        thumb_path = event_dir / str(metadata.get("thumbnail_filename", "thumbnail.jpg"))
        video_path = event_dir / str(metadata.get("video_filename", "video.mp4"))
        thumb_img = cv2.imread(str(thumb_path)) if thumb_path.exists() else None
        video_b64 = ""
        if video_path.exists() and video_path.stat().st_size <= 4 * 1024 * 1024:
            try:
                video_b64 = "data:video/mp4;base64," + base64.b64encode(video_path.read_bytes()).decode("ascii")
            except Exception:
                video_b64 = ""

        clip_meta = {
            "event_id": metadata.get("event_id"),
            "segment_index": metadata.get("segment_index", 1),
            "continued_from": metadata.get("continued_from"),
            "clip_timeline": "-3s_pre -> 0s_onset -> 2s_alert -> 10s_close",
            "pre_event_coverage_seconds": metadata.get("pre_event_coverage_seconds"),
            "post_confirmation_coverage_seconds": metadata.get("post_confirmation_coverage_seconds"),
            "duration_actual_seconds": metadata.get("duration_actual_seconds"),
            "frame_count": metadata.get("frame_count"),
            "sd_video_path": metadata.get("sd_video_path"),
            "video_mp4_base64": video_b64,
        }

        if self.cloud_sync is not None and getattr(self.cloud_sync, "enabled", False):
            try:
                self.cloud_sync.dispatch_violation(
                    alert_type=f"{metadata.get('event_type', 'DROWSY')}_ALERT",
                    frame=thumb_img,
                    started_at=metadata.get("event_start_time"),
                    confirmed_at=metadata.get("confirmed_at_time"),
                    duration_seconds=float(metadata.get("duration_actual_seconds") or 10.0),
                    confidence=metadata.get("confidence", 1.0),
                    meta=clip_meta,
                )
                return True
            except Exception:
                return False
        return False
