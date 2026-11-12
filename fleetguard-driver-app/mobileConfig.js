const SERVER_IP = "172.16.62.179";

export const API_CONFIG = {
  USE_MOCK: false,
  BASE_URL: `http://${SERVER_IP}:3000/api/v1`,
  SOCKET_URL: `http://${SERVER_IP}:3000`,
  TIMEOUT_MS: 15000,
};

export const COLORS = {
  brandDeep: "#090d16",
  brandDark: "#0f172a",
  brandSurface: "#1e293b",
  brandSurfaceElevated: "#334155",
  brandPrimary: "#3b82f6",
  brandPrimaryDark: "#1d4ed8",
  brandBlueNight: "#1e3a8a",
  brandBlueSky: "#60a5fa",
  brandBlueLight: "#bae6fd",
  brandBorder: "#475569",
  brandBorderDark: "#334155",
  textPrimary: "#ffffff",
  textSecondary: "#f1f5f9",
  textMuted: "#cbd5e1",
  textSubtle: "#94a3b8",
  alertDanger: "#f87171",
  alertDangerBg: "rgba(127, 29, 29, 0.45)",
  alertWarning: "#fbbf24",
  alertWarningBg: "rgba(120, 53, 15, 0.45)",
  alertSafe: "#34d399",
  alertSafeBg: "rgba(6, 78, 59, 0.45)",
};

export const VIOLATION_TYPES = {
  MICROSLEEP: {
    code: "MICROSLEEP",
    label: "Ngủ gật (Microsleep)",
    defaultPenalty: 10,
    color: "#f87171",
  },
  YAWNING_FATIGUE: {
    code: "YAWNING_FATIGUE",
    label: "Ngáp mệt mỏi",
    defaultPenalty: 5,
    color: "#fbbf24",
  },
  HIGH_CO2: {
    code: "HIGH_CO2",
    label: "CO2 / Khí cabin cao",
    defaultPenalty: 2,
    color: "#fbbf24",
  },
  PHONE_DISTRACTION: {
    code: "PHONE_DISTRACTION",
    label: "Dùng điện thoại",
    defaultPenalty: 5,
    color: "#fb923c",
  },
  DEVICE_TAMPERING: {
    code: "DEVICE_TAMPERING",
    label: "Che / Rút cáp thiết bị",
    defaultPenalty: 15,
    color: "#ef4444",
  },
};
