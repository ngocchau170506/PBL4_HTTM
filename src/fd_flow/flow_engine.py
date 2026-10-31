"""Bộ điều phối cây luồng trung tâm FLOW_TREE (FEAT-012 & tích hợp G2 FEAT-020/021/022).

- Nối các fd: connect.camera -> signal -> process.sampler -> (process.baseline & process.ai) -> response.
- Kiểm tra kiểu gói dữ liệu tại mỗi cạnh trong FLOW_TREE (sai kiểu -> E_FLOW_EDGE_TYPE kèm tên cạnh).
- Đồng bộ trạng thái hệ thống (BASELINE <-> MONITOR) theo tiến trình khóa BaselineProfile của từng tài xế.
- Cô lập lỗi và đẩy ErrorEvent sang fd_diagnose (ErrorRecorder).
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, Optional, Tuple, Type, Union

from contracts.logic import (
    FLOW_TREE,
    AiAdapter,
    DetectionResult,
    ErrorEvent,
    FlowEdge,
    Health,
    NodeId,
    RawPacket,
    SignalPacket,
    ViolationEvent,
)
from contracts.state import BaselineProfile
from fd_config.config_loader import ConfigLoader, SystemConfig
from fd_connect.camera_source import CameraSource
from fd_connect.esp32_commander import Esp32Commander
from fd_connect.esp32_source import Esp32Source
from fd_diagnose.error_recorder import ErrorRecorder
from fd_flow.circuit_breaker import CircuitBreaker
from fd_flow.queue_manager import EdgeQueueManager
from fd_process.ai.adapter_base import run_ai
from fd_process.ai.stub_adapter import StubAdapter
from fd_process.ai.yolo_adapter import YoloAdapter
from fd_process.baseline.baseline_collector import BaselineCollector
from fd_process.rules.eye_drowsiness import EyeDrowsinessRule
from fd_process.rules.mouth_yawn import MouthYawnRule
from fd_process.rules.rules_engine import RulesEngine, RulesOutput
from fd_process.sampler.fps_throttle import FpsThrottle
from fd_response.local_alert import ResponseLogger
from fd_response.ring_buffer import FrameRingBuffer
from fd_response.server_dispatcher import ServerDispatcher
from fd_signal.frame_normalizer import FrameNormalizer

PACKET_CLASS_MAP: Dict[str, Type[Any]] = {
    "RawPacket": RawPacket,
    "SignalPacket": SignalPacket,
    "DetectionResult": DetectionResult,
    "ViolationEvent": ViolationEvent,
    "ErrorEvent": ErrorEvent,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_rules_from_config(cfg: SystemConfig) -> RulesEngine:
    """Dựng RulesEngine (eye_drowsiness + mouth_yawn) từ SystemConfig đã kiểm tra hợp lệ."""
    if cfg.rules is None:
        raise ValueError("SystemConfig thiếu mục rules")
    rc = cfg.rules
    eye = EyeDrowsinessRule(
        closed_labels=frozenset(rc.eye_drowsiness.closed_labels),
        open_labels=frozenset(rc.eye_drowsiness.open_labels),
        min_conf=rc.min_conf,
        trigger_ms=rc.eye_drowsiness.trigger_ms,
        max_gap_ms=rc.max_gap_ms,
    )
    yawn = MouthYawnRule(
        yawn_labels=frozenset(rc.mouth_yawn.yawn_labels),
        no_yawn_labels=frozenset(rc.mouth_yawn.no_yawn_labels),
        min_conf=rc.min_conf,
        trigger_ms=rc.mouth_yawn.trigger_ms,
        max_gap_ms=rc.max_gap_ms,
    )
    return RulesEngine(
        rules=(eye, yawn),
        pre_event_ms=cfg.response.pre_event_ms,
        history_frames=cfg.response.buffer_frames,
    )


class FlowEngine:
    """Thực thi NodeContract cho node flow (hệ điều hành nhỏ nối các fd)."""

    node_id: NodeId = "flow"

    def __init__(
        self,
        config_loader: Optional[ConfigLoader] = None,
        camera: Optional[CameraSource] = None,
        esp32: Optional[Esp32Source] = None,
        normalizer: Optional[FrameNormalizer] = None,
        sampler: Optional[FpsThrottle] = None,
        baseline: Optional[BaselineCollector] = None,
        ai_adapter: Optional[AiAdapter] = None,
        response_logger: Optional[ResponseLogger] = None,
        diagnose_recorder: Optional[ErrorRecorder] = None,
        max_queue_depth: int = 32,
        enforce_sampler: bool = False,
        rules_engine: Optional[RulesEngine] = None,
        ring_buffer: Optional[FrameRingBuffer] = None,
        server_dispatcher: Optional[ServerDispatcher] = None,
        esp32_commander: Optional[Esp32Commander] = None,
        enable_rules_from_config: bool = False,
    ) -> None:
        self._config_loader = config_loader or ConfigLoader()
        self._config: Optional[SystemConfig] = None
        self._camera = camera
        self._esp32 = esp32
        self._esp32_commander = esp32_commander
        self._normalizer = normalizer
        self._sampler = sampler
        self._baseline = baseline
        self._custom_ai_adapter = ai_adapter is not None
        self._ai_adapter: AiAdapter = ai_adapter or StubAdapter()
        self._response = response_logger or ResponseLogger(max_logs=50)
        self._diagnose = diagnose_recorder or ErrorRecorder(max_events=200)
        self._queues = EdgeQueueManager(max_depth_per_edge=max_queue_depth)
        self._breaker = CircuitBreaker()
        self._enforce_sampler = enforce_sampler
        self._rules = rules_engine
        self._ring = ring_buffer
        self._dispatcher = server_dispatcher
        self._enable_rules_from_config = bool(enable_rules_from_config)
        self._last_clip: Optional[Tuple[int, ...]] = None
        self._edges: Dict[Tuple[NodeId, NodeId], FlowEdge] = {
            (e.src, e.dst): e for e in FLOW_TREE
        }
        self._ai_timeout_ms: int = 500
        self._health: Health = "OK"
        self._booted: bool = False
        self._mode: str = "sim"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_embedded(self) -> bool:
        return self._mode == "rasp"

    @property
    def camera(self) -> Optional[CameraSource]:
        return self._camera

    @property
    def esp32(self) -> Optional[Esp32Source]:
        return self._esp32

    @property
    def normalizer(self) -> Optional[FrameNormalizer]:
        return self._normalizer

    @property
    def sampler(self) -> Optional[FpsThrottle]:
        return self._sampler

    @property
    def baseline(self) -> Optional[BaselineCollector]:
        return self._baseline

    @property
    def diagnose(self) -> ErrorRecorder:
        return self._diagnose

    @property
    def response(self) -> ResponseLogger:
        return self._response

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def queues(self) -> EdgeQueueManager:
        return self._queues

    @property
    def rules(self) -> Optional[RulesEngine]:
        return self._rules

    @property
    def ring_buffer(self) -> Optional[FrameRingBuffer]:
        return self._ring

    @property
    def dispatcher(self) -> Optional[ServerDispatcher]:
        return self._dispatcher

    @property
    def last_clip(self) -> Optional[Tuple[int, ...]]:
        """frame_id của cụm vi phạm vừa đóng gần nhất (từ -pre_event_ms tới hết sự kiện)."""
        return self._last_clip

    def health(self) -> Health:
        state = self._breaker.system_state
        if state == "FAULT":
            return "DOWN"
        if state == "DEGRADED":
            return "DEGRADED"
        return self._health

    def _dispatch_error(self, event: ErrorEvent) -> ErrorEvent:
        """Ghi nhận lỗi vào fd_diagnose và cập nhật trạng thái trên CircuitBreaker."""
        self._diagnose.record(event)
        sys_state = self._breaker.on_error(event)
        if sys_state == "FAULT":
            self._health = "DOWN"
        elif sys_state == "DEGRADED":
            self._health = "DEGRADED"
        return event

    def boot(self, trace_id: str = "flow-boot") -> Optional[ErrorEvent]:
        """Nạp cấu hình, mở camera, khởi tạo sampler/baseline/AI và đưa hệ thống sang MONITOR."""
        cfg_res = self._config_loader.load(trace_id=trace_id)
        if isinstance(cfg_res, ErrorEvent):
            return self._dispatch_error(cfg_res)

        self._config = cfg_res
        self._mode = cfg_res.mode
        self._ai_timeout_ms = cfg_res.ai.timeout_ms

        if self._mode == "rasp":
            if self._queues.max_depth > 16:
                self._queues = EdgeQueueManager(max_depth_per_edge=16)
            if self._diagnose.max_capacity > 100:
                self._diagnose = ErrorRecorder(max_events=100)
            if getattr(self._response, "_max_logs", 50) > 25:
                self._response = ResponseLogger(max_logs=25)

        if self._camera is None:
            self._camera = CameraSource(
                source=cfg_res.camera.source,
                index=cfg_res.camera.index,
                uri=cfg_res.camera.uri,
                fps=cfg_res.camera.fps,
                width=cfg_res.camera.width,
                height=cfg_res.camera.height,
            )

        if self._normalizer is None:
            self._normalizer = FrameNormalizer(
                expected_width=cfg_res.camera.width,
                expected_height=cfg_res.camera.height,
            )

        if self._sampler is None:
            self._sampler = FpsThrottle(target_fps=cfg_res.sampler.target_fps)

        if self._baseline is None:
            self._baseline = BaselineCollector(
                seconds_min=cfg_res.baseline.seconds_min,
                seconds_max=cfg_res.baseline.seconds_max,
            )

        if self._esp32 is None:
            self._esp32 = Esp32Source(
                enabled=cfg_res.esp32.enabled,
                uri=cfg_res.esp32.uri,
                gps_attached=False,
            )

        if self._esp32_commander is None and cfg_res.esp32.enabled:
            self._esp32_commander = Esp32Commander(
                enabled=cfg_res.esp32.enabled,
                serial_mutex=self._esp32.serial_mutex,
                transport_sender=self._esp32.send_command_bytes,
            )

        if self._enable_rules_from_config:
            if self._rules is None and cfg_res.rules is not None:
                self._rules = build_rules_from_config(cfg_res)
            if self._ring is None:
                self._ring = FrameRingBuffer(
                    capacity=cfg_res.response.buffer_frames,
                    pre_event_ms=cfg_res.response.pre_event_ms,
                )

        open_err = self._camera.open(trace_id=f"{trace_id}-cam")
        if open_err is not None:
            return self._dispatch_error(open_err)

        if self._esp32.enabled:
            esp_err = self._esp32.open(trace_id=f"{trace_id}-esp32")
            if esp_err is not None:
                self._dispatch_error(esp_err)

        if not self._custom_ai_adapter:
            if cfg_res.ai.adapter == "yolo":
                self._ai_adapter = YoloAdapter(
                    model_path=cfg_res.ai.model_path,
                    landmarker_path=cfg_res.ai.landmarker_path,
                    conf_threshold=cfg_res.ai.confidence_threshold,
                    fallback_mode=cfg_res.ai.fallback_mode,
                )
            else:
                self._ai_adapter = StubAdapter()

        try:
            self._ai_adapter.load()
        except Exception as exc:
            err = ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="process.ai",
                code="E_PROC_MODEL_LOAD",
                node_path=f"process.ai({self._ai_adapter.name})",
                err_class="process",
                message=f"Không thể nạp adapter AI: {exc}",
                recoverable=False,
            )
            return self._dispatch_error(err)

        self._breaker.transition_to("IDLE")
        self._breaker.transition_to("BASELINE")
        self._breaker.transition_to("MONITOR")
        self._health = "OK"
        self._booted = True
        return None

    def send_across_edge(
        self,
        src: NodeId,
        dst: NodeId,
        packet: Any,
        trace_id: str = "flow-edge",
    ) -> Optional[ErrorEvent]:
        """Kiểm tra hợp đồng kiểu gói tại cạnh src->dst và đẩy vào hàng đợi cạnh."""
        edge = self._edges.get((src, dst))
        safe_trace = getattr(packet, "trace_id", "") or trace_id or "flow-no-trace"

        if edge is None:
            err = ErrorEvent(
                trace_id=safe_trace,
                ts_ms=_now_ms(),
                source="flow",
                code="E_FLOW_EDGE_TYPE",
                node_path=f"{src}->{dst}",
                err_class="contract",
                message=f"Cạnh {src}->{dst} không tồn tại trong FLOW_TREE",
                recoverable=False,
            )
            return self._dispatch_error(err)

        expected_cls = PACKET_CLASS_MAP.get(edge.carries)
        if expected_cls is None or not isinstance(packet, expected_cls):
            actual_name = type(packet).__name__
            err = ErrorEvent(
                trace_id=safe_trace,
                ts_ms=_now_ms(),
                source="flow",
                code="E_FLOW_EDGE_TYPE",
                node_path=f"{src}->{dst}",
                err_class="contract",
                message=(
                    f"Sai kiểu gói trên cạnh {src}->{dst}: "
                    f"cần {edge.carries}, nhận {actual_name}"
                ),
                recoverable=False,
            )
            return self._dispatch_error(err)

        q_err = self._queues.enqueue(src, dst, packet, trace_id=safe_trace)
        if q_err is not None:
            return self._dispatch_error(q_err)

        self._breaker.update_node(
            node_id=src,
            health="OK",
            last_packet_ts_ms=getattr(packet, "ts_ms", _now_ms()),
            queue_depth=self._queues.depth(src, dst),
        )
        return None

    def _sync_baseline_state(self, profile: BaselineProfile) -> None:
        """Đồng bộ trạng thái hệ thống (BASELINE <-> MONITOR) theo trạng thái hồ sơ tài xế."""
        cur_sys = self._breaker.system_state
        if profile.state == "COLLECTING" and cur_sys == "MONITOR":
            self._breaker.transition_to("BASELINE")
        elif profile.state == "LOCKED" and cur_sys == "BASELINE":
            self._breaker.transition_to("MONITOR")

    def _push_ring(self, sig: SignalPacket) -> None:
        """Đưa khung đã lấy mẫu vào buffer vòng (nếu có). Lỗi buffer đi vào fd_diagnose, không chặn luồng."""
        if self._ring is None:
            return
        err = self._ring.push(sig)
        if err is not None:
            self._dispatch_error(err)

    def _drive_timeline(self, out: RulesOutput) -> Tuple[ViolationEvent, ...]:
        """Đồng bộ pha buffer vòng theo kết quả luật và ghép tiền sự kiện (-2s) vào ViolationEvent.

        Luật đo thời gian theo DetectionResult.ts_ms, buffer đo theo ts của SignalPacket. Hai nguồn
        giờ khác nhau, nên khóa nối là frame_id: t0 của buffer lấy từ ts của khung đầu sự kiện.
        """
        ring = self._ring
        if ring is None:
            return out.violations

        if not out.violations:
            if out.suspects and ring.phase == "WATCHING":
                ring.mark_suspect()
            elif not out.suspects:
                if ring.phase == "SUSPECT":
                    ring.clear_suspect()
                elif ring.phase == "ACTIVE":
                    self._last_clip = ring.close_violation()
                    ring.reset()
            return ()

        if ring.phase == "WATCHING":
            ring.mark_suspect()
        merged_events = []
        for ev in out.violations:
            first_ts = ring.ts_of(ev.frame_ids[0]) if ev.frame_ids else None
            if first_ts is None:
                self._dispatch_error(
                    ErrorEvent(
                        trace_id=ev.trace_id,
                        ts_ms=_now_ms(),
                        source="response",
                        code="E_RESP_BUFFER_OVERRUN",
                        node_path="response.ring_buffer",
                        err_class="performance",
                        message="Khung đầu sự kiện đã rời buffer vòng, không ghép được tiền sự kiện",
                        recoverable=True,
                    )
                )
                merged_events.append(ev)
                continue
            if ring.phase == "SUSPECT":
                ring.begin_violation(first_ts)
            pre = ring.pre_event_frames()
            merged_events.append(
                dataclasses.replace(
                    ev,
                    t0_ms=first_ts,
                    frame_ids=tuple(sorted(set(pre) | set(ev.frame_ids))),
                )
            )
        return tuple(merged_events)

    def _finish_rules_stage(
        self, det: DetectionResult, trace_id: str
    ) -> Optional[ErrorEvent]:
        """Chặng cuối: process.rules -> response.

        Không gắn rules_engine: giữ hành vi G1/G2, response nhận thẳng DetectionResult.
        Có rules_engine: response chỉ nhận ViolationEvent qua cạnh process.rules -> response.
        """
        if self._rules is None:
            resp_res = self._response.handle(det)
            if isinstance(resp_res, ErrorEvent):
                return self._dispatch_error(resp_res)
            return None

        out = self._rules.process(det)
        if isinstance(out, ErrorEvent):
            return self._dispatch_error(out)

        for ev in self._drive_timeline(out):
            edge_err = self.send_across_edge("process.rules", "response", ev, trace_id)
            if edge_err is not None:
                return edge_err
            vio_pkt = self._queues.dequeue("process.rules", "response")
            resp_res = self._response.handle(vio_pkt)
            if isinstance(resp_res, ErrorEvent):
                return self._dispatch_error(resp_res)

            if self._esp32_commander is not None:
                self._esp32_commander.handle_violation(vio_pkt)

            if self._dispatcher is not None:
                clip_images = None
                if self._ring is not None and hasattr(self._ring, "get_clip_images"):
                    clip_images = self._ring.get_clip_images(vio_pkt.frame_ids)
                latest_sensor = (
                    self._normalizer.latest_sensor
                    if self._normalizer is not None
                    else None
                )
                disp_err = self._dispatcher.enqueue(
                    vio_pkt,
                    clip_frame_ids=vio_pkt.frame_ids,
                    evidence_frames=clip_images,
                    telemetry=latest_sensor,
                )
                if disp_err is not None:
                    return self._dispatch_error(disp_err)

        if out.ended and not out.suspects and self._esp32_commander is not None:
            self._esp32_commander.handle_recovery(trace_id=trace_id)

        return None

    def poll_esp32(
        self, trace_id: str = "flow-esp32"
    ) -> Union[Optional[Dict[str, float]], ErrorEvent]:
        """Đọc 1 gói từ nhánh optional connect.esp32 -> signal.

        Nếu ESP32 mất gói hoặc chưa lắp GPS (G2.5), đẩy ErrorEvent sang fd_diagnose
        và chuyển trạng thái sang DEGRADED nhưng không làm dừng luồng Camera.
        """
        if not self._booted:
            boot_err = self.boot(trace_id=f"{trace_id}-autoboot")
            if boot_err is not None:
                return boot_err

        assert self._esp32 is not None
        assert self._normalizer is not None

        if not self._esp32.enabled:
            return None

        raw_or_err = self._esp32.read_packet(trace_id=trace_id)
        if isinstance(raw_or_err, ErrorEvent):
            return self._dispatch_error(raw_or_err)

        edge_err = self.send_across_edge("connect.esp32", "signal", raw_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        esp_pkt = self._queues.dequeue("connect.esp32", "signal")

        sensor_or_err = self._normalizer.ingest_esp32_packet(esp_pkt)
        if isinstance(sensor_or_err, ErrorEvent):
            return self._dispatch_error(sensor_or_err)

        gps_err = self._esp32.check_gps_ready(trace_id=f"{trace_id}-gps")
        if gps_err is not None:
            self._dispatch_error(gps_err)

        return sensor_or_err

    def step_sampled(
        self,
        trace_id: str,
        driver_id: str = "drv-sim",
        ts_override_ms: Optional[int] = None,
    ) -> Union[Optional[DetectionResult], ErrorEvent]:
        """Thực thi 1 nhịp luồng G2 đầy đủ:

        connect.camera -> signal -> process.sampler -> (process.baseline & process.ai) -> response.
        - Trả về None nếu FpsThrottle lược bỏ khung hình để hạ nhiệt.
        - Trả về DetectionResult nếu khung hình được lấy mẫu và xử lý thành công.
        - Trả về ErrorEvent nếu bất kỳ node/cạnh nào phát hiện vi phạm.
        """
        if not self._booted:
            boot_err = self.boot(trace_id=f"{trace_id}-autoboot")
            if boot_err is not None:
                return boot_err

        assert self._camera is not None
        assert self._normalizer is not None
        assert self._sampler is not None
        assert self._baseline is not None

        raw_or_err = self._camera.read_packet(trace_id=trace_id)
        if isinstance(raw_or_err, ErrorEvent):
            return self._dispatch_error(raw_or_err)

        if ts_override_ms is not None:
            raw_or_err = RawPacket(
                trace_id=raw_or_err.trace_id,
                ts_ms=ts_override_ms,
                source=raw_or_err.source,
                payload=raw_or_err.payload,
                sensor_kind=raw_or_err.sensor_kind,
            )

        edge_err = self.send_across_edge("connect.camera", "signal", raw_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        raw_pkt = self._queues.dequeue("connect.camera", "signal")

        sig_or_err = self._normalizer.normalize(raw_pkt, driver_id=driver_id)
        if isinstance(sig_or_err, ErrorEvent):
            return self._dispatch_error(sig_or_err)

        edge_err = self.send_across_edge("signal", "process.sampler", sig_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        sig_in_pkt = self._queues.dequeue("signal", "process.sampler")

        sampled_or_err = self._sampler.filter_packet(sig_in_pkt)
        if isinstance(sampled_or_err, ErrorEvent):
            return self._dispatch_error(sampled_or_err)
        if sampled_or_err is None:
            return None
        self._push_ring(sampled_or_err)

        edge_err = self.send_across_edge(
            "process.sampler", "process.baseline", sampled_or_err, trace_id
        )
        if edge_err is not None:
            return edge_err
        base_in_pkt = self._queues.dequeue("process.sampler", "process.baseline")

        base_or_err = self._baseline.process_frame(base_in_pkt, driver_id_override=driver_id)
        if isinstance(base_or_err, ErrorEvent):
            return self._dispatch_error(base_or_err)
        self._sync_baseline_state(base_or_err)

        edge_err = self.send_across_edge(
            "process.sampler", "process.ai", sampled_or_err, trace_id
        )
        if edge_err is not None:
            return edge_err
        ai_in_pkt = self._queues.dequeue("process.sampler", "process.ai")

        det_or_err = run_ai(self._ai_adapter, ai_in_pkt, timeout_ms=self._ai_timeout_ms)
        if isinstance(det_or_err, ErrorEvent):
            return self._dispatch_error(det_or_err)

        edge_err = self.send_across_edge("process.ai", "process.rules", det_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        rules_in_pkt = self._queues.dequeue("process.ai", "process.rules")

        resp_err = self._finish_rules_stage(rules_in_pkt, trace_id)
        if resp_err is not None:
            return resp_err

        return rules_in_pkt

    def step_once(
        self,
        trace_id: str,
        driver_id: str = "drv-sim",
    ) -> Union[DetectionResult, ErrorEvent]:
        """Thực thi 1 nhịp luồng thẳng (không lược bỏ frame) phục vụ kiểm thử công suất tối đa."""
        if self._enforce_sampler:
            sampled_res = self.step_sampled(trace_id=trace_id, driver_id=driver_id)
            if sampled_res is not None:
                return sampled_res

        if not self._booted:
            boot_err = self.boot(trace_id=f"{trace_id}-autoboot")
            if boot_err is not None:
                return boot_err

        assert self._camera is not None
        assert self._normalizer is not None

        raw_or_err = self._camera.read_packet(trace_id=trace_id)
        if isinstance(raw_or_err, ErrorEvent):
            return self._dispatch_error(raw_or_err)

        edge_err = self.send_across_edge("connect.camera", "signal", raw_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        raw_pkt = self._queues.dequeue("connect.camera", "signal")

        sig_or_err = self._normalizer.normalize(raw_pkt, driver_id=driver_id)
        if isinstance(sig_or_err, ErrorEvent):
            return self._dispatch_error(sig_or_err)

        edge_err = self.send_across_edge("signal", "process.sampler", sig_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        sampled_pkt = self._queues.dequeue("signal", "process.sampler")
        self._push_ring(sampled_pkt)

        edge_err = self.send_across_edge("process.sampler", "process.ai", sampled_pkt, trace_id)
        if edge_err is not None:
            return edge_err
        ai_in_pkt = self._queues.dequeue("process.sampler", "process.ai")

        det_or_err = run_ai(self._ai_adapter, ai_in_pkt, timeout_ms=self._ai_timeout_ms)
        if isinstance(det_or_err, ErrorEvent):
            return self._dispatch_error(det_or_err)

        edge_err = self.send_across_edge("process.ai", "process.rules", det_or_err, trace_id)
        if edge_err is not None:
            return edge_err
        rules_in_pkt = self._queues.dequeue("process.ai", "process.rules")

        resp_err = self._finish_rules_stage(rules_in_pkt, trace_id)
        if resp_err is not None:
            return resp_err

        return rules_in_pkt

    def trim_memory(self) -> None:
        """Giải phóng bộ đệm không sử dụng để bảo toàn trần RAM trên bo mạch nhúng."""
        self._queues.clear_all()
        if self._ring is not None and hasattr(self._ring, "reset"):
            self._ring.reset()

    def self_test(self) -> Tuple[bool, str]:
        boot_err = self.boot(trace_id="flow-selftest-boot")
        if boot_err is not None:
            return False, f"{boot_err.code}: {boot_err.message}"
        res = self.step_once(trace_id="flow-selftest-step")
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"FlowEngine OK (frame_id={res.frame_id}, state={self._breaker.system_state})"
