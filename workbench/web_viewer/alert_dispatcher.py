"""Bộ điều phối cảnh báo tự động từ AI sang ESP32 và Loa I2S (Alert Dispatcher).

Giám sát kết quả nhận diện (YOLO best.pt hoặc MediaPipe face_landmarker.task):
- Nhắm mắt kéo dài (>= eye_closed_sec, mặc định 1.2s): Tự động gửi VOICE:1 (hoặc ALERT:2).
- Ngáp há miệng kéo dài (>= yawn_sec, mặc định 1.5s): Tự động gửi VOICE:2.
- Gục đầu hoặc mất tập trung (>= head_sec, mặc định 1.5s): Tự động gửi VOICE:3.
- Cơ chế giãn cách (cooldown) tránh gửi lệnh dồn dập làm nghẽn còi/loa.
- Ghi nhận trạng thái vào StateHub để Web UI và Video Overlay hiển thị trực quan.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Set


DROWSY_LABELS: Set[str] = {
    "Eyeclosed",
    "asleep",
    "closed",
    "close",
    "Drowsy eye",
}

YAWN_LABELS: Set[str] = {
    "Yawn",
    "yawn",
    "Open-Mouth",
}

ATTENTIVE_LABELS: Set[str] = {
    "Attentive eye",
    "open",
    "noYawn",
}


class AlertDispatcher:
    """Bộ điều phối cảnh báo tự động kết nối AI với ESP32 Bridge."""

    def __init__(
        self,
        bridge: Optional[Any] = None,
        hub: Optional[Any] = None,
        eye_closed_sec: float = 1.2,
        yawn_sec: float = 1.5,
        head_sec: float = 1.5,
        cooldown_sec: float = 5.0,
        min_conf: float = 0.30,
        enabled: bool = True,
    ) -> None:
        self._bridge = bridge
        self._hub = hub
        self._eye_closed_sec = max(0.5, float(eye_closed_sec))
        self._yawn_sec = max(0.5, float(yawn_sec))
        self._head_sec = max(0.5, float(head_sec))
        self._cooldown_sec = max(1.0, float(cooldown_sec))
        self._min_conf = float(min_conf)
        self._enabled = bool(enabled)

        # Trạng thái theo dõi thời gian
        self._eye_closed_start: Optional[float] = None
        self._yawn_start: Optional[float] = None
        self._head_distracted_start: Optional[float] = None

        self._last_alert_ts: Dict[str, float] = {
            "eye": -999999.0,
            "yawn": -999999.0,
            "head": -999999.0,
        }

        self._alert_counts: Dict[str, int] = {
            "eye": 0,
            "yawn": 0,
            "head": 0,
        }

        self._last_event: Optional[Dict[str, Any]] = None
        self._active_alert_banner: str = ""
        self._banner_clear_ts: float = 0.0

        self._sync_hub_meta()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.reset_state()
        self._sync_hub_meta()

    def set_bridge(self, bridge: Optional[Any]) -> None:
        self._bridge = bridge
        self._sync_hub_meta()

    def reset_state(self) -> None:
        self._eye_closed_start = None
        self._yawn_start = None
        self._head_distracted_start = None
        self._active_alert_banner = ""
        self._banner_clear_ts = 0.0

    def feed_detections(
        self,
        items: Sequence[Dict[str, Any]],
        now_s: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phân tích các đối tượng nhận diện từ YOLO (best.pt) và kích hoạt cảnh báo."""
        if not self._enabled:
            return None

        now = float(now_s) if now_s is not None else time.time()
        has_closed_eye = False
        has_yawn = False

        for it in items:
            lbl = str(it.get("label", ""))
            conf = float(it.get("conf", 0.0))
            if conf < self._min_conf:
                continue
            if lbl in DROWSY_LABELS:
                has_closed_eye = True
            elif lbl in YAWN_LABELS:
                has_yawn = True

        event_triggered: Optional[Dict[str, Any]] = None

        # 1. Theo dõi trạng thái nhắm mắt
        if has_closed_eye:
            if self._eye_closed_start is None:
                self._eye_closed_start = now
            duration = now - self._eye_closed_start
            if duration >= self._eye_closed_sec:
                if (now - self._last_alert_ts["eye"]) >= self._cooldown_sec:
                    event_triggered = self._trigger_alert(
                        event_type="eye",
                        command="VOICE:1",
                        duration=duration,
                        message=f"Phát hiện buồn ngủ (nhắm mắt {duration:.1f}s)",
                        now=now,
                    )
        else:
            self._eye_closed_start = None

        # 2. Theo dõi trạng thái ngáp
        if has_yawn and event_triggered is None:
            if self._yawn_start is None:
                self._yawn_start = now
            duration = now - self._yawn_start
            if duration >= self._yawn_sec:
                if (now - self._last_alert_ts["yawn"]) >= self._cooldown_sec:
                    event_triggered = self._trigger_alert(
                        event_type="yawn",
                        command="VOICE:2",
                        duration=duration,
                        message=f"Phát hiện ngáp dài ({duration:.1f}s)",
                        now=now,
                    )
        else:
            self._yawn_start = None

        self._clean_banner_if_expired(now)
        return event_triggered

    def feed_landmarks_event(
        self,
        ear: Optional[float],
        mar: Optional[float],
        pose: Optional[Sequence[float]] = None,
        now_s: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phân tích kết quả từ MediaPipe face_landmarker (EAR, MAR, Pitch/Yaw/Roll)."""
        if not self._enabled:
            return None

        now = float(now_s) if now_s is not None else time.time()
        event_triggered: Optional[Dict[str, Any]] = None

        # Ngưỡng hình học MediaPipe
        is_eye_closed = (ear is not None) and (ear < 0.20)
        is_yawning = (mar is not None) and (mar > 0.55)
        is_distracted = False
        if pose is not None and len(pose) >= 3:
            pitch, yaw, roll = float(pose[0]), float(pose[1]), float(pose[2])
            if abs(yaw) > 35.0 or abs(pitch) > 22.0 or abs(roll) > 25.0:
                is_distracted = True

        # 1. Nhắm mắt (EAR)
        if is_eye_closed:
            if self._eye_closed_start is None:
                self._eye_closed_start = now
            duration = now - self._eye_closed_start
            if duration >= self._eye_closed_sec:
                if (now - self._last_alert_ts["eye"]) >= self._cooldown_sec:
                    event_triggered = self._trigger_alert(
                        event_type="eye",
                        command="VOICE:1",
                        duration=duration,
                        message=f"Phát hiện buồn ngủ qua EAR ({ear:.2f}, {duration:.1f}s)",
                        now=now,
                    )
        else:
            self._eye_closed_start = None

        # 2. Ngáp (MAR)
        if is_yawning and event_triggered is None:
            if self._yawn_start is None:
                self._yawn_start = now
            duration = now - self._yawn_start
            if duration >= self._yawn_sec:
                if (now - self._last_alert_ts["yawn"]) >= self._cooldown_sec:
                    event_triggered = self._trigger_alert(
                        event_type="yawn",
                        command="VOICE:2",
                        duration=duration,
                        message=f"Phát hiện ngáp qua MAR ({mar:.2f}, {duration:.1f}s)",
                        now=now,
                    )
        else:
            self._yawn_start = None

        # 3. Mất tập trung / quay đầu (Pose)
        if is_distracted and event_triggered is None:
            if self._head_distracted_start is None:
                self._head_distracted_start = now
            duration = now - self._head_distracted_start
            if duration >= self._head_sec:
                if (now - self._last_alert_ts["head"]) >= self._cooldown_sec:
                    event_triggered = self._trigger_alert(
                        event_type="head",
                        command="VOICE:3",
                        duration=duration,
                        message=f"Phát hiện mất tập trung ({duration:.1f}s)",
                        now=now,
                    )
        else:
            self._head_distracted_start = None

        self._clean_banner_if_expired(now)
        return event_triggered

    def _trigger_alert(
        self,
        event_type: str,
        command: str,
        duration: float,
        message: str,
        now: float,
    ) -> Dict[str, Any]:
        self._last_alert_ts[event_type] = now
        self._alert_counts[event_type] = self._alert_counts.get(event_type, 0) + 1

        sent_to_esp32 = False
        error_msg: Optional[str] = None
        if self._bridge is not None:
            try:
                res = self._bridge.send_command(command)
                sent_to_esp32 = bool(res.get("ok", False))
                if not sent_to_esp32:
                    error_msg = res.get("error")
            except Exception as exc:
                error_msg = str(exc)

        event_data = {
            "type": event_type,
            "command": command,
            "duration": round(duration, 2),
            "message": message,
            "ts": now,
            "sent_esp32": sent_to_esp32,
            "error": error_msg,
        }
        self._last_event = event_data
        self._active_alert_banner = f"CANH BAO: {message.upper()} -> {command}"
        self._banner_clear_ts = now + 4.0

        if self._hub is not None:
            self._hub.set_meta("last_auto_alert", event_data)
            self._hub.set_meta("auto_alert_banner", self._active_alert_banner)
            self._hub.set_meta("auto_alert_counts", dict(self._alert_counts))

        return event_data

    def _clean_banner_if_expired(self, now: float) -> None:
        if self._active_alert_banner and now >= self._banner_clear_ts:
            self._active_alert_banner = ""
            if self._hub is not None:
                self._hub.set_meta("auto_alert_banner", "")

    def _sync_hub_meta(self) -> None:
        if self._hub is not None:
            self._hub.set_meta("auto_alert_enabled", self._enabled)
            self._hub.set_meta("auto_alert_counts", dict(self._alert_counts))
            self._hub.set_meta("auto_alert_banner", self._active_alert_banner)

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "eye_closed_sec": self._eye_closed_sec,
            "yawn_sec": self._yawn_sec,
            "head_sec": self._head_sec,
            "cooldown_sec": self._cooldown_sec,
            "counts": dict(self._alert_counts),
            "last_event": self._last_event,
            "active_banner": self._active_alert_banner,
        }
