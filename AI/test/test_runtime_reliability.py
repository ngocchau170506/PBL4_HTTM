import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from AI.scripts.sensor_fusion import SensorFusion
from AI.scripts.esp_communication import EspCommunicator
from AI.scripts.telemetry import Telemetry


class FusionTests(unittest.TestCase):
    def sample(self, fusion, now, a, b, face=True):
        return fusion.update(now, {"eye": a, "mouth": a},
                             {"eye": b, "mouth": b}, face)[0]

    def test_yolo_only_alerts(self):
        f = SensorFusion()
        self.sample(f, 0, True, None)
        result = self.sample(f, 2, True, None)
        self.assertEqual(result["eye"].state, "SUSPECTED")
        self.assertEqual(result["eye"].source, "YOLO")

    def test_mediapipe_only_alerts(self):
        f = SensorFusion()
        self.sample(f, 0, None, True)
        result = self.sample(f, 2, None, True)
        self.assertEqual(result["eye"].state, "SUSPECTED")
        self.assertEqual(result["eye"].source, "MEDIAPIPE")

    def test_both_agree_confirmed(self):
        f = SensorFusion()
        self.sample(f, 0, True, True)
        self.assertEqual(self.sample(f, 2, True, True)["eye"].state, "CONFIRMED")

    def test_disagreement_is_conflict(self):
        self.assertEqual(self.sample(SensorFusion(), 0, True, False)["eye"].state, "CONFLICT")

    def test_both_unknown_becomes_tracking_lost(self):
        f = SensorFusion()
        self.sample(f, 0, None, None, False)
        self.assertEqual(self.sample(f, 2, None, None, False)["eye"].state, "TRACKING_LOST")

    def test_unknown_breaks_old_timer(self):
        f = SensorFusion()
        self.sample(f, 0, True, None)
        self.sample(f, 1, None, None, False)
        self.assertEqual(self.sample(f, 2, True, None)["eye"].duration, 0)

    def test_normal_recovery_clears_alarm(self):
        f = SensorFusion()
        self.sample(f, 0, True, True)
        self.sample(f, 3, True, True)
        self.assertEqual(self.sample(f, 4, False, None)["eye"].state, "IDLE")

    def test_eye_and_mouth_are_independent(self):
        f = SensorFusion()
        for now in (0, 1):
            result, _ = f.update(now, {"eye": None, "mouth": True},
                                 {"eye": False, "mouth": None}, True)
        self.assertEqual(result["eye"].state, "IDLE")
        self.assertEqual(result["mouth"].state, "SUSPECTED")


