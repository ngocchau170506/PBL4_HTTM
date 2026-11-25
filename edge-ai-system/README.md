# `PI_AI` — Hệ Thống Giám Sát Tài Xế 2 Tầng AI (Pi 4 Edge Reflex ↔ ESP32 ↔ Server of AI)

Kho builder sạch tách biệt hoàn toàn khỏi kho gốc chỉ-đọc `e:\AI\IOTv5`.

## Cấu trúc 4 nhánh tách bạch

- **`contracts/`** — Hợp đồng quyết định bất biến (`PROJECT_CONTRACT.md`), schema JSON chung (`pi_server_schema.json`), bộ kiểm thử đối chiếu chéo (`test_cross_contract.py`) và **Fail-Closed Gate Runner** (`gate_runner.py`).
- **`server_ai/`** — **Server of AI** (FastAPI + SQLite WAL + SSE thread-safe + Tầng 2 `DeepVerifier` CNN bất đồng bộ + ghép clip MP4 + Dashboard offline-safe + RBAC `strict`).
- **`pi_edge/`** — **Raspberry Pi 4 Edge Safety Reflex** (YOLO `best.pt` + BlazeFace 1-pass + `SensorFusion` vùng giữ mù `<= 0.8s` + gửi chuỗi 10–20 ảnh JPEG burst thay cho nén MP4 + 1 SQLite spool có trần + Web siêu nhẹ theo `RUN_MODE=local|server` + RBAC `strict`).
- **`firmware/esp32_audio_gps/`** — **ESP32 #1 (`AUDIO_MQ135_NODE`, `115200`)** (Loa I2S MAX98357A `<5ms`, `VOL:10..100`, `MUTE` phần mềm, `MQ-135` GPIO34 warm-up 25s, tắt hẳn radio Wi-Fi/BT & GPS để triệt tiêu brownout).
- **`firmware/esp32_ble_wifi_hub/`** — **ESP32 #2 (`COMMS_BLE_GPS_HUB`, `115200`)** (BLE NUS kết nối ĐT tài xế, GPS UART2 GPIO16/17 @ 9600, On-Demand Wi-Fi Survival Bridge chỉ bật khi Pi ngưng `>= 3.5s`).

## Chạy nhanh

```powershell
# 1. Chạy toàn bộ chuỗi Gate kiểm định PC Pre-flight (Fail-Closed)
python contracts/gate_runner.py --stage pc-preflight --strict

# 2. Chạy Server of AI trên máy Server của nhóm (production với tokens.json hoặc lab 127.0.0.1)
$env:SERVER_ENV_PROFILE="lab"
python server_ai/run_server.py

# 3. Mở Cloudflare Tunnel HTTPS trên máy Server (Phương án B — không cài VPN lên Pi 4)
.\server_ai\run_tunnel.ps1
```
