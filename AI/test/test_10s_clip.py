import os
import sys
import time
import json
from pathlib import Path
import numpy as np
import cv2

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from AI.scripts.event_recorder import EventRecorder

def run_test():
    print("=================================================================")
    print("  KIỂM THỬ ĐỘC LẬP BỘ GHI VIDEO VI PHẠM 10S VÀ CUỐN CHIẾU ĐOẠN MỚI")
    print("=================================================================")
    test_storage_dir = PROJECT_ROOT / "AI" / "data" / "test_data"
    test_storage_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = test_storage_dir.parent / "test_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    recorder = EventRecorder(
        storage_dir=str(test_storage_dir),
        buffer_seconds=5.0,
        sample_fps=10.0,
        pre_event_seconds=3.0,
        post_detection_seconds=5.0,
        max_clip_seconds=10.0,
    )
    recorder.storage.evidence_dir = evidence_dir

    base_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    base_ns = time.monotonic_ns()

    print("-> Đang giả lập luồng camera 30 FPS trong 16 giây...")
    # 480 frames = 16 seconds @ 30 FPS
    for i in range(480):
        elapsed = i / 30.0
        rel_t = elapsed - 3.0
        sim_ns = base_ns + int(elapsed * 1e9)
        frame = base_frame.copy()
        cv2.putText(frame, f"TEST t={rel_t:+.2f}s #{i+1}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        if 0.0 <= rel_t < 0.04:
            recorder.start_event("DROWSY", event_start_timestamp_ns=sim_ns)

        recorder.append_frame(
            frame,
            capture_timestamp_ns=sim_ns,
            face_valid=True,
            metadata={"ear": 0.14 if 0.0 <= rel_t < 9.0 else 0.29, "rel_t": round(rel_t, 2)},
            frame_id=i + 1,
        )

        if 0.0 <= rel_t < 2.0:
            recorder.update_event(
                "DROWSY",
                timestamp_ns=sim_ns,
                local_status="VERIFYING",
                confirmed=False,
                metadata={"frame_id": i + 1, "phase": "0s_to_2s"},
                currently_active=True,
            )
        elif 2.0 <= rel_t < 9.0:
            recorder.update_event(
                "DROWSY",
                timestamp_ns=sim_ns,
                local_status="CONFIRMED",
                confirmed=True,
                metadata={"frame_id": i + 1, "phase": "CONFIRMED_2s_TO_10s_ROLLOVER"},
                confirmed_at_ns=sim_ns,
                currently_active=True,
            )
        elif 9.0 <= rel_t < 9.05:
            recorder.end_detection("DROWSY", timestamp_ns=sim_ns)

    time.sleep(2.0)
    snap = recorder.get_status_snapshot(base_ns + int(16.0 * 1e9))
    print(f"[OK] Completed segments: {snap.get('completed_segments')}")
    print(f"[OK] Rollover segments: {snap.get('rollover_segments')}")
    print(f"[OK] Total videos saved: {snap.get('total_videos_saved_sd')}")
    
    # Check created videos
    videos = list(evidence_dir.glob("*.mp4")) + list(test_storage_dir.rglob("*.mp4"))
    print(f"[OK] Số file video .mp4 tạo ra thực tế: {len(videos)}")
    for v in videos:
        print(f"     - {v.name} ({v.stat().st_size} bytes)")

    recorder.close()
    
    # Clean up test dir
    import shutil
    shutil.rmtree(test_storage_dir, ignore_errors=True)
    shutil.rmtree(evidence_dir, ignore_errors=True)
    
    if len(videos) >= 2:
        print("\n=================================================================")
        print("  KIỂM THỬ THÀNH CÔNG: TỰ ĐỘNG LƯU ĐOẠN 10S VÀ CUỐN CHIẾU SEGMENT 2!")
        print("=================================================================")
        return True
    else:
        print("[LỖI] Số lượng video tạo ra không đủ 2 đoạn cuốn chiếu")
        return False

if __name__ == '__main__':
    ok = run_test()
    if not ok:
        sys.exit(1)
