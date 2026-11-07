#!/usr/bin/env bash
# ==============================================================================
# Script dọn dẹp hệ thống & giải phóng bộ nhớ tự động cho Raspberry Pi 4
# Sử dụng: ./clean_pi.sh (hoặc sudo ./clean_pi.sh)
# An toàn tuyệt đối: Giữ nguyên môi trường venv, model AI và cấu hình .env
# ==============================================================================

set -uo pipefail

echo "=================================================================="
echo "    BẮT ĐẦU DỌN DẸP HỆ THỐNG & GIẢI PHÓNG BỘ NHỚ TRÊN PI"
echo "=================================================================="

# 1. Hiển thị thông số trước khi dọn
echo ">>> [1/5] Dung lượng trước khi dọn:"
df -h / | awk 'NR==1 || NR==2'
free -h | awk 'NR==1 || NR==2'

# 2. Dọn rác trong mã nguồn dự án (Code, cache Python, log cũ)
echo ""
echo ">>> [2/5] Đang dọn file tạm dự án (__pycache__, logs, evidence)..."
PROJ_DIR="${HOME}/iot_cam_project"
if [ -d "${PROJ_DIR}" ]; then
    find "${PROJ_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "${PROJ_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true
    rm -rf "${PROJ_DIR}/logs"/* "${PROJ_DIR}/AI/evidence"/* 2>/dev/null || true
    rm -f "${PROJ_DIR}/AI/data/events.jsonl" 2>/dev/null || true
    rm -rf /tmp/matplotlib 2>/dev/null || true
fi

# 3. Dọn rác IDE & VS Code Server (Cached VSIX, servers cũ, cache Playwright)
echo ">>> [3/5] Đang dọn rác VS Code Remote & Browser caches..."
rm -rf "${HOME}/.vscode-server/data/CachedExtensionVSIXs"/* 2>/dev/null || true
rm -rf "${HOME}/.cache/ms-playwright" 2>/dev/null || true
rm -rf "${HOME}/.cache/pip" 2>/dev/null || true

# 4. Dọn log hệ điều hành Linux & Cache cài đặt APT
echo ">>> [4/5] Đang dọn log hệ điều hành & APT cache..."
sudo journalctl --vacuum-size=30M >/dev/null 2>&1 || true
sudo apt-get clean >/dev/null 2>&1 || true
sudo rm -rf /var/log/*.gz /var/log/*.[0-9] 2>/dev/null || true

# 5. Xả sạch bộ nhớ đệm RAM (RAM Cache drop)
echo ">>> [5/5] Đang giải phóng RAM cache..."
sync
echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || true

echo ""
echo "=================================================================="
echo "    HOÀN TẤT DỌN DẸP! KẾT QUẢ SAU KHI DỌN:"
echo "=================================================================="
df -h / | awk 'NR==1 || NR==2'
free -h | awk 'NR==1 || NR==2'
echo "=================================================================="
