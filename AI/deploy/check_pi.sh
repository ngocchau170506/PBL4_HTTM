#!/usr/bin/env bash
# ==============================================================================
# Script Kiểm Tra Toàn Diện Kết Nối Trực Tiếp Trên SSH Của Raspberry Pi 4
# Cách chạy trên SSH của Pi:
#     bash AI/deploy/check_pi.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

if [ -f "${SCRIPT_DIR}/pi.env" ]; then
    set -a
    source "${SCRIPT_DIR}/pi.env"
    set +a
fi

if [ -d "${REPO_ROOT}/venv" ]; then
    source "${REPO_ROOT}/venv/bin/activate"
elif [ -d "${REPO_ROOT}/.venv" ]; then
    source "${REPO_ROOT}/.venv/bin/activate"
elif [ -d "${HOME}/iot_cam_project/venv" ]; then
    source "${HOME}/iot_cam_project/venv/bin/activate"
fi

export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/AI:${REPO_ROOT}/src:${PYTHONPATH:-}"

python3 AI/scripts/check_pi_env.py
