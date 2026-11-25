#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"

echo "=== [1/4] Cai dat goi he thong (khong dung mang/nmcli) ==="
sudo apt update
sudo apt install -y python3-venv python3-pip v4l-utils libgl1 libglib2.0-0 udev
sudo apt install -y raspi-utils 2>/dev/null || true
sudo usermod -aG dialout,video,input "${CURRENT_USER}"

echo "=== [2/4] Khoi tao venv & cai requirements-pi.txt (headless only, khong cai mediapipe SIGILL) ==="
cd "${REPO_ROOT}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r "${REPO_ROOT}/pi_edge/requirements-pi.txt"

echo "=== [3/4] Chuan hoa thu muc & quyen bao mat (chmod 600 pi.env) ==="
if [ ! -f "${SCRIPT_DIR}/pi.env" ]; then
    cp "${SCRIPT_DIR}/pi.env.example" "${SCRIPT_DIR}/pi.env"
    echo "[CHU Y] Hay dien token RBAC + FD_CAMERA_PASSWORD + REMOTE_SERVER_URL vao ${SCRIPT_DIR}/pi.env truoc khi khoi dong!"
fi
mkdir -p "${REPO_ROOT}/pi_edge/logs/audit" "${REPO_ROOT}/pi_edge/violations" "${REPO_ROOT}/pi_edge/calibration"
chown -R "${CURRENT_USER}:${CURRENT_USER}" "${SCRIPT_DIR}/pi.env" "${REPO_ROOT}/pi_edge/logs" "${REPO_ROOT}/pi_edge/violations" "${REPO_ROOT}/pi_edge/calibration" "${REPO_ROOT}/venv" 2>/dev/null || true
chmod 600 "${SCRIPT_DIR}/pi.env"
chmod 750 "${REPO_ROOT}/pi_edge/violations" "${REPO_ROOT}/pi_edge/logs/audit"
chmod +x "${SCRIPT_DIR}"/*.sh

if [ -f "${SCRIPT_DIR}/99-pi-ai-esp32.rules" ]; then
    tr -d '\r' < "${SCRIPT_DIR}/99-pi-ai-esp32.rules" | sudo tee /etc/udev/rules.d/99-pi-ai-esp32.rules >/dev/null
    sudo udevadm control --reload-rules && sudo udevadm trigger || true
fi

echo "=== [4/4] Cai dat systemd service (bao toan ban backup IOTv5 & giu nguyen pi.env RUN_MODE) ==="
if [ -f /etc/systemd/system/driver-monitor.service ] && [ ! -f /etc/systemd/system/driver-monitor.service.iotv5.bak ]; then
    sudo cp /etc/systemd/system/driver-monitor.service /etc/systemd/system/driver-monitor.service.iotv5.bak
fi
tr -d '\r' < "${SCRIPT_DIR}/driver-monitor.service" | sed -e "s|User=raspberrypi|User=${CURRENT_USER}|g" \
    -e "s|Group=raspberrypi|Group=${CURRENT_USER}|g" \
    -e "s|/home/raspberrypi/pi_ai_edge|${REPO_ROOT}|g" | sudo tee /etc/systemd/system/driver-monitor.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable driver-monitor.service
echo "Hoan tat! Kiem tra: sudo systemctl start driver-monitor.service"
