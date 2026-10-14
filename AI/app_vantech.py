import os
import sys
import time

# [THIẾT LẬP FILE CẤU HÌNH ĐỘC LẬP CHO CAMERA NGOẠI VANTECH]
os.environ["YOLO26_CONFIG_FILE"] = "config_vantech.json"

from flask import Flask, request, g
from flask_socketio import SocketIO

from core.http_reporter import http_reporter
from core.error_handlers import register_error_handlers
from core.routes import register_routes
from core.camera_service import start_camera_loop
from core.arduino_node import arduino_node
from core.state import system_config

app = Flask(__name__)
app.config["SECRET_KEY"] = "yolo26_vantech_fleet_secret_ai_iot"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

@app.before_request
def start_timer():
    g.start_time = time.time()

@app.after_request
def log_http_quietly(response):
    duration_ms = (time.time() - getattr(g, "start_time", time.time())) * 1000
    http_reporter.log_request(
        method=request.method,
        path=request.path,
        ip=request.remote_addr or "127.0.0.1",
        status_code=response.status_code,
        duration_ms=duration_ms
    )
    return response

register_error_handlers(app)
register_routes(app, socketio)

# =========================================================================
# [BỘ XỬ LÝ THOÁT SẠCH SẼ KHI BẤM CTRL+C: THOÁT NGAY LẬP TỨC 100%]
# =========================================================================
import signal

def cleanup_and_exit(signum=None, frame=None):
    print("\n[YOLO26 Shutdown] Dang dung Camera Vantech, giai phong phan cung va thoat...")
    try:
        from core.camera_service import get_active_detector
        det = get_active_detector()
        if det:
            det.release()
    except Exception:
        pass
    try:
        arduino_node.is_running = False
        if arduino_node.ser and arduino_node.ser.is_open:
            arduino_node.ser.close()
    except Exception:
        pass
    print("[YOLO26 Shutdown] DA THOAT HOAN TOAN HE THONG. TAM BIET!\n")
    os._exit(0)

signal.signal(signal.SIGINT, cleanup_and_exit)
signal.signal(signal.SIGTERM, cleanup_and_exit)

if __name__ == "__main__":
    # 1. Bật luồng Camera Vantech IP 1080p với độ trễ siêu thấp <= 0.15s
    start_camera_loop(socketio)
    
    # 2. Bật tiến trình kết nối phần cứng Arduino/ESP32 thật (MQ-135)
    arduino_node.set_socketio(socketio)
    arduino_node.start()

    print("\n" + "="*75)
    print(">> [YOLO26 - VANTECH IP] HỆ THỐNG GIÁM SÁT AN TOÀN TÀI XẾ (CAM NGOẠI 1080P) <<")
    print("="*75)
    print(">> Cấu hình chuyên biệt Camera Ngoại Vantech:")
    print("   [+] Nguồn Video:         Camera IP Vantech RTSP (1080p Full HD)")
    print("   [+] Động cơ AI:          best_unified.pt (Gánh trọn Mắt, Miệng, Điện thoại, Tư thế)")
    print("   [+] Giới hạn độ trễ:     ÉP ĐỘ TRỄ <= 0.15 GIÂY (150ms siêu tốc)")
    print("   [+] Góc lắp đặt:         Tự động LẬT 180° chuẩn xác cho camera treo trần")
    print("   [+] Chống rớt mạng:      Auto-Reconnect Watchdog tự hồi phục sau 1s")
    print("   [+] Tác hợp ESP32:       Cảm biến MQ-135 thật, còi hú PAM8403, cờ SLEEP/ALERT")
    print("   [+] Thoát hệ thống:      Bấm phím Ctrl+C để thoát ngay lập tức 100%")
    print("-"*75)
    print(">> Web Dashboard Trực Tuyến:   http://127.0.0.1:5000")
    print(">> Bảng Lịch Sử Vi Phạm:       http://127.0.0.1:5000/history")
    print(">> API Báo Cáo Tổng Hợp:       http://127.0.0.1:5000/api/report/summary")
    print("="*75 + "\n")

    try:
        socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
    except (KeyboardInterrupt, SystemExit):
        cleanup_and_exit()
