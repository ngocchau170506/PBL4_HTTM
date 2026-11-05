import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  PhoneCall,
  RotateCcw,
  Wind,
  Wrench,
  XCircle,
} from "lucide-react";

const BADGE_CONFIG = {
  // 1. TRẠNG THÁI TÀI XẾ (DRIVER ALERT LEVELS)
  NORMAL: {
    label: "Bình thường",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    icon: CheckCircle2,
  },
  WARNING_YELLOW: {
    label: "Cảnh báo",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    icon: AlertTriangle,
  },
  SUSPENDED_RED: {
    label: "Đình chỉ",
    style: "border-alert-danger/40 bg-alert-danger/15 text-alert-danger",
    pingColor: "bg-alert-danger",
  },

  // 2. LOẠI VI PHẠM AI (VIOLATION TYPES)
  MICROSLEEP: {
    label: "Ngủ gật (Microsleep)",
    style: "border-alert-danger/40 bg-alert-danger/15 text-alert-danger",
    pingColor: "bg-alert-danger",
  },
  PHONE_DISTRACTION: {
    label: "Dùng điện thoại",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    icon: PhoneCall,
  },
  HIGH_CO2: {
    label: "CO2 buồng lái cao",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    icon: Wind,
  },
  YAWNING_FATIGUE: {
    label: "Ngáp mệt mỏi",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    icon: AlertTriangle,
  },
  DEVICE_TAMPERING: {
    label: "Che / Rút cáp thiết bị",
    style:
      "border-brand-primary/40 bg-brand-blue-night/50 text-brand-blue-light",
    icon: Cpu,
  },

  // 3. TRẠNG THÁI XÁC THỰC VI PHẠM (VERIFICATION STATUS)
  CONFIRMED: {
    label: "Đã xác nhận",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    icon: CheckCircle2,
  },
  AI_CONFIRMED: {
    label: "AI Tự động duyệt",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    icon: CheckCircle2,
  },
  PENDING_MANUAL_REVIEW: {
    label: "Chờ duyệt tay",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    pingColor: "bg-alert-warning",
  },
  RESOLVED_SAFE: {
    label: "Đã nghỉ kịp thời",
    style: "border-alert-info/30 bg-alert-info/15 text-alert-info",
    icon: CheckCircle2,
  },
  REJECTED: {
    label: "Bác bỏ (False Alarm)",
    style: "border-brand-border bg-brand-surface/70 text-brand-text-muted",
    icon: XCircle,
  },

  // 4. TRẠNG THÁI PHƯƠNG TIỆN (VEHICLE STATUS)
  IN_USE: {
    label: "Đang vận hành",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    pingColor: "bg-alert-safe",
  },
  ACTIVE: {
    label: "Sẵn sàng nhận ca",
    style: "border-brand-primary/30 bg-brand-blue-night/40 text-brand-blue-sky",
    icon: CheckCircle2,
  },
  MAINTENANCE: {
    label: "Đang bảo dưỡng",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    icon: Wrench,
  },

  // 5. TRẠNG THÁI CA CHẠY & KHIẾU NẠI (SHIFTS & DISPUTES)
  SCHEDULED: {
    label: "Chờ nhận",
    style: "border-brand-border bg-brand-surface text-brand-text-secondary",
    icon: Clock,
  },
  ACCEPTED: {
    label: "Đã chốt ca",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    icon: CheckCircle2,
  },
  IN_PROGRESS: {
    label: "Đang thực hiện",
    style: "border-alert-info/30 bg-alert-info/15 text-alert-info",
    pingColor: "bg-alert-info",
  },
  OPEN: {
    label: "Chờ giải quyết (48h)",
    style: "border-alert-warning/40 bg-alert-warning/15 text-alert-warning",
    pingColor: "bg-alert-warning",
  },
  APPROVED: {
    label: "Chấp thuận (Hoàn điểm)",
    style: "border-alert-safe/30 bg-alert-safe/15 text-alert-safe",
    icon: RotateCcw,
  },
};

export default function StatusBadge({ code, customLabel }) {
  const normalizedCode = String(code || "").toUpperCase();
  const config = BADGE_CONFIG[normalizedCode] || {
    label: customLabel || code || "Không xác định",
    style: "border-brand-border bg-brand-surface text-brand-text-muted",
  };

  const Icon = config.icon;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${config.style}`}
    >
      {config.pingColor && (
        <span className="relative flex h-2 w-2">
          <span
            className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${config.pingColor}`}
          />
          <span
            className={`relative inline-flex h-2 w-2 rounded-full ${config.pingColor}`}
          />
        </span>
      )}
      {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
      <span>{customLabel || config.label}</span>
    </span>
  );
}
