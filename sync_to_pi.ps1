# =====================================================================
# Script don sach ban cu tren Pi va nap moi toan bo tu E:\AI\IOTv4
# =====================================================================

$PI_USER = "raspberrypi"
$PI_IP = "192.168.1.17"
$PI_DEST = "~/iot_cam_project"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   LAM SACH PI VA NAP MOI TOAN BO MA NGUON TU IOTv4" -ForegroundColor Cyan
Write-Host "   Dich den: ${PI_USER}@${PI_IP}:${PI_DEST}" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Kiem tra ket noi mang toi Pi
Write-Host "`n[1/4] Kiem tra ket noi toi Pi ($PI_IP)..." -ForegroundColor Yellow
$ping = Test-Connection -ComputerName $PI_IP -Count 1 -Quiet
if (-not $ping) {
    Write-Host "[LOI] Khong the ket noi toi Raspberry Pi tai dia chi $PI_IP!" -ForegroundColor Red
    Write-Host "Vui long kiem tra lai day mang hoac ket noi Wi-Fi." -ForegroundColor Red
    Exit 1
}
Write-Host "[OK] Da ket noi thong suot toi Raspberry Pi!" -ForegroundColor Green

# 2. Don sach cac thu muc backup cu va code cu tren Pi (GIU LAI venv)
Write-Host "`n[2/4] Xoa sach cac ban backup cu va code cu tren Pi (GIU LAI venv)..." -ForegroundColor Yellow
$cleanup_cmd = "sudo systemctl stop driver-monitor 2>/dev/null; " +
               "rm -rf ~/backup_* ~/iot_cam_project_backup_*; " +
               "cd ~/iot_cam_project && rm -rf AI src logs __pycache__ *.pyc"
ssh "${PI_USER}@${PI_IP}" $cleanup_cmd
Write-Host "[OK] Da don sach thu muc cu tren Pi thanh cong!" -ForegroundColor Green

# 3. Nap moi toan bo thu muc AI va src tu IOTv4 sang Pi
Write-Host "`n[3/4] Dang nap moi toan bo thu muc AI va src sang Pi..." -ForegroundColor Yellow
Write-Host "-> Copy toan bo thu muc AI (app, scripts, models, calibration)..." -ForegroundColor DarkGray
scp -r AI "${PI_USER}@${PI_IP}:${PI_DEST}/"

Write-Host "-> Copy toan bo thu muc src..." -ForegroundColor DarkGray
scp -r src "${PI_USER}@${PI_IP}:${PI_DEST}/"

Write-Host "-> Copy cac file cau hinh can thiet..." -ForegroundColor DarkGray
if (Test-Path "requirements-pi.txt") {
    scp requirements-pi.txt "${PI_USER}@${PI_IP}:${PI_DEST}/"
}
if (Test-Path "AI/requirements-pi.txt") {
    scp AI/requirements-pi.txt "${PI_USER}@${PI_IP}:${PI_DEST}/"
}

# 4. Kiem tra va phan quyen
Write-Host "`n[4/4] Hoan tat va phan quyen tren Pi..." -ForegroundColor Yellow
ssh "${PI_USER}@${PI_IP}" "chmod -R +x ~/iot_cam_project/AI/scripts/*.py 2>/dev/null || true"

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "   DA DON SACH VA NAP MOI 100% THANH CONG SANG PI!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Tren terminal Raspberry Pi, ban chi can chay lenh:" -ForegroundColor Cyan
Write-Host "   cd ~/iot_cam_project" -ForegroundColor White
Write-Host "   set -a; source AI/deploy/pi.env; set +a" -ForegroundColor White
Write-Host "   python -m AI.app.main" -ForegroundColor White
Write-Host "Hoac khoi dong service:" -ForegroundColor Cyan
Write-Host "   sudo systemctl restart driver-monitor" -ForegroundColor White
