#!/usr/bin/env bash
# ==============================================================================
# Script don dep & giai phong dung luong toi da tren Raspberry Pi 4 (ARM64)
# ==============================================================================

set -euo pipefail

echo "=================================================================="
echo " KIEM TRA DUNG LUONG THE NHO TRUOC KHI DON DEP:"
echo "=================================================================="
df -h /
echo ""

# 1. Don dep APT package cache & goi phan mem khong con su dung
echo "[1/6] Don sach APT cache & cac goi thu vien rac..."
sudo apt update -qq || true
sudo apt autoremove --purge -y
sudo apt clean
sudo apt autoclean

# 2. Xoa cache banh xe cua PIP (thuong chiem tu 1GB - 3GB)
echo "[2/6] Xoa bo nho dem banh xe (PIP wheel cache)..."
if command -v pip3 >/dev/null 2>&1; then
    pip3 cache purge 2>/dev/null || true
fi
rm -rf ~/.cache/pip

# 3. Thu gon Systemd Journal & Nhat ky he thong
echo "[3/6] Thu gon nhat ky Systemd Journal va Logs he thong ve toi da 50MB..."
sudo journalctl --vacuum-size=50M 2>/dev/null || true
sudo journalctl --vacuum-time=1d 2>/dev/null || true
sudo find /var/log -type f -name "*.gz" -delete 2>/dev/null || true
sudo find /var/log -type f -name "*.[0-9]" -delete 2>/dev/null || true

# 4. Don dep cache giao dien, trinh duyet va thumbnails
echo "[4/6] Don dep thu muc cache nguoi dung (~/.cache)..."
rm -rf ~/.cache/thumbnails
rm -rf ~/.cache/chromium 2>/dev/null || true

# 5. Xoa toan bo Python __pycache__ va file .pyc rac
echo "[5/6] Quet va xoa toan bo __pycache__ va file tam .pyc..."
find ~/ -maxdepth 4 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find ~/ -maxdepth 4 -type f -name "*.pyc" -delete 2>/dev/null || true

# 6. Goi y don sach thu muc du an cu hoac venv cu
echo "[6/6] Kiem tra cac thu muc du an va moi truong ao cu..."
echo "  Neu ban muon xoa ban IOT cu de giai phong them 2GB - 4GB:"
echo "  - Xoa venv cu:  rm -rf ~/driver-monitor/.venv ~/.venv"
echo "  - Xoa folder cu: rm -rf ~/IOT ~/IOI_v1 ~/IOTv2"

echo ""
echo "=================================================================="
echo " HOAN TAT! DUNG LUONG THE NHO SAU KHI DON DEP:"
echo "=================================================================="
df -h /
echo "=================================================================="
