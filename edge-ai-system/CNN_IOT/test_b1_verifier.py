"""Self-contained Unit & Adversarial Fault Test Suite for CNN_IOT (Option B1 Verifier).

Run from repo root or inside CNN_IOT:
    python CNN_IOT/test_b1_verifier.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from CNN_IOT.tier2_b1_verifier import PretrainedServerVerifier


def _write_jpg(path: Path, luma: int = 120) -> Path:
    img = np.full((120, 160, 3), luma, dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


class TestB1Verifier(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _make_frames(self, n: int = 6, luma: int = 120):
        return [_write_jpg(self.root / f"f{i:02d}.jpg", luma=luma) for i in range(n)]

    def test_01_drowsy_confirmed_and_false_alarm_rejected_by_ear(self):
        frames = self._make_frames(6, 120)
        # Real drowsy burst: EAR=0.14 on all frames -> CONFIRMED
        v_ok = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.14, "mar": 0.15, "yaw_ratio": 0.05},
            yolo_detector=lambda _im: [],
        )
        res_ok = v_ok.verify_burst(frames, {"alert_type": "DROWSY", "duration_seconds": 2.2})
        self.assertEqual(res_ok.status, "SERVER_CNN_CONFIRMED")
        self.assertIn("EAR_mean=", res_ok.detail)

        # False alarm: driver eyes wide open (EAR=0.31) -> REJECTED
        v_fp = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.31, "mar": 0.15, "yaw_ratio": 0.04},
            yolo_detector=lambda _im: [],
        )
        res_fp = v_fp.verify_burst(frames, {"alert_type": "DROWSY", "duration_seconds": 2.2})
        self.assertEqual(res_fp.status, "SERVER_CNN_REJECTED")

    def test_02_yawning_and_distracted_geometry_verdicts(self):
        frames = self._make_frames(5, 130)
        v_yawn = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.28, "mar": 0.72, "yaw_ratio": 0.06},
            yolo_detector=lambda _im: [],
        )
        self.assertEqual(
            v_yawn.verify_burst(frames, {"alert_type": "YAWNING", "duration_seconds": 2.0}).status,
            "SERVER_CNN_CONFIRMED",
        )
        v_dist = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.28, "mar": 0.15, "yaw_ratio": 0.44},
            yolo_detector=lambda _im: [],
        )
        self.assertEqual(
            v_dist.verify_burst(frames, {"alert_type": "DISTRACTED", "duration_seconds": 2.2}).status,
            "SERVER_CNN_CONFIRMED",
        )

    def test_03_phone_and_smoking_yolo_hd_confirm_and_reject(self):
        frames = self._make_frames(4, 110)
        v_phone = PretrainedServerVerifier(
            face_analyzer=lambda _im: None,
            yolo_detector=lambda _im: [("cell phone", 0.78)],
        )
        r_ok = v_phone.verify_burst(frames, {"alert_type": "PHONE", "duration_seconds": 1.5})
        self.assertEqual(r_ok.status, "SERVER_CNN_CONFIRMED")
        self.assertIn("YoloHD PHONE", r_ok.detail)

        v_none = PretrainedServerVerifier(
            face_analyzer=lambda _im: None,
            yolo_detector=lambda _im: [("cup", 0.80)],
        )
        r_fp = v_none.verify_burst(frames, {"alert_type": "PHONE", "duration_seconds": 1.5})
        self.assertEqual(r_fp.status, "SERVER_CNN_REJECTED")

    def test_04_adversarial_black_corrupt_transient_and_non_penalty(self):
        # Black frames -> REJECTED
        black = self._make_frames(4, 0)
        v = PretrainedServerVerifier(face_analyzer=lambda _im: {"ear": 0.12, "mar": 0.1, "yaw_ratio": 0.0}, yolo_detector=lambda _im: [])
        self.assertEqual(v.verify_burst(black, {"alert_type": "DROWSY", "duration_seconds": 2.0}).status, "SERVER_CNN_REJECTED")

        # Corrupt JPEG bytes -> REJECTED
        bad = self.root / "corrupt.jpg"
        bad.write_bytes(b"NOT_A_VALID_JPEG_" * 20)
        self.assertEqual(v.verify_burst([bad], {"alert_type": "DROWSY", "duration_seconds": 2.0}).status, "SERVER_CNN_REJECTED")

        # Transient blink < 0.35s -> REJECTED
        good = self._make_frames(4, 120)
        self.assertEqual(v.verify_burst(good, {"alert_type": "DROWSY", "duration_seconds": 0.20}).status, "SERVER_CNN_REJECTED")

        # Non-penalty advisory events (AUTO_CO2_CRIT, SOS) -> CONFIRMED without failing on 0 frames
        for adv in ("AUTO_CO2_CRIT", "AUTO_CO2_WARN", "SOS", "ESP_BROWNOUT"):
            r = v.verify_burst([], {"alert_type": adv, "duration_seconds": 0.0})
            self.assertEqual(r.status, "SERVER_CNN_CONFIRMED")
            self.assertIn("non-penalty", r.detail.lower())

    def test_05_concurrent_worker_thread_safety(self):
        frames = self._make_frames(4, 125)
        v = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.15, "mar": 0.2, "yaw_ratio": 0.05},
            yolo_detector=lambda _im: [("phone", 0.85)],
        )
        errors = []
        def _worker():
            try:
                for _ in range(5):
                    r1 = v.verify_burst(frames, {"alert_type": "DROWSY", "duration_seconds": 2.1})
                    r2 = v.verify_burst(frames, {"alert_type": "PHONE", "duration_seconds": 1.6})
                    assert r1.status == "SERVER_CNN_CONFIRMED" and r2.status == "SERVER_CNN_CONFIRMED"
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])

    def test_06_pitch_distraction_and_uniform_burst_sampling(self):
        # Looking down (pitch_ratio >= 0.32, low yaw) must confirm DISTRACTED (fixes F-018)
        frames = self._make_frames(24, 125)
        v_pitch = PretrainedServerVerifier(
            face_analyzer=lambda _im: {"ear": 0.30, "mar": 0.15, "yaw_ratio": 0.05, "pitch_ratio": 0.40},
            yolo_detector=lambda _im: [],
        )
        r = v_pitch.verify_burst(frames, {"alert_type": "DISTRACTED", "duration_seconds": 2.1})
        self.assertEqual(r.status, "SERVER_CNN_CONFIRMED")
        imgs, valid_c, non_b = v_pitch._load_valid_frames(frames)
        self.assertEqual(len(imgs), 16)
        self.assertEqual(valid_c, 16)
        self.assertEqual(non_b, 16)


if __name__ == "__main__":
    unittest.main()
