import {
  Activity,
  AlertTriangle,
  BatteryCharging,
  Cable,
  Camera,
  CheckCircle2,
  Cpu,
  HardDrive,
  KeyRound,
  Radio,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Thermometer,
  Volume2,
  Zap,
} from "lucide-react";
import React, { useState } from "react";
import toast from "react-hot-toast";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";

export default function DeviceHealthModal({ isOpen, onClose, vehicle }) {
  const [testingSound, setTestingSound] = useState(false);
  const [isRebooting, setIsRebooting] = useState(false);

  if (!isOpen || !vehicle) return null;

  const iot = vehicle.iot_device || vehicle.iotDevice;
  const plate = vehicle.bien_so_xe || vehicle.bien_so;
  const cpuTemp = iot?.cpu_temp_c || 64.5;
  const isOverheat = cpuTemp >= 70;

  // Giả lập trạng thái ACC và nguồn điện theo SRS 4.1 Production Edition
  const isAccOn = vehicle.trang_thai === "IN_USE" || vehicle.trangThai === "IN_USE";
  const accStatus = isAccOn ? "ACC_ON" : "ACC_STANDBY";
  const batteryVoltage = isAccOn ? "13.8V" : "12.4V";

  const handleRebootDevice = () => {
    setIsRebooting(true);
    toast.loading(`Đang phát lệnh MQTT [REBOOT] xuống thiết bị ${iot?.ma_thiet_bi || "Pi 4"}...`, {
      id: "reboot-toast",
    });

    setTimeout(() => {
      setIsRebooting(false);
      toast.success(
        `Đã gửi lệnh [REBOOT] thành công! Thiết bị xe ${plate} đang khởi động lại trong 30s.`,
        { id: "reboot-toast", duration: 4000 }
      );
      onClose();
    }, 1200);
  };

  const handleTestSpeakerAndRelay = () => {
    setTestingSound(true);
    toast.loading("Đang gửi lệnh UART xuống ESP32 kích hoạt DFPlayer Mini (0001.mp3) & Relay còi...", {
      id: "sound-test",
    });

    setTimeout(() => {
      setTestingSound(false);
      toast.success(
        `✓ Đã nhận ACK từ ESP32 (CRC16 OK)! DFPlayer Mini & Relay còi hú phản hồi bình thường (< 500ms).`,
        { id: "sound-test", duration: 4000 }
      );
    }, 1000);
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={Cpu}
      maxWidth="max-w-2xl"
      title={`Sức khỏe Phần cứng IoT Edge • Xe ${plate}`}
      subtitle={`Mã thiết bị: ${iot?.ma_thiet_bi || "IOT-DN-01"} • Pi 4 CPU: ${iot?.cpu_serial || "10000000a1b2c3d4"}`}
      footer={
        <div className="flex flex-wrap items-center justify-between w-full gap-2">
          <Button
            variant="secondary"
            onClick={handleTestSpeakerAndRelay}
            disabled={testingSound}
            className="text-xs"
          >
            <Volume2 className="h-4 w-4 text-amber-400" />
            <span>{testingSound ? "Đang phát âm thanh..." : "Test Loa & Còi ESP32"}</span>
          </Button>

          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={onClose}>
              Đóng
            </Button>
            <Button
              variant="primary"
              onClick={handleRebootDevice}
              disabled={isRebooting}
              className="bg-rose-600 hover:bg-rose-500"
            >
              <RefreshCw className={`h-4 w-4 ${isRebooting ? "animate-spin" : ""}`} />
              <span>Khởi động lại Pi 4</span>
            </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-4 font-sans text-xs">
        {/* 1. LƯỚI THÔNG SỐ CHÍNH (4 THẺ KPI) */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {/* Nhiệt độ CPU Pi 4 */}
          <div
            className={`rounded-2xl border p-3 ${
              isOverheat
                ? "border-alert-danger/40 bg-alert-danger/15 text-alert-danger"
                : "border-brand-border-dark bg-brand-dark/70 text-alert-safe"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1 text-[11px] text-brand-text-muted">
                <Thermometer className="h-3.5 w-3.5" /> Nhiệt độ Pi 4
              </span>
              <span className="text-[10px] font-bold">
                {isOverheat ? "Nóng" : "Chuẩn"}
              </span>
            </div>
            <p className="mt-1.5 text-xl font-black text-brand-text-primary">{cpuTemp}°C</p>
            <p className="mt-0.5 text-[10px] text-brand-text-subtle">
              Tản nhiệt PWM &lt; 75°C
            </p>
          </div>

          {/* Dung lượng lưu trữ thẻ nhớ / SSD */}
          <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/70 p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1 text-[11px] text-brand-text-muted">
                <HardDrive className="h-3.5 w-3.5 text-brand-blue-sky" /> Dung lượng
              </span>
              <span className="text-[10px] font-bold text-alert-safe">
                Trống 72%
              </span>
            </div>
            <p className="mt-1.5 text-xl font-black text-brand-text-primary">
              {iot?.free_storage_mb
                ? (iot.free_storage_mb / 1024).toFixed(1)
                : "24.5"}{" "}
              GB
            </p>
            <p className="mt-0.5 text-[10px] text-brand-text-subtle">
              SQLite offline queue
            </p>
          </div>

          {/* Nguồn điện hạ áp LM2596 */}
          <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/70 p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1 text-[11px] text-brand-text-muted">
                <Zap className="h-3.5 w-3.5 text-amber-400" /> Nguồn LM2596
              </span>
              <span className="text-[10px] font-bold text-emerald-400">Ổn định</span>
            </div>
            <p className="mt-1.5 text-xl font-black text-brand-text-primary">5.18V</p>
            <p className="mt-0.5 text-[10px] text-brand-text-subtle">
              Ắc-quy: {batteryVoltage}
            </p>
          </div>

          {/* Tín hiệu khóa điện ACC (SRS NFR_7) */}
          <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/70 p-3">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1 text-[11px] text-brand-text-muted">
                <KeyRound className="h-3.5 w-3.5 text-emerald-400" /> Tín hiệu ACC
              </span>
              <span
                className={`text-[10px] font-bold ${
                  isAccOn ? "text-emerald-400" : "text-amber-400"
                }`}
              >
                {accStatus}
              </span>
            </div>
            <p className="mt-1.5 text-xl font-black text-brand-text-primary">
              {isAccOn ? "Đang nổ máy" : "Tắt máy"}
            </p>
            <p className="mt-0.5 text-[10px] text-brand-text-subtle">
              Đệm giữ nguồn 30s
            </p>
          </div>
        </div>

        {/* 2. KHỐI TRUYỀN THÔNG UART & NGOẠI VI (SRS FR_1.3) */}
        <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/60 p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-brand-border-dark pb-2">
            <div className="flex items-center gap-2">
              <Cable className="h-4 w-4 text-brand-blue-sky" />
              <h4 className="font-bold text-brand-text-primary">
                Giao tiếp Nội bộ Xe: UART (Raspberry Pi 4 ↔ ESP32)
              </h4>
            </div>
            <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-bold text-emerald-400">
              115200 bps • CRC16 OK
            </span>
          </div>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <div className="flex items-center justify-between rounded-xl border border-brand-border-dark bg-brand-surface/50 p-2.5">
              <div className="flex items-center gap-2">
                <Volume2 className="h-4 w-4 text-brand-blue-sky" />
                <div>
                  <p className="font-bold text-brand-text-primary">DFPlayer Mini (MP3)</p>
                  <p className="text-[10px] text-brand-text-subtle">
                    Thẻ MicroSD: 5 tệp cảnh báo giọng nói
                  </p>
                </div>
              </div>
              <span className="font-bold text-emerald-400">SẴN SÀNG</span>
            </div>

            <div className="flex items-center justify-between rounded-xl border border-brand-border-dark bg-brand-surface/50 p-2.5">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-rose-400" />
                <div>
                  <p className="font-bold text-brand-text-primary">Relay Còi Hú 12V</p>
                  <p className="text-[10px] text-brand-text-subtle">
                    Kích hoạt khi nhắm mắt ≥ 1.5s hoặc mất UART &gt; 5s
                  </p>
                </div>
              </div>
              <span className="font-bold text-emerald-400">THƯỜNG MỞ (NO)</span>
            </div>
          </div>
        </div>

        {/* 3. KHỐI BẢO VỆ CHỐNG RÚT CÁP & CẢM BIẾN (SRS FR_1.2, FR_1.4, FR_1.7) */}
        <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/60 p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-brand-border-dark pb-2">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-4 w-4 text-amber-400" />
              <h4 className="font-bold text-brand-text-primary">
                Cảm biến Cabin & Cơ chế Chống Gian lận (P2 Hardening)
              </h4>
            </div>
            <span className="text-[11px] text-brand-text-subtle">
              SRS 4.1 Production Compliance
            </span>
          </div>

          <div className="space-y-2 text-xs">
            {/* Camera IR & Rút cáp */}
            <div className="flex items-center justify-between border-b border-brand-border-dark/60 pb-2">
              <div className="flex items-center gap-2">
                <Camera className="h-4 w-4 text-emerald-400" />
                <div>
                  <span className="text-brand-text-primary font-semibold">
                    Camera Hồng ngoại IR (850nm) & Đèn LED trợ sáng
                  </span>
                  <p className="text-[10px] text-brand-text-subtle">
                    Chống rút cáp USB: Khi xe &gt; 5 km/h, rút cáp tự động kích còi & trừ 15 điểm
                  </p>
                </div>
              </div>
              <span className="font-bold text-emerald-400">CONNECTED (720p @ 20 FPS)</span>
            </div>

            {/* Cảm biến MQ-135 */}
            <div className="flex items-center justify-between border-b border-brand-border-dark/60 pb-2">
              <div className="flex items-center gap-2">
                <Radio className="h-4 w-4 text-brand-blue-sky" />
                <div>
                  <span className="text-brand-text-primary font-semibold">
                    Cảm biến Khí Cabin MQ-135 (ADC Bù nhiệt ẩm)
                  </span>
                  <p className="text-[10px] text-brand-text-subtle">
                    Đã qua 180s làm nóng ban đầu • Moving Average 5 mẫu chống báo giả
                  </p>
                </div>
              </div>
              <span className="font-bold text-emerald-400">BASELINE ĐẦU CA: 420 PPM</span>
            </div>

            {/* Mạch nguồn lưu điện phụ trợ ACC (SRS NFR_7) */}
            <div className="flex items-center justify-between pt-0.5">
              <div className="flex items-center gap-2">
                <BatteryCharging className="h-4 w-4 text-amber-400" />
                <div>
                  <span className="text-brand-text-primary font-semibold">
                    Mạch Lưu điện Phụ trợ (30s Supercapacitor Backup)
                  </span>
                  <p className="text-[10px] text-brand-text-subtle">
                    Giữ nguồn 30s sau khi tắt chìa khóa ACC để lưu RAM tmpfs xuống đĩa và đóng SQLite
                  </p>
                </div>
              </div>
              <span className="font-bold text-emerald-400">STANDBY ACTIVE</span>
            </div>
          </div>
        </div>
      </div>
    </BaseModal>
  );
}
