"""Bộ tự động tổng hợp báo cáo đánh giá hạ tầng và chẩn đoán sự cố (FEAT-052).

- Thu thập số liệu từ MetricsCollector (fd_eval), kết quả kịch bản lỗi từ FaultInjector (fd_faultsim)
  và nhật ký ErrorEvent từ ErrorRecorder (fd_diagnose).
- Đối chiếu tự động với ngưỡng cấu hình, kết xuất báo cáo chuẩn hóa dạng Markdown và JSON.
- Tự động phân loại lỗi và sinh bản nháp hồ sơ BUG-xxx theo đúng cấu trúc changes/BUG-TEMPLATE.md.
- Chỉ dùng thư viện chuẩn của Python và kiểu trong contracts.logic (Luật R2, R3).
"""
from __future__ import annotations

import datetime
import gc
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

from contracts.logic import ErrorClass, ErrorCode, ErrorEvent, Health, NodeId

_ERR_CLASS_VN: Dict[str, str] = {
    "source": "nguồn",
    "data": "dữ liệu",
    "contract": "hợp đồng",
    "algorithm": "thuật toán",
    "performance": "hiệu năng",
    "config": "cấu hình",
    "process": "quy trình",
}

_ROOT_CAUSE_HINTS: Dict[str, str] = {
    "E_CONN_OPEN": "Không mở được thiết bị nguồn (Camera/ESP32); kiểm tra cáp vật lý hoặc URI kết nối.",
    "E_CONN_LOST": "Mất kết nối thiết bị ngoại vi giữa chừng; nhánh optional cần chuyển sang DEGRADED.",
    "E_CONN_TIMEOUT": "Quá thời gian chờ gói tin hoặc ACK từ phần cứng ngoại vi.",
    "E_SIG_BAD_FORMAT": "Gói tin thô sai cấu trúc hoặc thiếu trường định danh trace_id.",
    "E_SIG_EMPTY": "Khung hình hoặc payload rỗng (0 byte) từ nguồn thu.",
    "E_SIG_TS_DRIFT": "Độ lệch timestamp giữa các khung vượt ngưỡng; tốc độ cấp khung hình bị sụt giảm.",
    "E_PROC_MODEL_LOAD": "Không nạp được AiAdapter; kiểm tra cấu hình adapter hoặc tài nguyên.",
    "E_PROC_TIMEOUT": "Thời gian suy diễn vượt quá timeout_ms trong cấu hình.",
    "E_PROC_BAD_OUTPUT": "Đầu ra xử lý có giá trị không hợp lệ (NaN, âm, hoặc sai miền giá trị).",
    "E_FLOW_EDGE_TYPE": "Gói dữ liệu đi qua cạnh cây luồng không khớp kiểu khai báo trong FLOW_TREE.",
    "E_FLOW_QUEUE_FULL": "Hàng đợi cạnh bị đầy do tốc độ tiêu thụ chậm hơn tốc độ bơm gói.",
    "E_FLOW_NODE_DOWN": "Node xử lý trên đường chạy chính đã ngừng hoạt động (DOWN).",
    "E_RESP_SEND": "Lỗi kết nối mạng khi gửi gói vi phạm về máy chủ; kích hoạt offline spool.",
    "E_RESP_NO_ACK": "Máy chủ không phản hồi ACK đúng hạn; kiểm tra Circuit Breaker và spool.",
    "E_RESP_BUFFER_OVERRUN": "Bộ đệm vòng hoặc offline spool vượt trần dung lượng cho phép.",
    "E_CFG_INVALID": "Cấu hình JSON hoặc kịch bản thiếu khóa bắt buộc hoặc giá trị ngoài miền hợp lệ.",
    "E_SCOPE_VIOLATION": "Yêu cầu tác động vào file nằm ngoài phạm vi giai đoạn được duyệt.",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class DiagnosisFinding:
    """Bản ghi chẩn đoán tự động cho một nhóm lỗi (theo code + node_path)."""

    bug_id: str
    code: ErrorCode
    node_path: str
    err_class: ErrorClass
    err_class_vn: str
    occurrences: int
    recoverable: bool
    sample_trace_id: str
    sample_message: str
    root_cause_hint: str
    bug_draft_markdown: str


@dataclass(frozen=True)
class EvaluationReport:
    """Báo cáo tổng hợp đánh giá hạ tầng tự sinh (Markdown & JSON)."""

    gate: str
    generated_at_iso: str
    overall_verdict: Literal["PASS", "FAIL"]
    metrics_passed: bool
    faultsim_passed: bool
    total_packets: int
    overall_fps: float
    p50_latency_ms: float
    p90_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    peak_queue_depth: int
    ram_growth_mb: float
    threshold_violations: Tuple[str, ...]
    scenario_summaries: Tuple[Dict[str, Any], ...]
    findings: Tuple[DiagnosisFinding, ...]
    markdown_text: str
    json_text: str


class AutoReporter:
    """Thực thi NodeContract cho bộ tự sinh báo cáo đánh giá và chẩn đoán (FEAT-052)."""

    node_id: NodeId = "diagnose"

    def __init__(self) -> None:
        self._health: Health = "OK"

    def health(self) -> Health:
        return self._health

    def build_bug_draft(
        self,
        bug_id: str,
        event: ErrorEvent,
        occurrences: int = 1,
        gate: str = "G5",
        date_str: Optional[str] = None,
    ) -> str:
        """Sinh hồ sơ nháp BUG-xxx tuân thủ đúng mẫu changes/BUG-TEMPLATE.md."""
        day = date_str or datetime.date.today().isoformat()
        cls_vn = _ERR_CLASS_VN.get(str(event.err_class), str(event.err_class))
        hint = _ROOT_CAUSE_HINTS.get(str(event.code), "Cần kiểm tra lại luồng dữ liệu tại node phát sinh lỗi.")
        return (
            f"# {bug_id} — Lỗi {event.code} tại {event.node_path}\n\n"
            f"- Ngày phát hiện: {day}\n"
            f"- Giai đoạn: {gate}\n"
            f"- Người ghi (vai): Diagnoser\n"
            f"- Trạng thái: MỞ\n\n"
            f"## Triệu chứng\n"
            f"Phát hiện {occurrences} sự kiện `{event.code}` tại `{event.node_path}` "
            f"(trace_id mẫu: `{event.trace_id}`, ts_ms: `{event.ts_ms}`). "
            f"Chi tiết: {event.message}\n\n"
            f"## nodePath và ErrorCode\n"
            f"- node_path: `{event.node_path}`\n"
            f"- ErrorCode: `{event.code}`\n"
            f"- Phân loại: {cls_vn}\n\n"
            f"## Cách tái hiện ở workbench\n"
            f"Chạy bộ kiểm thử tương ứng với node `{event.node_path}` hoặc kịch bản trong `workbench/faults/`.\n\n"
            f"## Nguyên nhân gốc\n"
            f"{hint}\n\n"
            f"## Thay đổi nhỏ nhất đề xuất (F3)\n"
            f"Kiểm tra và khoanh vùng tại `{event.node_path}`, giữ nguyên hợp đồng `src/contracts/`.\n\n"
            f"## Test phải chạy lại (F5)\n"
            f"Test đơn vị của `{event.node_path}` và `python workbench/gate.py`.\n\n"
            f"## Kết quả sau sửa\n"
            f"Chờ Fixer thực hiện và xác nhận qua `python workbench/gate.py`.\n"
        )

    def analyze_errors(
        self,
        events: Sequence[ErrorEvent],
        gate: str = "G5",
    ) -> Tuple[DiagnosisFinding, ...]:
        """Gom nhóm danh sách ErrorEvent và sinh danh sách DiagnosisFinding."""
        grouped: Dict[Tuple[str, str], List[ErrorEvent]] = {}
        for ev in events:
            if not isinstance(ev, ErrorEvent):
                continue
            key = (str(ev.code), str(ev.node_path))
            grouped.setdefault(key, []).append(ev)

        findings: List[DiagnosisFinding] = []
        for idx, ((code_str, path_str), group) in enumerate(sorted(grouped.items()), 1):
            sample = group[0]
            bug_id = f"BUG-G5-{idx:03d}"
            cls_vn = _ERR_CLASS_VN.get(str(sample.err_class), str(sample.err_class))
            hint = _ROOT_CAUSE_HINTS.get(code_str, "Kiểm tra điều kiện biên tại node phát lỗi.")
            recoverable_all = all(e.recoverable for e in group)
            draft = self.build_bug_draft(
                bug_id=bug_id,
                event=sample,
                occurrences=len(group),
                gate=gate,
            )
            findings.append(
                DiagnosisFinding(
                    bug_id=bug_id,
                    code=sample.code,
                    node_path=path_str,
                    err_class=sample.err_class,
                    err_class_vn=cls_vn,
                    occurrences=len(group),
                    recoverable=recoverable_all,
                    sample_trace_id=sample.trace_id,
                    sample_message=sample.message,
                    root_cause_hint=hint,
                    bug_draft_markdown=draft,
                )
            )
        return tuple(findings)

    def generate_report(
        self,
        metrics_summary: Any,
        fault_reports: Sequence[Any] = (),
        error_events: Sequence[ErrorEvent] = (),
        gate: str = "G5",
        timestamp_iso: Optional[str] = None,
    ) -> EvaluationReport:
        """Tổng hợp số liệu đo đạc và kết quả mô phỏng lỗi thành báo cáo chuẩn hóa."""
        gen_iso = timestamp_iso or _now_iso()

        total_packets = int(getattr(metrics_summary, "total_packets", 0))
        overall_fps = float(getattr(metrics_summary, "overall_fps", 0.0))
        p50 = float(getattr(metrics_summary, "p50_latency_ms", 0.0))
        p90 = float(getattr(metrics_summary, "p90_latency_ms", 0.0))
        p95 = float(getattr(metrics_summary, "p95_latency_ms", 0.0))
        p99 = float(getattr(metrics_summary, "p99_latency_ms", 0.0))
        max_lat = float(getattr(metrics_summary, "max_latency_ms", 0.0))
        peak_q = int(getattr(metrics_summary, "peak_queue_depth", 0))
        ram_growth = float(getattr(metrics_summary, "ram_growth_mb", 0.0))
        metrics_passed = bool(getattr(metrics_summary, "passed_thresholds", True))
        violations = tuple(str(v) for v in getattr(metrics_summary, "violations", ()))
        stages_map = getattr(metrics_summary, "stages", {}) or {}

        scenario_list: List[Dict[str, Any]] = []
        faultsim_passed = True
        for rep in fault_reports:
            sc_passed = bool(getattr(rep, "passed", False))
            if not sc_passed:
                faultsim_passed = False
            emitted_errs = getattr(rep, "emitted_errors", ()) or ()
            scenario_list.append(
                {
                    "scenario_id": str(getattr(rep, "scenario_id", "UNKNOWN")),
                    "kind": str(getattr(rep, "kind", "unknown")),
                    "total_steps": int(getattr(rep, "total_steps", 0)),
                    "error_count": len(emitted_errs),
                    "matched_expected_codes": bool(getattr(rep, "matched_expected_codes", False)),
                    "matched_node_paths": bool(getattr(rep, "matched_node_paths", False)),
                    "camera_survival_rate": float(getattr(rep, "camera_survival_rate", 0.0)),
                    "recovered_after_fault": bool(getattr(rep, "recovered_after_fault", False)),
                    "passed": sc_passed,
                }
            )

        findings = self.analyze_errors(error_events, gate=gate)
        has_unrecoverable_unexpected = any(not f.recoverable for f in findings)

        overall_ok = metrics_passed and faultsim_passed and not has_unrecoverable_unexpected
        verdict: Literal["PASS", "FAIL"] = "PASS" if overall_ok else "FAIL"

        stage_dict_json: Dict[str, Any] = {}
        for st_name, st_obj in sorted(stages_map.items()):
            stage_dict_json[str(st_name)] = {
                "count": int(getattr(st_obj, "count", 0)),
                "fps": round(float(getattr(st_obj, "fps", 0.0)), 2),
                "mean_latency_ms": round(float(getattr(st_obj, "mean_latency_ms", 0.0)), 4),
                "p50_latency_ms": round(float(getattr(st_obj, "p50_latency_ms", 0.0)), 4),
                "p95_latency_ms": round(float(getattr(st_obj, "p95_latency_ms", 0.0)), 4),
                "p99_latency_ms": round(float(getattr(st_obj, "p99_latency_ms", 0.0)), 4),
                "max_latency_ms": round(float(getattr(st_obj, "max_latency_ms", 0.0)), 4),
            }

        payload_json = {
            "gate": gate,
            "generated_at": gen_iso,
            "verdict": verdict,
            "metrics_passed": metrics_passed,
            "faultsim_passed": faultsim_passed,
            "metrics": {
                "total_packets": total_packets,
                "overall_fps": round(overall_fps, 2),
                "p50_latency_ms": round(p50, 4),
                "p90_latency_ms": round(p90, 4),
                "p95_latency_ms": round(p95, 4),
                "p99_latency_ms": round(p99, 4),
                "max_latency_ms": round(max_lat, 4),
                "peak_queue_depth": peak_q,
                "ram_growth_mb": round(ram_growth, 4),
                "threshold_violations": list(violations),
                "stages": stage_dict_json,
            },
            "fault_scenarios": scenario_list,
            "diagnostic_findings": [
                {
                    "bug_id": f.bug_id,
                    "code": f.code,
                    "node_path": f.node_path,
                    "err_class": f.err_class,
                    "occurrences": f.occurrences,
                    "recoverable": f.recoverable,
                    "root_cause_hint": f.root_cause_hint,
                }
                for f in findings
            ],
        }
        json_text = json.dumps(payload_json, ensure_ascii=False, indent=2)

        md_lines: List[str] = [
            f"# BENCHMARK-{gate} — Báo cáo Đánh giá Tổng hợp Hạ tầng & Mô phỏng lỗi",
            "",
            f"- **Thời điểm kết xuất**: `{gen_iso}`",
            f"- **Giai đoạn**: `{gate}`",
            f"- **Kết luận tổng thể**: **{verdict}**",
            f"- **Đạt ngưỡng Metrics (`fd_eval`)**: `{'PASS' if metrics_passed else 'FAIL'}`",
            f"- **Đạt kịch bản Phòng vệ lỗi (`fd_faultsim`)**: `{'PASS' if faultsim_passed else 'FAIL'}`",
            "",
            "## 1. Số liệu Tổng hợp Hạ tầng Luồng (`fd_eval`)",
            "",
            "| Chỉ số | Giá trị |",
            "|---|---|",
            f"| Tổng số gói xử lý | `{total_packets}` |",
            f"| Thông lượng tổng thể (FPS) | `{overall_fps:.2f}` |",
            f"| Độ trễ P50 (ms) | `{p50:.4f}` |",
            f"| Độ trễ P90 (ms) | `{p90:.4f}` |",
            f"| Độ trễ P95 (ms) | `{p95:.4f}` |",
            f"| Độ trễ P99 (ms) | `{p99:.4f}` |",
            f"| Độ trễ cực đại Max (ms) | `{max_lat:.4f}` |",
            f"| Chiều sâu hàng đợi đỉnh | `{peak_q}` |",
            f"| Tăng trưởng bộ nhớ RAM (MB) | `{ram_growth:.4f}` |",
            "",
        ]

        if stage_dict_json:
            md_lines.extend(
                [
                    "### Phân rã theo từng chặng luồng",
                    "",
                    "| Chặng (`stage`) | Số gói | FPS | P50 (ms) | P95 (ms) | P99 (ms) | Max (ms) |",
                    "|---|---|---|---|---|---|---|",
                ]
            )
            for st_name, st_info in stage_dict_json.items():
                md_lines.append(
                    f"| `{st_name}` | {st_info['count']} | {st_info['fps']:.2f} | "
                    f"{st_info['p50_latency_ms']:.4f} | {st_info['p95_latency_ms']:.4f} | "
                    f"{st_info['p99_latency_ms']:.4f} | {st_info['max_latency_ms']:.4f} |"
                )
            md_lines.append("")

        if violations:
            md_lines.append("### Vi phạm ngưỡng cấu hình")
            md_lines.append("")
            for v in violations:
                md_lines.append(f"- `{v}`")
            md_lines.append("")

        md_lines.append("## 2. Kết quả Kịch bản Tiêm lỗi Hạ tầng (`fd_faultsim`)")
        md_lines.append("")
        if scenario_list:
            md_lines.extend(
                [
                    "| Kịch bản | Loại | Số lỗi bắt được | Khớp mã & nodePath | Luồng Cam sống sót | Tự hồi phục | Kết quả |",
                    "|---|---|---|---|---|---|---|",
                ]
            )
            for sc in scenario_list:
                matched_both = sc["matched_expected_codes"] and sc["matched_node_paths"]
                md_lines.append(
                    f"| `{sc['scenario_id']}` | `{sc['kind']}` | {sc['error_count']} | "
                    f"`{matched_both}` | `{sc['camera_survival_rate'] * 100.0:.1f}%` | "
                    f"`{sc['recovered_after_fault']}` | **{'PASS' if sc['passed'] else 'FAIL'}** |"
                )
            md_lines.append("")
        else:
            md_lines.append("- Không có kịch bản tiêm lỗi nào trong phiên này.")
            md_lines.append("")

        md_lines.append("## 3. Chẩn đoán Sự cố Tự động (`fd_diagnose`)")
        md_lines.append("")
        if findings:
            for f in findings:
                md_lines.append(
                    f"- **{f.bug_id}** (`{f.code}` tại `{f.node_path}`, phân loại: *{f.err_class_vn}*, "
                    f"số lần: {f.occurrences}, tự hồi phục: `{f.recoverable}`): {f.root_cause_hint}"
                )
            md_lines.append("")
        else:
            md_lines.append("- Không có sự cố ngoài kịch bản cần tạo hồ sơ BUG.")
            md_lines.append("")

        markdown_text = "\n".join(md_lines)
        self._health = "OK"
        gc.collect()

        return EvaluationReport(
            gate=gate,
            generated_at_iso=gen_iso,
            overall_verdict=verdict,
            metrics_passed=metrics_passed,
            faultsim_passed=faultsim_passed,
            total_packets=total_packets,
            overall_fps=overall_fps,
            p50_latency_ms=p50,
            p90_latency_ms=p90,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            max_latency_ms=max_lat,
            peak_queue_depth=peak_q,
            ram_growth_mb=ram_growth,
            threshold_violations=violations,
            scenario_summaries=tuple(scenario_list),
            findings=findings,
            markdown_text=markdown_text,
            json_text=json_text,
        )

    def write_report(
        self,
        report: EvaluationReport,
        markdown_path: Optional[Union[str, Path]] = None,
        json_path: Optional[Union[str, Path]] = None,
        trace_id: str = "reporter-write",
    ) -> Optional[ErrorEvent]:
        """Lưu báo cáo Markdown và/hoặc JSON ra đường dẫn được chỉ định."""
        if markdown_path is None and json_path is None:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="diagnose",
                code="E_CFG_INVALID",
                node_path="diagnose.reporter",
                err_class="config",
                message="Phải cung cấp ít nhất một đường dẫn markdown_path hoặc json_path",
                recoverable=True,
            )

        try:
            if markdown_path is not None:
                md_file = Path(markdown_path)
                md_file.parent.mkdir(parents=True, exist_ok=True)
                md_file.write_text(report.markdown_text, encoding="utf-8")
            if json_path is not None:
                js_file = Path(json_path)
                js_file.parent.mkdir(parents=True, exist_ok=True)
                js_file.write_text(report.json_text, encoding="utf-8")
        except OSError as exc:
            self._health = "DEGRADED"
            return ErrorEvent(
                trace_id=trace_id,
                ts_ms=_now_ms(),
                source="diagnose",
                code="E_CFG_INVALID",
                node_path="diagnose.reporter",
                err_class="process",
                message=f"Không thể ghi file báo cáo: {exc}",
                recoverable=True,
            )

        self._health = "OK"
        return None

    def self_test(self) -> Tuple[bool, str]:
        """Tự kiểm tra nhanh bộ sinh báo cáo trên dữ liệu giả lập."""

        @dataclass(frozen=True)
        class _DummyMetrics:
            total_packets: int = 100
            overall_fps: float = 4500.0
            p50_latency_ms: float = 0.12
            p90_latency_ms: float = 0.20
            p95_latency_ms: float = 0.25
            p99_latency_ms: float = 0.40
            max_latency_ms: float = 0.80
            peak_queue_depth: int = 4
            ram_growth_mb: float = 0.05
            passed_thresholds: bool = True
            violations: Tuple[str, ...] = ()
            stages: Dict[str, Any] = None

        rep = self.generate_report(_DummyMetrics(stages={}), gate="G5")
        if rep.overall_verdict != "PASS" or "BENCHMARK-G5" not in rep.markdown_text:
            return False, "AutoReporter self_test thất bại khi tạo báo cáo chuẩn"
        return True, f"AutoReporter sẵn sàng (verdict={rep.overall_verdict})"
