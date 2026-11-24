#!/usr/bin/env bash
# Manual launcher for PI_AI Edge (100% READ-ONLY on network: ZERO nmcli, ZERO ip addr/route edits!)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet driver-monitor.service; then
    echo "[PI_AI] driver-monitor.service dang chay nen! Dung 'sudo systemctl stop driver-monitor.service' truoc khi chay tay."
    exit 1
fi

if [ ! -f "${SCRIPT_DIR}/pi.env" ]; then
    echo "[PI_AI] Thieu ${SCRIPT_DIR}/pi.env. Copy tu pi.env.example va dien token RBAC."
    exit 1
fi

set -a
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/pi.env"
set +a

# shellcheck disable=SC1091
source "${REPO_ROOT}/venv/bin/activate"
export PYTHONPATH="${REPO_ROOT}"

echo "[PI_AI] Read-only eth0 route check (netplan-eth0 preserved):"
ip route show 192.168.1.210/32 2>/dev/null || true

exec python3 -m pi_edge.app.main "$@"
