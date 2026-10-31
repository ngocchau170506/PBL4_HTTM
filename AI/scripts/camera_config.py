"""Vantech IP Camera (Ethernet LAN RTSP) & fallback camera settings shared by startup and reconnect."""
import os
import sys
from typing import Optional
import cv2

DEFAULT_RTSP_TEMPLATE = (
    "rtsp://192.168.1.210:554/"
    "user=admin_password=${FD_CAMERA_PASSWORD}_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream"
)


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


def resolve_camera_target():
    uri = os.getenv("FD_CAMERA_URI", "").strip()
    if not uri:
        pwd = os.getenv("FD_CAMERA_PASSWORD", "").strip()
        if pwd:
            uri = DEFAULT_RTSP_TEMPLATE.replace("${FD_CAMERA_PASSWORD}", pwd)
    if not uri:
        uri = os.getenv("CAMERA_DEVICE", "").strip()
    if uri:
        return int(uri) if uri.isdigit() else uri
    return 0


def open_camera():
    target = resolve_camera_target()
    if isinstance(target, str) and target.lower().startswith("rtsp://"):
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|stimeout;3000000",
        )
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
    elif isinstance(target, str) and os.path.exists(target):
        cap = cv2.VideoCapture(target)
    else:
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        cap = cv2.VideoCapture(target, backend)
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
