#!/usr/bin/env bash
# ==============================================================================
# Setup script for Driver Monitoring System on Raspberry Pi 4 Model B (64-bit)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CURRENT_USER="${SUDO_USER:-$USER}"

echo "=== [1/6] Cap nhat he thong & cai dat thu vien phan cung ==="
sudo apt update
sudo apt install -y \
    python3-venv \
    python3-pip \
    v4l-utils \
    libgl1 \
    libglib2.0-0 \
    udev
# Goi raspi-utils tren he dieu hanh Bookworm (Debian 12) neu co
sudo apt install -y raspi-utils 2>/dev/null || true

echo "=== [2/6] Phan quyen truy cap Serial UART & Camera cho user '${CURRENT_USER}' ==="
sudo usermod -aG dialout,video,input "${CURRENT_USER}"

echo "=== [3/6] Khoi tao Virtualenv & Cai dat Python Dependencies ==="
cd "${REPO_ROOT}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r AI/requirements-pi.txt

echo "=== [4/6] Thiet lap file cau hinh moi truong pi.env ==="
if [ ! -f "${SCRIPT_DIR}/pi.env" ]; then
    cp "${SCRIPT_DIR}/pi.env.example" "${SCRIPT_DIR}/pi.env"
    echo "Da tao file ${SCRIPT_DIR}/pi.env tu template. Vui long kiem tra va dien mat khau camera."
fi

echo "=== [5/6] Cai dat & Kich hoat Systemd Service ==="
SERVICE_FILE="${SCRIPT_DIR}/driver-monitor.service"
TARGET_SERVICE="/etc/systemd/system/driver-monitor.service"

if [ -f "${SERVICE_FILE}" ]; then
    # Cap nhat user va working directory theo vi tri repo thuc te
    sudo sed -e "s|User=pi|User=${CURRENT_USER}|g" \
             -e "s|Group=pi|Group=${CURRENT_USER}|g" \
             -e "s|/home/pi/driver-monitor|${REPO_ROOT}|g" \
             "${SERVICE_FILE}" | sudo tee "${TARGET_SERVICE}" > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable driver-monitor.service
    echo "Da kich hoat service: driver-monitor.service"
fi

echo "=== [6/6] Kiem tra phan cung SoC & Thiet bi ngoai vi ==="
echo -n "Nhiet do SoC: "
if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp || true
    echo -n "Trang thai throttling: "
    vcgencmd get_throttled || true
else
    echo "vcgencmd khong kha dung tren moi truong hien tai."
fi

echo "=============================================================================="
echo "Hoan tat cai dat tren Raspberry Pi 4!"
echo "De khoi dong he thong thu cong:  systemctl start driver-monitor"
echo "De theo doi nhat ky thoi gian thuc: journalctl -u driver-monitor -f"
echo "=============================================================================="
