import sys
import time
import threading
from flask import Flask, request, g
from flask_socketio import SocketIO

# Import hệ thống quản lý log HTTP và Báo cáo độc lập
from core.http_reporter import http_reporter
from core.error_handlers import register_error_handlers
from core.routes import register_routes
from core.camera_service import start_camera_loop
from core.arduino_node import arduino_node
from core.state import system_config

app = Flask(__name__)
app.config["SECRET_KEY"] = "yolo26_fleet_secret_ai_iot"

# [THIẾT KẾ: GIAO TIẾP THỜI GIAN THỰC WEBSOCKET]
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# =========================================================================
# [BỘ LỌC HTTP CHUYÊN BIỆT: CHUYỂN TOÀN BỘ LOG REQUEST VÀO FILE NHẬT KÝ BÁO CÁO]
# Giữ cho Terminal console hoàn toàn sạch sẽ, không bị tràn dòng mỗi giây
# =========================================================================
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

# Nạp hệ thống bắt lỗi (Middleware)
register_error_handlers(app)

# Nạp các đường dẫn (Routing API, Web & Báo cáo)
register_routes(app, socketio)

# =========================================================================
# [BỘ XỬ LÝ THOÁT SẠCH SẼ KHI BẤM CTRL+C: THOÁT NGAY LẬP TỨC 100%]
# Giải phóng Camera, đóng cổng Serial và thoát triệt để, không bị treo ngầm
# =========================================================================
import os
import signal

def cleanup_and_exit(signum=None, frame=None):
    print("\n[YOLO26 Shutdown] Dang dung camera, giai phong phan cung va thoat ngay lap tuc...")
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
    # 1. Bật luồng Camera AI Siêu tốc (Zero-Lag Frame Grabber + Inference 320px)
    start_camera_loop(socketio)
    
    # 2. BẬT TIẾN TRÌNH KẾT NỐI PHẦN CỨNG ARDUINO THẬT (ĐỌC CẢM BIẾN MQ-135)
    arduino_node.set_socketio(socketio)
    arduino_node.start()

    print("\n" + "="*75)
    print(">> [YOLO26 - WEBCAM LOCAL] HỆ THỐNG GIÁM SÁT AN TOÀN TÀI XẾ (CAM MÁY TÍNH) <<")
    print("="*75)
    print(">> Cấu hình chuyên biệt Webcam Máy Tính:")
    print("   [+] Nguồn Video:         Webcam Local (Device Index 0)")
    print("   [+] Động cơ AI:          best_unified.pt (Gánh trọn Mắt, Miệng, Điện thoại, Tư thế)")
    print("   [+] Độ phân giải:        HD 720p (1280x720) Zero-Lag")
    print("   [+] Tác hợp ESP32:       Cảm biến MQ-135 thật, còi hú PAM8403, cờ SLEEP/ALERT")
    print("   [+] Thoát hệ thống:      Bấm phím Ctrl+C để thoát ngay lập tức 100%")
    print("-"*75)
    print(">> Web Dashboard Trực Tuyến:   http://127.0.0.1:5000")
    print(">> Bảng Lịch Sử Vi Phạm:       http://127.0.0.1:5000/history")
    print(">> API Báo Cáo Tổng Hợp:       http://127.0.0.1:5000/api/report/summary")
    print("="*75 + "\n")

    # Khởi chạy Web Server kèm bắt ngắt an toàn
    try:
        socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
    except (KeyboardInterrupt, SystemExit):
        cleanup_and_exit()
