#!/usr/bin/env bash
# ==============================================================================
# Script Chạy Kiểm Thử Tọa Độ XYZ & Web Dashboard Trên Raspberry Pi
# Cho phép mở trình duyệt từ PC để theo dõi trực quan thời gian thực.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

# Nạp file môi trường pi.env nếu có
if [ -f "${SCRIPT_DIR}/pi.env" ]; then
    set -a
    source "${SCRIPT_DIR}/pi.env"
    set +a
fi

# Kích hoạt virtualenv nếu tồn tại (.venv hoặc venv), nếu lỡ xóa thì tự động tạo lại!
if [ -d "${REPO_ROOT}/venv" ]; then
    source "${REPO_ROOT}/venv/bin/activate"
elif [ -d "${REPO_ROOT}/.venv" ]; then
    source "${REPO_ROOT}/.venv/bin/activate"
elif [ -d "${HOME}/iot_cam_project/venv" ]; then
    source "${HOME}/iot_cam_project/venv/bin/activate"
else
    echo "=============================================================================="
    echo "  📦 PHÁT HIỆN CHƯA CÓ MÔI TRƯỜNG ẢO (venv) -> ĐANG TỰ ĐỘNG KHÔI PHỤC..."
    echo "=============================================================================="
    python3 -m venv "${REPO_ROOT}/venv"
    source "${REPO_ROOT}/venv/bin/activate"
    pip install --upgrade pip
    pip install numpy pyserial psutil requests opencv-python ultralytics
    pip install mediapipe 2>/dev/null || echo "  [GHI CHÚ] Bỏ qua mediapipe trên Python 3.13 (Hệ thống dùng YOLOv8)."
fi

# Tự động lấy IP của Pi trong mạng LAN
PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
[ -z "$PI_IP" ] && PI_IP="127.0.0.1"

PORT="${WEB_PORT:-8080}"
SERVER="${REMOTE_SERVER_URL:-http://192.168.1.6:5000}"

echo "=============================================================================="
echo "  🚀 AI DRIVER MONITOR - KIỂM THỬ TRỰC TIẾP TRÊN RASPBERRY PI"
echo "=============================================================================="
echo "  📍 IP CỦA PI TRONG MẠNG LAN : ${PI_IP}"
echo "  🌐 ĐƯỜNG DẪN WEB XEM TRÊN PC: http://${PI_IP}:${PORT}"
echo "  🖥️  SERVER BẠN TÔI (MÁY TÍNH): ${SERVER}"
echo "  🧭 ĐỊNH VỊ 3D TỌA ĐỘ XYZ     : Server-Assisted & Hardware GPS (Đà Nẵng)"
echo "=============================================================================="
echo "  👉 Hãy mở trình duyệt máy tính của bạn và truy cập link trên!"
echo "  Nhấn Ctrl+C để dừng."
echo "=============================================================================="

export GPS_ONLY=0
export DISABLE_CAMERA=0
export DISABLE_AI=0
export HEADLESS=1
export SKIP_CALIBRATION=1
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/AI:${REPO_ROOT}/src:${PYTHONPATH:-}"

# Tự động dò tìm IP mới của Máy Chủ PC (E:\AI\Server) nếu bạn đổi sang mạng Wi-Fi / VLAN khác:
DETECTED_SRV=$(python3 -c "from AI.scripts.cloud_sync import auto_discover_pc_server; print(auto_discover_pc_server('${SERVER}'))" 2>/dev/null || echo "${SERVER}")
if [ -n "${DETECTED_SRV}" ]; then
    export REMOTE_SERVER_URL="${DETECTED_SRV}"
    SERVER="${DETECTED_SRV}"
fi

# Tự động định tuyến Camera IP Vantech (192.168.1.210) qua cổng dây mạng RJ45 (eth0/end0)
# nếu phát hiện có dây cáp LAN cắm vào cổng RJ45 của Raspberry Pi:
for iface in eth0 end0; do
    if ip link show "$iface" >/dev/null 2>&1; then
        sudo ip link set "$iface" up 2>/dev/null || true
        sleep 0.5
        CARRIER=$(cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo "0")
        if [ "$CARRIER" = "1" ]; then
            if ! ip route show 192.168.1.210/32 2>/dev/null | grep -q "dev $iface"; then
                echo "  🔌 Phát hiện cáp LAN trên $iface -> Định tuyến Camera Vantech (192.168.1.210) qua $iface..."
                sudo nmcli dev set "$iface" managed no 2>/dev/null || true
                sudo ip link set "$iface" up 2>/dev/null || true
                sudo ip addr replace 192.168.1.209/32 dev "$iface" 2>/dev/null || true
                sudo ip route replace 192.168.1.210/32 dev "$iface" src 192.168.1.209 2>/dev/null || true
            fi
        else
            # Nếu cổng RJ45 trên Pi không cắm dây, xóa route tĩnh (nếu có) để tìm qua Wi-Fi wlan0
            sudo ip route del 192.168.1.210/32 dev "$iface" 2>/dev/null || true
        fi
    fi
done

# Tự động kiểm tra môi trường & kết nối phần cứng trên Pi trước khi mở Web Server
python3 AI/scripts/check_pi_env.py
echo ""

python3 -m AI.app.main