class SerialTests(unittest.TestCase):
    class FakeSerial:
        def __init__(self, fail=False, ack=True):
            self.is_open = True
            self.fail = fail
            self.ack = ack
            self.packets = []

        def write(self, packet):
            if self.fail:
                raise OSError("unplugged")
            self.packets.append(packet)

        def flush(self):
            pass

        def readline(self):
            if self.ack and self.packets:
                seq = self.packets[-1].decode().strip().split(",")[-1]
                return f"ACK,{seq}\n".encode()
            time.sleep(0.005)
            return b""

        def close(self):
            self.is_open = False

    def wait_until(self, predicate, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Worker did not reach expected state")

    def test_write_failure_reconnects_and_delivers_newest_status(self):
        broken, working = self.FakeSerial(fail=True), self.FakeSerial()
        devices = iter((broken, working))
        def open_fake(esp):
            esp._serial = next(devices, working)
        with patch.object(EspCommunicator, "_open_serial", open_fake):
            esp = EspCommunicator("SERIAL", "fake")
            try:
                esp.send_status("DROWSY")
                self.wait_until(lambda: esp.last_result is not None and esp.last_result.error is not None)
                esp.send_status("NORMAL")
                self.wait_until(lambda: len(working.packets) > 0)
                self.assertIn(b",NORMAL,", working.packets[0])
                self.assertFalse(broken.is_open)
            finally:
                esp.close()

    def test_ack_mode_matches_sequence(self):
        device = self.FakeSerial()
        with patch.object(EspCommunicator, "_open_serial", lambda esp: setattr(esp, "_serial", device)):
            esp = EspCommunicator("SERIAL", "fake", require_ack=True)
            try:
                esp.send_status("DROWSY")
                self.wait_until(lambda: esp.last_result is not None and esp.last_result.acknowledged)
            finally:
                esp.close()

    def test_missing_ack_retries_same_sequence(self):
        device = self.FakeSerial(ack=False)
        with patch.object(EspCommunicator, "_open_serial", lambda esp: setattr(esp, "_serial", device)):
            esp = EspCommunicator("SERIAL", "fake", require_ack=True, timeout=0.02)
            try:
                esp.send_status("YAWNING")
                self.wait_until(lambda: len(device.packets) >= 2)
                self.assertEqual(device.packets[0].split(b",")[-1], device.packets[1].split(b",")[-1])
                self.assertFalse(esp.last_result.acknowledged)
            finally:
                esp.close()


class TelemetryTests(unittest.TestCase):
    def test_csv_contains_metrics_and_flushes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "perf.csv"
            telemetry = Telemetry(path)
            try:
                telemetry.record({"timestamp": "now", "ai_fps": 5, "eye_source": "YOLO"})
                self.assertIn("YOLO", path.read_text())
                self.assertIn("frame_age_ms", path.read_text())
            finally:
                telemetry.close()


class CameraRecoveryTests(unittest.TestCase):
    def test_unplugged_capture_reopens_and_delivers_new_frame(self):
        import numpy as np
        from AI.scripts.camera_frame_source import CameraFrameSource
        class Capture:
            def __init__(self, working):
                self.working = working
                self.released = False
            def read(self):
                if self.working:
                    time.sleep(0.01)
                    return True, np.zeros((4, 4, 3), dtype=np.uint8)
                return False, None
            def release(self):
                self.released = True
        broken, working = Capture(False), Capture(True)
        source = CameraFrameSource(broken, capture_factory=lambda: working)
        try:
            ok, *_ = source.read_latest(timeout=2.5)
            self.assertTrue(ok)
            self.assertTrue(broken.released)
        finally:
            source.close()
        self.assertTrue(working.released)


class StorageQuotaTests(unittest.TestCase):
    def test_offline_pending_events_are_bounded(self):
        from AI.scripts.event_storage import EventStorage
        with tempfile.TemporaryDirectory() as directory:
            storage = EventStorage(directory)
            try:
                base = storage._disk_usage()
                storage.max_storage_bytes = base + 3000
                for index in range(10):
                    storage.save_event({"event_id": str(index), "recording_end_time": str(index),
                                        "payload": "x" * 1000}, [])
                self.assertLessEqual(storage._disk_usage(), storage.max_storage_bytes)
                self.assertLess(len(storage.pending()), 10)
            finally:
                storage.close()


class PipelineTests(unittest.TestCase):
    def run_scenario(self, yolo_name, mp_valid, ear=0.1, fail_yolo=False):
        import numpy as np
        from AI.scripts.drowsiness_pipeline import DrowsinessPipeline
        boxes = [] if yolo_name is None else [SimpleNamespace(cls=[np.array(0)], conf=[np.array(0.9)])]
        model = SimpleNamespace(names={0: yolo_name})
        def predict(**kwargs):
            if fail_yolo:
                raise RuntimeError("simulated inference failure")
            return [SimpleNamespace(boxes=boxes, plot=lambda: kwargs["source"].copy())]
        model.predict = predict
        measured = {"valid": mp_valid, "ear": ear if mp_valid else None,
                    "mar": 0.1 if mp_valid else None, "reason": "OK" if mp_valid else "NO_FACE"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("AI.scripts.drowsiness_pipeline.YOLO", return_value=model), \
                 patch.object(DrowsinessPipeline, "_run_mediapipe", return_value=measured), \
                 patch.dict("os.environ", {"PERF_CSV": "", "HEADLESS": "1"}):
                pipeline = DrowsinessPipeline(model_path=__file__, enable_event_recorder=False,
                                              save_evidence=False, evidence_dir=directory)
                try:
                    now = time.monotonic_ns()
                    for offset in (0, 1, 2.1):
                        _, state = pipeline.process(np.zeros((8, 8, 3), dtype=np.uint8),
                                                    capture_timestamp_ns=now + int(offset * 1e9))
                    return state, pipeline._active_esp_alert
                finally:
                    pipeline.close()

    def test_full_pipeline_yolo_only_sends_drowsy(self):
        state, desired = self.run_scenario("Eye_Closed", False)
        self.assertEqual(state["confirmed_alert"], "SUSPECTED")
        self.assertEqual(state["eye_source"], "YOLO")
        self.assertEqual(desired, "DROWSY")

    def test_full_pipeline_mediapipe_only_sends_drowsy(self):
        state, desired = self.run_scenario(None, True, fail_yolo=True)
        self.assertEqual(state["eye_source"], "MEDIAPIPE")
        self.assertEqual(desired, "DROWSY")
        self.assertIn("simulated", state["yolo_error"])

    def test_full_pipeline_conflict_does_not_send_drowsy(self):
        state, desired = self.run_scenario("Eye_Closed", True, ear=0.8)
        self.assertEqual(state["confirmed_alert"], "CONFLICT")
        self.assertIsNone(desired)

    def test_full_pipeline_no_observations_is_tracking_lost(self):
        state, desired = self.run_scenario(None, False)
        self.assertEqual(state["confirmed_alert"], "TRACKING_LOST")
        self.assertIsNone(desired)

    def test_head_detection_is_face_evidence_but_not_an_eye_vote(self):
        state, desired = self.run_scenario("Attentive Head", False)
        self.assertFalse(state["tracking_lost"])
        self.assertEqual(state["eye_source"], "NONE")
        self.assertEqual(state["confirmed_alert"], "UNKNOWN")
        self.assertIsNone(desired)


if __name__ == "__main__":
    unittest.main()
