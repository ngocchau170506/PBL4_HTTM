/**
 * state.ts — MÁY TRẠNG THÁI (khung, chưa phải code chạy)
 * Đổi file này = Change Request, MAIN duyệt.
 */
import type { NodeId, ErrorEvent } from "./logic";

// ---------- Trạng thái toàn hệ thống ----------
export type SystemState =
  | "BOOT"          // nạp config, tự kiểm tra các node
  | "IDLE"          // chưa có tài xế
  | "BASELINE"      // học khung chuẩn 5–10s cho 1 tài xế
  | "MONITOR"       // YOLO/AI giám sát liên tục
  | "DEGRADED"      // 1 nhánh optional hỏng (vd ESP32), phần còn lại chạy tiếp
  | "FAULT"         // node lõi hỏng: dừng, chờ MAIN/diagnose
  | "SHUTDOWN";

// Chuyển trạng thái hợp lệ — chuyển ngoài bảng này = lỗi
export const TRANSITIONS: Record<SystemState, SystemState[]> = {
  BOOT:     ["IDLE", "FAULT"],
  IDLE:     ["BASELINE", "SHUTDOWN", "FAULT"],
  BASELINE: ["MONITOR", "IDLE", "FAULT"],          // xong baseline -> MONITOR; hủy -> IDLE
  MONITOR:  ["BASELINE", "IDLE", "DEGRADED", "FAULT"], // đổi tài xế -> BASELINE
  DEGRADED: ["MONITOR", "FAULT", "IDLE"],
  FAULT:    ["IDLE", "SHUTDOWN"],                  // chỉ ra khỏi FAULT sau khi có hồ sơ BUG
  SHUTDOWN: [],
};

// ---------- Trạng thái từng node ----------
export type NodeHealth = "OK" | "DEGRADED" | "DOWN";

export interface NodeStatus {
  id: NodeId;
  health: NodeHealth;
  lastPacketTs?: number;
  queueDepth?: number;
  lastError?: ErrorEvent;
}

// ---------- Baseline theo tài xế ----------
export type BaselineState = "NONE" | "COLLECTING" | "LOCKED" | "EXPIRED";

export interface BaselineProfile {
  driver_id: string;
  state: BaselineState;
  startedMs: number;
  durationMs: number;      // trong khoảng baselineSeconds ở config
  frameCount: number;
  // dữ liệu chuẩn cụ thể (tư thế, vị trí khung...) sẽ thêm bằng CR ở G2
}
// Luật: mỗi driver_id 1 profile riêng; đổi tài xế = tạo profile mới, không ghi đè.

// ---------- Timeline vi phạm (-2s -> 0s -> hết) ----------
export type ViolationPhase = "WATCHING" | "SUSPECT" | "ACTIVE" | "CLOSED";
// WATCHING: bình thường, buffer vòng giữ ~2s gần nhất
// SUSPECT : có dấu hiệu (-2s .. 0s)
// ACTIVE  : t=0 đã xác nhận vi phạm, đang ghi
// CLOSED  : hết sự kiện, đóng gói gửi server

export const VIOLATION_TRANSITIONS: Record<ViolationPhase, ViolationPhase[]> = {
  WATCHING: ["SUSPECT"],
  SUSPECT:  ["ACTIVE", "WATCHING"],    // dấu hiệu giả -> quay lại
  ACTIVE:   ["CLOSED"],
  CLOSED:   ["WATCHING"],
};

// ---------- Trạng thái quy trình làm việc của AI (khớp CLAUDE.md mục 1) ----------
export type WorkState =
  | "PROPOSE" | "APPROVED" | "BUILD" | "TEST" | "REPORT" | "REVIEW" | "MERGED"
  | "DIAGNOSE" | "FIX" | "BLOCKED";

export const WORK_TRANSITIONS: Record<WorkState, WorkState[]> = {
  PROPOSE:  ["APPROVED", "BLOCKED"],        // chỉ MAIN đẩy sang APPROVED
  APPROVED: ["BUILD"],
  BUILD:    ["TEST"],
  TEST:     ["REPORT", "DIAGNOSE"],
  DIAGNOSE: ["FIX", "BLOCKED"],
  FIX:      ["TEST"],
  REPORT:   ["REVIEW"],
  REVIEW:   ["MERGED", "BUILD", "BLOCKED"], // chỉ MAIN đẩy sang MERGED
  MERGED:   [],
  BLOCKED:  ["PROPOSE"],                    // sau khi MAIN trả lời / ra CR
};
// Quy tắc: mọi bước bị chặn nếu thiếu chữ ký MAIN ở PROPOSE->APPROVED và REVIEW->MERGED.
