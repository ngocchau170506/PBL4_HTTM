"""Low-rate CSV telemetry. Hardware counters remain empty on non-Pi hosts."""
import csv
from pathlib import Path
import subprocess
import time


class Telemetry:
    fields = ("timestamp", "ai_fps", "yolo_latency_ms", "mp_latency_ms",
              "pipeline_latency_ms", "frame_age_ms", "alert_latency_ms",
              "rss_mb", "cpu_percent", "temp_c", "throttled", "eye_source",
              "mouth_source", "confirmed_alert", "esp_connected", "esp_acknowledged")

    def __init__(self, path, interval=5.0):
        self.path = Path(path) if path else None
        self.interval = interval
        self.last = float("-inf")
        self.file = None
        try:
            import psutil
            self.process = psutil.Process()
            self.process.cpu_percent()
        except ImportError:
            self.process = None

    def record(self, state):
        now = time.monotonic()
        if self.path is None or now - self.last < self.interval:
            return
        self.last = now
        row = {key: state.get(key) for key in self.fields}
        if self.process:
            row["rss_mb"] = round(self.process.memory_info().rss / 1024**2, 2)
            row["cpu_percent"] = self.process.cpu_percent()
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        try:
            row["temp_c"] = float(thermal.read_text()) / 1000
        except (OSError, ValueError):
            pass
        try:
            row["throttled"] = subprocess.run(["vcgencmd", "get_throttled"],
                                             capture_output=True, text=True, timeout=0.2).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        result = state.get("esp_result")
        row["esp_acknowledged"] = getattr(result, "acknowledged", None)
        try:
            if self.file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                has_header = self.path.exists() and self.path.stat().st_size > 0
                self.file = self.path.open("a", newline="", encoding="utf-8")
                self.writer = csv.DictWriter(self.file, fieldnames=self.fields)
                if not has_header:
                    self.writer.writeheader()
            self.writer.writerow(row)
            self.file.flush()
        except OSError as exc:
            print(f"[TELEMETRY] {exc}")
            self.close()

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
