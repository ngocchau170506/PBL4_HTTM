# Atomic staged deploy from e:\AI\PI_AI -> Raspberry Pi 4 (NEVER deletes old code before upload succeeds!)
param(
    [string]$PI_USER = "raspberrypi",
    [string]$PI_IP = "192.168.1.9",
    [string]$PI_DEST = "~/pi_ai_edge",
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $RepoRoot
try {
    Write-Host "[1/5] Kiem tra ket noi toi Pi ($PI_IP)..." -ForegroundColor Yellow
    if (-not (Test-Connection -ComputerName $PI_IP -Count 1 -Quiet)) {
        throw "Khong ping duoc Pi tai $PI_IP"
    }

    Write-Host "[2/5] Tao staging tam (umask 077) & backup phien ban PI_AI hien tai tren Pi..." -ForegroundColor Yellow
    $prepCmd = @"
umask 077
rm -rf /tmp/pi_ai_stage && mkdir -p /tmp/pi_ai_stage/pi_edge /tmp/pi_ai_stage/contracts ~/backups
if [ -d ${PI_DEST}/pi_edge ]; then
  TS=`$(date -u +%Y%m%d_%H%M%S)
  tar -czf ~/backups/pi_ai_backup_`${TS}.tar.gz --exclude='pi_edge/violations' --exclude='pi_edge/logs' --exclude='pi_edge/models' -C ${PI_DEST} contracts pi_edge 2>/dev/null || true
  ls -1t ~/backups/pi_ai_backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
fi
"@ -replace "`r`n", "`n"
    ssh "${PI_USER}@${PI_IP}" $prepCmd
    if ($LASTEXITCODE -ne 0) { throw "SSH staging/backup that bai (exit=$LASTEXITCODE)" }

    Write-Host "[3/5] Tai code sach sang staging (khong cham pi.env/violations/IOTv5)..." -ForegroundColor Yellow
    scp -r contracts "${PI_USER}@${PI_IP}:/tmp/pi_ai_stage/"
    if ($LASTEXITCODE -ne 0) { throw "SCP contracts that bai (exit=$LASTEXITCODE)" }
    scp -r pi_edge/app pi_edge/core pi_edge/camera pi_edge/ai pi_edge/evidence pi_edge/comms pi_edge/web pi_edge/deploy pi_edge/requirements-pi.txt pi_edge/__init__.py "${PI_USER}@${PI_IP}:/tmp/pi_ai_stage/pi_edge/"
    if ($LASTEXITCODE -ne 0) { throw "SCP pi_edge that bai (exit=$LASTEXITCODE)" }
    if (Test-Path "APP_IOT") {
        scp -r APP_IOT "${PI_USER}@${PI_IP}:/tmp/pi_ai_stage/"
        if ($LASTEXITCODE -ne 0) { throw "SCP APP_IOT that bai (exit=$LASTEXITCODE)" }
    }

    if (-not $SkipModels -and (Test-Path "pi_edge/models")) {
        Write-Host "[4/5] Dong bo pi_edge/models (best.pt, face_landmarker.task)..." -ForegroundColor Yellow
        scp -r pi_edge/models "${PI_USER}@${PI_IP}:/tmp/pi_ai_stage/pi_edge/"
        if ($LASTEXITCODE -ne 0) { throw "SCP pi_edge/models that bai (exit=$LASTEXITCODE)" }
    } else {
        Write-Host "[4/5] Bo qua pi_edge/models (-SkipModels hoac thu muc chua co)." -ForegroundColor DarkYellow
    }

    Write-Host "[5/5] Chuyen doi & bao toan pi.env (khong copy pi.env ra /tmp!) / calibration / violations..." -ForegroundColor Yellow
    $swapCmd = @"
mkdir -p ${PI_DEST}/pi_edge/deploy ${PI_DEST}/pi_edge/violations ${PI_DEST}/pi_edge/calibration ${PI_DEST}/pi_edge/logs/audit ${PI_DEST}/pi_edge/models
# Never overwrite Pi's live pi.env with any accidental local PC pi.env!
rm -f /tmp/pi_ai_stage/pi_edge/deploy/pi.env
sed -i 's/\r$//' /tmp/pi_ai_stage/pi_edge/deploy/*.sh /tmp/pi_ai_stage/pi_edge/deploy/*.rules /tmp/pi_ai_stage/pi_edge/deploy/*.service /tmp/pi_ai_stage/pi_edge/deploy/pi.env.example 2>/dev/null || true
cp -r /tmp/pi_ai_stage/contracts ${PI_DEST}/
cp -r /tmp/pi_ai_stage/pi_edge/* ${PI_DEST}/pi_edge/
if [ -d /tmp/pi_ai_stage/APP_IOT ]; then
  cp -r /tmp/pi_ai_stage/APP_IOT ${PI_DEST}/
fi
rm -rf /tmp/pi_ai_stage
chmod 600 ${PI_DEST}/pi_edge/deploy/pi.env 2>/dev/null || true
chmod +x ${PI_DEST}/pi_edge/deploy/*.sh 2>/dev/null || true
echo "ATOMIC_SYNC_OK"
"@ -replace "`r`n", "`n"
    ssh "${PI_USER}@${PI_IP}" $swapCmd
    if ($LASTEXITCODE -ne 0) { throw "SSH swap that bai (exit=$LASTEXITCODE)" }
} finally {
    Pop-Location
}
