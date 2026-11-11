"""Vantech IP Camera (Ethernet LAN RTSP) & External Camera Configuration.

NOTE BẢO MẬT & VẬN HÀNH:
- Tuyệt đối KHÔNG chạy webcam máy tính (index 0).
- Nếu không bắt được camera ngoại: lặp lại thử kết nối liên tục đến khi bắt được thì dừng việc chờ.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any, List, Optional, Tuple, Union

os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")
import cv2

try:
    cv2.setLogLevel(0)
    cv2.setNumThreads(2)
except Exception:
    pass

# Thông tin xác thực và địa chỉ mặc định của Camera IP Vantech / Dahua (Dự án IOTv3)
# Lưu ý: Mật khẩu và IP thật đọc từ biến môi trường (AI/deploy/pi.env), không gán cứng mật khẩu vào mã nguồn.
DEFAULT_CAMERA_IP = os.getenv("FD_CAMERA_IP", "192.168.1.210").strip() or "192.168.1.210"
DEFAULT_CAMERA_PORT = int(os.getenv("FD_CAMERA_PORT", "554"))
DEFAULT_CAMERA_USER = os.getenv("FD_CAMERA_USER", "admin").strip() or "admin"
DEFAULT_CAMERA_PASSWORD = os.getenv("FD_CAMERA_PASSWORD", "").strip()

# Danh sách các định dạng URL RTSP chuẩn của Camera IP Vantech (192.168.1.210)
DEFAULT_RTSP_CANDIDATES = [
    f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/user={DEFAULT_CAMERA_USER}_password={DEFAULT_CAMERA_PASSWORD}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream",
    f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/user={DEFAULT_CAMERA_USER}_password={DEFAULT_CAMERA_PASSWORD}_channel=1_stream=0&protocol=unicast&onvif=true.sdp?real_stream",
    f"rtsp://{DEFAULT_CAMERA_USER}:{DEFAULT_CAMERA_PASSWORD}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/ch01/0",
    f"rtsp://{DEFAULT_CAMERA_USER}:{DEFAULT_CAMERA_PASSWORD}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/ch01/1",
    f"rtsp://{DEFAULT_CAMERA_USER}:{DEFAULT_CAMERA_PASSWORD}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/cam/realmonitor?channel=1&subtype=0",
    f"rtsp://{DEFAULT_CAMERA_USER}:{DEFAULT_CAMERA_PASSWORD}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/cam/realmonitor?channel=1&subtype=1",
]

DEFAULT_RTSP_TEMPLATE = DEFAULT_RTSP_CANDIDATES[0]


_ETH_ROUTE_WARNED = False


def ensure_pi_eth_camera_route(camera_ip: str = DEFAULT_CAMERA_IP) -> bool:
    """Kiểm tra (Read-Only, KHÔNG gọi sudo ngầm) xem trên Raspberry Pi (Linux),
    đường truyền tới Camera IP (192.168.1.210) đã được NetworkManager định tuyến
    qua cổng dây mạng RJ45 (eth0 / end0) hay chưa.
    """
    global _ETH_ROUTE_WARNED
    if not sys.platform.startswith("linux"):
        return False

    net_dir = Path("/sys/class/net")
    if not net_dir.exists():
        return False

    wired_ifaces = []
    for iface_path in sorted(net_dir.iterdir()):
        name = iface_path.name
        if name.startswith(("eth", "end", "enx", "enp")):
            wired_ifaces.append(name)

    if not wired_ifaces:
        return False

    iface = wired_ifaces[0]
    try:
        carrier_file = Path(f"/sys/class/net/{iface}/carrier")
        carrier = carrier_file.read_text().strip() if carrier_file.exists() else "0"
        if carrier != "1":
            return False

        # Kiểm tra bảng định tuyến không cần quyền sudo
        chk = subprocess.run(
            ["ip", "route", "show", f"{camera_ip}/32"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if chk.returncode == 0 and f"dev {iface}" in (chk.stdout or ""):
            return True

        if not _ETH_ROUTE_WARNED:
            _ETH_ROUTE_WARNED = True
            print(
                f"[CAMERA NET WARN] Cổng {iface} đang cắm cáp LAN (carrier=1) nhưng chưa có route {camera_ip}/32 qua {iface}. "
                f"Vui lòng kiểm tra profile NetworkManager (netplan-{iface}) thay vì gọi sudo ngầm."
            )
        return False
    except Exception:
        return False


class RotatedVideoCapture:
    """Wrapper bọc quanh cv2.VideoCapture để hỗ trợ xoay hoặc lật khung hình (ví dụ gắn ngược camera)."""

    def __init__(self, cap: cv2.VideoCapture, rotation: int = 0, flip_code: Optional[int] = None):
        self._cap = cap
        self._rotation = rotation
        self._flip_code = flip_code

    def _transform(self, frame):
        if frame is None:
            return None
        if self._rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if self._flip_code is not None:
            frame = cv2.flip(frame, self._flip_code)
        return frame

    def isOpened(self) -> bool:
        return bool(self._cap.isOpened())

    def read(self, image=None):
        ok, frame = self._cap.read(image) if image is not None else self._cap.read()
        if not ok or frame is None:
            return ok, frame
        return ok, self._transform(frame)

    def retrieve(self, image=None, flag=0):
        if image is not None:
            ok, frame = self._cap.retrieve(image, flag)
        else:
            ok, frame = self._cap.retrieve()
        if not ok or frame is None:
            return ok, frame
        return ok, self._transform(frame)

    def grab(self) -> bool:
        return bool(self._cap.grab())

    def release(self) -> None:
        self._cap.release()

    def get(self, propId: int):
        return self._cap.get(propId)

    def set(self, propId: int, value: float) -> bool:
        return bool(self._cap.set(propId, value))

    def __getattr__(self, name: str):
        return getattr(self._cap, name)


def get_camera_credentials() -> dict:
    """Trả về thông tin URL và Mật khẩu của camera."""
    pwd = os.getenv("FD_CAMERA_PASSWORD", "").strip() or DEFAULT_CAMERA_PASSWORD
    uri = os.getenv("FD_CAMERA_URI", "").strip()
    if not uri:
        uri = (
            f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/"
            f"user={DEFAULT_CAMERA_USER}_password={pwd}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream"
        )
    return {
        "user": DEFAULT_CAMERA_USER,
        "password": pwd,
        "primary_url": uri,
        "candidate_urls": [
            f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/user={DEFAULT_CAMERA_USER}_password={pwd}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream",
            f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/user={DEFAULT_CAMERA_USER}_password={pwd}_channel=1_stream=0&protocol=unicast&onvif=true.sdp?real_stream",
            f"rtsp://{DEFAULT_CAMERA_USER}:{pwd}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/ch01/0",
            f"rtsp://{DEFAULT_CAMERA_USER}:{pwd}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/ch01/1",
            f"rtsp://{DEFAULT_CAMERA_USER}:{pwd}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/cam/realmonitor?channel=1&subtype=0",
            f"rtsp://{DEFAULT_CAMERA_USER}:{pwd}@{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/cam/realmonitor?channel=1&subtype=1",
        ],
        "default_ip": DEFAULT_CAMERA_IP,
        "default_port": DEFAULT_CAMERA_PORT,
    }


def get_hardware_video_devices() -> List[Union[str, int]]:
    """Tự động quét các thiết bị camera USB / V4L2 vật lý THỰC SỰ (bỏ qua ISP & Codec VideoCore video10..31 của Pi)."""
    devices: List[Union[str, int]] = []
    is_linux = sys.platform.startswith("linux")

    if is_linux:
        # Kiểm tra qua /sys/class/video4linux để lọc chính xác camera thật (tránh quét ISP gây timeout 10s)
        sys_v4l = Path("/sys/class/video4linux")
        if sys_v4l.exists():
            for entry in sorted(sys_v4l.iterdir()):
                v_name = entry.name
                if not v_name.startswith("video"):
                    continue
                num_str = v_name.replace("video", "")
                if not num_str.isdigit():
                    continue
                v_num = int(num_str)

                # Không quét video10..video31 (bộ giải mã phần cứng bcm2835-codec & ISP của Pi)
                if v_num >= 10:
                    continue

                if not os.path.exists(f"/dev/video{v_num}"):
                    continue

                name_file = entry / "name"
                dev_desc = ""
                if name_file.exists():
                    try:
                        dev_desc = name_file.read_text(encoding="utf-8", errors="replace").strip().lower()
                    except Exception:
                        pass

                if any(k in dev_desc for k in ("bcm2835", "codec", "isp", "m2m", "meta", "pisp")):
                    continue

                if v_num not in devices:
                    devices.append(v_num)
    else:
        if os.getenv("ALLOW_BUILTIN_CAMERA", "0") == "1":
            devices.append(0)
        devices.extend([1, 2])

    return devices


def resolve_camera_target() -> Union[str, int]:
    """Phân giải mục tiêu camera ngoại ưu tiên (RTSP LAN hoặc USB ngoại vi)."""
    is_linux = sys.platform.startswith("linux")
    dev = os.getenv("CAMERA_DEVICE", "").strip()

    if dev:
        if dev.isdigit():
            dev_idx = int(dev)
            if dev_idx == 0 and not is_linux and os.getenv("ALLOW_BUILTIN_CAMERA", "0") != "1":
                print("[CẢNH BÁO BẢO VỆ] Phát hiện CAMERA_DEVICE=0 trên Windows (Webcam máy tính).")
                print("                 Hệ thống chuyển hướng sang camera ngoại RTSP theo quy tắc an toàn.")
            else:
                return dev_idx
        else:
            return dev

    uri = os.getenv("FD_CAMERA_URI", "").strip()
    if not uri:
        pwd = os.getenv("FD_CAMERA_PASSWORD", "").strip() or DEFAULT_CAMERA_PASSWORD
        uri = (
            f"rtsp://{DEFAULT_CAMERA_IP}:{DEFAULT_CAMERA_PORT}/"
            f"user={DEFAULT_CAMERA_USER}_password={pwd}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream"
        )

    return uri


def _substream_variant(url: str) -> Optional[str]:
    """Suy ra URL luồng phụ từ URL luồng chính (Vantech/Dahua); None nếu không nhận dạng được."""
    if not url.lower().startswith("rtsp://"):
        return None
    explicit = os.getenv("FD_CAMERA_URI_SUB", "").strip()
    if explicit:
        return explicit
    for main_tok, sub_tok in (("stream=0", "stream=1"), ("subtype=0", "subtype=1")):
        if main_tok in url:
            return url.replace(main_tok, sub_tok, 1)
    if url.endswith("/ch01/0"):
        return url[:-1] + "1"
    return None


def get_candidate_camera_targets() -> List[Union[str, int]]:
    """Tạo danh sách toàn diện tất cả các nguồn Camera ngoại khả dĩ (RTSP LAN + USB Webcams)."""
    candidates: List[Union[str, int]] = []
    primary = resolve_camera_target()
    # Ưu tiên luồng phụ (sub-stream, độ phân giải thấp): giải mã H.264 1080p liên tục là nguồn
    # nhiệt lớn nhất trên Pi; AI chỉ cần ~640x480. Nếu camera không có sub-stream thì tự rơi
    # về luồng chính ở bước dưới (mở không đọc được khung hình -> thử mục tiêu kế tiếp).
    if os.getenv("CAMERA_PREFER_SUBSTREAM", "1") == "1" and isinstance(primary, str):
        sub = _substream_variant(primary)
        if sub and sub not in candidates:
            candidates.append(sub)
    if primary not in candidates:
        candidates.append(primary)

    creds = get_camera_credentials()
    for u in creds["candidate_urls"]:
        if u not in candidates:
            candidates.append(u)

    # Thêm các cổng camera USB / V4L2 vật lý
    hw_devs = get_hardware_video_devices()
    for d in hw_devs:
        if d not in candidates:
            candidates.append(d)

    return candidates


def _apply_video_properties(cap: cv2.VideoCapture) -> cv2.VideoCapture:
    """Thiết lập kích thước, FPS và cơ chế xoay/lật khung hình."""
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(os.getenv("CAMERA_WIDTH", "640")))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(os.getenv("CAMERA_HEIGHT", "480")))
    cap.set(cv2.CAP_PROP_FPS, float(os.getenv("CAMERA_FPS", "25")))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    rotation_str = os.getenv("CAMERA_ROTATION", "0").strip()
    rotation = int(rotation_str) if rotation_str.isdigit() else 0
    flip_raw = os.getenv("CAMERA_FLIP", "").strip().lower()
    flip_code = None
    if flip_raw in ("h", "horizontal", "1"):
        flip_code = 1
    elif flip_raw in ("v", "vertical", "0"):
        flip_code = 0
    elif flip_raw in ("both", "hv", "vh", "-1"):
        flip_code = -1

    if rotation in (90, 180, 270) or flip_code is not None:
        return RotatedVideoCapture(cap, rotation=rotation, flip_code=flip_code)
    return cap


def is_rtsp_online(url: str, timeout: float = 0.5) -> bool:
    """Kiểm tra nhanh xem cổng RTSP (554) của camera có đang phản hồi không trước khi gọi FFMPEG."""
    try:
        match = re.search(r"@?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)(?::([0-9]+))?", url)
        if match:
            host = match.group(1)
            port = int(match.group(2)) if match.group(2) else 554
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            res = s.connect_ex((host, port))
            s.close()
            return res == 0
    except Exception:
        pass
    return False


def _try_open_target(target: Union[str, int]) -> Optional[cv2.VideoCapture]:
    """Thử mở một mục tiêu camera ngoại cụ thể (RTSP hoặc USB/V4L2)."""
    is_linux = sys.platform.startswith("linux")

    # BẢO VỆ: Tuyệt đối không mở webcam tích hợp máy tính trên Windows
    if target == 0 and not is_linux and os.getenv("ALLOW_BUILTIN_CAMERA", "0") != "1":
        return None

    try:
        if isinstance(target, str) and target.lower().startswith("rtsp://"):
            # Kiểm tra nhanh cổng mạng LAN: nếu camera chưa cắm/chưa bật nguồn, thoát ngay trong 0.5s
            if not is_rtsp_online(target, timeout=0.5):
                return None
            os.environ.setdefault(
                "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|threads;2|stimeout;2000000",
            )
            cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        elif isinstance(target, str) and (target.startswith("/dev/video") or os.path.exists(target)):
            m = re.search(r"video(\d+)", target)
            dev_idx = int(m.group(1)) if m else target
            backend = cv2.CAP_V4L2 if is_linux else cv2.CAP_ANY
            cap = cv2.VideoCapture(dev_idx, backend)
        elif isinstance(target, int):
            backend = cv2.CAP_V4L2 if is_linux else cv2.CAP_ANY
            cap = cv2.VideoCapture(target, backend)
        else:
            backend = cv2.CAP_V4L2 if is_linux else cv2.CAP_ANY
            cap = cv2.VideoCapture(target, backend)

        if cap is not None and cap.isOpened():
            # Thử đọc 1 frame thực tế để đảm bảo camera hoạt động thật
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                return _apply_video_properties(cap)
            cap.release()
    except Exception:
        pass
    return None


def open_camera() -> Optional[cv2.VideoCapture]:
    """Mở camera ngoại (RTSP/USB ngoại vi) một lần duy nhất. Tự động chuyển đổi nếu một nguồn offline."""
    ensure_pi_eth_camera_route()
    targets = get_candidate_camera_targets()
    for target in targets:
        cap = _try_open_target(target)
        if cap is not None:
            return cap
    return None


def wait_for_external_camera(
    retry_interval: float = 1.0,
    stop_event: Optional[Any] = None,
    max_wait_seconds: float = 3.0,
) -> Optional[cv2.VideoCapture]:
    """Thử quét kết nối camera ngoại (RTSP LAN hoặc USB Webcam trên Pi).
    
    Nếu camera đã cắm/bật sẵn -> trả về cap ngay lập tức.
    Nếu camera chưa cắm/chưa có điện sau max_wait_seconds -> trả về None để hệ thống
    (Web Server, ESP32, Telemetry sang PC Server) khởi động ngay mà không bị treo,
    sau đó CameraFrameSource sẽ tự động bắt luồng ngay khi camera được cắm vào!
    """
    ensure_pi_eth_camera_route()
    candidates = get_candidate_camera_targets()
    creds = get_camera_credentials()

    print("\n" + "=" * 70)
    print(" [HỆ THỐNG GIÁM SÁT] KHỞI ĐỘNG CHẾ ĐỘ QUÉT CAMERA NGOẠI (RTSP + USB)")
    print(f" -> Danh sách mục tiêu quét: {candidates}")
    pwd_raw = creds.get("password", "")
    pwd_masked = (pwd_raw[:2] + "***" + pwd_raw[-1:]) if len(pwd_raw) > 3 else ("***" if pwd_raw else "(chưa đặt)")
    print(f" -> Mật khẩu camera IP:      {pwd_masked}")
    print(" -> QUY TẮC AN TOÀN:         TUYỆT ĐỐI KHÔNG SỬ DỤNG WEBCAM MÁY TÍNH TRÊN PC")
    print("=" * 70 + "\n")

    t_start = time.time()
    attempt = 1
    while True:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            raise KeyboardInterrupt("Đã nhận tín hiệu dừng khi đang chờ camera ngoại.")

        fresh_candidates = get_candidate_camera_targets()

        for target in fresh_candidates:
            cap = _try_open_target(target)
            if cap is not None and cap.isOpened():
                print("\n" + "*" * 70)
                print(f" [THÀNH CÔNG] ĐÃ BẮT ĐƯỢC TÍN HIỆU CAMERA NGOẠI TẠI:")
                print(f"              {target}")
                print(f"              BẮT ĐẦU VẬN HÀNH THU HÌNH & MÔ HÌNH AI!")
                print("*" * 70 + "\n")
                return cap

        if max_wait_seconds > 0 and (time.time() - t_start) >= max_wait_seconds:
            print("\n" + "-" * 70)
            print(" [CHẾ ĐỘ TỰ ĐỘNG BẮT LUỒNG NGẦM (ASYNC AUTO-CONNECT)]")
            print(" Chưa phát hiện Camera ngay lúc này (RTSP 192.168.1.210 chưa có điện hoặc chưa cắm USB Cam vào Pi).")
            print(" -> BẬT NGAY Web Dashboard & Dịch Vụ Truyền Tin sang Máy Chủ PC!")
            print(" -> CameraFrameSource sẽ tự động nhận diện và mở hình ngay khi bạn cắm camera.")
            print("-" * 70 + "\n")
            return None

        print(
            f"[CAMERA NGOẠI] Lần thử {attempt}: Đang dò tìm camera ({fresh_candidates})... "
            f"Thử lại sau {retry_interval}s..."
        )
        attempt += 1

        try:
            time.sleep(retry_interval)
        except KeyboardInterrupt:
            raise KeyboardInterrupt("Người dùng đã hủy chờ kết nối camera ngoại.")
