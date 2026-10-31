# Raspberry Pi 4 Model B — Triển Khai Thực Chiến Trên Xe (G7)

Sử dụng hệ điều hành **Raspberry Pi OS 64-bit** (Debian Bookworm/Bullseye). Thực thi các lệnh từ thư mục gốc của repository.

---

## 1. Cài Đặt Tự Động (Khuyến nghị)

Chạy script cấu hình tự động để cài đặt gói hệ thống, thiết lập quyền truy cập phần cứng và kích hoạt Systemd Service:

```bash
chmod +x AI/deploy/setup_pi.sh
./AI/deploy/setup_pi.sh
```

---

## 2. Cài Đặt Thủ Công

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 udev
sudo usermod -aG dialout,video,input "$USER"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r AI/requirements-pi.txt
cp AI/deploy/pi.env.example AI/deploy/pi.env
```

---

## 3. Cấu Hình Dịch Vụ Tự Khởi Động (Systemd Service)

Hệ thống được đóng gói dịch vụ `driver-monitor.service` tự động kích hoạt khi xe bật khóa điện:

```bash
# Sao chép và đăng ký dịch vụ
sudo cp AI/deploy/driver-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable driver-monitor.service

# Các lệnh điều khiển vận hành
sudo systemctl start driver-monitor     # Khởi động dịch vụ
sudo systemctl status driver-monitor    # Kiểm tra trạng thái
sudo systemctl stop driver-monitor      # Tắt dịch vụ
sudo systemctl restart driver-monitor   # Khởi động lại dịch vụ

# Theo dõi nhật ký thời gian thực
journalctl -u driver-monitor -f
```

---

## 4. Giám Sát Phần Cứng & Phòng Ngừa Sự Cố SoC

Hệ thống tích hợp module `HardwareMonitor` (trong `src/fd_diagnose/hardware_monitor.py`) tự động phát hiện và cảnh báo:
- **Quá nhiệt CPU (> 80°C)**: Phát `ErrorEvent` để cảnh báo hạ nhịp lấy mẫu hoặc kiểm tra quạt tản nhiệt.
- **Sụt áp nguồn (Under-voltage)**: Kiểm tra cờ bit 0/16 từ `vcgencmd get_throttled` (thường do dây nguồn tẩu sạc xe hơi không cấp đủ 5V/3A).
- **Bóp xung (Throttling)**: Cảnh báo khi CPU ARM bị giảm xung nhịp xuống dưới 600MHz.

Kiểm tra nhanh qua Terminal:
```bash
vcgencmd measure_temp
vcgencmd get_throttled
```


Edit pi.env. Prefer a persistent /dev/v4l/by-id/... camera path. Requested
640x480 / 15 FPS may be negotiated differently by the camera. Check with v4l2-ctl.
HEADLESS=1 avoids window creation. SKIP_CALIBRATION=1 uses existing baseline or
default thresholds; otherwise alignment has a timeout and calibration is automatic.

Python/ARM wheel availability must be checked on the actual Pi. If the import
check fails, resolve the reported dependency/version before deployment. These
constraints are not a Pi-tested lockfile. After successful hardware tests:

```bash
python -m pip freeze > AI/requirements-pi.lock.txt
set -a
source AI/deploy/pi.env
set +a
python -m AI.app.main
```

For serial: add your login to dialout (`sudo usermod -aG dialout "$USER"`),
log out/in, select the actual ESP_PORT under /dev/serial/by-id, set ESP_MODE=SERIAL.
Default protocol remains `ALERT,code,status,timestamp,duration_ms\n`.
ESP_REQUIRE_ACK=1 appends `,sequence` before newline; firmware must reply
`ACK,sequence\n` and deduplicate repeated sequences. Firmware is not in this repo.
Without ACK mode, sent means serial write succeeded, not confirmed hardware action.
The worker retries with 1/2/4/8/10-second backoff and refreshes the current state
every 5 seconds. It sends the newest state, not a backlog of obsolete alerts.
NORMAL clears an active alarm on camera loss/conflict; tracking loss is shown in
software and does not have a dedicated ESP event code in the existing protocol.

## Decision rules

Each eye/mouth observation is true (abnormal), false (normal), or unknown.
Both available and agreeing: use both. One unavailable: use the other, reporting
SUSPECTED for a single-source alert. Both available and disagreeing: CONFLICT.
Eye closure requires 2 seconds, yawning 1 second by default. No observations
break the timer; 1.5 seconds without face evidence becomes TRACKING_LOST.
MediaPipe runs on every processed frame so it can detect events independently
of YOLO. This costs CPU compared with event-triggered verification; measure on Pi.
Only recognized eye/mouth labels count as YOLO observations. If your trained
model uses other labels, update the label mapping in drowsiness_pipeline.py.

## Verification and soak test

```bash
python -m unittest AI.test.test_runtime_reliability AI.test.test_cnn_event_recorder -v
watch -n 2 'vcgencmd measure_temp; vcgencmd get_throttled; free -m'
```

CSV is written every 5 seconds to PERF_CSV. It includes inference FPS/latencies,
frame age, alert detection latency (including decision threshold), process CPU/RAM,
temperature, throttle flags, decision source and ESP connection/ACK snapshot.
Alert latency is to the software decision, NOT to physical buzzer activation or
ACK. Measure physical activation independently; ACK snapshot is not an ACK latency.
Start with 10 minutes, then 1 hour and 4 hours. Test each sensor alone, conflict,
face occlusion, camera unplug/replug and ESP unplug/replug. No Pi speed or thermal
result is claimed by desktop tests. PerformanceMonitor CPU may exceed 100% on
multiple cores. CSV/log files need rotation for unattended long-term operation.

For a timed real-camera benchmark (using the existing baseline/default thresholds):
`python -m AI.scripts.benchmark_comparison --seconds 600`.
It runs the full inference/event-recording pipeline headlessly, always using MOCK
ESP, and prints measured throughput, mean/p95 pipeline latency and frame age.
The former estimated architecture-comparison numbers have been removed.

Storage keeps about 512 MiB of event data, removing uploaded events first then
the oldest pending events with a console message. One in-flight write may
temporarily exceed the limit. The RAM save queue is capped at 8 events; full queue
or disk failure drops an event with a log rather than growing RAM indefinitely.
Upload remains MOCK: a real server endpoint/API contract is required to implement
transport. Existing standalone evidence JPG/JSONL files are separate from this quota.

Optional NCNN: export best.pt using Ultralytics on a supported environment, then
set YOLO_MODEL_PATH to the exported model directory. Compare accuracy on the same
videos before adopting it; current code accepts both model files and directories.
