#!/usr/bin/env bash
# Safe systemd cutover & 1-command rollback between legacy IOTv5 and PI_AI (LCK-01).
# NEVER touches ~/iot_cam_project, netplan-eth0, wlan0, or nmcli!
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CURRENT_USER="${SUDO_USER:-${USER:-raspberrypi}}"
SERVICE_DST="/etc/systemd/system/driver-monitor.service"
BACKUP_DST="/etc/systemd/system/driver-monitor.service.iotv5.bak"

ACTION="${1:---cutover}"

if [[ "${ACTION}" == "--rollback" ]]; then
    if [[ ! -f "${BACKUP_DST}" ]]; then
        echo "[ERROR] Khong tim thay ban backup ${BACKUP_DST}. Huy rollback." >&2
        exit 1
    fi
    echo "=== [ROLLBACK] Khoi phuc driver-monitor.service ve Golden Baseline IOTv5 ==="
    sudo cp "${BACKUP_DST}" "${SERVICE_DST}"
    sudo systemctl daemon-reload
    sudo systemctl restart driver-monitor.service
    sudo systemctl status driver-monitor.service --no-pager -l | head -n 18
    exit 0
fi

if [[ "${ACTION}" != "--cutover" ]]; then
    echo "Usage: $0 [--cutover | --rollback]" >&2
    exit 2
fi

if [[ ! -f "${SCRIPT_DIR}/pi.env" ]]; then
    echo "[ERROR] Thieu ${SCRIPT_DIR}/pi.env. Hay cau hinh token truoc khi cutover!" >&2
    exit 1
fi

echo "=== [1/4] Bao ve Golden Baseline IOTv5 (backup service cu) ==="
if [[ -f "${SERVICE_DST}" && ! -f "${BACKUP_DST}" ]]; then
    sudo cp "${SERVICE_DST}" "${BACKUP_DST}"
    echo "Da sao luu: ${BACKUP_DST}"
else
    echo "Ban sao luu da ton tai hoac chua co service cu: ${BACKUP_DST}"
fi

echo "=== [2/4] Siet quyen thu muc & pi.env (chmod 600) ==="
mkdir -p "${REPO_ROOT}/pi_edge/logs/audit" "${REPO_ROOT}/pi_edge/violations" "${REPO_ROOT}/pi_edge/calibration"
chmod 600 "${SCRIPT_DIR}/pi.env"
chmod 750 "${REPO_ROOT}/pi_edge/violations" "${REPO_ROOT}/pi_edge/logs/audit"

echo "=== [3/4] Cai dat udev Dual-ESP32 & driver-monitor.service tro toi ${REPO_ROOT} ==="
if [[ -f "${SCRIPT_DIR}/99-pi-ai-esp32.rules" ]]; then
    tr -d '\r' < "${SCRIPT_DIR}/99-pi-ai-esp32.rules" | sudo tee /etc/udev/rules.d/99-pi-ai-esp32.rules >/dev/null
    sudo udevadm control --reload-rules && sudo udevadm trigger || true
fi
sed -e "s|User=raspberrypi|User=${CURRENT_USER}|g" \
    -e "s|Group=raspberrypi|Group=${CURRENT_USER}|g" \
    -e "s|/home/raspberrypi/pi_ai_edge|${REPO_ROOT}|g" \
    "${SCRIPT_DIR}/driver-monitor.service" | tr -d '\r' | sudo tee "${SERVICE_DST}" >/dev/null

echo "=== [4/4] Kich hoat & kiem tra trang thai systemd ==="
sudo systemctl daemon-reload
sudo systemctl enable driver-monitor.service
sudo systemctl restart driver-monitor.service
sleep 2
sudo systemctl is-active --quiet driver-monitor.service && echo "SYSTEMD_CUTOVER_OK: driver-monitor.service ACTIVE"
sudo systemctl status driver-monitor.service --no-pager -l | head -n 18
