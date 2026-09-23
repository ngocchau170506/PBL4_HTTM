export const API_URLS = {
  // Auth
  LOGIN: '/auth/login',
  REFRESH_TOKEN: '/auth/refresh',
  LOGOUT: '/auth/logout',

  // Vi phạm (Violations)
  VIOLATIONS: '/violations',
  VIOLATION_DETAIL: (id) => `/violations/${id}`,
  VIOLATION_REVIEW: (id) => `/violations/${id}/review`,

  // Tài xế & Điểm uy tín
  DRIVERS: '/drivers',
  DRIVER_SCORE: (id) => `/drivers/${id}/score`,
  DRIVER_SCORE_HISTORY: (id) => `/drivers/${id}/score/history`,
  DRIVER_BIOMETRIC: (id) => `/drivers/${id}/biometric-profile`,

  // Lịch phân ca & Chuyến đi
  SHIFTS: '/shifts',
  TRIPS: '/trips',
  FLEET_STATUS: '/fleet/status'
};