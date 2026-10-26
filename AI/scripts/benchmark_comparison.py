"""Measure actual USB-camera throughput without estimated comparison numbers."""
import argparse
import os
import statistics
import time
import mediapipe as mp
from AI.scripts.calibration import MODEL_PATH
from AI.scripts.camera_config import open_camera
from AI.scripts.camera_frame_source import CameraFrameSource
from AI.scripts.drowsiness_pipeline import DrowsinessPipeline, YOLO_MODEL_PATH
from AI.scripts.esp_communication import EspCommunicator


def run_benchmark(seconds=600):
    os.environ["HEADLESS"] = "1"
    capture = open_camera()
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("Could not open USB camera")
    landmarker = pipeline = source = None
    latencies, frame_ages = [], []
    gaps = 0
    try:
        try:
            landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(
                mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
                    running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=1))
        except Exception as exc:
            print(f"[BENCHMARK] MediaPipe unavailable: {exc}")
        pipeline = DrowsinessPipeline(
            model_path=os.getenv("YOLO_MODEL_PATH", str(YOLO_MODEL_PATH)),
            face_landmarker=landmarker, esp_communicator=EspCommunicator("MOCK"))
        landmarker = None
        source = CameraFrameSource(capture, on_frame=pipeline.event_recorder.append_frame,
                                   capture_factory=open_camera)
        frame_id = 0
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            ok, frame, frame_id, timestamp_ns, utc = source.read_latest(frame_id)
            if not ok:
                gaps += 1
                pipeline.reset_tracking()
                continue
            _, state = pipeline.process(frame, frame_id, timestamp_ns, utc)
            latencies.append(state["pipeline_latency_ms"])
            frame_ages.append(state["frame_age_ms"])
        elapsed = time.monotonic() - start
        print(f"Measured frames={len(latencies)}, elapsed={elapsed:.1f}s, camera_gaps={gaps}")
        if latencies:
            ordered = sorted(latencies)
            p95 = ordered[min(len(ordered)-1, int(len(ordered)*0.95))]
            print(f"Throughput={len(latencies)/elapsed:.2f} FPS; pipeline mean={statistics.mean(latencies):.1f}ms; "
                  f"p95={p95:.1f}ms; mean frame age={statistics.mean(frame_ages):.1f}ms")
        print("ESP is MOCK. CSV contains temperature/RAM; hardware alert latency is not measured.")
    finally:
        if source is not None:
            source.close()
        else:
            capture.release()
        if pipeline is not None:
            pipeline.close()
        if landmarker is not None:
            landmarker.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=600)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    run_benchmark(args.seconds)
