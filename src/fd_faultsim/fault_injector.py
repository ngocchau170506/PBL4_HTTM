"""Bộ mô phỏng và tiêm lỗi hạ tầng có kiểm soát (FEAT-051).

- Đọc kịch bản lỗi từ workbench/faults/*.yaml bằng bộ phân tích thuần thư viện chuẩn.
- Hỗ trợ 5 nhóm kịch bản:
  1. network_drop: Mạng yếu / mất kết nối máy chủ (E_RESP_SEND, E_RESP_NO_ACK, E_RESP_BUFFER_OVERRUN).
  2. uart_degraded: Mất gói / trễ nhịp UART ESP32 (E_CONN_LOST, E_CONN_TIMEOUT, E_SIG_BAD_FORMAT).
  3. camera_fps_drop: Sụt FPS camera / trôi nhịp thời gian (E_SIG_TS_DRIFT, E_SIG_EMPTY).
  4. edge_contract_violation: Gói tin sai kiểu trên cạnh luồng (E_FLOW_EDGE_TYPE).
  5. E_SCOPE_VIOLATION: Kiểm tra chặn yêu cầu vi phạm phạm vi (FI-001).
- Chỉ dùng thư viện chuẩn của Python và kiểu trong contracts.logic (Luật R2, R3).
"""
from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from contracts.logic import (
    FLOW_TREE,
    ErrorClass,
    ErrorCode,
    ErrorEvent,
    Health,
    NodeId,
    RawPacket,
    SignalPacket,
)

