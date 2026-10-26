"""Chạy FlowEngine với camera thật cho web kiểm tra (workbench/web_viewer, thành phần B).

Chỉ chạy trên PC, không import vào src/. Dùng OpenCV + numpy (đã MAIN duyệt trong requirements-web.txt).

Vì sao cần lớp này: trong luồng chính, RawPacket.payload chỉ là header 13 byte (FDRW) mô tả khung,
không chứa ảnh. CameraSource cũng không tự mở camera. Nên ảnh thật được giữ ở đây; runner cấp header
khớp ảnh thật qua frame_provider (kích thước, seq, độ sáng trung bình) để signal kiểm chứng như thường.
Bộ nhận diện (nếu có) đọc đúng ảnh đó vì mỗi nhịp step() chạy đồng bộ trong một luồng:
ảnh đọc ở frame_provider chính là ảnh mà process.ai xử lý ngay sau đó.

Không sửa src/, không ghi đè config.json: cấu hình camera được ghi đè trên bản sao tạm.
Mật khẩu RTSP chỉ lấy từ FD_CAMERA_PASSWORD hoặc FD_CAMERA_URI, không bao giờ được in ra hay ghi vào hub.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple, Union

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from contracts.logic import FLOW_TREE, Detection, DetectionResult, ErrorEvent, Health, SignalPacket
from fd_config.config_loader import DEFAULT_CONFIG_PATH, ConfigLoader, SystemConfig
from fd_connect.camera_source import CameraSource, build_rtsp_low_latency_options, encode_raw_frame
from fd_flow.flow_engine import FlowEngine
from state_hub import StateHub

MODES = ("mock", "webcam", "rtsp", "video")
GREEN = (0, 255, 0)
YELLOW = (0, 255, 255)


def _now_ms() -> int:
    return int(time.time() * 1000)


class FrameSource(Protocol):
    """Nguồn ảnh BGR. open() trả None nếu thành công, hoặc thông báo lỗi (không chứa địa chỉ/mật khẩu)."""

    def open(self) -> Optional[str]: ...

    def read(self) -> Optional[np.ndarray]: ...

    def close(self) -> None: ...


class Detector(Protocol):
    """Giao diện bộ nhận diện cắm vào runner (thành phần C sẽ hiện thực)."""

    name: str

    def load(self) -> None: ...

    def health(self) -> Health: ...

    def detect(self, bgr: np.ndarray) -> Tuple[Detection, ...]: ...


class MockFrameSource:
    """Ảnh tổng hợp có kiểm soát để chạy test mà không cần camera."""

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fail_after: Optional[int] = None,
        black_frames: frozenset = frozenset(),
    ) -> None:
        self._w = width
        self._h = height
        self._fail_after = fail_after
        self._black = black_frames
        self._idx = 0
        ramp = np.linspace(40, 200, width, dtype=np.uint8)
        self._base = np.dstack([np.tile(ramp, (height, 1))] * 3)

    def open(self) -> Optional[str]:
        return None

    def read(self) -> Optional[np.ndarray]:
        self._idx += 1
        if self._fail_after is not None and self._idx > self._fail_after:
            return None
        if self._idx in self._black:
            return np.zeros((self._h, self._w, 3), dtype=np.uint8)
        img = self._base.copy()
        cx = 60 + (self._idx * 7) % max(1, self._w - 120)
        cv2.circle(img, (cx, self._h // 2), 40, YELLOW, -1)
        return img

    def close(self) -> None:
        return None


class CvFrameSource:
    """Webcam / RTSP / file video qua OpenCV.

    Webcam và RTSP đọc bằng luồng nền, luôn giữ ảnh mới nhất để tránh tích lũy trễ do bộ đệm.
    File video đọc đồng bộ, hết file thì read() trả None (không lặp lại, để thấy E_CONN_LOST thật).
    """

    def __init__(self, kind: str, target: Union[int, str], read_timeout_s: float = 0.9) -> None:
        if kind not in ("webcam", "rtsp", "video"):
            raise ValueError(f"kind không hợp lệ: {kind}")
        self._kind = kind
        self._target = target
        self._timeout = read_timeout_s
        self._cap: Optional[Any] = None
        self._live = kind in ("webcam", "rtsp")
        self._cond = threading.Condition()
        self._latest: Optional[np.ndarray] = None
        self._seq = 0
        self._seen = 0
        self._eof = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def open(self) -> Optional[str]:
        if self._kind == "rtsp":
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", build_rtsp_low_latency_options())
        if self._kind == "webcam" and os.name == "nt":
            cap = cv2.VideoCapture(int(self._target), cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(self._target)
        if not cap.isOpened():
            cap.release()
            return f"Không mở được nguồn {self._kind}"
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        if self._live:
            self._thread = threading.Thread(target=self._reader, name="live-reader", daemon=True)
            self._thread.start()
        return None

    def _reader(self) -> None:
        assert self._cap is not None
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            with self._cond:
                if ok:
                    self._latest = frame
                    self._seq += 1
                else:
                    self._eof = True
                self._cond.notify_all()
            if not ok:
                return

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            return None
        if not self._live:
            ok, frame = self._cap.read()
            return frame if ok else None
        with self._cond:
            self._cond.wait_for(lambda: self._seq > self._seen or self._eof, timeout=self._timeout)
            if self._seq > self._seen:
                self._seen = self._seq
                return self._latest
            return None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None


def draw_overlay(
    bgr: np.ndarray,
    items: List[Dict[str, Any]],
    banner_lines: List[str],
) -> np.ndarray:
    """Trả ảnh mới có khung nhận diện và dòng trạng thái. Không sửa ảnh gốc. Chữ ASCII (font OpenCV)."""
    out = bgr.copy()
    for it in items:
        box = it.get("box")
        if box is None:
            continue
        x1, y1, x2, y2 = (int(v) for v in box)
        cv2.rectangle(out, (x1, y1), (x2, y2), GREEN, 2)
        text = f"{it['label']} {it['conf']:.2f}"
        cv2.putText(out, text, (x1, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 1, cv2.LINE_AA)
    for i, line in enumerate(banner_lines):
        y = 18 + i * 18
        cv2.putText(out, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out


class _DetectorAdapter:
    """Bọc Detector thành AiAdapter; đọc ảnh hiện tại của runner (đồng bộ cùng luồng với step)."""

    node_id = "process.ai"

    def __init__(self, detector: Detector, image_getter: Any) -> None:
        self._detector = detector
        self._get_image = image_getter
        self.name: str = detector.name

    def load(self) -> None:
        self._detector.load()

    def health(self) -> Health:
        return self._detector.health()

    def infer(self, frame: SignalPacket) -> DetectionResult:
        img = self._get_image()
        if img is None:
            raise RuntimeError(f"không có ảnh hiện tại cho frame_id={frame.frame_id}")
        items = self._detector.detect(img)
        return DetectionResult(
            trace_id=frame.trace_id,
            ts_ms=_now_ms(),
            source="process.ai",
            frame_id=frame.frame_id,
            items=tuple(items),
        )

    def self_test(self) -> Tuple[bool, str]:
        self.load()
        return self.health() != "DOWN", f"{self.name} health={self.health()}"


class LiveRunner:
    """Chạy FlowEngine từng nhịp và đẩy ảnh + số liệu vào StateHub."""

    def __init__(
        self,
        hub: StateHub,
        mode: str = "mock",
        source: Optional[FrameSource] = None,
        uri: Optional[str] = None,
        index: int = 0,
        detector: Optional[Detector] = None,
        driver_id: str = "drv-live",
        env: Optional[Mapping[str, str]] = None,
        dotenv_path: Optional[Path] = None,
        pace: bool = False,
        jpeg_quality: int = 80,
        rotation: int = 0,
        fps: Optional[float] = None,
        flip_h: bool = False,
        alert_dispatcher: Optional[Any] = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode phải thuộc {MODES}, nhận {mode!r}")
        self._hub = hub
        self._mode = mode
        self._source = source
        self._uri = uri
        self._index = index
        self._detector = detector
        self._driver_id = driver_id
        self._env = env
        self._dotenv_path = dotenv_path
        self._pace = pace
        self._jpeg_quality = jpeg_quality
        self._rotation: int = int(rotation) % 360
        self._custom_fps: Optional[float] = float(fps) if fps is not None else None
        self._flip_h: bool = bool(flip_h)
        self._alert_dispatcher = alert_dispatcher

        self._engine: Optional[FlowEngine] = None
        self._cfg: Optional[SystemConfig] = None
        self._tmp_dir: Optional[str] = None
        self._current: Optional[np.ndarray] = None
        self._read_ms = 0.0
        self._frame_read = False
        self._cam_seq = 0
        self._steps = 0
        self._ai_calls = 0
        self._last_items: List[Dict[str, Any]] = []
        self._ai_name = "stub"
        self._ai_is_stub = True
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def engine(self) -> Optional[FlowEngine]:
        return self._engine

    @property
    def alert_dispatcher(self) -> Optional[Any]:
        return self._alert_dispatcher

    def _report_error(self, code: str, node_path: str, trace_id: str, message: str) -> None:
        self._hub.add_error(code, node_path, trace_id, message, _now_ms())

    def _write_temp_config(self) -> Path:
        """Bản sao config.json với mục camera theo mode, không ghi đè file gốc (CLAUDE.md mục 6)."""
        base = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        cam = dict(base["camera"])
        cam["source"] = self._mode
        cam["index"] = self._index
        if self._mode in ("mock", "webcam"):
            cam["uri"] = None
        elif self._mode in ("rtsp", "video"):
            if self._uri is not None:
                cam["uri"] = self._uri
        if self._custom_fps is not None:
            cam["fps"] = int(self._custom_fps)
        base["camera"] = cam
        self._tmp_dir = tempfile.mkdtemp(prefix="web_viewer_cfg_")
        path = Path(self._tmp_dir) / "config.json"
        path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        return path

    def setup(self) -> Optional[str]:
        """Dựng engine và mở nguồn. Trả None nếu sẵn sàng, hoặc thông báo lỗi (đã ghi vào hub)."""
        env = dict(os.environ) if self._env is None else dict(self._env)
        loader = ConfigLoader(
            config_path=self._write_temp_config(),
            dotenv_path=self._dotenv_path if self._dotenv_path is not None else None,
            env=env,
        )
        cfg = loader.load(trace_id="live-cfg")
        if isinstance(cfg, ErrorEvent):
            self._report_error(cfg.code, cfg.node_path, cfg.trace_id, cfg.message)
            return f"{cfg.code}: {cfg.message}"
        self._cfg = cfg

        if self._mode == "rtsp" and (not cfg.camera.uri or "${" in cfg.camera.uri):
            msg = "Thiếu FD_CAMERA_PASSWORD hoặc FD_CAMERA_URI cho nguồn rtsp"
            self._report_error("E_CONN_OPEN", "connect.camera", "live-cfg", msg)
            return msg

        if self._source is None:
            self._source = self._build_source(cfg)
        open_msg = self._source.open()
        if open_msg is not None:
            self._report_error("E_CONN_OPEN", "connect.camera", "live-open", open_msg)
            return open_msg

        adapter = _DetectorAdapter(self._detector, lambda: self._current) if self._detector is not None else None
        camera = CameraSource(
            source=cfg.camera.source,
            index=cfg.camera.index,
            uri=cfg.camera.uri,
            fps=cfg.camera.fps,
            width=cfg.camera.width,
            height=cfg.camera.height,
            frame_provider=self._provide,
        )
        self._engine = FlowEngine(
            config_loader=loader,
            camera=camera,
            ai_adapter=adapter,
            enable_rules_from_config=True,
        )
        boot_err = self._engine.boot(trace_id="live-boot")
        if boot_err is not None:
            self._report_error(boot_err.code, boot_err.node_path, boot_err.trace_id, boot_err.message)
            return f"{boot_err.code}: {boot_err.message}"

        ai_name = adapter.name if adapter is not None else "stub"
        self._ai_name = ai_name
        self._ai_is_stub = adapter is None
        self._hub.set_meta("mode", self._mode)
        self._hub.set_meta("adapter", ai_name)
        self._hub.set_meta("ai_is_stub", self._ai_is_stub)
        self._hub.set_meta("sampler_target_fps", cfg.sampler.target_fps)
        self._hub.set_meta("camera_fps_config", cfg.camera.fps)
        self._hub.set_meta("rules_status", "CHUA_XAC_DINH: ngưỡng luật là giá trị tạm")
        return None

    def _build_source(self, cfg: SystemConfig) -> FrameSource:
        if self._mode == "mock":
            return MockFrameSource(cfg.camera.width, cfg.camera.height)
        if self._mode == "webcam":
            return CvFrameSource("webcam", self._index)
        if self._mode == "rtsp":
            return CvFrameSource("rtsp", cfg.camera.uri or "")
        return CvFrameSource("video", self._uri or "")

    def _provide(self, seq: int) -> Optional[bytes]:
        """frame_provider của CameraSource: đọc ảnh thật, trả header FDRW khớp ảnh đó."""
        assert self._cfg is not None
        t0 = time.perf_counter()
        frame = self._source.read() if self._source is not None else None
        self._read_ms = (time.perf_counter() - t0) * 1000.0
        if frame is None:
            self._current = None
            self._frame_read = False
            return None
        if self._rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if self._flip_h:
            frame = cv2.flip(frame, 1)

        w, h = self._cfg.camera.width, self._cfg.camera.height
        if frame.shape[1] != w or frame.shape[0] != h:
            frame = cv2.resize(frame, (w, h))
        self._current = frame
        self._frame_read = True
        self._cam_seq = seq
        luma = min(255, int(round(float(frame.mean()))))
        return encode_raw_frame(width=w, height=h, seq=seq, luma_mean=luma)

    def step(self) -> Union[DetectionResult, ErrorEvent, None]:
        """Một nhịp: engine.step_sampled, rồi cập nhật hub. Trả kết quả gốc của engine."""
        if self._engine is None:
            raise RuntimeError("gọi setup() thành công trước khi step()")
        self._steps += 1
        trace_id = f"live-{self._steps:06d}"
        self._frame_read = False
        t0 = time.perf_counter()
        res = self._engine.step_sampled(trace_id=trace_id, driver_id=self._driver_id)
        total_ms = (time.perf_counter() - t0) * 1000.0
        self._hub.record_latency(max(0.0, total_ms - self._read_ms))

        if isinstance(res, ErrorEvent):
            self._report_error(res.code, res.node_path, res.trace_id, res.message)
        elif isinstance(res, DetectionResult):
            self._ai_calls += 1
            self._last_items = [
                {"label": d.label, "conf": d.conf, "box": list(d.box) if d.box is not None else None}
                for d in res.items
            ]
            self._hub.set_detections(res.frame_id, self._last_items)
            if self._alert_dispatcher is not None:
                self._alert_dispatcher.feed_detections(self._last_items)

        if self._frame_read and self._current is not None:
            self._hub.record_frame()
            self._publish_image()
        self._publish_status()
        return res

    def _publish_image(self) -> None:
        assert self._engine is not None and self._current is not None
        banner = [
            f"{self._mode} | cam#{self._cam_seq} | {self._engine.breaker.system_state}",
            "AI: STUB - KET QUA GIA" if self._ai_is_stub else f"AI: {self._ai_name}",
        ]
        if self._alert_dispatcher is not None:
            active_banner = str(getattr(self._alert_dispatcher, "active_banner", "") or getattr(self._alert_dispatcher, "_active_alert_banner", "")).strip()
            if active_banner:
                banner.append(active_banner)
        shown = draw_overlay(self._current, self._last_items, banner)
        ok, buf = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        if not ok:
            self._report_error("E_SIG_BAD_FORMAT", "signal", f"live-{self._steps:06d}", "Không mã hóa được JPEG")
            return
        self._hub.set_frame(buf.tobytes(), self._cam_seq, _now_ms())

    def _publish_status(self) -> None:
        eng = self._engine
        assert eng is not None
        if eng.camera is not None:
            self._hub.set_node_status("connect.camera", eng.camera.health())
        if eng.normalizer is not None:
            self._hub.set_node_status("signal", eng.normalizer.health())
        if eng.esp32 is not None and eng.esp32.enabled:
            self._hub.set_node_status("connect.esp32", eng.esp32.health())
        self._hub.set_node_status("flow", eng.health())
        self._hub.set_meta("system_state", eng.breaker.system_state)
        self._hub.set_meta("ai_calls", self._ai_calls)
        self._hub.set_meta("steps", self._steps)
        self._hub.set_meta("capture_wait_ms", round(self._read_ms, 2))
        self._hub.set_meta(
            "queues", {f"{e.src}->{e.dst}": eng.queues.depth(e.src, e.dst) for e in FLOW_TREE}
        )
        if eng.camera is not None:
            self._hub.set_meta("camera_measured_fps", eng.camera.measured_fps)
        self._hub.set_meta("camera_rotation", self._rotation)
        self._hub.set_meta("camera_flip_h", self._flip_h)
        self._hub.set_meta("camera_fps_config", int(self.fps))

    @property
    def rotation(self) -> int:
        return self._rotation

    @property
    def flip_h(self) -> bool:
        return self._flip_h

    @property
    def fps(self) -> float:
        if self._custom_fps is not None:
            return self._custom_fps
        return float(self._cfg.camera.fps) if self._cfg is not None else 25.0

    def set_rotation(self, deg: int) -> int:
        self._rotation = int(deg) % 360
        self._hub.set_meta("camera_rotation", self._rotation)
        return self._rotation

    def toggle_rotate_180(self) -> int:
        return self.set_rotation(self._rotation + 180)

    def toggle_flip_h(self) -> bool:
        self._flip_h = not self._flip_h
        self._hub.set_meta("camera_flip_h", self._flip_h)
        return self._flip_h

    def set_fps(self, fps: float) -> float:
        val = max(1.0, min(120.0, float(fps)))
        self._custom_fps = val
        self._hub.set_meta("camera_fps_config", int(val))
        return val

    def start(self) -> None:
        """Chạy vòng lặp nền. Gọi sau setup() thành công."""
        if self._engine is None:
            raise RuntimeError("gọi setup() thành công trước khi start()")
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("runner đã chạy")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="live-runner", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        assert self._cfg is not None
        while not self._stop.is_set():
            active_fps = self._custom_fps if self._custom_fps is not None else float(self._cfg.camera.fps)
            interval = 1.0 / max(1.0, active_fps)
            t0 = time.perf_counter()
            res = self.step()
            if isinstance(res, ErrorEvent) and res.code == "E_CONN_LOST":
                self._stop.wait(0.5)
                continue
            if self._pace:
                self._stop.wait(max(0.0, interval - (time.perf_counter() - t0)))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._source is not None:
            self._source.close()
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Chạy thử live_runner không cần web (in tóm tắt số liệu).")
    parser.add_argument("--source", choices=MODES, default="mock")
    parser.add_argument("--index", type=int, default=0, help="chỉ số webcam")
    parser.add_argument("--uri", default=None, help="đường dẫn file khi --source video")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--save-jpeg", default=None, help="lưu ảnh cuối ra file để xem ngay")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    hub = StateHub()
    runner = LiveRunner(hub, mode=args.source, uri=args.uri, index=args.index, pace=True)
    err = runner.setup()
    if err is not None:
        print(f"[FAIL] setup: {err}")
        return 1
    try:
        for _ in range(args.frames):
            runner.step()
            if args.source != "mock":
                continue
            time.sleep(0.02)
    finally:
        runner.stop()

    snap = hub.snapshot()
    summary = {k: snap[k] for k in ("fps", "frames_total", "latency_ms", "nodes", "meta")}
    summary["errors"] = {"total": snap["errors"]["total"], "by_code": snap["errors"]["by_code"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.save_jpeg:
        frame = hub.get_frame()
        if frame is None:
            print("[FAIL] chưa có ảnh để lưu")
            return 1
        Path(args.save_jpeg).write_bytes(frame[0])
        print(f"Đã lưu ảnh: {args.save_jpeg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
