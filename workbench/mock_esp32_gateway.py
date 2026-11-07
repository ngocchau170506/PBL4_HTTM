#!/usr/bin/env python3
"""Trình giả lập ESP32 Telematics Gateway độc lập trên Workbench (FEAT-112 / G11).

- Đóng vai trò thiết bị ESP32 phần cứng tiếp nhận khung tin FDFRAME từ Pi và điều phối viễn thông.
- Các chức năng chính:
    1. Tiếp nhận và giải mã khung tin FDFRAME (Telemetry, Violation, Image Chunk).
    2. Chuyển tiếp luồng Telemetry và Mảnh ảnh lên Partner Server qua HTTP POST.
    3. Trả về cho Pi khung tin trạng thái mạng WiFi (FRAME_TYPE_NETWORK_STATUS)
       và biên nhận kèm tọa độ Server XYZ (FRAME_TYPE_DOWNLINK_ACK).
    4. Quản lý trạng thái còi/loa khi nhận tín hiệu vi phạm (DROWSY / YAWNING).
- Hỗ trợ cả chế độ chạy trực tiếp (Standalone Service) và kiểm thử Unit Test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fd_connect.fd_frame import (
    FRAME_TYPE_COMMAND,
    FRAME_TYPE_DOWNLINK_ACK,
    FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_IMAGE_CHUNK,
    FRAME_TYPE_NETWORK_STATUS,
    FRAME_TYPE_TELEMETRY,
    FRAME_TYPE_VIOLATION,
    FdFrame,
    decode_fd_frame,
    encode_fd_frame,
)


class MockEsp32TelematicsGateway:
    """Bộ mô phỏng hoạt động của vi điều khiển ESP32 Telematics Gateway (Dual-Target Offloader)."""

    def __init__(
        self,
        partner_server_url: str = "http://127.0.0.1:8088/api/v1/telemetry",
        host_pc_url: Optional[str] = "http://127.0.0.1:8080/api/v1/pi/telemetry",
        wifi_connected: bool = True,
        partner_online: bool = False,
        host_pc_online: bool = False,
        downlink_sender: Optional[Callable[[bytes], bool]] = None,
    ) -> None:
        self.partner_server_url = partner_server_url
        self.host_pc_url = host_pc_url
        self.wifi_connected = bool(wifi_connected)
        self.partner_online = bool(partner_online)
        self.host_pc_online = bool(host_pc_online)
        self.downlink_sender = downlink_sender

        self._rx_buffer = bytearray()
        self._received_telemetries: List[Dict[str, Any]] = []
        self._received_violations: List[Dict[str, Any]] = []
        self._received_chunks: List[Dict[str, Any]] = []
        self._buzzer_active: bool = False
        self._last_alert_code: float = 0.0
        self._forwarded_to_server_count: int = 0
        self._forwarded_to_host_count: int = 0
        self._seq: int = 0

    @property
    def received_telemetry_count(self) -> int:
        return len(self._received_telemetries)

    @property
    def received_chunk_count(self) -> int:
        return len(self._received_chunks)

    @property
    def buzzer_active(self) -> bool:
        return self._buzzer_active

    @property
    def forwarded_count(self) -> int:
        return self._forwarded_to_server_count + self._forwarded_to_host_count

    @property
    def forwarded_to_server_count(self) -> int:
        return self._forwarded_to_server_count

    @property
    def forwarded_to_host_count(self) -> int:
        return self._forwarded_to_host_count

    def ingest_bytes(self, raw_bytes: bytes) -> List[FdFrame]:
        """Tiếp nhận dòng byte thô từ kênh UART, bóc tách và xử lý các khung tin FDFRAME."""
        self._rx_buffer.extend(raw_bytes)
        frames: List[FdFrame] = []

        while len(self._rx_buffer) >= 9:
            frame, consumed, err = decode_fd_frame(bytes(self._rx_buffer))
            if frame is not None:
                frames.append(frame)
                self._handle_frame(frame)
                del self._rx_buffer[:consumed]
            elif consumed > 0:
                # Bỏ qua phần byte rác hoặc lỗi
                del self._rx_buffer[:consumed]
            else:
                # Đang chờ nhận thêm byte
                break

        return frames

    def _handle_frame(self, frame: FdFrame) -> None:
        """Phân loại và xử lý từng loại khung FDFRAME."""
        if frame.frame_type == FRAME_TYPE_TELEMETRY:
            parsed = frame.to_json()
            if parsed:
                self._received_telemetries.append(parsed)
                self._forward_to_destinations(parsed)
                # Gửi phản hồi ACK kèm tọa độ Downlink cho Pi nếu có kênh truyền ngược
                self.send_downlink_ack(ack_msg="ACK_TELEMETRY", server_xyz={"x": 106.7008, "y": 10.7768, "z": 12.5})

        elif frame.frame_type == FRAME_TYPE_VIOLATION:
            parsed = frame.to_json()
            if parsed:
                self._received_violations.append(parsed)
                self._buzzer_active = True
                self._last_alert_code = 1.0
                self._forward_to_destinations({"type": "VIOLATION", "detail": parsed})

        elif frame.frame_type == FRAME_TYPE_IMAGE_CHUNK:
            parsed = frame.to_json()
            if parsed:
                self._received_chunks.append(parsed)
                self._forward_to_destinations({"type": "CHUNK", "detail": parsed})

        elif frame.frame_type == FRAME_TYPE_COMMAND:
            # Xử lý lệnh từ Pi (ví dụ tắt còi)
            parsed = frame.to_json()
            if parsed and parsed.get("action") == "BUZZER_OFF":
                self._buzzer_active = False

    def _send_http_post(self, url: str, payload: Dict[str, Any]) -> bool:
        """Thực hiện HTTP POST an toàn có timeout ngắn."""
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def _forward_to_destinations(self, payload: Dict[str, Any]) -> Tuple[bool, bool]:
        """Chuyển tiếp đa kênh (Dual-Target): vừa gửi về Máy chủ (PC) vừa gửi về Server bạn tôi."""
        sent_server = False
        sent_host = False

        # Kênh 1: Gửi về Server Bạn Tôi (Central Cloud)
        if self.wifi_connected and self.partner_online and self.partner_server_url:
            sent_server = self._send_http_post(self.partner_server_url, payload)
            if sent_server:
                self._forwarded_to_server_count += 1

        # Kênh 2: Gửi về Máy chủ điều hành (Local Host / PC)
        if self.host_pc_online and self.host_pc_url:
            sent_host = self._send_http_post(self.host_pc_url, payload)
            if sent_host:
                self._forwarded_to_host_count += 1

        return sent_server, sent_host

    def generate_network_status_frame(self, is_paused: bool = False) -> bytes:
        """Tạo khung báo cáo trạng thái mạng gửi ngược lên Pi."""
        self._seq = (self._seq + 1) & 0xFFFF
        status_data = {
            "wifi_connected": self.wifi_connected,
            "partner_server_online": self.partner_online,
            "rssi_dbm": -62,
            "is_paused": is_paused,
            "pending_chunks": len(self._rx_buffer),
            "uptime_sec": 3600,
        }
        return encode_fd_frame(
            frame_type=FRAME_TYPE_NETWORK_STATUS,
            payload=status_data,
            seq=self._seq,
        )

    def send_downlink_ack(self, ack_msg: str = "ACK", server_xyz: Optional[Dict[str, float]] = None) -> bool:
        """Gửi khung tin Downlink ACK về cho Pi."""
        if self.downlink_sender is None:
            return False

        self._seq = (self._seq + 1) & 0xFFFF
        ack_payload = {
            "status": "OK",
            "msg": ack_msg,
            "server_xyz": server_xyz or {"x": 106.7008, "y": 10.7768, "z": 10.0},
            "ts_ms": int(time.time() * 1000),
        }
        ack_frame_bytes = encode_fd_frame(
            frame_type=FRAME_TYPE_DOWNLINK_ACK,
            payload=ack_payload,
            seq=self._seq,
        )
        return self.downlink_sender(ack_frame_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Khởi chạy giả lập ESP32 Telematics Gateway")
    parser.add_argument("--server", type=str, default="http://127.0.0.1:8088/api/v1/telemetry")
    parser.add_argument("--wifi", action="store_true", default=True)
    args = parser.parse_args()

    gateway = MockEsp32TelematicsGateway(partner_server_url=args.server, wifi_connected=args.wifi)
    print(f"[Mock ESP32 Gateway] READY | Target: {gateway.partner_server_url} | Host PC: {gateway.host_pc_url} | WiFi: {gateway.wifi_connected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
