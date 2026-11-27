# Script Demo nhanh cho giảng viên xem luong ket noi IOT Gateway -> Backend -> Frontend
# Cach chay: .\demo_send_to_frontend.ps1

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "   KỊCH BẢN DEMO KẾT NỐI IOT GATEWAY -> BACKEND -> FRONTEND" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan

$gatewayUrl = "http://127.0.0.1:5000"
$backendUrl = "http://127.0.0.1:3000"
$deviceToken = "de37103895a154174ee1b4425ab85a24710a02f770791961"

function Send-TelemetryNormal {
    Write-Host "`n>>> [KỊCH BẢN 1] Gửi Telemetry GPS bình thường (Xe 43C-123.45)" -ForegroundColor Yellow
    $headers = @{
        "Authorization" = "Bearer $deviceToken"
    }
    $bodyJson = @{
        vehicle_id = "XE-01"
        driver_name = "Le Minh Tuan"
        gps = @{
            lat = 16.0544
            lon = 108.2022
            speed_kmh = 50.0
            is_valid = $true
        }
        mq135 = @{
            co2_ppm = 450.0
        }
        pi = @{
            cpu_temp_c = 52.0
        }
    } | ConvertTo-Json -Depth 5

    try {
        $res = Invoke-RestMethod -Uri "$gatewayUrl/api/v1/telemetry" -Method Post -Headers $headers -Body ([System.Text.Encoding]::UTF8.GetBytes($bodyJson)) -ContentType "application/json; charset=utf-8"
        Write-Host "   [IoT Gateway :5000]: Tiếp nhận thành công và đẩy sang Backend :3000!" -ForegroundColor Green
        Write-Host "   -> Mời thầy nhìn trên Frontend: Tọa độ xe nhảy số, Marker màu XANH AN TOÀN." -ForegroundColor Green
    } catch {
        Write-Host "   Lỗi: $_" -ForegroundColor Red
    }
}

function Send-TelemetryDangerCO2 {
    Write-Host "`n>>> [KỊCH BẢN 2] Cảnh báo khí CO2 khoang lái cao (> 1200 ppm)" -ForegroundColor Yellow
    $headers = @{
        "Authorization" = "Bearer $deviceToken"
    }
    $bodyJson = @{
        vehicle_id = "XE-01"
        driver_name = "Le Minh Tuan"
        gps = @{
            lat = 16.0680
            lon = 108.2150
            speed_kmh = 60.0
            is_valid = $true
        }
        mq135 = @{
            co2_ppm = 1380.0
        }
        pi = @{
            cpu_temp_c = 58.0
        }
    } | ConvertTo-Json -Depth 5

    try {
        $res = Invoke-RestMethod -Uri "$gatewayUrl/api/v1/telemetry" -Method Post -Headers $headers -Body ([System.Text.Encoding]::UTF8.GetBytes($bodyJson)) -ContentType "application/json; charset=utf-8"
        Write-Host "   [IoT Gateway :5000]: Đã chuyển tiếp cảnh báo CO2 sang Backend!" -ForegroundColor Green
        Write-Host "   -> Mời thầy nhìn trên Frontend: Marker trên bản đồ đổi sang màu ĐỎ CẢNH BÁO!" -ForegroundColor Green
    } catch {
        Write-Host "   Lỗi: $_" -ForegroundColor Red
    }
}

function Send-AiViolationMicrosleep {
    Write-Host "`n>>> [KỊCH BẢN 3] Phát hiện AI Vi phạm ngủ gật (Microsleep)" -ForegroundColor Yellow
    $eventId = "DEMO_BURST_" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $body = @{
        trip_id = 2
        ma_loai = "MICROSLEEP"
        ai_confidence_score = 0.96
        mo_ta = "AI YOLO phát hiện mắt tài xế nhắm liên tục 2.4s khi xe đang chạy 50 km/h."
        client_event_id = $eventId
        video_url = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
    } | ConvertTo-Json -Depth 5

    try {
        $res = Invoke-RestMethod -Uri "$backendUrl/api/v1/edge/violations" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
        Write-Host "   [Backend :3000]: Đã xác nhận vi phạm và trừ 10 điểm uy tín tài xế Lê Minh Tuấn!" -ForegroundColor Green
        Write-Host "   -> Mời thầy nhìn trên Frontend:" -ForegroundColor Green
        Write-Host "      1. Chuông Socket Alert màu ĐỎ rực kêu ở góc màn hình!" -ForegroundColor Green
        Write-Host "      2. Tab Nhật ký vi phạm xuất hiện lỗi kèm Video clip 10s bằng chứng!" -ForegroundColor Green
    } catch {
        Write-Host "   Lỗi: $_" -ForegroundColor Red
    }
}

Write-Host "Chạy kịch bản Demo hoàn chỉnh trong 6 giây..." -ForegroundColor Cyan
Send-TelemetryNormal
Start-Sleep -Seconds 3
Send-TelemetryDangerCO2
Start-Sleep -Seconds 3
Send-AiViolationMicrosleep

Write-Host "`n=====================================================================" -ForegroundColor Cyan
Write-Host "   DEMO THÀNH CÔNG TỐT ĐẸP!" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