_VALID_ERROR_CODES: Tuple[ErrorCode, ...] = (
    "E_CONN_OPEN",
    "E_CONN_LOST",
    "E_CONN_TIMEOUT",
    "E_SIG_BAD_FORMAT",
    "E_SIG_EMPTY",
    "E_SIG_TS_DRIFT",
    "E_PROC_MODEL_LOAD",
    "E_PROC_TIMEOUT",
    "E_PROC_BAD_OUTPUT",
    "E_FLOW_EDGE_TYPE",
    "E_FLOW_QUEUE_FULL",
    "E_FLOW_NODE_DOWN",
    "E_RESP_SEND",
    "E_RESP_NO_ACK",
    "E_RESP_BUFFER_OVERRUN",
    "E_CFG_INVALID",
    "E_SCOPE_VIOLATION",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_scalar(val: str) -> Any:
    v = val.strip()
    if not v:
        return ""
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v in ("null", "None", "~"):
        return None
    if v == "[]":
        return []
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Phân tích cú pháp tập con YAML dùng cho kịch bản lỗi (thuần thư viện chuẩn)."""
    result: Dict[str, Any] = {}
    lines = text.splitlines()
    idx = 0
    n = len(lines)

    while idx < n:
        raw_line = lines[idx]
        if "#" in raw_line:
            raw_line = raw_line.split("#", 1)[0]
        stripped = raw_line.strip()
        if not stripped:
            idx += 1
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        if indent == 0 and ":" in stripped:
            key_part, val_part = stripped.split(":", 1)
            key = key_part.strip()
            val_str = val_part.strip()

            if val_str == "|":
                block_lines: List[str] = []
                idx += 1
                while idx < n:
                    nxt = lines[idx]
                    nxt_clean = nxt.split("#", 1)[0] if "#" in nxt else nxt
                    if nxt_clean.strip() and (len(nxt_clean) - len(nxt_clean.lstrip())) == 0:
                        break
                    block_lines.append(nxt_clean.strip())
                    idx += 1
                result[key] = "\n".join(ln for ln in block_lines if ln)
                continue

            if val_str == "":
                sub_list: List[Any] = []
                sub_dict: Dict[str, Any] = {}
                idx += 1
                while idx < n:
                    nxt = lines[idx]
                    nxt_no_comment = nxt.split("#", 1)[0] if "#" in nxt else nxt
                    nxt_strip = nxt_no_comment.strip()
                    if not nxt_strip:
                        idx += 1
                        continue
                    nxt_indent = len(nxt_no_comment) - len(nxt_no_comment.lstrip())
                    if nxt_indent <= indent:
                        break
                    if nxt_strip.startswith("- "):
                        sub_list.append(_parse_scalar(nxt_strip[2:]))
                    elif ":" in nxt_strip:
                        sk, sv = nxt_strip.split(":", 1)
                        sub_dict[sk.strip()] = _parse_scalar(sv)
                    idx += 1
                result[key] = sub_list if sub_list else sub_dict
                continue

            result[key] = _parse_scalar(val_str)
        idx += 1

    return result


@dataclass(frozen=True)
class FaultScenario:
    """Cấu hình một kịch bản tiêm lỗi đã nạp và kiểm tra hợp lệ."""

    scenario_id: str
    kind: str
    description: str
    target_node_path: str
    expected_codes: Tuple[ErrorCode, ...]
    total_steps: int
    fault_start_step: int
    recovery_step: int
    isolate_camera: bool = True
    spool_capacity: int = 5
    failure_threshold: int = 3
    drift_delta_ms: int = 3500
    edge_src: str = "process.ai"
    edge_dst: str = "process.rules"


@dataclass(frozen=True)
class FaultExecutionReport:
    """Kết quả thực thi một kịch bản tiêm lỗi."""

    scenario_id: str
    kind: str
    total_steps: int
    emitted_errors: Tuple[ErrorEvent, ...]
    matched_expected_codes: bool
    matched_node_paths: bool
    camera_frames_expected: int
    camera_frames_delivered: int
    camera_survival_rate: float
    recovered_after_fault: bool
    final_health: Health
    passed: bool
    detail: str


class FaultInjector:
    """Thực thi NodeContract cho node faultsim (FEAT-051)."""

    node_id: NodeId = "faultsim"

    def __init__(self) -> None:
        self._health: Health = "OK"
        self._emitted_count: int = 0

    def health(self) -> Health:
        return self._health

    def load_scenario(
        self, path: Union[str, Path], trace_id: str = "faultsim-load"
    ) -> Union[FaultScenario, ErrorEvent]:
        """Đọc và kiểm tra tính hợp lệ của file kịch bản YAML."""
        file_path = Path(path)
        safe_trace = trace_id.strip() if isinstance(trace_id, str) and trace_id.strip() else "faultsim-trace"
        if not file_path.exists() or not file_path.is_file():
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=_now_ms(),
                source="faultsim",
                code="E_CFG_INVALID",
                node_path="faultsim",
                err_class="config",
                message=f"Không tìm thấy file kịch bản lỗi: {file_path.name}",
                recoverable=True,
            )

        try:
            raw_text = file_path.read_text(encoding="utf-8")
            data = parse_simple_yaml(raw_text)
        except OSError as exc:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=_now_ms(),
                source="faultsim",
                code="E_CFG_INVALID",
                node_path="faultsim",
                err_class="config",
                message=f"Lỗi đọc file kịch bản: {exc}",
                recoverable=True,
            )

        return self.validate_scenario_dict(data, trace_id=safe_trace)

    def validate_scenario_dict(
        self, data: Dict[str, Any], trace_id: str = "faultsim-dict"
    ) -> Union[FaultScenario, ErrorEvent]:
        """Kiểm tra tính hợp lệ của từ điển kịch bản lỗi."""
        if not isinstance(data, dict) or not data:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="faultsim",
                code="E_CFG_INVALID",
                node_path="faultsim",
                err_class="config",
                message="Kịch bản lỗi rỗng hoặc sai định dạng",
                recoverable=True,
            )

        scenario_id = str(data.get("id", "")).strip()
        kind = str(data.get("loai", "")).strip()
        description = str(data.get("mo_ta", "")).strip()

        if not scenario_id or not kind:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="faultsim",
                code="E_CFG_INVALID",
                node_path="faultsim",
                err_class="config",
                message="Kịch bản lỗi thiếu trường bắt buộc 'id' hoặc 'loai'",
                recoverable=True,
            )

        raw_codes = data.get("ma_loi_ky_vong")
        if raw_codes is None:
            ky_vong = data.get("ky_vong")
            if isinstance(ky_vong, dict) and "ma_loi" in ky_vong:
                raw_codes = [ky_vong["ma_loi"]]
            elif kind in _VALID_ERROR_CODES:
                raw_codes = [kind]

        if isinstance(raw_codes, str):
            raw_codes = [raw_codes]
        if not isinstance(raw_codes, list) or not raw_codes:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="faultsim",
                code="E_CFG_INVALID",
                node_path="faultsim",
                err_class="config",
                message=f"Kịch bản {scenario_id} thiếu danh sách mã lỗi kỳ vọng hợp lệ",
                recoverable=True,
            )

        validated_codes: List[ErrorCode] = []
        for c in raw_codes:
            c_str = str(c).strip()
            if c_str not in _VALID_ERROR_CODES:
                self._health = "DEGRADED"
                return ErrorEvent(
                    trace_id=trace_id,
                    ts_ms=_now_ms(),
                    source="faultsim",
                    code="E_CFG_INVALID",
                    node_path="faultsim",
                    err_class="config",
                    message=f"Mã lỗi '{c_str}' không nằm trong hợp đồng ErrorCode",
                    recoverable=True,
                )
            validated_codes.append(c_str)

        target_node = str(data.get("muc_tieu_node", "flow")).strip()
        total_steps = int(data.get("tong_buoc", 10))
        fault_start = int(data.get("buoc_bat_dau_loi", 1))
        recovery_step = int(data.get("buoc_hoi_phuc", max(2, total_steps - 1)))
        isolate_cam = bool(data.get("co_lap_luong_cam", True))
        spool_cap = int(data.get("spool_capacity", 5))
        fail_thresh = int(data.get("failure_threshold", 3))
        drift_ms = int(data.get("drift_delta_ms", 3500))
        edge_src = str(data.get("canh_nguon", "process.ai")).strip()
        edge_dst = str(data.get("canh_dich", "process.rules")).strip()

        self._health = "OK"
        return FaultScenario(
            scenario_id=scenario_id,
            kind=kind,
            description=description,
            target_node_path=target_node,
            expected_codes=tuple(validated_codes),
            total_steps=max(1, total_steps),
            fault_start_step=max(0, fault_start),
            recovery_step=max(fault_start + 1, recovery_step),
            isolate_camera=isolate_cam,
            spool_capacity=max(1, spool_cap),
            failure_threshold=max(1, fail_thresh),
            drift_delta_ms=max(100, drift_ms),
            edge_src=edge_src,
            edge_dst=edge_dst,
        )

    def make_network_transport(
        self, scenario: FaultScenario
    ) -> Callable[[Any, int], Tuple[bool, str]]:
        """Tạo hàm transport giả lập mạng yếu/rớt mạng để cắm vào ServerDispatcher."""
        call_counter = {"step": 0}

        def _transport(pkg: Any, timeout_ms: int) -> Tuple[bool, str]:
            step = call_counter["step"]
            call_counter["step"] += 1
            if scenario.fault_start_step <= step < scenario.recovery_step:
                if (step - scenario.fault_start_step) % 2 == 0:
                    raise OSError("Simulated network drop (connection reset)")
                return False, "Simulated no ACK from server"
            return True, f"ACK-OK-{step}"

        return _transport

    def inject_esp32_step(
        self,
        step_idx: int,
        scenario: FaultScenario,
        trace_id: str = "faultsim-esp32",
    ) -> Tuple[Optional[RawPacket], Optional[ErrorEvent]]:
        """Tiêm lỗi mất gói/trễ nhịp/sai cú pháp vào nhánh optional connect.esp32."""
        ts_val = 10000 + step_idx * 100
        if scenario.fault_start_step <= step_idx < scenario.recovery_step:
            phase = (step_idx - scenario.fault_start_step) % 3
            if phase == 0:
                code: ErrorCode = "E_CONN_LOST"
                err_cls: ErrorClass = "source"
                msg = f"Mất kết nối UART ESP32 tại bước {step_idx}"
            elif phase == 1:
                code = "E_CONN_TIMEOUT"
                err_cls = "source"
                msg = f"Quá hạn chờ gói telemetry ESP32 tại bước {step_idx}"
            else:
                code = "E_SIG_BAD_FORMAT"
                err_cls = "data"
                msg = f"Bản tin UART ESP32 hỏng định dạng tại bước {step_idx}"
            self._emitted_count += 1
            return None, ErrorEvent(
                trace_id=f"{trace_id}-{step_idx}",
                ts_ms=ts_val,
                source="connect.esp32",
                code=code,
                node_path=scenario.target_node_path,
                err_class=err_cls,
                message=msg,
                recoverable=True,
            )

        payload = f'FDESP:{{"seq":{step_idx},"co2_ppm":420,"gps_ready":true}}'.encode("utf-8")
        return (
            RawPacket(
                trace_id=f"{trace_id}-{step_idx}",
                ts_ms=ts_val,
                source="connect.esp32",
                payload=payload,
                sensor_kind="esp32",
            ),
            None,
        )

    def inject_camera_step(
        self,
        step_idx: int,
        scenario: FaultScenario,
        base_ts_ms: int = 10000,
        normal_interval_ms: int = 40,
        trace_id: str = "faultsim-cam",
    ) -> Tuple[RawPacket, Optional[ErrorEvent]]:
        """Tiêm lỗi sụt FPS / trôi nhịp hoặc khung rỗng vào luồng Camera."""
        ts_val = base_ts_ms + step_idx * normal_interval_ms
        if scenario.fault_start_step <= step_idx < scenario.recovery_step:
            self._emitted_count += 1
            if (step_idx - scenario.fault_start_step) % 2 == 0:
                drifted_ts = ts_val + scenario.drift_delta_ms
                pkt = RawPacket(
                    trace_id=f"{trace_id}-{step_idx}",
                    ts_ms=drifted_ts,
                    source="connect.camera",
                    payload=b"frame-bytes",
                    sensor_kind="camera",
                )
                return pkt, ErrorEvent(
                    trace_id=pkt.trace_id,
                    ts_ms=drifted_ts,
                    source="signal",
                    code="E_SIG_TS_DRIFT",
                    node_path=scenario.target_node_path,
                    err_class="performance",
                    message=f"Sụt giảm FPS gây trôi timestamp +{scenario.drift_delta_ms}ms",
                    recoverable=True,
                )
            empty_pkt = RawPacket(
                trace_id=f"{trace_id}-{step_idx}",
                ts_ms=ts_val,
                source="connect.camera",
                payload=b"",
                sensor_kind="camera",
            )
            return empty_pkt, ErrorEvent(
                trace_id=empty_pkt.trace_id,
                ts_ms=ts_val,
                source="signal",
                code="E_SIG_EMPTY",
                node_path=scenario.target_node_path,
                err_class="data",
                message="Khung hình camera rỗng (0 bytes)",
                recoverable=True,
            )

        normal_pkt = RawPacket(
            trace_id=f"{trace_id}-{step_idx}",
            ts_ms=ts_val,
            source="connect.camera",
            payload=b"valid-frame-payload",
            sensor_kind="camera",
        )
        return normal_pkt, None

    def inject_edge_step(
        self,
        step_idx: int,
        scenario: FaultScenario,
        trace_id: str = "faultsim-edge",
    ) -> Tuple[Any, Optional[ErrorEvent]]:
        """Tiêm gói tin sai kiểu vào cạnh cây luồng để kiểm chứng phát hiện E_FLOW_EDGE_TYPE."""
        ts_val = 10000 + step_idx * 40
        edge_path = f"{scenario.edge_src}->{scenario.edge_dst}"

        expected_kind = "DetectionResult"
        for edge in FLOW_TREE:
            if edge.src == scenario.edge_src and edge.dst == scenario.edge_dst:
                expected_kind = edge.carries
                break

        if scenario.fault_start_step <= step_idx < scenario.recovery_step:
            self._emitted_count += 1
            wrong_pkt = RawPacket(
                trace_id=f"{trace_id}-{step_idx}",
                ts_ms=ts_val,
                source="connect.camera",
                payload=b"wrong-edge-payload",
                sensor_kind="camera",
            )
            return wrong_pkt, ErrorEvent(
                trace_id=wrong_pkt.trace_id,
                ts_ms=ts_val,
                source="flow",
                code="E_FLOW_EDGE_TYPE",
                node_path=edge_path,
                err_class="contract",
                message=f"Sai kiểu gói trên cạnh {edge_path}: kỳ vọng {expected_kind}, nhận RawPacket",
                recoverable=True,
            )

        valid_pkt = SignalPacket(
            trace_id=f"{trace_id}-{step_idx}",
            ts_ms=ts_val,
            source="signal",
            frame_id=step_idx,
            width=640,
            height=480,
        )
        return valid_pkt, None

    def run_scenario(
        self, scenario: FaultScenario, total_steps: Optional[int] = None
    ) -> FaultExecutionReport:
        """Thực thi trọn vẹn kịch bản tiêm lỗi và kiểm chứng toàn bộ tiêu chí phòng vệ."""
        steps = total_steps if (total_steps is not None and total_steps > 0) else scenario.total_steps
        emitted: List[ErrorEvent] = []
        cam_expected = steps
        cam_delivered = 0
        recovered = False

        for step in range(steps):
            in_fault = scenario.fault_start_step <= step < scenario.recovery_step

            if scenario.kind == "network_drop":
                cam_delivered += 1
                if in_fault:
                    offset = step - scenario.fault_start_step
                    if offset < scenario.failure_threshold:
                        code: ErrorCode = "E_RESP_SEND" if (offset % 2 == 0) else "E_RESP_NO_ACK"
                        msg = f"Lỗi gửi gói về máy chủ tại bước {step} ({code})"
                    elif offset < scenario.failure_threshold + scenario.spool_capacity:
                        code = "E_RESP_NO_ACK"
                        msg = f"Máy chủ không phản hồi ACK, đẩy vào offline spool (bước {step})"
                    else:
                        code = "E_RESP_BUFFER_OVERRUN"
                        msg = f"Offline spool vượt trần {scenario.spool_capacity} gói tại bước {step}"
                    self._emitted_count += 1
                    emitted.append(
                        ErrorEvent(
                            trace_id=f"net-{scenario.scenario_id}-{step}",
                            ts_ms=10000 + step * 50,
                            source="response",
                            code=code,
                            node_path=scenario.target_node_path,
                            err_class="source" if code != "E_RESP_BUFFER_OVERRUN" else "performance",
                            message=msg,
                            recoverable=True,
                        )
                    )
                elif step >= scenario.recovery_step:
                    recovered = True

            elif scenario.kind == "uart_degraded":
                cam_delivered += 1
                _, err = self.inject_esp32_step(step, scenario, trace_id=f"uart-{scenario.scenario_id}")
                if err is not None:
                    emitted.append(err)
                elif step >= scenario.recovery_step:
                    recovered = True

            elif scenario.kind == "camera_fps_drop":
                _, err = self.inject_camera_step(step, scenario, trace_id=f"fps-{scenario.scenario_id}")
                if err is not None:
                    emitted.append(err)
                else:
                    cam_delivered += 1
                    if step >= scenario.recovery_step:
                        recovered = True

            elif scenario.kind == "edge_contract_violation":
                cam_delivered += 1
                _, err = self.inject_edge_step(step, scenario, trace_id=f"edge-{scenario.scenario_id}")
                if err is not None:
                    emitted.append(err)
                elif step >= scenario.recovery_step:
                    recovered = True

            else:
                cam_delivered += 1
                if in_fault:
                    self._emitted_count += 1
                    emitted.append(
                        ErrorEvent(
                            trace_id=f"scope-{scenario.scenario_id}-{step}",
                            ts_ms=10000 + step * 50,
                            source="flow",
                            code=scenario.expected_codes[0],
                            node_path=scenario.target_node_path,
                            err_class="contract",
                            message=f"Phát hiện vi phạm hợp đồng/phạm vi ({scenario.scenario_id})",
                            recoverable=False,
                        )
                    )
                elif step >= scenario.recovery_step:
                    recovered = True

        emitted_codes = {e.code for e in emitted}
        expected_set = set(scenario.expected_codes)
        matched_codes = bool(emitted) and expected_set.issubset(emitted_codes) and emitted_codes.issubset(set(_VALID_ERROR_CODES))
        matched_paths = bool(emitted) and all(
            bool(e.node_path and e.node_path == scenario.target_node_path and e.trace_id)
            for e in emitted
        )

        survival_rate = (cam_delivered / float(cam_expected)) if cam_expected > 0 else 1.0
        cam_ok = (survival_rate == 1.0) if scenario.isolate_camera else (survival_rate > 0.0)

        passed = matched_codes and matched_paths and cam_ok
        self._health = "OK" if passed else "DEGRADED"
        gc.collect()

        detail = (
            f"scenario={scenario.scenario_id} ({scenario.kind}): "
            f"errors={len(emitted)}, codes={sorted(emitted_codes)}, "
            f"cam_survival={survival_rate * 100.0:.1f}%, recovered={recovered}"
        )
        return FaultExecutionReport(
            scenario_id=scenario.scenario_id,
            kind=scenario.kind,
            total_steps=steps,
            emitted_errors=tuple(emitted),
            matched_expected_codes=matched_codes,
            matched_node_paths=matched_paths,
            camera_frames_expected=cam_expected,
            camera_frames_delivered=cam_delivered,
            camera_survival_rate=survival_rate,
            recovered_after_fault=recovered,
            final_health=self._health,
            passed=passed,
            detail=detail,
        )

    def self_test(self) -> Tuple[bool, str]:
        """Tự kiểm tra nhanh bộ tiêm lỗi trên kịch bản mẫu."""
        sample = FaultScenario(
            scenario_id="SELFTEST-051",
            kind="uart_degraded",
            description="Tu kiem tra co lap loi ESP32",
            target_node_path="connect.esp32",
            expected_codes=("E_CONN_LOST", "E_CONN_TIMEOUT", "E_SIG_BAD_FORMAT"),
            total_steps=10,
            fault_start_step=2,
            recovery_step=7,
            isolate_camera=True,
        )
        report = self.run_scenario(sample)
        if not report.passed:
            return False, f"FaultInjector self_test thất bại: {report.detail}"
        return True, f"FaultInjector sẵn sàng ({report.detail})"
