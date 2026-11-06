import { ShieldAlert, Sparkles, Wind } from "lucide-react";
import { useState } from "react";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";
import { formatFullDateTime } from "../../../utils/formatDateTime";

export default function ViolationReviewModal({
  isOpen,
  onClose,
  violation,
  onConfirmReview,
}) {
  const [reviewNote, setReviewNote] = useState("");
  if (!isOpen || !violation) return null;

  const driverName =
    violation.driver?.user?.ho_ten || violation.driver_name || "Lê Minh Tuấn";
  const vehiclePlate =
    violation.vehicle?.bien_so_xe ||
    violation.vehicle_plate ||
    violation.trip?.vehicle?.bien_so_xe ||
    "43C-123.45";
  const videoUrl = violation.video_url || violation.duong_dan_video;
  const isVideoRequired = Boolean(videoUrl);
  const status = violation.trang_thai_xac_thuc || violation.trang_thai;
  const isPending = status === "PENDING_MANUAL_REVIEW";

  const handleAction = (decision) => {
    onConfirmReview(violation.id, decision, reviewNote);
    setReviewNote("");
    onClose();
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={ShieldAlert}
      maxWidth="max-w-2xl"
      title={`Thẩm tra Vi phạm IoT #${violation.id} • Xe ${vehiclePlate}`}
      subtitle={`Tài xế: ${driverName} • Chuyến #${violation.trip_id || "501"}`}
      footer={
        <div className="flex w-full items-center justify-between">
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
          {isPending ? (
            <div className="flex gap-2.5">
              <Button variant="danger" onClick={() => handleAction("REJECTED")}>
                Bỏ qua (Báo giả)
              </Button>
              <Button
                variant="primary"
                onClick={() => handleAction("CONFIRMED")}
              >
                Xác nhận &amp; Trừ điểm
              </Button>
            </div>
          ) : (
            <span className="text-brand-text-subtle text-[11px] italic">
              Bản ghi đã chốt trạng thái ({status})
            </span>
          )}
        </div>
      }
    >
      <div className="space-y-4">
        {isVideoRequired ? (
          <div className="border-brand-border-dark relative overflow-hidden rounded-2xl border bg-black shadow-inner">
            <video
              src={videoUrl}
              controls
              autoPlay
              muted
              className="max-h-68 w-full object-contain"
            />
            <div className="border-brand-border/60 bg-brand-dark/90 text-brand-text-primary absolute top-3 left-3 flex items-center gap-2 rounded-lg border px-2.5 py-1 text-[10px] font-bold backdrop-blur-md">
              <span className="bg-alert-danger h-2 w-2 animate-ping rounded-full"></span>
              <span>Camera Buồng lái CCTV (10 giây)</span>
            </div>
          </div>
        ) : (
          <div className="border-alert-warning/30 bg-alert-warning/15 rounded-2xl border p-5 text-center">
            <Wind className="text-alert-warning mx-auto h-9 w-9" />
            <p className="text-alert-warning mt-2 text-sm font-bold">
              Ghi nhận Telemetry Môi trường Cabin (Không có video)
            </p>
            <p className="text-brand-text-muted mt-1 text-xs">
              Chỉ số CO2 đạt 1,340 ppm (vượt ngưỡng 1,200 ppm) kéo dài quá 5
              phút.
            </p>
          </div>
        )}

        <div className="border-brand-border-dark bg-brand-dark/70 grid grid-cols-2 gap-3.5 rounded-2xl border p-4">
          <div>
            <span className="text-brand-text-subtle text-[11px]">
              Loại vi phạm
            </span>
            <p className="text-brand-text-primary mt-0.5 font-bold">
              {violation.ten_loai || violation.ma_loai}
            </p>
          </div>
          <div>
            <span className="text-brand-text-subtle text-[11px]">
              Mức phạt quy định
            </span>
            <p className="text-alert-danger mt-0.5 font-bold">
              -{violation.diem_bi_tru ?? violation.diem_tru ?? 0} điểm uy tín
            </p>
          </div>
          <div>
            <span className="text-brand-text-subtle text-[11px]">
              Thời điểm chốt
            </span>
            <p className="text-brand-text-primary mt-0.5">
              {formatFullDateTime(
                violation.thoi_gian_chot_vi_pham || violation.thoi_gian_vi_pham,
              )}
            </p>
          </div>
          <div>
            <span className="text-brand-text-subtle text-[11px]">
              Độ tin cậy Edge AI / Server:
            </span>
            <p className="text-alert-safe mt-0.5 font-bold">
              {violation.ai_confidence_score
                ? `${(violation.ai_confidence_score * 100).toFixed(0)}% (Hậu kiểm Cloud)`
                : "Quy tắc Cảm biến"}
            </p>
          </div>
        </div>

        <div className="border-brand-border-active/30 bg-brand-blue-night/20 rounded-2xl border p-4">
          <div className="text-brand-blue-sky flex items-center gap-1.5 text-[11px] font-bold tracking-wider uppercase">
            <Sparkles className="h-4 w-4" />
            <span>Phân tích Thuật toán Thẩm định (AI Engine)</span>
          </div>
          <p className="text-brand-text-secondary mt-1.5 leading-relaxed">
            {violation.mo_ta ||
              "Mô hình YOLO26 phát hiện mắt nhắm liên tục > 1.5s và góc nghiêng đầu gục xuống vô lăng. Điểm EAR = 0.14 thấp hơn ngưỡng chuẩn."}
          </p>
        </div>

        {isPending && (
          <div>
            <label className="text-brand-text-muted mb-1 block font-bold">
              Ghi chú nhận định của Điều phối viên (Lưu vết Audit Log):
            </label>
            <textarea
              rows={2}
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              placeholder="VD: Video rõ ràng tài xế ngủ gật khi xe đang chạy 75km/h, xác nhận trừ điểm..."
              className="border-brand-border bg-brand-dark text-brand-text-primary focus:border-brand-primary w-full rounded-xl border p-2.5 text-xs outline-none"
            />
          </div>
        )}
      </div>
    </BaseModal>
  );
}
