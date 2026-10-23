import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from AI.scripts.cnn_frame_buffer import CnnFrameBuffer
from AI.scripts.camera_frame_source import CameraFrameSource
from AI.scripts.event_recorder import EventRecorder
from AI.scripts.event_storage import EventStorage


class CnnFrameBufferTests(unittest.TestCase):
    def test_samples_by_timestamp_and_purges_old_frames(self):
        buffer = CnnFrameBuffer(buffer_seconds=1.0, sample_fps=5.0, image_size=(8, 8))
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        for index in range(8):
            buffer.append(index, index * 200_000_000, image, "2026-01-01T00:00:00+00:00")
        frames = buffer.snapshot(1_400_000_000)
        self.assertEqual([frame.frame_id for frame in frames], [2, 3, 4, 5, 6, 7])
        self.assertEqual(frames[-1].image.shape[:2], (8, 8))

    def test_does_not_duplicate_frames_to_reach_target_rate(self):
        buffer = CnnFrameBuffer(buffer_seconds=5.0, sample_fps=5.0)
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        self.assertIsNotNone(buffer.append(1, 0, image, "now"))
        self.assertIsNone(buffer.append(2, 1, image, "now"))
        self.assertEqual(len(buffer), 1)


class CameraFrameSourceTests(unittest.TestCase):
    class _FakeCapture:
        def __init__(self):
            self.frames = [np.zeros((4, 4, 3), dtype=np.uint8) for _ in range(4)]
            self.released = False

        def read(self):
            if self.frames:
                return True, self.frames.pop(0)
            return False, None

        def release(self):
            self.released = True

    def test_capture_thread_delivers_latest_and_invokes_sampler(self):
        capture = self._FakeCapture()
        sampled_ids = []
        source = CameraFrameSource(
            capture,
            on_frame=lambda _frame, frame_id, **_metadata: sampled_ids.append(frame_id),
        )
        try:
            ok, frame, frame_id, timestamp_ns, capture_time = source.read_latest(timeout=1.0)
            self.assertTrue(ok)
            self.assertIsNotNone(frame)
            self.assertGreaterEqual(frame_id, 1)
            self.assertGreater(timestamp_ns, 0)
            self.assertTrue(capture_time.endswith("+00:00"))
            self.assertTrue(sampled_ids)
        finally:
            source.close()
        self.assertTrue(capture.released)


