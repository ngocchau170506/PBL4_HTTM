# PI_AI Method B — Cloudflare Tunnel Launcher for Remote Server PC
# Runs on the group member's Server PC so Raspberry Pi 4 (4G/Hotspot) and
# Fleet Manager browsers reach server_ai over HTTPS without opening router ports
# or installing any VPN software on Raspberry Pi 4.
#
# Usage:
#   Quick Tunnel (free, no domain needed; URL changes each restart):
#     .\server_ai\run_tunnel.ps1
#
#   Named Tunnel (fixed HTTPS domain, e.g. https://pi-ai.yourdomain.com):
#     $env:CLOUDFLARE_TUNNEL_TOKEN = "<TOKEN>"; .\server_ai\run_tunnel.ps1
#     (or pass -TunnelToken "<TOKEN>" — env var preferred so token stays out of process history)
param(
    [string]$LocalUrl = "http://127.0.0.1:5000",
    [string]$TunnelToken = ""
)

$ErrorActionPreference = "Stop"

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    Write-Host "[ERROR] Chua cai cloudflared tren may Server." -ForegroundColor Red
    Write-Host "Cai nhanh bang winget:  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
    Write-Host "Hoac tai tai:           https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" -ForegroundColor Yellow
    exit 1
}

$EffectiveToken = if ($TunnelToken) { $TunnelToken } elseif ($env:CLOUDFLARE_TUNNEL_TOKEN) { $env:CLOUDFLARE_TUNNEL_TOKEN } else { "" }

if ($EffectiveToken) {
    Write-Host "[PI_AI Tunnel] Dang khoi dong Named Cloudflare Tunnel (URL co dinh)..." -ForegroundColor Green
    $env:TUNNEL_TOKEN = $EffectiveToken
    & cloudflared tunnel --no-autoupdate run
} else {
    Write-Host "[PI_AI Tunnel] Dang khoi dong Quick Cloudflare Tunnel (CHI DUNG CHO LAB/TEST) -> $LocalUrl" -ForegroundColor Cyan
    Write-Host "[PI_AI Tunnel] Luu y: Quick Tunnel doi URL moi lan khoi dong lai! Dung Named Tunnel cho xe chay that." -ForegroundColor Yellow
    & cloudflared tunnel --no-autoupdate --url $LocalUrl
}
