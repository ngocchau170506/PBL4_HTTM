"""USB camera settings shared by startup and reconnect."""
import os
import sys
import cv2


def open_camera():
    device = os.getenv("CAMERA_DEVICE", "0")
    device = int(device) if device.isdigit() else device
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    cap = cv2.VideoCapture(device, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(os.getenv("CAMERA_WIDTH", "640")))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(os.getenv("CAMERA_HEIGHT", "480")))
    cap.set(cv2.CAP_PROP_FPS, float(os.getenv("CAMERA_FPS", "15")))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap
