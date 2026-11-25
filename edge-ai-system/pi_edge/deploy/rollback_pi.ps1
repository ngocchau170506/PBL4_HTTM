# One-command PI_AI release rollback on Raspberry Pi 4 (restores latest ~/backups/pi_ai_backup_*.tar.gz)
# Preserves live pi.env, violations SQLite spool, calibration, and IOTv5 Golden Baseline!
param(
    [string]$PI_USER = "raspberrypi",
    [string]$PI_IP = "192.168.1.9",
    [string]$PI_DEST = "~/pi_ai_edge",
    [string]$BackupFile = ""
)

$ErrorActionPreference = "Stop"
Write-Host "[PI_AI Rollback] Ket noi toi ${PI_USER}@${PI_IP}..." -ForegroundColor Yellow

$rbCmd = @"
set -euo pipefail
TARGET="${BackupFile}"
if [ -z "`$TARGET" ]; then
  TARGET=`$(ls -1t ~/backups/pi_ai_backup_*.tar.gz 2>/dev/null | head -n 1 || true)
fi
if [ -z "`$TARGET" ] || [ ! -f "`$TARGET" ]; then
  echo "[ERROR] Khong tim thay ban backup pi_ai_backup_*.tar.gz trong ~/backups/" >&2
  exit 1
fi
echo "Restoring snapshot: `$TARGET"
tar -xzf "`$TARGET" --exclude='pi_edge/deploy/pi.env' -C ${PI_DEST}
chmod 600 ${PI_DEST}/pi_edge/deploy/pi.env 2>/dev/null || true
sudo systemctl restart driver-monitor.service
sleep 2
sudo systemctl is-active --quiet driver-monitor.service && echo "PI_AI_ROLLBACK_OK: ACTIVE"
"@ -replace "`r`n", "`n"

ssh "${PI_USER}@${PI_IP}" $rbCmd
if ($LASTEXITCODE -ne 0) { throw "Rollback that bai (exit=$LASTEXITCODE)" }
