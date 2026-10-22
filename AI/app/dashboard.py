"""Modern OpenCV dashboard for YOLO-based driver monitoring."""

import textwrap
from datetime import datetime

import cv2
import numpy as np

from AI.scripts.time_utils import duration_hms


class Dashboard:
    WINDOW = "AI Driver Monitor"
    CANVAS_SIZE = (1440, 810)

    # OpenCV uses BGR colors.
    BG = (18, 20, 25)
    PANEL = (27, 30, 37)
    CARD = (34, 38, 46)
    BORDER = (57, 64, 76)
    WHITE = (242, 245, 250)
    MUTED = (155, 165, 180)
    GREEN = (90, 220, 145)
    AMBER = (45, 190, 255)
    RED = (75, 80, 245)
    CYAN = (235, 195, 75)
    PURPLE = (205, 115, 235)

    def __init__(self):
        self.action = None
        self._buttons = {}
        self._eye_duration = 0.0
        self._mouth_duration = 0.0
        self._last_state = {}
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, *self.CANVAS_SIZE)
        cv2.setMouseCallback(self.WINDOW, self._on_mouse)

    def _on_mouse(self, event, x, y, _flags, _param):
        if event != cv2.EVENT_LBUTTONUP:
            return
        try:
            _, _, window_width, window_height = cv2.getWindowImageRect(self.WINDOW)
            if window_width > 0 and window_height > 0:
                x = int(x * self.CANVAS_SIZE[0] / window_width)
                y = int(y * self.CANVAS_SIZE[1] / window_height)
        except cv2.error:
            pass
        for name, (x1, y1, x2, y2) in self._buttons.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.action = name

    def poll_action(self):
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            return "quit"
        action, self.action = self.action, None
        return action

    @staticmethod
    def _fit_frame(frame, width, height):
        source_height, source_width = frame.shape[:2]
        scale = min(width / source_width, height / source_height)
        resized = cv2.resize(
            frame,
            (max(1, int(source_width * scale)), max(1, int(source_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        top = (height - resized.shape[0]) // 2
        bottom = height - resized.shape[0] - top
        left = (width - resized.shape[1]) // 2
        right = width - resized.shape[1] - left
        return cv2.copyMakeBorder(
            resized, top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=(12, 14, 18),
        )

    @staticmethod
    def _text(canvas, value, origin, size=0.55, color=None, thickness=1):
        if color is None:
            color = Dashboard.WHITE
        cv2.putText(
            canvas, str(value), origin, cv2.FONT_HERSHEY_SIMPLEX,
            size, color, thickness, cv2.LINE_AA,
        )

    @staticmethod
    def _rounded_card(canvas, rect, fill, border=None, radius=14, thickness=1):
        x1, y1, x2, y2 = rect
        border = border or fill
        cv2.rectangle(canvas, (x1 + radius, y1), (x2 - radius, y2), fill, -1)
        cv2.rectangle(canvas, (x1, y1 + radius), (x2, y2 - radius), fill, -1)
        for center in ((x1 + radius, y1 + radius), (x2 - radius, y1 + radius),
                       (x1 + radius, y2 - radius), (x2 - radius, y2 - radius)):
            cv2.circle(canvas, center, radius, fill, -1, cv2.LINE_AA)
        cv2.rectangle(canvas, (x1 + radius, y1), (x2 - radius, y2), border, thickness)
        cv2.rectangle(canvas, (x1, y1 + radius), (x2, y2 - radius), border, thickness)

    def _card(self, canvas, rect, title, accent=None):
        self._rounded_card(canvas, rect, self.CARD, self.BORDER, 12, 1)
        x1, y1, x2, _ = rect
        if accent:
            cv2.line(canvas, (x1 + 14, y1 + 17), (x1 + 14, y1 + 43), accent, 4, cv2.LINE_AA)
        self._text(canvas, title, (x1 + 28, y1 + 32), 0.43, self.MUTED, 1)

    def _base(self, title, subtitle):
        canvas = np.full((self.CANVAS_SIZE[1], self.CANVAS_SIZE[0], 3), self.BG, dtype=np.uint8)
        self._text(canvas, title, (32, 43), 0.88, self.WHITE, 2)
        self._text(canvas, subtitle, (35, 70), 0.42, self.MUTED, 1)
        now = datetime.now()
        self._text(canvas, now.strftime("%d/%m/%Y"), (1190, 38), 0.47, self.WHITE, 1)
        self._text(canvas, now.strftime("%H:%M:%S"), (1320, 38), 0.47, self.CYAN, 1)
        cv2.line(canvas, (32, 91), (1408, 91), self.BORDER, 1, cv2.LINE_AA)
        return canvas

    def _side_panel(self, canvas, rect):
        x1, y1, x2, y2 = rect
        self._rounded_card(canvas, rect, self.PANEL, self.BORDER, 16, 1)

    def _button(self, canvas, name, rect, label, enabled=True):
        self._buttons[name] = rect
        x1, y1, x2, y2 = rect
        color = self.GREEN if enabled else self.BORDER
        self._rounded_card(canvas, rect, color, color, 10, 1)
        self._text(canvas, label, (x1 + 15, y1 + 29), 0.48,
                   self.BG if enabled else self.MUTED, 2)

    @staticmethod
    def _format_metric(value, digits=2):
        if value is None or value == "N/A":
            return "—"
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    def show_alignment(self, frame, status, metrics=None, can_continue=False):
        canvas = self._base("DRIVER SAFETY MONITOR", "STEP 1  /  FACE ALIGNMENT")
        canvas[112:752, 32:912] = self._fit_frame(frame, 880, 640)
        self._side_panel(canvas, (936, 112, 1408, 752))
        self._text(canvas, "ALIGNMENT CHECK", (968, 157), 0.55, self.CYAN, 1)
        for index, (label, value) in enumerate((metrics or {}).items()):
            y = 215 + index * 48
            self._text(canvas, label, (970, y), 0.43, self.MUTED)
            self._text(canvas, value, (1170, y), 0.53, self.GREEN if value == "OK" else self.WHITE, 1)
        self._card(canvas, (964, 490, 1380, 585), "STATUS",
                   self.GREEN if can_continue else self.AMBER)
        for i, line in enumerate(textwrap.wrap(status, 40)[:2]):
            self._text(canvas, line, (984, 538 + i * 25), 0.43,
                       self.GREEN if can_continue else self.AMBER)
        self._buttons.clear()
        self._button(canvas, "continue", (964, 665, 1150, 715), "CONTINUE", can_continue)
        self._button(canvas, "quit", (1170, 665, 1380, 715), "QUIT")
        cv2.imshow(self.WINDOW, canvas)

    def show_calibration(self, frame, data):
        canvas = self._base("DRIVER SAFETY MONITOR", "STEP 2  /  PERSONAL CALIBRATION")
        canvas[112:752, 32:912] = self._fit_frame(frame, 880, 640)
        self._side_panel(canvas, (936, 112, 1408, 752))
        self._text(canvas, "CALIBRATING BASELINE", (968, 157), 0.55, self.CYAN)
        remaining = float(data.get("remaining", 0.0))
        duration = float(data.get("duration", 15.0))
        self._text(canvas, f"{remaining:04.1f}s remaining", (970, 207), 0.7, self.WHITE, 2)
        progress = 1.0 - min(remaining / duration, 1.0) if duration > 0 else 1.0
        self._progress(canvas, (970, 225, 1375, 238), progress, self.CYAN)
        rows = (
            ("FACE", "DETECTED" if data.get("face_detected") else "NOT DETECTED"),
            ("EAR", self._format_metric(data.get("ear"))),
            ("MAR", self._format_metric(data.get("mar"))),
            ("YAW", self._format_metric(data.get("yaw"), 1)),
            ("PITCH", self._format_metric(data.get("pitch"), 1)),
            ("ROLL", self._format_metric(data.get("roll"), 1)),
            ("VALID SAMPLES", data.get("sample_count", 0)),
        )
        for i, (label, value) in enumerate(rows):
            y = 290 + i * 48
            self._text(canvas, label, (970, y), 0.43, self.MUTED)
            self._text(canvas, value, (1170, y), 0.5, self.WHITE)
        self._buttons.clear()
        self._button(canvas, "quit", (1170, 665, 1380, 715), "QUIT")
        cv2.imshow(self.WINDOW, canvas)

    def show_message(self, frame, title, message, can_start=False):
        canvas = self._base(title, "DRIVER SAFETY MONITOR")
        canvas[112:752, 32:912] = self._fit_frame(frame, 880, 640)
        self._side_panel(canvas, (936, 112, 1408, 752))
        self._text(canvas, "SYSTEM STATUS", (968, 157), 0.55, self.CYAN)
        for i, line in enumerate(textwrap.wrap(message, 40)[:8]):
            self._text(canvas, line, (970, 205 + i * 30), 0.45, self.WHITE)
        self._buttons.clear()
        if can_start:
            self._button(canvas, "start", (964, 665, 1160, 715), "START MONITORING")
        self._button(canvas, "quit", (1170, 665, 1380, 715), "QUIT")
        cv2.imshow(self.WINDOW, canvas)

    @staticmethod
    def _progress(canvas, rect, ratio, color):
        x1, y1, x2, y2 = rect
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (56, 62, 73), -1)
        fill = x1 + int((x2 - x1) * max(0.0, min(1.0, float(ratio))))
        if fill > x1:
            cv2.rectangle(canvas, (x1, y1), (fill, y2), color, -1)

    def _duration_card(self, canvas, rect, title, duration, threshold, color, extra_text=None):
        self._card(canvas, rect, title, color)
        x1, y1, x2, _ = rect
        self._text(canvas, f"{duration:.1f}s / {threshold:.1f}s",
                   (x1 + 18, y1 + 58), 0.53, self.WHITE, 1)
        self._progress(canvas, (x1 + 18, y1 + 67, x2 - 18, y1 + 76),
                       duration / threshold if threshold else 0, color)
        if extra_text:
            self._text(canvas, str(extra_text), (x1 + 18, y1 + 93), 0.34, self.MUTED, 1)

    @staticmethod
    def _normalize_detections(state):
        detections = state.get("detections")
        if detections:
            normalized = []
            for item in detections:
                try:
                    name, confidence = item[0], float(item[1])
                    normalized.append((str(name), confidence))
                except (TypeError, ValueError, IndexError):
                    continue
            return normalized

        # Compatibility with the simple YOLO-only pipeline that returns
        # detected_classes and status.
        names = state.get("detected_classes", [])
        if isinstance(names, str):
            names = [names]
        return [(str(name), 0.0) for name in names]

    def show_monitor(self, dashboard_frame, state=None):
        if state is None:
            cv2.imshow(self.WINDOW, dashboard_frame)
            return

        state = state or {}
        detections = self._normalize_detections(state)
        self._eye_duration = max(0.0, float(state.get("eye_duration", 0.0) or 0.0))
        self._mouth_duration = max(0.0, float(state.get("mouth_duration", 0.0) or 0.0))
        canvas = self._base("DRIVER SAFETY MONITOR", "LIVE MONITORING  /  YOLO OBJECT DETECTION")

        # Camera view
        canvas[112:752, 32:912] = self._fit_frame(dashboard_frame, 880, 640)
        # Small camera tag
        self._rounded_card(canvas, (52, 128, 230, 166), (20, 25, 31), (20, 25, 31), 8, 1)
        self._text(canvas, "LIVE CAMERA", (67, 153), 0.38, self.GREEN, 1)

        self._side_panel(canvas, (936, 112, 1408, 752))
        self._text(canvas, "● SYSTEM ACTIVE", (1195, 145), 0.38, self.GREEN, 1)

        names = [name.casefold().replace(" ", "_") for name, _ in detections]
        phone = any("phone" in n or "mobile" in n for n in names)
        smoking = any("smok" in n or "cigarette" in n for n in names)
        distracted = any("distracted" in n or "head_turn" in n for n in names)
        normal = any(n == "normal" or "attentive" in n or "eye_open" in n for n in names)

        confirmed_alert = str(state.get("confirmed_alert", "NORMAL")).upper()
        mp_active = bool(state.get("mp_active", False))
        drowsy_state = str(state.get("drowsy_state", "IDLE")).upper()
        yawning_state = str(state.get("yawning_state", "IDLE")).upper()

        if confirmed_alert == "DROWSY":
            status = "DROWSINESS WARNING"
            status_color = self.RED
        elif confirmed_alert == "SUSPECTED":
            status = "SUSPECTED DROWSINESS"
            status_color = self.AMBER
        elif confirmed_alert == "YAWNING":
            status = "YAWNING WARNING"
            status_color = self.AMBER
        elif confirmed_alert == "CONFLICT":
            status = "VERIFICATION CONFLICT"
            status_color = self.AMBER
        elif confirmed_alert == "TRACKING_LOST":
            status = "FACE TRACKING LOST"
            status_color = self.AMBER
        elif mp_active:
            status = "VERIFYING (MEDIAPIPE ACTIVE)"
            status_color = self.CYAN
        elif drowsy_state == "WATCHING" or yawning_state == "WATCHING":
            status = "WATCHING FOR DROWSINESS"
            status_color = self.CYAN
        elif phone or smoking or distracted:
            status = "DISTRACTION DETECTED"
            status_color = self.RED
        elif normal:
            status = "ATTENTIVE"
            status_color = self.GREEN
        elif state.get("status") and str(state["status"]).upper() not in ("N/A", "NONE"):
            status = str(state["status"]).upper()[:28]
            status_color = self.AMBER
        elif detections:
            status = "OBJECTS DETECTED"
            status_color = self.CYAN
        else:
            status = "SEARCHING FOR DRIVER"
            status_color = self.AMBER

        self._card(canvas, (960, 168, 1384, 258), "CURRENT DRIVER STATUS", status_color)
        self._text(canvas, status, (982, 225), 0.56, status_color, 2)

        # Hiển thị cả hai state machine vì drowsiness và yawning được đánh giá độc lập.
        self._text(
            canvas,
            f"State machine: EYE={drowsy_state} | MOUTH={yawning_state}",
            (982, 246),
            0.29,
            self.MUTED,
        )

        # Thông tin MediaPipe trên card thời lượng.
        eye_extra = None
        if mp_active:
            v_samples = int(state.get("drowsy_valid_samples", 0) or 0)
            v_ratio = float(state.get("drowsy_vote_ratio", 0.0) or 0.0)
            eye_extra = (
                f"EAR: {self._format_metric(state.get('mp_ear'), 3)}"
                f" | Valid votes: {v_samples} ({v_ratio * 100:.0f}%)"
            )
        elif state.get("ear_threshold") is not None:
            eye_extra = f"EAR Thresh: {state.get('ear_threshold'):.3f}"

        yawn_extra = None
        if mp_active:
            y_samples = int(state.get("yawning_valid_samples", 0) or 0)
            y_ratio = float(state.get("yawning_vote_ratio", 0.0) or 0.0)
            yawn_extra = (
                f"MAR: {self._format_metric(state.get('mp_mar'), 3)}"
                f" | Valid votes: {y_samples} ({y_ratio * 100:.0f}%)"
            )
        elif state.get("mar_threshold") is not None:
            yawn_extra = f"MAR Thresh: {state.get('mar_threshold'):.3f}"

        self._duration_card(canvas, (960, 274, 1168, 378), "EYE CLOSURE",
                            self._eye_duration, float(state.get("eye_threshold_seconds", 2.0)), self.RED, eye_extra)
        self._duration_card(canvas, (1180, 274, 1384, 378), "YAWN / MOUTH",
                            self._mouth_duration, float(state.get("yawn_threshold_seconds", 1.0)), self.AMBER, yawn_extra)

        self._card(canvas, (960, 394, 1384, 548), "DETECTIONS & CONFIDENCE", self.CYAN)
        if not detections:
            self._text(canvas, "No objects detected in this frame", (982, 444), 0.42, self.MUTED)
        else:
            for i, (name, confidence) in enumerate(sorted(detections, key=lambda x: x[1], reverse=True)[:4]):
                y = 438 + i * 27
                display_name = name.replace("_", " ").upper()[:20]
                self._text(canvas, display_name, (982, y), 0.37, self.WHITE)
                if confidence > 0:
                    self._text(canvas, f"{confidence * 100:.0f}%", (1320, y), 0.37, self.WHITE)
                    self._progress(canvas, (1190, y - 12, 1305, y - 5), confidence, self.CYAN)
                else:
                    self._text(canvas, "detected", (1300, y), 0.33, self.MUTED)

        # Card tổng kết trạng thái và hiệu năng
        self._card(canvas, (960, 560, 1384, 736), "DRIVER STATUS & PERFORMANCE SUMMARY", self.PURPLE)
        running_seconds = float(state.get("monitoring_duration_seconds", 0.0) or 0.0)
        ai_fps = float(state.get("ai_fps", 0.0) or 0.0)
        pipeline_ms = float(state.get("pipeline_latency_ms", 0.0) or 0.0)
        yolo_ms = float(state.get("yolo_latency_ms", 0.0) or 0.0)
        mp_ms = float(state.get("mp_latency_ms", 0.0) or 0.0)
        mp_count = int(state.get("mp_total_activations", 0) or 0)
        detailed_alert = state.get("detailed_alert") or confirmed_alert

        self._text(canvas, f"Running: {duration_hms(running_seconds)} | State machine: EYE={drowsy_state} / MOUTH={yawning_state}", (982, 588), 0.32, self.CYAN)
        self._text(canvas, f"Alert confirmed: {confirmed_alert} ({detailed_alert})", (982, 609), 0.36, status_color)
        self._text(canvas, f"AI Perf: {ai_fps:.1f} FPS | Latency: {pipeline_ms:.1f}ms (YOLO {yolo_ms:.0f}ms, MP {mp_ms:.0f}ms)", (982, 630), 0.34, self.WHITE)
        mp_status_str = "ACTIVE" if mp_active else "IDLE"
        self._text(canvas, f"MediaPipe: {mp_status_str} (total: {mp_count}) | Baseline: {'VALID' if state.get('baseline_valid') else 'FALLBACK'}", (982, 651), 0.34, self.GREEN if mp_active else self.MUTED)
        self._text(canvas, f"Instantaneous: {state.get('instantaneous_status', 'NORMAL')} | Buffer: {state.get('buffer_count', 0)}/{state.get('buffer_capacity', 10)} frames", (982, 672), 0.34, self.MUTED)

        esp_mode = str(state.get("esp_mode", "MOCK")).upper()
        esp_connected = state.get("esp_connected", False)
        connection_text = "connected" if esp_connected else "not confirmed"
        self._text(canvas, f"ESP: {esp_mode} ({connection_text})", (982, 693), 0.35, self.GREEN if esp_connected else self.MUTED)

        esp_result = state.get("esp_result")
        packet = getattr(esp_result, "packet", None) if esp_result else None
        send_text = packet.strip() if packet else "No state change"
        self._text(canvas, f"Last packet: {send_text[:30]}", (982, 714), 0.33, self.MUTED)

        self._buttons.clear()
        self._button(canvas, "quit", (1242, 742, 1384, 782), "QUIT")
        cv2.imshow(self.WINDOW, canvas)

    def close(self):
        try:
            cv2.destroyWindow(self.WINDOW)
        except cv2.error:
            pass
