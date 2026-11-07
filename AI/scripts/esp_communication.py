"""ESP alert transport with dedicated reader thread, voice_clips.h support, and simulation mode."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from AI.scripts.diagnostic_log import emit_error_event
from AI.scripts.fd_frame import (
    FRAME_TYPE_COMMAND,
    FRAME_TYPE_DOWNLINK_ACK,
    FRAME_TYPE_HEARTBEAT,
    FRAME_TYPE_IMAGE_CHUNK,
    FRAME_TYPE_NETWORK_STATUS,
    FRAME_TYPE_TELEMETRY,
    FRAME_TYPE_VIOLATION,
    MAGIC_HEADER,
    decode_fd_frame,
    encode_fd_frame,
)
from AI.scripts.time_utils import iso_timestamp

DEFAULT_HIGHSPEED_BAUDRATE = 921600
FALLBACK_BAUDRATE = 115200
SUPPORTED_BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)

VALID_STATUSES = {"DROWSY", "YAWNING", "DISTRACTED", "NORMAL"}
EVENT_CODES = {"NORMAL": "00", "DROWSY": "01", "YAWNING": "02", "DISTRACTED": "03"}


@dataclass
class SendResult:
    packet: str
    sent: bool
    acknowledged: bool
    error: Optional[str] = None


def auto_detect_serial_port(preferred: Optional[str] = None) -> Optional[str]:
    """Resolve a valid serial port across Windows (COMx) and Linux (/dev/ttyUSBx)."""
    if preferred:
        preferred = preferred.strip()
        if sys.platform.startswith("win"):
            if preferred.upper().startswith("COM"):
                return preferred
        else:
            if os.path.exists(preferred):
                return preferred
    try:
        list_ports = importlib.import_module("serial.tools.list_ports")
        ports = list(list_ports.comports())
        for p in ports:
            desc = f"{p.device} {p.description} {getattr(p, 'manufacturer', '')}".lower()
            if any(k in desc for k in ("cp210", "ch340", "usb-serial", "uart", "ttyusb", "ttyacm", "esp", "silicon labs")):
                return p.device
        if ports:
            return ports[0].device
    except Exception:
        pass
    if not sys.platform.startswith("win") and os.path.exists("/dev/ttyUSB0"):
        return "/dev/ttyUSB0"
    return preferred


class EspCommunicator:
    """Send newline-delimited alert states and G11 FDFRAME packets without coupling detection to serial I/O."""

    def __init__(
        self,
        mode: str = "MOCK",
        port: Optional[str] = None,
        baudrate: int = DEFAULT_HIGHSPEED_BAUDRATE,
        timeout: float = 0.2,
        require_ack: bool = False,
        heartbeat_seconds: float = 6.0,
        on_downlink_xyz: Optional[Callable[[Dict[str, float]], None]] = None,
    ):
        self.mode = mode.upper()
        if self.mode not in {"MOCK", "SERIAL"}:
            raise ValueError("ESP mode must be MOCK or SERIAL")
        self.port = auto_detect_serial_port(port)
        self.baudrate = int(baudrate) if int(baudrate) in SUPPORTED_BAUDRATES else DEFAULT_HIGHSPEED_BAUDRATE
        self.timeout = timeout
        self.require_ack = require_ack
        self.heartbeat_seconds = heartbeat_seconds
        self.on_downlink_xyz = on_downlink_xyz
        self._desired = None
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._ack_event = threading.Event()
        self._last_ack_line = ""
        self.latest_telemetry = ""
        self.latest_downlink_xyz: Optional[Dict[str, float]] = None
        self.gateway_wifi_connected: bool = False
        self.gateway_server_online: bool = False
        self.gateway_is_paused: bool = False
        self.gateway_rssi_dbm: int = 0
        self._gateway_status_ts: float = 0.0
        self.latest_sensor: Dict[str, Any] = {}
        self._stop = False
        self._sequence = 0
        self._frame_seq = 0
        self._serial = None
        self._needs_handshake = False
        self._last_status = None
        self._last_voice_sent_at = 0.0
        self._last_voice_status = None
        self.voice_cooldown_s = float(os.getenv("ESP_VOICE_COOLDOWN_S", "5.0"))
        self.reset_count = 0
        self.frames_sent = 0
        self.frames_received = 0
        self._valid_rx_seen = False
        self.last_result: Optional[SendResult] = None
        self.last_delivery_status: str = "READY"
        self._hw_events_lock = threading.Lock()
        self._hw_events: list[Dict[str, Any]] = []

        self._reader_thread = None
        if self.mode == "SERIAL":
            self._open_serial()
            self._reader_thread = threading.Thread(target=self._reader_loop, name="esp-reader", daemon=True)
            self._reader_thread.start()
            if self.connected:
                self._needs_handshake = False
                threading.Thread(target=self._handshake, name="esp-auto-baud", daemon=True).start()

        self._thread = threading.Thread(target=self._run, name="esp-transport", daemon=True)
        self._thread.start()

    @property
    def is_high_speed(self) -> bool:
        """Kiểm tra kênh UART có đang hoạt động ở tốc độ cao (>= 460,800 bps) hay không."""
        return self.baudrate >= 460800

    def _open_serial(self):
        resolved_port = auto_detect_serial_port(self.port)
        if resolved_port:
            self.port = resolved_port
        if not self.port:
            self.last_result = SendResult(
                packet="",
                sent=False,
                acknowledged=False,
                error="A serial port is required in SERIAL mode",
            )
            return
        try:
            serial = importlib.import_module("serial")
            ser = serial.Serial()
            ser.port = self.port
            ser.baudrate = self.baudrate
            ser.timeout = max(0.1, min(self.timeout, 0.3))
            ser.write_timeout = max(self.timeout, 1.0)
            ser.dtr = False
            ser.rts = False
            ser.open()
            self._serial = ser
            self._needs_handshake = True
            print(f"[ESP SERIAL] Da ket noi cong {self.port} ({self.baudrate} baud | High-Speed={self.is_high_speed})")
        except Exception as exc:
            self._serial = None
            emit_error_event(
                code="E_CONN_OPEN",
                source="connect.esp32",
                node_path="connect.esp32.serial_open",
                err_class="source",
                message=f"Could not open ESP serial port {self.port}: {exc}",
                recoverable=True,
            )
            self.last_result = SendResult(
                packet="",
                sent=False,
                acknowledged=False,
                error=f"Could not open ESP serial port {self.port}: {exc}",
            )

    def _reader_loop(self):
        """Continuously drain telemetry (FDESP:/CO2:/FDFRAME) and capture EVT,ACK/PONG lines."""
        last_reconnect_try = 0.0
        while not self._stop:
            ser = self._serial
            if ser is None or not ser.is_open:
                now = time.monotonic()
                if self.mode == "SERIAL" and (now - last_reconnect_try) >= 2.0:
                    last_reconnect_try = now
                    self._open_serial()
                time.sleep(0.1)
                continue
            try:
                raw = ser.readline()
                if not raw:
                    continue
                # 1. Kiểm tra khung nhị phân FDFRAME (0xAA 0x55) tốc độ cao G11
                magic_idx = raw.find(MAGIC_HEADER)
                if magic_idx >= 0:
                    frame, _, err = decode_fd_frame(raw[magic_idx:])
                    if frame is not None:
                        self._valid_rx_seen = True
                        self.frames_received += 1
                        parsed = frame.to_json()
                        if frame.frame_type == FRAME_TYPE_TELEMETRY and isinstance(parsed, dict):
                            self.latest_telemetry = "FDESP:" + json.dumps(parsed, separators=(",", ":"))
                            self._ingest_sensor_dict(parsed)
                        elif frame.frame_type == FRAME_TYPE_NETWORK_STATUS and isinstance(parsed, dict):
                            self.gateway_wifi_connected = bool(parsed.get("wifi_connected", False))
                            self.gateway_server_online = bool(parsed.get("partner_server_online", False))
                            self.gateway_is_paused = bool(parsed.get("is_paused", False))
                            self.gateway_rssi_dbm = int(parsed.get("rssi_dbm", 0) or 0)
                            self._gateway_status_ts = time.monotonic()
                        elif frame.frame_type == FRAME_TYPE_DOWNLINK_ACK and isinstance(parsed, dict):
                            self._last_ack_line = f"EVT,ACK,FDFRAME,{parsed.get('msg', 'OK')}"
                            self._ack_event.set()
                            srv_xyz = parsed.get("server_xyz")
                            if not isinstance(srv_xyz, dict):
                                # ESP chuyển nguyên phản hồi của Server: {"resp":{"downlink_xyz":{...}}}
                                resp = parsed.get("resp")
                                dl = resp.get("downlink_xyz") if isinstance(resp, dict) else None
                                if isinstance(dl, dict):
                                    srv_xyz = {
                                        "lat": dl.get("lat", 0.0),
                                        "lon": dl.get("lon", 0.0),
                                        "alt": dl.get("alt_m", dl.get("alt", 0.0)),
                                    }
                            if isinstance(srv_xyz, dict):
                                self.latest_downlink_xyz = srv_xyz
                                if self.on_downlink_xyz is not None:
                                    try:
                                        self.on_downlink_xyz(srv_xyz)
                                    except Exception:
                                        pass
                        continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                # Lọc bỏ rác bootloader đầu dòng nếu có
                if "FDESP:" in line:
                    line = line[line.index("FDESP:"):]
                elif "CO2:" in line:
                    line = line[line.index("CO2:"):]
                elif "EVT," in line:
                    line = line[line.index("EVT,"):]
                elif "PONG" in line:
                    line = "PONG"

                if "ACK" in line or line == "PONG":
                    # Khớp linh hoạt: chịu được rớt vài byte UART (vd "EVT,ACKOICE,4")
                    self._valid_rx_seen = True
                    self._last_ack_line = line
                    self._ack_event.set()
                elif line.startswith("FDESP:"):
                    self._valid_rx_seen = True
                    self.latest_telemetry = line
                    # Giải mã JSON đúng 1 lần ở luồng đọc UART; các nơi khác chỉ đọc latest_sensor
                    try:
                        jd = json.loads(line[6:])
                        if isinstance(jd, dict):
                            self._ingest_sensor_dict(jd)
                    except ValueError:
                        pass
                elif line.startswith("CO2:"):
                    self._valid_rx_seen = True
                    if not self.latest_telemetry.startswith("FDESP:"):
                        self.latest_telemetry = line
                elif line.startswith("EVT,DOWNLINK_XYZ,"):
                    self._valid_rx_seen = True
                    parts = line.split(",")
                    if len(parts) >= 5:
                        try:
                            xyz = {"x": float(parts[2]), "y": float(parts[3]), "z": float(parts[4])}
                            self.latest_downlink_xyz = xyz
                            if self.on_downlink_xyz is not None:
                                self.on_downlink_xyz(xyz)
                        except ValueError:
                            pass
                elif line.startswith("EVT,"):
                    self._valid_rx_seen = True
                    if "BOOT" in line:
                        self.reset_count += 1
                        self._last_voice_sent_at = 0.0
                        print(f"[ESP EVENT] ESP32 khoi dong ({line})")
                    elif "BROWNOUT" in line:
                        self._last_voice_sent_at = 0.0
                        print(f"[ESP WARN] ESP32 sut ap nguon loa ({line}) -> kiem tra nguon 5V cap cho MAX98357A!")
                        self._record_hw_event(
                            alert="ESP_BROWNOUT",
                            classes=["Sụt áp nguồn loa (Brownout Reset)", line],
                            cmd="HARDWARE_WARN",
                        )
                    elif "RESET_REASON" in line:
                        if "POWERON" not in line:
                            print(f"[ESP WARN] ESP32 reset bat thuong ({line})")
                            self._record_hw_event(
                                alert="ESP_RESET",
                                classes=[f"Lý do reset: {line}"],
                                cmd="HARDWARE_WARN",
                            )
                    elif line.startswith("EVT,AUTO_ALERT,CO2"):
                        print(f"[ESP CO2 ALERT] {line}")
                        parts = line.split(",")
                        co2_type = parts[2] if len(parts) >= 3 else "CO2_ALERT"
                        co2_val = parts[3] if len(parts) >= 4 else ""
                        voice_clip = "VOICE:5 (CO2 Nguy hiểm)" if "CRIT" in co2_type else "VOICE:4 (CO2 Bí khí)"
                        self._record_hw_event(
                            alert=f"AUTO_{co2_type}",
                            classes=[f"Cảm biến MQ-135 tự kích hoạt ({co2_val} ppm)"],
                            cmd=f"{voice_clip} [Tự phát trên ESP32]",
                        )
                    elif "MUTE" in line:
                        print(f"[ESP EVENT] {line}")
                        self._record_hw_event(
                            alert="MUTE_ACTIVE",
                            classes=["Nút nhấn tắt tiếng tạm thời 30s"],
                            cmd=line,
                        )
                    else:
                        print(f"[ESP EVENT] {line}")
            except Exception:
                self._disconnect()
                time.sleep(0.2)

    def _record_hw_event(self, alert: str, classes: list[str], cmd: str):
        with self._hw_events_lock:
            self._hw_events.append({
                "time": time.strftime("%H:%M:%S"),
                "alert": alert,
                "classes": classes,
                "cmd": cmd,
            })
            if len(self._hw_events) > 30:
                self._hw_events.pop(0)

    def pop_hw_events(self) -> list[Dict[str, Any]]:
        """Lấy và xóa các sự kiện phần cứng mới phát sinh từ ESP32/UART để hiển thị lên Web Dashboard."""
        with self._hw_events_lock:
            if not self._hw_events:
                return []
            items = list(self._hw_events)
            self._hw_events.clear()
            return items

    def _ingest_sensor_dict(self, data: Dict[str, Any]) -> None:
        """Lưu bản tin cảm biến ESP32 đã giải mã (thay vì mỗi nơi tự json.loads chuỗi thô)."""
        self.latest_sensor = data

    @property
    def gateway_ready(self) -> bool:
        """True khi ESP32 THỰC SỰ có WiFi + Server sống + chưa nghẽn (trạng thái còn mới <8s)."""
        return bool(
            self.connected
            and self.gateway_wifi_connected
            and self.gateway_server_online
            and not self.gateway_is_paused
            and (time.monotonic() - self._gateway_status_ts) < 8.0
        )

    def send_telemetry_frame(self, payload: Dict[str, Any], max_bytes: int = 3800) -> bool:
        """Đẩy telemetry tổng hợp cho ESP32 đăng lên Server qua WiFi của ESP (Pi không mở HTTP).

        Trả False nếu payload quá lớn / cổng đóng -> bên gọi tự dự phòng HTTP trực tiếp.
        """
        if self.mode != "SERIAL" or not self.connected:
            return False
        try:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return False
        if len(body) > max_bytes:
            return False
        self._frame_seq = (self._frame_seq + 1) & 0xFFFF
        return self.send_frame_bytes(
            encode_fd_frame(frame_type=FRAME_TYPE_TELEMETRY, payload=body, seq=self._frame_seq)
        )

    @property
    def connected(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    @property
    def mode_label(self) -> str:
        return self.mode

    def send_frame_bytes(self, frame_bytes: bytes) -> bool:
        """Truyền khối byte khung FDFRAME trực tiếp xuống ESP32 qua UART tốc độ cao."""
        if self.mode == "MOCK":
            self.frames_sent += 1
            return True
        if not self.connected:
            self._open_serial()
        if not self.connected or not frame_bytes:
            return False
        ser = self._serial
        if ser is None:
            return False
        try:
            with self._write_lock:
                ser.write(frame_bytes)
                if not frame_bytes.endswith(b"\n"):
                    ser.write(b"\n")
                ser.flush()
            self.frames_sent += 1
            return True
        except Exception:
            self._disconnect()
            return False

    def send_fd_frame(self, frame_type: int, payload: Any) -> bool:
        """Đóng gói dữ liệu thành khung nhị phân FDFRAME (CRC16) và bắn qua UART sang ESP32."""
        self._frame_seq = (self._frame_seq + 1) & 0xFFFF
        frame_bytes = encode_fd_frame(frame_type=frame_type, payload=payload, seq=self._frame_seq)
        return self.send_frame_bytes(frame_bytes)

    def send_raw_command(self, cmd: str, wait_ack: bool = True, timeout: float = 1.2) -> tuple[bool, str]:
        """Send a direct firmware command (e.g. VOICE:1, STOP, PING, MUTE) and wait for ACK."""
        if self.mode != "SERIAL":
            return False, "NOT_SERIAL_MODE"
        if not self.connected:
            self._open_serial()
        if not self.connected:
            return False, "DISCONNECTED"
        # Thêm '\n' phía trước để xả sạch mọi byte rác còn sót trong bộ đệm rxLine của ESP32
        wire = "\n" + cmd.strip() + "\n"
        self._ack_event.clear()
        ser = self._serial
        if ser is None:
            return False, "DISCONNECTED"
        try:
            with self._write_lock:
                ser.write(wire.encode("ascii"))
                ser.flush()
        except Exception as exc:
            self._disconnect()
            return False, f"WRITE_ERROR: {exc}"
        if not wait_ack:
            return True, ""
        ok = self._ack_event.wait(timeout=timeout)
        return ok, (self._last_ack_line if ok else "")

    def send_status(
        self,
        status: str,
        duration_seconds: float = 0.0,
        timestamp: Optional[str] = None,
    ) -> Optional[SendResult]:
        status = status.upper()
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported ESP status: {status}")
        with self._condition:
            if self._desired is None or self._desired[0] != status:
                self._sequence += 1
                self._desired = (status, duration_seconds, timestamp, self._sequence)
                self._condition.notify_all()
        return self.last_result

    def _transmit(self, status, duration_seconds, timestamp, sequence):
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported ESP status: {status}")

        duration_ms = max(0, round(float(duration_seconds) * 1000))
        event_code = EVENT_CODES[status]
        packet_timestamp = timestamp or iso_timestamp()
        packet = f"ALERT,{event_code},{status},{packet_timestamp},{duration_ms}\n"
        if self.require_ack:
            packet = packet.rstrip() + f",{sequence}\n"

        if self.mode == "MOCK":
            print(f"[ESP MOCK] {packet.rstrip()}")
            result = SendResult(packet, sent=False, acknowledged=False)
            self.last_result = result
            return result

        if not self.connected:
            result = SendResult(
                packet,
                sent=False,
                acknowledged=False,
                error=self.last_result.error if self.last_result else "Serial port is closed",
            )
            self.last_result = result
            return result

        try:
            use_voice = os.getenv("ESP_USE_VOICE", "1") != "0"
            interrupt_voice = os.getenv("ESP_INTERRUPT_VOICE", "0") == "1"
            if status == "NORMAL" and use_voice and self._last_status in {"DROWSY", "YAWNING", "DISTRACTED"} and not interrupt_voice:
                self._last_status = status
                print("[ESP SERIAL] State -> NORMAL (giu nguyen de ESP32 doc het cau thoai trong voice_clips.h)")
                result = SendResult(packet, sent=True, acknowledged=True)
                self.last_result = result
                return result

            # Chống phát chồng/ngắt quãng: trong thời gian cooldown (>= độ dài câu thoại ~4.3s)
            # không gửi lại VOICE, trừ khi nâng mức nguy hiểm (YAWNING/DISTRACTED -> DROWSY).
            if use_voice and status in {"DROWSY", "YAWNING", "DISTRACTED"}:
                since = time.monotonic() - self._last_voice_sent_at
                escalate = status == "DROWSY" and self._last_voice_status in {"YAWNING", "DISTRACTED"}
                if since < self.voice_cooldown_s and not escalate:
                    self._last_status = status
                    cd_left = self.voice_cooldown_s - since
                    self.last_delivery_status = f"COOLDOWN ({cd_left:.1f}s)"
                    result = SendResult(packet, sent=True, acknowledged=True,
                                        error=f"cooldown {cd_left:.1f}s")
                    self.last_result = result
                    return result

            if status == "DROWSY":
                wire_cmd = "\nVOICE:1\n" if use_voice else f"\nALERT:2:{min(1500, max(500, duration_ms))}\n"
            elif status == "YAWNING":
                wire_cmd = "\nVOICE:2\n" if use_voice else f"\nALERT:1:{min(1500, max(300, duration_ms))}\n"
            elif status == "DISTRACTED":
                wire_cmd = "\nVOICE:3\n" if use_voice else "\nALERT:2:500\n"
            else:
                wire_cmd = "\nSTOP\n"

            self._ack_event.clear()
            ser = self._serial
            if ser is None:
                raise OSError("Serial port closed during transmit")
            with self._write_lock:
                ser.write(wire_cmd.encode("ascii"))
                ser.flush()
            if status in {"DROWSY", "YAWNING", "DISTRACTED"}:
                self._last_voice_sent_at = time.monotonic()
                self._last_voice_status = status

            acknowledged = False
            ack_line = ""
            if self.require_ack:
                if self._ack_event.wait(timeout=max(self.timeout, 1.0)):
                    acknowledged = True
                    ack_line = self._last_ack_line
            self._last_status = status
            clean_cmd = wire_cmd.strip()
            if status in {"DROWSY", "YAWNING", "DISTRACTED"}:
                if acknowledged:
                    self.last_delivery_status = f"{clean_cmd} [ACK: {ack_line}]"
                elif self.require_ack:
                    self.last_delivery_status = f"{clean_cmd} [NO_ACK_TIMEOUT]"
                    emit_error_event(
                        code="E_RESP_NO_ACK",
                        source="response",
                        node_path="response.esp32.ack",
                        err_class="source",
                        message=f"ESP32 khong phan hoi ACK cho lenh {clean_cmd} ({status})",
                        recoverable=True,
                    )
                else:
                    self.last_delivery_status = f"{clean_cmd} [SENT]"
            print(f"[ESP SERIAL] Sent {clean_cmd} ({status}) | ACK={acknowledged} {ack_line}".rstrip())
            result = SendResult(
                clean_cmd + "\n" if self.mode == "SERIAL" else packet,
                sent=True,
                acknowledged=acknowledged,
                error="ACK timeout" if self.require_ack and not acknowledged else None,
            )
        except Exception as exc:
            self._disconnect()
            self.last_delivery_status = f"ERR: {exc}"
            result = SendResult(packet, sent=False, acknowledged=False, error=str(exc))
        self.last_result = result
        return result

    def _disconnect(self):
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _handshake(self) -> bool:
        """Sau khi (mở lại) cổng: chờ ESP32 khởi động xong (khoảng 0.8s) và giữ nguyên 921,600 bps nếu đã thấy bản tin hợp lệ."""
        # Chờ tối đa 1.2s đầu tiên để ESP32 hoàn tất setup() nếu cổng USB vừa kích DTR/RTS reset
        t_start = time.monotonic()
        while (time.monotonic() - t_start) < 1.2:
            if self._valid_rx_seen or self.latest_telemetry:
                return True
            time.sleep(0.1)

        for _ in range(2):
            if self._valid_rx_seen or self.latest_telemetry:
                return True
            try:
                ok, ack = self.send_raw_command("PING", wait_ack=True, timeout=0.6)
            except Exception:
                ok, ack = False, ""
            if (ok and "PONG" in ack) or self._valid_rx_seen or self.latest_telemetry:
                return True

        # Chỉ thử tốc độ dự phòng (115,200 bps) nếu hoàn toàn chưa nhận được bất kỳ dòng hợp lệ nào ở 921,600 bps
        orig_baud = self.baudrate
        alt_baud = FALLBACK_BAUDRATE if orig_baud == DEFAULT_HIGHSPEED_BAUDRATE else DEFAULT_HIGHSPEED_BAUDRATE
        ser = self._serial
        if ser is not None and ser.is_open and not self._valid_rx_seen:
            try:
                ser.baudrate = alt_baud
                self.baudrate = alt_baud
                print(f"[ESP AUTO-BAUD] Thu kiem tra toc do du phong {alt_baud} bps...")
                for _ in range(2):
                    ok, ack = self.send_raw_command("PING", wait_ack=True, timeout=0.6)
                    if (ok and "PONG" in ack) or self._valid_rx_seen or self.latest_telemetry:
                        print(f"[ESP AUTO-BAUD] Dong bo thanh cong tai {alt_baud} bps!")
                        return True
                # Nếu tốc độ dự phòng cũng không phản hồi -> KHÔI PHỤC NGAY tốc độ gốc (921,600 bps)!
                ser.baudrate = orig_baud
                self.baudrate = orig_baud
                print(f"[ESP AUTO-BAUD] Khoi phuc toc do chuan {orig_baud} bps.")
            except Exception:
                try:
                    ser.baudrate = orig_baud
                    self.baudrate = orig_baud
                except Exception:
                    pass
        return False

    def _run(self):
        delivered = None
        retry = 1.0
        next_attempt = 0.0
        last_send = 0.0
        while True:
            with self._condition:
                if self._stop:
                    break
                desired = self._desired
                now = time.monotonic()
                is_active_alert = desired is not None and desired[0] in {"DROWSY", "YAWNING", "DISTRACTED"}
                due = desired is not None and (
                    desired != delivered or (is_active_alert and now - last_send >= self.heartbeat_seconds)
                )
                if not due or now < next_attempt:
                    self._condition.wait(0.1)
                    continue
            try:
                if self.mode == "SERIAL" and not self.connected:
                    self._open_serial()
                if self.mode == "SERIAL" and self.connected and self._needs_handshake:
                    self._needs_handshake = False
                    if not self._handshake():
                        print("[ESP WARN] ESP32 chua tra PONG sau khi mo cong (dang khoi dong?)")
                result = self._transmit(*desired)
            except Exception as exc:
                result = SendResult("", sent=False, acknowledged=False, error=str(exc))
            # Lệnh VOICE đã ghi xuống cổng thì KHÔNG gửi lại khi thiếu ACK (tránh bão phát lại
            # làm loa lặp/giật). Chỉ STOP mới cần ACK để chắc chắn đã tắt.
            is_voice = desired[0] in {"DROWSY", "YAWNING", "DISTRACTED"}
            success = self.mode == "MOCK" or (
                result.sent and (not self.require_ack or result.acknowledged or is_voice)
            )
            if success:
                delivered = desired
                last_send = time.monotonic()
                retry = 1.0
                next_attempt = 0.0
            else:
                next_attempt = time.monotonic() + retry
                retry = min(retry * 2, 3.0)
        self._disconnect()

    def close(self):
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(timeout=max(2.0, self.timeout * 3))
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        if self._thread.is_alive():
            raise RuntimeError("ESP worker did not stop")
