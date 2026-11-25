import {
  Cpu,
  Globe,
  Save,
  ShieldCheck,
  Sliders,
  Sparkles,
  Wind,
} from "lucide-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import Button from "../../components/common/Button";
import InputField from "../../components/common/InputField";
import dataService from "../../services/dataService";

export default function AdminAiConfigPage() {
  const [config, setConfig] = useState({
    fleet_scope: "GLOBAL",
    microsleep_eye_closed_sec: 1.5,
    head_down_sec: 1.0,
    yawn_count_threshold: 3,
    phone_use_sec: 3.0,
    co2_warning_threshold_ppm: 1200,
    mq135_warmup_sec: 180,
    auto_confirm_min: 0.85,
    manual_review_min: 0.6,
    weight_landmarks: 0.4,
    weight_cnn: 0.4,
    weight_edge: 0.2,
    penalty_cap_per_trip: 30,
    penalty_cap_per_day: 50,
    manual_review_sla_hours: 24,
    dispute_window_hours: 48,
    min_rest_hours: 10.0,
    timezone: "Asia/Ho_Chi_Minh",
  });
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    dataService.getAiConfig().then((data) => {
      if (data) setConfig((prev) => ({ ...prev, ...data, fleet_scope: "GLOBAL" }));
    });
  }, []);

  const handleChange = (e) => {
    const { name, value, type } = e.target;
    setConfig((prev) => ({
      ...prev,
      [name]: type === "number" || type === "range" ? Number(value) : value,
    }));
  };

  const handleSaveConfig = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      await dataService.updateAiConfig({ ...config, fleet_scope: "GLOBAL" });
      toast.success(
        "Đã lưu cấu hình vào bảng system_configs & ghi Audit Log!",
        { icon: "⚙️", duration: 3500 },
      );
    } catch {
      toast.error("Không thể lưu cấu hình!");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      {/* HEADER */}
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span className="bg-brand-primary h-2.5 w-2.5 rounded-full shadow-sm shadow-blue-500/50"></span>
            <p className="text-brand-blue-light text-[11px] font-bold tracking-widest uppercase">
              Quản trị Tham số Nghiệp vụ Phân cấp
            </p>
          </div>
          <h1 className="text-brand-text-primary mt-1 text-2xl font-black tracking-tight">
            Cấu hình Ngưỡng AI & Quy tắc Vận hành
          </h1>
          <p className="text-brand-text-muted text-xs">
            Thiết lập động ngưỡng YOLO26 tại Edge, trọng số hậu kiểm AI Cloud và
            trần trừ điểm sổ cái
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Button
            variant="primary"
            onClick={handleSaveConfig}
            isLoading={isSaving}
          >
            <Save className="h-4 w-4" />
            <span>Lưu Cấu hình Hệ thống</span>
          </Button>
        </div>
      </div>

      {/* PHẠM VI ÁP DỤNG CẤU HÌNH */}
      <div className="card-glass border-brand-border flex flex-col justify-between gap-4 rounded-3xl border p-5 shadow-xl sm:flex-row sm:items-center">
        <div className="flex items-center gap-3.5">
          <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border">
            <Globe className="h-5 w-5" />
          </div>
          <div>
            <h3 className="text-brand-text-primary text-sm font-bold">
              Phạm vi áp dụng cấu hình (System Scope)
            </h3>
            <p className="text-brand-text-muted text-xs">
              Áp dụng thống nhất toàn đội xe • Múi giờ:{" "}
              <span className="text-brand-blue-sky font-bold">
                {config.timezone}
              </span>
            </p>
          </div>
        </div>
        <span className="border-brand-primary/40 bg-brand-blue-night/50 text-brand-blue-sky inline-flex items-center rounded-xl border px-3.5 py-2 text-xs font-bold">
          GLOBAL — Toàn hệ thống
        </span>
      </div>

      {/* 3 KHỐI CẤU HÌNH CHÍNH */}
      <form
        onSubmit={handleSaveConfig}
        className="grid grid-cols-1 gap-6 lg:grid-cols-3"
      >
        {/* KHỐI 1: EDGE AI & CẢM BIẾN */}
        <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-xl">
          <div className="space-y-5">
            <div className="border-brand-border-dark flex items-center gap-3 border-b pb-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-rose-500/30 bg-rose-950/40 text-rose-400">
                <Cpu className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-brand-text-primary text-sm font-bold">
                  1. Ngưỡng Phản xạ Edge (Pi 4 + ESP32)
                </h2>
                <p className="text-brand-text-muted text-[11px]">
                  Kích hoạt Dual-ESP32 + Loa I2S MAX98357A tức thì (&lt; 1s)
                </p>
              </div>
            </div>

            <div>
              <div className="mb-1.5 flex justify-between text-xs">
                <span className="text-brand-text-primary font-bold">
                  Nhắm mắt ngủ gật
                </span>
                <span className="text-alert-danger font-black">
                  {config.microsleep_eye_closed_sec} giây
                </span>
              </div>
              <input
                type="range"
                name="microsleep_eye_closed_sec"
                min="1.0"
                max="3.0"
                step="0.1"
                value={config.microsleep_eye_closed_sec}
                onChange={handleChange}
                className="accent-brand-primary w-full cursor-pointer"
              />
              <p className="text-brand-text-subtle mt-1 text-[11px]">
                YOLO26 phát hiện `eye_closed` liên tục vượt ngưỡng sẽ hú còi và
                cắt clip 10s.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <InputField
                label="Gục đầu (giây)"
                name="head_down_sec"
                type="number"
                step="0.1"
                min="0.5"
                max="3.0"
                value={config.head_down_sec}
                onChange={handleChange}
              />
              <InputField
                label="Dùng ĐT (giây)"
                name="phone_use_sec"
                type="number"
                step="0.5"
                min="1.5"
                max="10"
                value={config.phone_use_sec}
                onChange={handleChange}
              />
            </div>

            <div className="border-brand-border-dark bg-brand-dark/60 space-y-3 rounded-2xl border p-4">
              <div className="flex items-center justify-between">
                <span className="text-alert-warning flex items-center gap-1.5 text-xs font-bold">
                  <Wind className="h-4 w-4" /> Ngưỡng khí độc / CO2 (MQ-135)
                </span>
                <span className="text-alert-warning text-xs font-black">
                  {config.co2_warning_threshold_ppm} ppm
                </span>
              </div>
              <input
                type="range"
                name="co2_warning_threshold_ppm"
                min="800"
                max="2000"
                step="50"
                value={config.co2_warning_threshold_ppm}
                onChange={handleChange}
                className="w-full cursor-pointer accent-amber-500"
              />
              <p className="text-brand-text-subtle text-[11px]">
                Bỏ qua <b>{config.mq135_warmup_sec}s (3 phút)</b> đầu làm nóng
                cảm biến để chống báo giả
              </p>
            </div>
          </div>

          <div className="border-brand-border-dark mt-6 border-t pt-3 text-[11px] text-emerald-400">
            ✓ Lưu trực tiếp vào `system_configs` (PostgreSQL) &amp; áp dụng ngay cho Backend
          </div>
        </div>

        {/* KHỐI 2: AI CLOUD WORKER & HỢP NHẤT ĐIỂM */}
        <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-xl">
          <div className="space-y-5">
            <div className="border-brand-border-dark flex items-center gap-3 border-b pb-4">
              <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-10 w-10 items-center justify-center rounded-2xl border">
                <Sparkles className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-brand-text-primary text-sm font-bold">
                  2. Hậu kiểm AI Cloud
                </h2>
                <p className="text-brand-text-muted text-[11px]">
                  Phân nhánh Tự động duyệt / Duyệt tay / Bác bỏ
                </p>
              </div>
            </div>

            <div>
              <div className="mb-1.5 flex justify-between text-xs">
                <span className="text-brand-text-primary font-bold">
                  Ngưỡng AI Tự động Xác nhận
                </span>
                <span className="text-alert-safe font-black">
                  &ge; {Math.round(config.auto_confirm_min * 100)}%
                </span>
              </div>
              <input
                type="range"
                name="auto_confirm_min"
                min="0.70"
                max="0.95"
                step="0.01"
                value={config.auto_confirm_min}
                onChange={handleChange}
                className="w-full cursor-pointer accent-emerald-500"
              />
            </div>

            <div>
              <div className="mb-1.5 flex justify-between text-xs">
                <span className="text-brand-text-primary font-bold">
                  Ngưỡng Chờ Điều phối viên duyệt tay
                </span>
                <span className="text-alert-warning font-black">
                  {Math.round(config.manual_review_min * 100)}% &rarr;{" "}
                  {Math.round(config.auto_confirm_min * 100)}%
                </span>
              </div>
              <input
                type="range"
                name="manual_review_min"
                min="0.40"
                max="0.70"
                step="0.05"
                value={config.manual_review_min}
                onChange={handleChange}
                className="w-full cursor-pointer accent-amber-500"
              />
              <p className="text-brand-text-subtle mt-1 text-[11px]">
                Dưới {Math.round(config.manual_review_min * 100)}% hệ thống tự
                động bác bỏ
              </p>
            </div>

            <div className="border-brand-border-active/30 bg-brand-blue-night/20 space-y-2.5 rounded-2xl border p-4 text-xs">
              <p className="text-brand-blue-sky font-bold">
                Công thức Trọng số Hợp nhất:
              </p>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="border-brand-border-dark bg-brand-dark/80 rounded-xl border p-2">
                  <p className="text-brand-text-muted text-[10px]">Landmarks</p>
                  <p className="text-brand-text-primary text-sm font-black">
                    {config.weight_landmarks}
                  </p>
                </div>
                <div className="border-brand-border-dark bg-brand-dark/80 rounded-xl border p-2">
                  <p className="text-brand-text-muted text-[10px]">CNN Model</p>
                  <p className="text-brand-text-primary text-sm font-black">
                    {config.weight_cnn}
                  </p>
                </div>
                <div className="border-brand-border-dark bg-brand-dark/80 rounded-xl border p-2">
                  <p className="text-brand-text-muted text-[10px]">YOLO Edge</p>
                  <p className="text-brand-text-primary text-sm font-black">
                    {config.weight_edge}
                  </p>
                </div>
              </div>
              <p className="text-brand-text-subtle text-[10px]">
                * Nếu Face Embedding phát hiện `nghi_ngo_sai_nguoi = true`, bắt
                buộc chuyển duyệt tay.
              </p>
            </div>
          </div>

          <div className="border-brand-border-dark text-brand-blue-sky mt-6 border-t pt-3 text-[11px]">
            ✓ Mô hình đang chạy: Pi 4 Edge YOLO + Server Tier-2 MediaPipe HD
          </div>
        </div>

        {/* KHỐI 3: SỔ CÁI ĐIỂM UY TÍN & SLA */}
        <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-xl">
          <div className="space-y-5">
            <div className="border-brand-border-dark flex items-center gap-3 border-b pb-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-emerald-500/30 bg-emerald-950/40 text-emerald-400">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-brand-text-primary text-sm font-bold">
                  3. Trần Trừ Điểm & Thời hạn SLA
                </h2>
                <p className="text-brand-text-muted text-[11px]">
                  Bảo vệ sổ cái (`SELECT FOR UPDATE`) & quyền khiếu nại
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <InputField
                label="Trần trừ / Chuyến"
                name="penalty_cap_per_trip"
                type="number"
                min="10"
                max="100"
                value={config.penalty_cap_per_trip}
                onChange={handleChange}
              />
              <InputField
                label="Trần trừ / Ngày"
                name="penalty_cap_per_day"
                type="number"
                min="20"
                max="100"
                value={config.penalty_cap_per_day}
                onChange={handleChange}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <InputField
                label="SLA Duyệt tay (Giờ)"
                name="manual_review_sla_hours"
                type="number"
                min="6"
                max="72"
                value={config.manual_review_sla_hours}
                onChange={handleChange}
              />
              <InputField
                label="Hạn Khiếu nại (Giờ)"
                name="dispute_window_hours"
                type="number"
                min="24"
                max="168"
                value={config.dispute_window_hours}
                onChange={handleChange}
              />
            </div>

            <InputField
              label="Nghỉ tối thiểu giữa 2 ca (Giờ)"
              name="min_rest_hours"
              type="number"
              step="0.5"
              min="6"
              max="24"
              value={config.min_rest_hours}
              onChange={handleChange}
            />

            <div className="border-brand-border-dark bg-brand-dark/60 space-y-2 rounded-2xl border p-4 text-xs">
              <div className="flex items-center gap-1.5 font-bold text-amber-300">
                <Sliders className="h-3.5 w-3.5" />
                <span>Mốc chế tài tự động:</span>
              </div>
              <p className="text-brand-text-muted text-[11px] leading-relaxed">
                • <b>&le; 65 điểm (`WARNING_YELLOW`):</b> Giảm 30% tần suất ca
                đêm.
                <br />• <b>&le; 20 điểm (`SUSPENDED_RED`):</b> Khóa cứng
                (`HARD_BLOCK`) mọi ca mới & gửi email khẩn qua `Nodemailer`.
              </p>
            </div>
          </div>

          <div className="border-brand-border-dark text-brand-text-muted mt-6 border-t pt-3 text-[11px]">
            ✓ Mọi thay đổi tham số đều tự động lưu vết vào bảng `audit_logs`
          </div>
        </div>
      </form>
    </div>
  );
}
