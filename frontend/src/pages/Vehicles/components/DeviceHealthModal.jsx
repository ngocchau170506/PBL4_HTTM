import {
  Cpu,
  HardDrive,
  Radio,
  RefreshCw,
  ShieldCheck,
  Thermometer,
} from "lucide-react";
import toast from "react-hot-toast";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";

export default function DeviceHealthModal({ isOpen, onClose, vehicle }) {
  if (!isOpen || !vehicle) return null;

  const iot = vehicle.iot_device || vehicle.iotDevice;
  const plate = vehicle.bien_so_xe || vehicle.bien_so;
  const cpuTemp = iot?.cpu_temp_c || 64.5;
  const isOverheat = cpuTemp >= 70;

  const handleRebootDevice = () => {
    toast.success(`Đã gửi lệnh MQTT [REBOOT] xuống thiết bị xe ${plate}!`, {
      duration: 3500,
    });
    onClose();
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={Cpu}
      maxWidth="max-w-lg"
      title={`Sức khỏe Phần cứng IoT Edge • Xe ${plate}`}
      subtitle={`Mã CPU: ${iot?.cpu_serial || "10000000a1b2c3d4"}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
          <Button variant="primary" onClick={handleRebootDevice}>
            <RefreshCw className="h-4 w-4" />
            <span>Khởi động lại Pi 4</span>
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          {/* Nhiệt độ CPU Pi 4 */}
          <div
            className={`rounded-2xl border p-3.5 ${
              isOverheat
                ? "border-alert-danger/40 bg-alert-danger/15 text-alert-danger"
                : "border-brand-border-dark bg-brand-dark/70 text-alert-safe"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-brand-text-muted flex items-center gap-1.5 text-xs">
                <Thermometer className="h-4 w-4" /> Nhiệt độ CPU
              </span>
              <span className="text-[10px] font-bold">
                {isOverheat ? "Nóng" : "Ổn định"}
              </span>
            </div>
            <p className="mt-2 text-2xl font-black">{cpuTemp}°C</p>
            <p className="text-brand-text-subtle mt-1 text-[10px]">
              Ngưỡng tối đa an toàn: &lt; 75°C (SRS NFR_7)
            </p>
          </div>

          {/* Dung lượng thẻ nhớ / SSD */}
          <div className="border-brand-border-dark bg-brand-dark/70 rounded-2xl border p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-brand-text-muted flex items-center gap-1.5 text-xs">
                <HardDrive className="text-brand-blue-sky h-4 w-4" /> Dung lượng
                trống
              </span>
              <span className="text-alert-safe text-[10px] font-bold">
                Trống 72%
              </span>
            </div>
            <p className="text-brand-text-primary mt-2 text-2xl font-black">
              {iot?.free_storage_mb
                ? (iot.free_storage_mb / 1024).toFixed(1)
                : "24.5"}{" "}
              GB
            </p>
            <p className="text-brand-text-subtle mt-1 text-[10px]">
              Lưu trữ video 10s &amp; SQLite offline
            </p>
          </div>
        </div>

        {/* Trạng thái dịch vụ con */}
        <div className="border-brand-border-dark bg-brand-dark/50 space-y-2.5 rounded-2xl border p-4 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-brand-text-muted flex items-center gap-1.5">
              <Radio className="text-alert-safe h-3.5 w-3.5" /> Kênh MQTT
              Telemetry (QoS 1)
            </span>
            <span className="text-alert-safe font-bold">ONLINE</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-brand-text-muted flex items-center gap-1.5">
              <ShieldCheck className="text-brand-blue-sky h-3.5 w-3.5" /> Mô
              hình Edge AI
            </span>
            <span className="text-brand-text-secondary font-semibold">
              {iot?.ai_model_version || "yolo26n-v1.1 (15-20 FPS)"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-brand-text-muted">Cảm biến nồng độ khí</span>
            <span className="text-alert-safe font-semibold">
              MQ-135 Sẵn sàng
            </span>
          </div>
        </div>
      </div>
    </BaseModal>
  );
}
