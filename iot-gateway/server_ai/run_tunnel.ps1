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
#     .\server_ai\run_tunnel.ps1 -TunnelToken "<CLOUDFLARE_TUNNEL_TOKEN>"
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

if ($TunnelToken) {
    Write-Host "[PI_AI Tunnel] Dang khoi dong Named Cloudflare Tunnel (URL co dinh)..." -ForegroundColor Green
    & cloudflared tunnel --no-autoupdate run --token $TunnelToken
} else {
    Write-Host "[PI_AI Tunnel] Dang khoi dong Quick Cloudflare Tunnel -> $LocalUrl" -ForegroundColor Cyan
    Write-Host "[PI_AI Tunnel] Copy URL https://*.trycloudflare.com in ra ben duoi vao REMOTE_SERVER_URL cua Pi 4!" -ForegroundColor Yellow
    & cloudflared tunnel --no-autoupdate --url $LocalUrl
}
