"""Dark, responsive OpenCV dashboard for the driver monitoring flow."""

import textwrap

import cv2
import numpy as np


class Dashboard:
    WINDOW = "AI Driver Monitor"
    CANVAS_SIZE = (1280, 720)
    CAMERA_RATIO = 0.64

    BG = (17, 20, 24)
    PANEL = (25, 29, 35)
    CARD = (31, 36, 43)
    BORDER = (52, 60, 70)
    WHITE = (235, 240, 245)
    MUTED = (145, 158, 170)
    GREEN = (91, 211, 126)
    AMBER = (42, 184, 245)
    RED = (65, 75, 235)
    CYAN = (214, 190, 68)

    def __init__(self):
        self.action = None
        self._buttons = {}
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, *self.CANVAS_SIZE)
        cv2.setMouseCallback(self.WINDOW, self._on_mouse)

    def _on_mouse(self, event, x, y, _flags, _param):
        if event != cv2.EVENT_LBUTTONUP:
            return
        _, _, window_width, window_height = cv2.getWindowImageRect(self.WINDOW)
        if window_width > 0 and window_height > 0:
            x = int(x * self.CANVAS_SIZE[0] / window_width)
            y = int(y * self.CANVAS_SIZE[1] / window_height)
        for name, (x1, y1, x2, y2) in self._buttons.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.action = name

    def poll_action(self):
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            return "quit"
        if key in (ord("c"), 13, 32):
            return "continue"
        if key == ord("s"):
            return "start"
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
            resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(10, 12, 15)
        )

    @staticmethod
    def _text(canvas, value, origin, size=0.55, color=WHITE, thickness=1):
        cv2.putText(canvas, str(value), origin, cv2.FONT_HERSHEY_SIMPLEX,
                    size, color, thickness, cv2.LINE_AA)

    @staticmethod
    def _card(canvas, rect, title=None, accent=None):
        x1, y1, x2, y2 = rect
        cv2.rectangle(canvas, (x1, y1), (x2, y2), Dashboard.CARD, -1)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), Dashboard.BORDER, 1)
        if accent:
            cv2.rectangle(canvas, (x1, y1), (x1 + 4, y2), accent, -1)
        if title:
            Dashboard._text(canvas, title, (x1 + 16, y1 + 24), 0.43,
                            Dashboard.MUTED, 1)

    def _button(self, canvas, name, rect, label, enabled=True):
        self._buttons[name] = rect
        x1, y1, x2, y2 = rect
        fill = self.GREEN if enabled else (67, 73, 80)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), fill, -1)
        self._text(canvas, label, (x1 + 14, y1 + 29), 0.55,
                   self.BG if enabled else self.MUTED, 2)

    @staticmethod
    def _format_metric(value, digits=3):
        return "N/A" if value is None else f"{value:.{digits}f}"

    def _base(self, title, subtitle):
        canvas = np.full((self.CANVAS_SIZE[1], self.CANVAS_SIZE[0], 3),
                         self.BG, dtype=np.uint8)
        self._text(canvas, title, (32, 40), 0.82, self.WHITE, 2)
        self._text(canvas, subtitle, (34, 63), 0.38, self.MUTED, 1)
        return canvas

    def _side_panel(self, canvas, x, y, width, height):
        cv2.rectangle(canvas, (x, y), (x + width, y + height), self.PANEL, -1)
        cv2.line(canvas, (x, y), (x, y + height), self.BORDER, 1)

    def show_alignment(self, frame, status, metrics=None, can_continue=False):
        canvas = self._base("FACE & HEAD ALIGNMENT",
                            "Position your face naturally before calibration")
        camera_width, camera_height = 805, 610
        canvas[82:82 + camera_height, 24:24 + camera_width] = self._fit_frame(
            frame, camera_width, camera_height
        )
        self._side_panel(canvas, 853, 82, 395, 610)
        self._text(canvas, "ALIGNMENT CHECK", (879, 120), 0.48, self.CYAN, 1)
        self._text(canvas, "FACE / POSE", (879, 151), 0.38, self.MUTED, 1)
        for index, (label, value) in enumerate((metrics or {}).items()):
            self._text(canvas, label, (879, 195 + index * 42), 0.40, self.MUTED, 1)
            self._text(canvas, value, (1020, 195 + index * 42), 0.55,
                      self.GREEN if value == "OK" else self.WHITE, 1)
        self._card(canvas, (875, 395, 1225, 465), "CURRENT STATUS",
                   self.GREEN if can_continue else self.AMBER)
        for index, line in enumerate(textwrap.wrap(status, 32)):
            self._text(canvas, line, (892, 435 + index * 21), 0.44,
                       self.GREEN if can_continue else self.AMBER, 1)
        self._buttons.clear()
        self._button(canvas, "continue", (875, 620, 1050, 670),
                     "TIEP TUC", can_continue)
        self._button(canvas, "quit", (1070, 620, 1225, 670), "THOAT")
        cv2.imshow(self.WINDOW, canvas)

    def show_calibration(self, frame, data):
        canvas = self._base("PERSONAL CALIBRATION",
                            "Keep a natural driving posture and expression")
        camera_width, camera_height = 805, 610
        canvas[82:82 + camera_height, 24:24 + camera_width] = self._fit_frame(
            frame, camera_width, camera_height
        )
        self._side_panel(canvas, 853, 82, 395, 610)
        remaining = float(data.get("remaining", 0.0))
        progress = 1.0 - min(remaining / 30.0, 1.0)
        self._text(canvas, "CALIBRATING BASELINE", (879, 120), 0.48, self.CYAN, 1)
        self._text(canvas, f"{remaining:04.1f}s remaining", (879, 166), 0.78,
                   self.WHITE, 2)
        self._progress(canvas, (879, 184, 1220, 194), progress, self.CYAN)
        rows = (
            ("FACE", "DETECTED" if data.get("face_detected") else "NOT DETECTED"),
            ("EAR", self._format_metric(data.get("ear"))),
            ("MAR", self._format_metric(data.get("mar"))),
            ("YAW", self._format_metric(data.get("yaw"), 1)),
            ("PITCH", self._format_metric(data.get("pitch"), 1)),
            ("ROLL", self._format_metric(data.get("roll"), 1)),
            ("VALID SAMPLES", data.get("sample_count", 0)),
        )
        for index, (label, value) in enumerate(rows):
            y = 235 + index * 42
            self._text(canvas, label, (879, y), 0.39, self.MUTED, 1)
            self._text(canvas, value, (1050, y), 0.51,
                       self.GREEN if label == "FACE" and value == "DETECTED"
                       else self.WHITE, 1)
        self._card(canvas, (875, 535, 1225, 590), "STATUS",
                   self.GREEN if data.get("face_detected") else self.AMBER)
        self._text(canvas, "Collecting valid face samples...",
                   (892, 570), 0.39, self.MUTED, 1)
        self._buttons.clear()
        self._button(canvas, "quit", (1070, 620, 1225, 670), "THOAT")
        cv2.imshow(self.WINDOW, canvas)

    def show_message(self, frame, title, message, can_start=False):
        canvas = self._base(title, "Driver Safety Monitor")
        canvas[82:82 + 610, 24:24 + 805] = self._fit_frame(frame, 805, 610)
        self._side_panel(canvas, 853, 82, 395, 610)
        self._text(canvas, "SYSTEM STATUS", (879, 125), 0.48, self.CYAN, 1)
        for index, line in enumerate(textwrap.wrap(message, 34)):
            self._text(canvas, line, (879, 170 + index * 27), 0.47,
                       self.WHITE, 1)
        self._card(canvas, (875, 310, 1225, 390), "BASELINE",
                   self.GREEN if can_start else self.AMBER)
        self._text(canvas, "READY TO MONITOR" if can_start else "CALIBRATION FAILED",
                   (892, 355), 0.53, self.GREEN if can_start else self.AMBER, 1)
        self._buttons.clear()
        if can_start:
            self._button(canvas, "start", (875, 620, 1080, 670),
                         "BAT DAU GIAM SAT")
        self._button(canvas, "quit", (1090, 620, 1225, 670), "THOAT")
        cv2.imshow(self.WINDOW, canvas)

    def _progress(self, canvas, rect, ratio, color):
        x1, y1, x2, y2 = rect
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (58, 65, 74), -1)
        fill = x1 + int((x2 - x1) * max(0.0, min(1.0, ratio)))
        cv2.rectangle(canvas, (x1, y1), (fill, y2), color, -1)

    def _duration_card(self, canvas, rect, title, duration, threshold, color):
        self._card(canvas, rect, title, color)
        x1, y1, x2, _ = rect
        self._text(canvas, f"{duration:.1f}s / {threshold:.1f}s",
                   (x1 + 16, y1 + 49), 0.54, self.WHITE, 1)
        self._progress(canvas, (x1 + 16, y1 + 64, x2 - 16, y1 + 73),
                       duration / threshold if threshold else 0, color)

    def show_monitor(self, dashboard_frame, state=None):
        """Render a monitoring frame; accepts the old one-argument call too."""
        if state is None:
            cv2.imshow(self.WINDOW, dashboard_frame)
            return
        canvas = self._base("DRIVER SAFETY MONITOR",
                            "Real-time driver state and object detection")
        camera_width, camera_height = 805, 610
        canvas[82:82 + camera_height, 24:24 + camera_width] = self._fit_frame(
            dashboard_frame, camera_width, camera_height
        )
        self._side_panel(canvas, 853, 82, 395, 610)
        self._text(canvas, "SYSTEM ACTIVE", (1080, 112), 0.38, self.GREEN, 1)
        status = state.get("combined_status", "N/A")
        status_color = self.GREEN if status == "NORMAL" else (
            self.RED if any(word in status for word in ("DROWSY", "PHONE", "SMOKING"))
            else self.AMBER
        )
        self._card(canvas, (875, 128, 1225, 195), "CURRENT DRIVER STATUS",
                   status_color)
        for index, line in enumerate(textwrap.wrap(status, 27)[:2]):
            self._text(canvas, line, (892, 163 + index * 21), 0.48,
                       status_color, 1)

        self._duration_card(
            canvas, (875, 210, 1045, 300), "EYE DURATION",
            state.get("eye_duration", 0.0),
            state.get("eye_threshold_seconds", 2.0), self.RED
        )
        self._duration_card(
            canvas, (1055, 210, 1225, 300), "YAWN DURATION",
            state.get("mouth_duration", 0.0),
            state.get("yawn_threshold_seconds", 1.0), self.AMBER
        )

        self._card(canvas, (875, 315, 1225, 455), "DETECTION CONFIDENCE",
                   self.CYAN)
        detections = state.get("detections", [])
        if not detections:
            self._text(canvas, "NO OBJECTS DETECTED", (892, 355), 0.42,
                       self.MUTED, 1)
        for index, (name, confidence) in enumerate(sorted(
                detections, key=lambda item: item[1], reverse=True)[:4]):
            y = 350 + index * 25
            self._text(canvas, name.upper()[:16], (892, y), 0.36, self.WHITE, 1)
            self._text(canvas, f"{confidence * 100:.0f}%", (1155, y),
                       0.36, self.WHITE, 1)
            self._progress(canvas, (1020, y - 12, 1145, y - 5),
                           confidence, self.CYAN)

        self._card(canvas, (875, 470, 1225, 610), "FEATURE SUMMARY", self.CYAN)
        face = "YES" if state.get("face") else "NO"
        phone = "YES" if any(name.casefold() == "phone"
                             for name, _ in detections) else "NO"
        smoking = "YES" if any(name.casefold() == "smoking"
                               for name, _ in detections) else "NO"
        features = (
            f"Eye   {self._format_metric(state.get('ear'))}  {state.get('eye_state', 'N/A')}",
            f"Mouth {self._format_metric(state.get('mar'))}  {state.get('mouth_state', 'N/A')}",
            f"Head  {state.get('head_status', 'N/A')}",
            f"Phone {phone}     Smoking {smoking}",
            f"Face  {face}       Buffer {state.get('buffer_count', 'N/A')}/10",
        )
        for index, line in enumerate(features):
            self._text(canvas, line, (892, 500 + index * 20), 0.36,
                       self.WHITE if "N/A" not in line else self.MUTED, 1)
        self._buttons.clear()
        cv2.imshow(self.WINDOW, canvas)

    def close(self):
        cv2.destroyWindow(self.WINDOW)
