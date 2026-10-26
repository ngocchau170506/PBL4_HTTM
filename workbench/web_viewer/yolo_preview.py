"""Bộ nhận diện YOLO xem thử cho web_viewer (workbench/web_viewer, thành phần C).

Chỉ dùng trong workbench trên PC để MAIN tự quan sát khung nhận diện mắt/miệng trên mặt mình.
Đây KHÔNG phải FEAT-030, không nằm trong src/, không import ngược vào src/ và không thay StubAdapter mặc định.
Hiện thực đồng thời giao diện Detector (cho LiveRunner) và AiAdapter/NodeContract (cho run_ai, FlowEngine).

Model models/best.pt hiện có 11 lớp mắt/miệng; chưa có lớp điện thoại và góc đầu (I-009).
Nếu thiếu thư viện hoặc file model hỏng/thiếu: health chuyển sang DOWN, tạo ErrorEvent mã E_PROC_MODEL_LOAD
tại node_path=process.ai(yolo_preview), và hàm attach_yolo_preview() trả None để web vẫn chạy với StubAdapter.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from contracts.logic import Detection, DetectionResult, ErrorEvent, Health, NodeId, SignalPacket
from fd_process.ai.adapter_base import run_ai
from live_runner import MODES, LiveRunner
from state_hub import StateHub

DEFAULT_MODEL_PATH = ROOT / "models" / "best.pt"

BEST_PT_LABELS: Tuple[str, ...] = (
    "Attentive eye",
    "Drowsy eye",
    "Eyeclosed",
    "Open-Mouth",
    "Yawn",
    "asleep",
    "close",
    "closed",
    "noYawn",
    "open",
    "yawn",
)

UNSUPPORTED_TASKS: Tuple[str, ...] = ("phone_distraction", "head_pose")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_backend_factory(model_path: Path) -> Any:
    """Nạp model YOLO từ ultralytics. Tách hàm riêng để test bơm backend giả không cần GPU."""
    ultra = importlib.import_module("ultralytics")
    yolo_cls = getattr(ultra, "YOLO")
    return yolo_cls(str(model_path))


def _to_float_list(val: Any) -> List[float]:
    if hasattr(val, "tolist"):
        raw = val.tolist()
        if isinstance(raw, list):
            return [float(x) for x in raw]
        return [float(raw)]
    if isinstance(val, (list, tuple)):
        return [float(x) for x in val]
    return [float(val)]


class YoloPreview:
    """Bộ nhận diện YOLO xem thử cho workbench (name='yolo_preview', node_id='process.ai')."""

    name: str = "yolo_preview"
    node_id: NodeId = "process.ai"

    def __init__(
        self,
        model_path: Optional[Path] = None,
        conf_threshold: float = 0.25,
        input_width: int = 640,
        input_height: int = 480,
        allowed_labels: Optional[Iterable[str]] = None,
        image_getter: Optional[Callable[[], Optional[np.ndarray]]] = None,
        backend_factory: Optional[Callable[[Path], Any]] = None,
        warmup: bool = True,
    ) -> None:
        if not 0.0 <= float(conf_threshold) <= 1.0:
            raise ValueError("conf_threshold phải nằm trong [0, 1]")
        if input_width <= 0 or input_height <= 0:
            raise ValueError("input_width và input_height phải > 0")
        self._model_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        self._conf_threshold = float(conf_threshold)
        self._input_width = int(input_width)
        self._input_height = int(input_height)
        self._allowed_labels = (
            frozenset(str(x) for x in allowed_labels)
            if allowed_labels is not None
            else frozenset(BEST_PT_LABELS)
        )
        self._image_getter = image_getter
        self._backend_factory = backend_factory or _default_backend_factory
        self._warmup = bool(warmup)

        self._model: Optional[Any] = None
        self._names: Dict[int, str] = {}
        self._loaded = False
        self._health: Health = (
            "OK" if (self._model_path.exists() and self._model_path.is_file()) else "DOWN"
        )
        self._last_error: Optional[ErrorEvent] = None
        self._last_infer_ms: float = 0.0

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def class_names(self) -> Tuple[str, ...]:
        return tuple(self._names[k] for k in sorted(self._names))

    @property
    def last_error(self) -> Optional[ErrorEvent]:
        return self._last_error

    @property
    def last_infer_ms(self) -> float:
        return self._last_infer_ms

    def set_image_getter(self, getter: Optional[Callable[[], Optional[np.ndarray]]]) -> None:
        self._image_getter = getter

    def health(self) -> Health:
        return self._health

    def _record_load_error(self, message: str, trace_id: str = "yolo-preview-load") -> ErrorEvent:
        self._loaded = False
        self._model = None
        self._health = "DOWN"
        err = ErrorEvent(
            trace_id=trace_id if trace_id else "yolo-preview-load",
            ts_ms=_now_ms(),
            source="process.ai",
            code="E_PROC_MODEL_LOAD",
            node_path=f"process.ai({self.name})",
            err_class="process",
            message=message,
            recoverable=False,
        )
        self._last_error = err
        return err

    def load(self) -> None:
        """Nạp trọng số best.pt và chạy 1 khung khởi động để tránh vượt timeout ở nhịp đầu."""
        if not self._model_path.exists() or not self._model_path.is_file():
            err = self._record_load_error(f"Không tìm thấy file trọng số: {self._model_path.name}")
            raise RuntimeError(err.message)

        try:
            model = self._backend_factory(self._model_path)
            raw_names = getattr(model, "names", None)
            if not isinstance(raw_names, dict) or not raw_names:
                raise ValueError("Model không có bảng nhãn names hợp lệ")
            names: Dict[int, str] = {int(k): str(v) for k, v in raw_names.items()}
            if self._warmup:
                blank = np.zeros((self._input_height, self._input_width, 3), dtype=np.uint8)
                model.predict(
                    blank,
                    verbose=False,
                    conf=self._conf_threshold,
                    imgsz=(self._input_height, self._input_width),
                )
        except Exception as exc:
            err = self._record_load_error(f"Không nạp được model {self._model_path.name}: {exc}")
            raise RuntimeError(err.message) from exc

        self._model = model
        self._names = names
        self._loaded = True
        self._health = "OK"
        self._last_error = None

    def try_load(self, trace_id: str = "yolo-preview-load") -> Optional[ErrorEvent]:
        """Thử nạp model an toàn: trả None nếu thành công, hoặc ErrorEvent(E_PROC_MODEL_LOAD) nếu lỗi."""
        try:
            self.load()
            return None
        except Exception:
            assert self._last_error is not None
            if trace_id and self._last_error.trace_id != trace_id:
                self._last_error = ErrorEvent(
                    trace_id=trace_id,
                    ts_ms=self._last_error.ts_ms,
                    source=self._last_error.source,
                    code=self._last_error.code,
                    node_path=self._last_error.node_path,
                    err_class=self._last_error.err_class,
                    message=self._last_error.message,
                    recoverable=self._last_error.recoverable,
                )
            return self._last_error

    def detect(self, bgr: np.ndarray) -> Tuple[Detection, ...]:
        """Chạy nhận diện trên ảnh BGR, trả về các Detection thuộc tập nhãn mắt/miệng của best.pt."""
        if not isinstance(bgr, np.ndarray) or bgr.ndim != 3 or bgr.shape[2] != 3 or bgr.size == 0:
            raise ValueError("bgr phải là mảng numpy 3 kênh khác rỗng")
        if not self._loaded or self._model is None or self._health == "DOWN":
            raise RuntimeError("yolo_preview đang ở trạng thái DOWN hoặc chưa gọi load()")

        orig_h, orig_w = int(bgr.shape[0]), int(bgr.shape[1])
        if orig_w != self._input_width or orig_h != self._input_height:
            work_img = cv2.resize(bgr, (self._input_width, self._input_height))
            scale_x = orig_w / float(self._input_width)
            scale_y = orig_h / float(self._input_height)
        else:
            work_img = bgr
            scale_x = 1.0
            scale_y = 1.0

        t0 = time.perf_counter()
        results = self._model.predict(
            work_img,
            verbose=False,
            conf=self._conf_threshold,
            imgsz=(self._input_height, self._input_width),
        )
        self._last_infer_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        if not results:
            return ()
        first = results[0]
        boxes = getattr(first, "boxes", None)
        if boxes is None:
            return ()

        return self._extract_detections(boxes, scale_x, scale_y, orig_w, orig_h)

    def _extract_detections(
        self,
        boxes: Any,
        scale_x: float,
        scale_y: float,
        orig_w: int,
        orig_h: int,
    ) -> Tuple[Detection, ...]:
        xyxy_raw = getattr(boxes, "xyxy", None)
        conf_raw = getattr(boxes, "conf", None)
        cls_raw = getattr(boxes, "cls", None)
        if xyxy_raw is None or conf_raw is None or cls_raw is None:
            return ()

        xyxy_list = xyxy_raw.tolist() if hasattr(xyxy_raw, "tolist") else list(xyxy_raw)
        conf_list = _to_float_list(conf_raw)
        cls_list = _to_float_list(cls_raw)

        items: List[Detection] = []
        count = min(len(xyxy_list), len(conf_list), len(cls_list))
        for i in range(count):
            cls_id = int(round(cls_list[i]))
            label = self._names.get(cls_id)
            if not label or label not in self._allowed_labels:
                continue
            conf = float(conf_list[i])
            if conf < self._conf_threshold or not 0.0 <= conf <= 1.0:
                continue
            coords: Sequence[float] = xyxy_list[i]
            if len(coords) != 4:
                continue
            x1 = max(0, min(orig_w, int(round(float(coords[0]) * scale_x))))
            y1 = max(0, min(orig_h, int(round(float(coords[1]) * scale_y))))
            x2 = max(x1, min(orig_w, int(round(float(coords[2]) * scale_x))))
            y2 = max(y1, min(orig_h, int(round(float(coords[3]) * scale_y))))
            items.append(Detection(label=label, conf=round(conf, 4), box=(x1, y1, x2, y2)))
        return tuple(items)

    def infer(self, frame: SignalPacket) -> DetectionResult:
        """Thực thi giao diện AiAdapter để chạy qua cổng kiểm soát run_ai."""
        if not self._loaded or self._health == "DOWN":
            raise RuntimeError("yolo_preview đang DOWN")
        img = self._image_getter() if self._image_getter is not None else None
        if img is None:
            img = np.zeros((frame.height, frame.width, 3), dtype=np.uint8)
        items = self.detect(img)
        return DetectionResult(
            trace_id=frame.trace_id,
            ts_ms=_now_ms(),
            source="process.ai",
            frame_id=frame.frame_id,
            items=items,
        )

    def self_test(self) -> Tuple[bool, str]:
        err = self.try_load(trace_id="yolo-preview-selftest")
        if err is not None:
            return False, f"{err.code}: {err.message}"
        sig = SignalPacket(
            trace_id="yolo-preview-selftest",
            ts_ms=_now_ms(),
            source="signal",
            frame_id=1,
            width=self._input_width,
            height=self._input_height,
        )
        res = run_ai(self, sig, timeout_ms=500)
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"YoloPreview OK (classes={len(self._names)}, infer_ms={self._last_infer_ms})"


def attach_yolo_preview(
    hub: StateHub,
    preview: Optional[YoloPreview] = None,
    trace_id: str = "yolo-preview-boot",
) -> Optional[YoloPreview]:
    """Thử nạp YoloPreview cho web_viewer; nếu lỗi thì ghi E_PROC_MODEL_LOAD vào hub và trả None (chạy StubAdapter)."""
    det = preview if preview is not None else YoloPreview()
    hub.set_meta("yolo_supported_labels", list(BEST_PT_LABELS))
    hub.set_meta("yolo_unsupported_tasks", list(UNSUPPORTED_TASKS))

    err = det.try_load(trace_id=trace_id)
    if err is not None:
        hub.add_error(err.code, err.node_path, err.trace_id, err.message, err.ts_ms)
        hub.set_node_status("process.ai", "DOWN")
        hub.set_meta("yolo_preview_status", "DOWN")
        hub.set_meta("yolo_load_error", err.message)
        return None

    hub.set_node_status("process.ai", "OK")
    hub.set_meta("yolo_preview_status", "OK")
    hub.set_meta("yolo_model_classes", list(det.class_names))
    return det


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Chạy thử YoloPreview với LiveRunner trong workbench.")
    parser.add_argument("--source", choices=MODES, default="mock")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--uri", default=None)
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--save-jpeg", default=None)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    hub = StateHub()
    preview = YoloPreview(model_path=Path(args.model), conf_threshold=args.conf)
    active_detector = attach_yolo_preview(hub, preview=preview)
    runner = LiveRunner(
        hub,
        mode=args.source,
        uri=args.uri,
        index=args.index,
        detector=active_detector,
        pace=True,
    )
    err = runner.setup()
    if err is not None:
        print(f"[FAIL] setup: {err}")
        return 1
    try:
        for _ in range(args.frames):
            runner.step()
            if args.source == "mock":
                time.sleep(0.02)
    finally:
        runner.stop()

    snap = hub.snapshot()
    out = {
        "adapter": snap["meta"].get("adapter"),
        "ai_is_stub": snap["meta"].get("ai_is_stub"),
        "yolo_preview_status": snap["meta"].get("yolo_preview_status"),
        "last_infer_ms": preview.last_infer_ms,
        "frames_total": snap["frames_total"],
        "ai_calls": snap["meta"].get("ai_calls"),
        "detections": snap["detections"],
        "errors": snap["errors"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
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