class EventRecorderTests(unittest.TestCase):
    def _append(self, recorder, timestamp_ns, frame_id=None):
        return recorder.append_frame(
            np.full((24, 24, 3), 127, dtype=np.uint8),
            timestamp_ns,
            "2026-01-01T00:00:00+00:00",
            frame_id=frame_id,
        )

    def _record_confirmed_event(self, fps, confirmed_ns=3_000_000_000, close_ns=None):
        directory = tempfile.TemporaryDirectory()
        recorder = EventRecorder(
            directory.name,
            sample_fps=5,
            pre_event_seconds=500_000_000 / 1e9,
            post_detection_seconds=500_000_000 / 1e9,
        )
        interval = int(1_000_000_000 / fps)
        timestamps = range(0, 4_001_000_000, interval)
        started = False
        confirmed_at = None
        for frame_id, timestamp_ns in enumerate(timestamps, start=1):
            if timestamp_ns >= 1_000_000_000 and not started:
                recorder.start_event(
                    "DROWSY",
                    1_000_000_000,
                    "2026-01-01T00:00:01+00:00",
                )
                started = True
            if timestamp_ns >= confirmed_ns and confirmed_at is None:
                confirmed_at = timestamp_ns
                recorder.update_event(
                    "DROWSY",
                    timestamp_ns,
                    "CONFIRMED",
                    True,
                    confirmed_at_ns=timestamp_ns,
                    confirmed_at_time="2026-01-01T00:00:03+00:00",
                )
            self._append(recorder, timestamp_ns, frame_id)
            if close_ns is not None and timestamp_ns >= close_ns:
                break
        recorder.close()
        event_dirs = list((Path(directory.name) / "pending_events").iterdir())
        self.assertEqual(len(event_dirs), 1)
        metadata = json.loads((event_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
        return directory, event_dirs[0], metadata

    def test_unconfirmed_event_is_not_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EventRecorder(directory, sample_fps=5)
            for timestamp in range(0, 1_000_000_001, 200_000_000):
                self._append(recorder, timestamp)
            recorder.start_event("DROWSY", 1_000_000_000)
            recorder.update_event("DROWSY", 1_000_000_000, "WATCHING", False)
            recorder.end_detection("DROWSY", 1_200_000_000)
            recorder.close()
            self.assertEqual(list((Path(directory) / "pending_events").iterdir()), [])

    def test_confirmed_event_has_pre_and_post_frames_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = EventRecorder(directory, sample_fps=5, pre_event_seconds=0.5, post_detection_seconds=0.5)
            for timestamp in range(0, 1_000_000_001, 200_000_000):
                self._append(recorder, timestamp)
            recorder.start_event("DROWSY", 1_000_000_000)
            recorder.update_event("DROWSY", 1_000_000_000, "CONFIRMED", True)
            self._append(recorder, 1_200_000_000)
            self._append(recorder, 1_400_000_000)
            self._append(recorder, 1_600_000_000)
            recorder.close()
            event_dirs = list((Path(directory) / "pending_events").iterdir())
            self.assertEqual(len(event_dirs), 1)
            metadata = json.loads((event_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["event_type"], "DROWSY")
            self.assertEqual(metadata["verification_status"], "PENDING")
            self.assertGreaterEqual(metadata["frame_count"], 3)
            self.assertTrue(all("frame_id" in frame for frame in metadata["frames"]))
            self.assertIn("event_start_ns", metadata)
            self.assertIn("actual_first_frame_ns", metadata)

    def test_ten_fps_event_has_pre_event_and_online_post_boundary(self):
        directory, event_dir, metadata = self._record_confirmed_event(10)
        try:
            timestamps = [frame["capture_timestamp_ns"] for frame in metadata["frames"]]
            frame_ids = [frame["frame_id"] for frame in metadata["frames"]]
            self.assertLessEqual(timestamps[0], 1_000_000_000)
            self.assertGreaterEqual(timestamps[-1], 3_500_000_000)
            self.assertEqual(timestamps, sorted(timestamps))
            self.assertEqual(len(frame_ids), len(set(frame_ids)))
            self.assertFalse(metadata["sequence_complete"])
            self.assertAlmostEqual(metadata["pre_event_coverage_seconds"], 0.4, delta=0.01)
            self.assertEqual(metadata["confirmed_at_ns"], 3_000_000_000)
            self.assertEqual(metadata["target_end_ns"], 3_500_000_000)
            self.assertAlmostEqual(metadata["sample_fps_actual"], 5.0, delta=0.6)
        finally:
            directory.cleanup()

    def test_nine_fps_event_does_not_invent_frames(self):
        directory, event_dir, metadata = self._record_confirmed_event(9)
        try:
            timestamps = [frame["capture_timestamp_ns"] for frame in metadata["frames"]]
            self.assertEqual(len(timestamps), len(set(timestamps)))
            self.assertTrue(all(b > a for a, b in zip(timestamps, timestamps[1:])))
            self.assertGreaterEqual(metadata["actual_last_frame_ns"], metadata["target_end_ns"])
            self.assertLess(metadata["sample_fps_actual"], 5.1)
            self.assertGreaterEqual(metadata["duration_actual_seconds"], 3.0)
        finally:
            directory.cleanup()

    def test_delayed_confirmation_uses_confirmation_time_for_post_window(self):
        directory, event_dir, metadata = self._record_confirmed_event(
            10, confirmed_ns=3_400_000_000
        )
        try:
            self.assertEqual(metadata["confirmed_at_ns"], 3_400_000_000)
            self.assertEqual(metadata["target_end_ns"], 3_900_000_000)
            self.assertGreaterEqual(metadata["actual_last_frame_ns"], 3_900_000_000)
        finally:
            directory.cleanup()

    def test_shutdown_during_post_window_marks_sequence_incomplete(self):
        directory, event_dir, metadata = self._record_confirmed_event(
            10, close_ns=3_300_000_000
        )
        try:
            self.assertFalse(metadata["sequence_complete"])
            self.assertLess(metadata["actual_last_frame_ns"], metadata["target_end_ns"])
        finally:
            directory.cleanup()

    def test_storage_has_readable_jpegs_and_sqlite_queue(self):
        directory, event_dir, metadata = self._record_confirmed_event(10)
        try:
            image_paths = [event_dir / frame["jpeg_path"] for frame in metadata["frames"]]
            self.assertEqual(len(image_paths), metadata["frame_count"])
            self.assertTrue(all(path.is_file() for path in image_paths))
            self.assertTrue(all(cv2.imread(str(path)) is not None for path in image_paths))
            storage = EventStorage(directory.name)
            self.assertEqual(storage.pending()[0][0], metadata["event_id"])
            storage.close()
        finally:
            directory.cleanup()

    def test_mock_upload_keeps_pending_event_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = EventStorage(directory)
            storage.save_event(
                {
                    "event_id": "restart-test",
                    "event_type": "YAWNING",
                    "recording_end_time": "2026-01-01T00:00:00+00:00",
                },
                [],
            )
            storage.close()
            restarted = EventStorage(directory)
            self.assertEqual([row[0] for row in restarted.pending()], ["restart-test"])
            restarted.close()


if __name__ == "__main__":
    unittest.main()
