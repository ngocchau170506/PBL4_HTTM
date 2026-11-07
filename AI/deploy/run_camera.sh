#!/usr/bin/env bash
# ==============================================================================
# Khởi Động Đầy Đủ: Camera Ngoại (RTSP LAN / USB) + AI YOLOv8 + ESP32 + Web
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

# Nạp cấu hình pi.env
if [ -f "${SCRIPT_DIR}/pi.env" ]; then
    set -a
    source "${SCRIPT_DIR}/pi.env"
    set +a
fi

# Kích hoạt venv (nếu lỡ xóa thì tự động tạo lại)
if [ -d "${REPO_ROOT}/venv" ]; then
    source "${REPO_ROOT}/venv/bin/activate"
elif [ -d "${REPO_ROOT}/.venv" ]; then
    source "${REPO_ROOT}/.venv/bin/activate"
elif [ -d "${HOME}/iot_cam_project/venv" ]; then
    source "${HOME}/iot_cam_project/venv/bin/activate"
else
    echo "  📦 Đang tự động tạo lại môi trường ảo (venv)..."
    python3 -m venv "${REPO_ROOT}/venv"
    source "${REPO_ROOT}/venv/bin/activate"
    pip install --upgrade pip
    pip install numpy pyserial psutil requests opencv-python ultralytics
    pip install mediapipe 2>/dev/null || true
fi

# BẬT CAMERA THẬT & BẬT AI (TẮT CHẾ ĐỘ GPS_ONLY)
export GPS_ONLY=0
export DISABLE_CAMERA=0
export DISABLE_AI=0
export HEADLESS=1
export WEB_PORT="${WEB_PORT:-8080}"
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/AI:${REPO_ROOT}/src:${PYTHONPATH:-}"

for iface in eth0 end0; do
    if ip link show "$iface" >/dev/null 2>&1; then
        sudo ip link set "$iface" up 2>/dev/null || true
        sleep 0.5
        CARRIER=$(cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo "0")
        if [ "$CARRIER" = "1" ]; then
            if ! ip route show 192.168.1.210/32 2>/dev/null | grep -q "dev $iface"; then
                sudo nmcli dev set "$iface" managed no 2>/dev/null || true
                sudo ip link set "$iface" up 2>/dev/null || true
                sudo ip addr replace 192.168.1.209/32 dev "$iface" 2>/dev/null || true
                sudo ip route replace 192.168.1.210/32 dev "$iface" src 192.168.1.209 2>/dev/null || true
            fi
        else
            sudo ip route del 192.168.1.210/32 dev "$iface" 2>/dev/null || true
        fi
    fi
done

echo "=============================================================================="
echo "  🚀 KHỞI ĐỘNG HỆ THỐNG GIÁM SÁT TÀI XẾ TOÀN DIỆN (CAMERA NGOẠI + AI)"
echo "=============================================================================="
echo "  📷 NGUỒN CAMERA : ${FD_CAMERA_URI:-RTSP Vantech /dev/video*}"
echo "  🧠 MÔ HÌNH AI   : YOLOv8 + MediaPipe FaceLandmarker"
echo "  🔌 ESP32 UART   : ${ESP_PORT:-/dev/ttyUSB0} @ ${ESP_BAUDRATE:-921600} baud"
echo "  🌐 WEB DASHBOARD: http://$(hostname -I | awk '{print $1}'):${WEB_PORT}"
echo "=============================================================================="

python3 -m AI.app.main
