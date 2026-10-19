/**
 * logic.ts — HỢP ĐỒNG DỮ LIỆU & LUẬT LUỒNG (khung, chưa phải code chạy)
 * Đổi file này = Change Request, MAIN duyệt. Runtime thật (py/js) phải bám đúng các kiểu này.
 */

// ---------- Định danh node & cây luồng ----------
export type NodeId =
  | "connect.camera" | "connect.esp32"
  | "signal"
  | "process.baseline" | "process.sampler" | "process.ai" | "process.rules"
  | "response"
  | "flow" | "diagnose" | "faultsim" | "registry" | "config" | "eval";

export type NodePath = string; // vd: "flow>process.ai" hoặc cạnh "signal->process.sampler"

export interface FlowEdge {
  from: NodeId;
  to: NodeId;
  carries: PacketKind;
  optional?: boolean;      // true = mất cạnh này thì chạy suy giảm được
}

/** Cây luồng khai báo MỘT chỗ; fd_flow đọc để kiểm kiểu gói tại mỗi cạnh. */
export const FLOW_TREE: FlowEdge[] = [
  { from: "connect.camera", to: "signal",           carries: "RawPacket" },
  { from: "connect.esp32",  to: "signal",           carries: "RawPacket", optional: true },
  { from: "signal",         to: "process.sampler",  carries: "SignalPacket" },
  { from: "process.sampler",to: "process.baseline", carries: "SignalPacket" },
  { from: "process.sampler",to: "process.ai",       carries: "SignalPacket" },
  { from: "process.ai",     to: "process.rules",    carries: "DetectionResult" },
  { from: "process.rules",  to: "response",         carries: "ViolationEvent" },
];

// ---------- Gói dữ liệu ----------
export type PacketKind = "RawPacket" | "SignalPacket" | "DetectionResult" | "ViolationEvent" | "ErrorEvent";

export interface PacketBase { trace_id: string; ts_ms: number; source: NodeId; }

export interface RawPacket extends PacketBase { kind: "RawPacket"; payload: Uint8Array | number[]; sourceKind: "camera" | "esp32"; }
export interface SignalPacket extends PacketBase { kind: "SignalPacket"; frame_id?: number; width?: number; height?: number; driver_id?: string; sensor?: Record<string, number>; }
export interface DetectionResult extends PacketBase { kind: "DetectionResult"; frame_id: number; items: { label: string; conf: number; box?: [number, number, number, number] }[]; }
export interface ViolationEvent extends PacketBase {
  kind: "ViolationEvent";
  rule: string;
  t0_ms: number;          // thời điểm bắt đầu vi phạm (0s)
  pre_ms: number;         // phần tiền sự kiện lấy lại (mặc định 2000 = -2s)
  frame_ids: number[];
}

// ---------- Lỗi ----------
export type ErrorCode =
  | "E_CONN_OPEN" | "E_CONN_LOST" | "E_CONN_TIMEOUT"
  | "E_SIG_BAD_FORMAT" | "E_SIG_EMPTY" | "E_SIG_TS_DRIFT"
  | "E_PROC_MODEL_LOAD" | "E_PROC_TIMEOUT" | "E_PROC_BAD_OUTPUT"
  | "E_FLOW_EDGE_TYPE" | "E_FLOW_QUEUE_FULL" | "E_FLOW_NODE_DOWN"
  | "E_RESP_SEND" | "E_RESP_NO_ACK" | "E_RESP_BUFFER_OVERRUN"
  | "E_CFG_INVALID"
  | "E_SCOPE_VIOLATION";     // yêu cầu AI vượt phạm vi/giai đoạn

export type ErrorClass = "source" | "data" | "contract" | "algorithm" | "performance" | "config" | "process";

export interface ErrorEvent extends PacketBase {
  kind: "ErrorEvent";
  code: ErrorCode;
  nodePath: NodePath;         // BẮT BUỘC: chỉ đúng nơi lỗi
  errClass: ErrorClass;
  message: string;
  recoverable: boolean;
}

// ---------- Giao diện AI cắm vào (luật R6) ----------
export interface AiAdapter {
  name: string;                                   // "stub" | "yolo"
  load(): Promise<void>;
  infer(frame: SignalPacket): Promise<DetectionResult>;
  health(): "OK" | "DEGRADED" | "DOWN";
}

// ---------- Cổng chạy AI: TRƯỚC -> GỌI -> SAU (luật R6) ----------
// Mọi lần gọi AI (stub hay yolo) đều đi qua runAi. Không ai gọi adapter.infer trực tiếp.
// timeoutMs lấy từ fd_config, không hard-code ở đây (luật R4).
export type AiRunResult =
  | { ok: true; result: DetectionResult }
  | { ok: false; error: ErrorEvent };

function aiError(
  frame: SignalPacket, adapter: AiAdapter, code: ErrorCode, errClass: ErrorClass,
  message: string, recoverable: boolean,
): AiRunResult {
  return {
    ok: false,
    error: {
      kind: "ErrorEvent", trace_id: frame.trace_id || "NO_TRACE", ts_ms: Date.now(),
      source: "process.ai", code, errClass, message, recoverable,
      nodePath: `process.sampler->process.ai(${adapter.name})`,
    },
  };
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("AI_TIMEOUT")), ms);
    p.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); },
    );
  });
}

export async function runAi(adapter: AiAdapter, frame: SignalPacket, timeoutMs: number): Promise<AiRunResult> {
  // TRƯỚC: chặn đầu vào sai để lỗi dừng ở đúng cạnh, không lọt vào model
  if (!frame.trace_id) {
    return aiError(frame, adapter, "E_SIG_BAD_FORMAT", "contract", "SignalPacket thiếu trace_id", false);
  }
  if (frame.frame_id === undefined) {
    return aiError(frame, adapter, "E_SIG_BAD_FORMAT", "contract", "SignalPacket thiếu frame_id", false);
  }
  if (adapter.health() === "DOWN") {
    return aiError(frame, adapter, "E_FLOW_NODE_DOWN", "process", `adapter ${adapter.name} đang DOWN`, true);
  }

  // GỌI: một chỗ duy nhất gọi model, có giới hạn thời gian
  let result: DetectionResult;
  try {
    result = await withTimeout(adapter.infer(frame), timeoutMs);
  } catch (e) {
    const timedOut = e instanceof Error && e.message === "AI_TIMEOUT";
    return timedOut
      ? aiError(frame, adapter, "E_PROC_TIMEOUT", "performance", `infer quá ${timeoutMs}ms`, true)
      : aiError(frame, adapter, "E_PROC_BAD_OUTPUT", "algorithm", `adapter ném lỗi: ${String(e)}`, true);
  }

  // SAU: không tin đầu ra của model, kiểm trước khi đưa sang fd_flow
  if (!result || result.kind !== "DetectionResult" || !Array.isArray(result.items)) {
    return aiError(frame, adapter, "E_PROC_BAD_OUTPUT", "contract", "đầu ra không phải DetectionResult", true);
  }
  if (result.trace_id !== frame.trace_id || result.frame_id !== frame.frame_id) {
    return aiError(frame, adapter, "E_PROC_BAD_OUTPUT", "contract", "kết quả không khớp trace_id/frame_id của ảnh vào", true);
  }
  if (result.items.some((it) => !(it.conf >= 0 && it.conf <= 1))) {
    return aiError(frame, adapter, "E_PROC_BAD_OUTPUT", "algorithm", "conf nằm ngoài [0,1]", true);
  }
  return { ok: true, result };
}

// ---------- Giao diện mọi node phải có ----------
export interface NodeContract {
  id: NodeId;
  health(): "OK" | "DEGRADED" | "DOWN";
  selfTest(): Promise<{ ok: boolean; detail: string }>;   // dùng cho workbench
}

// ---------- Luật luồng ----------
export const FLOW_RULES = {
  noDirectImportBetweenNodes: true,     // chỉ nói qua fd_flow
  edgeTypeCheck: true,                  // sai kiểu -> E_FLOW_EDGE_TYPE kèm tên cạnh
  neverSwallowErrors: true,
  degradedModeAllowedOn: ["connect.esp32"] as NodeId[],   // mất ESP32 không được làm sập luồng cam
};

// ---------- Cấu hình timeline mặc định (đổi trong fd_config, không sửa ở đây) ----------
export const DEFAULTS = {
  baselineSeconds: [5, 10] as [number, number],
  preEventMs: 2000,
  mode: "sim" as "sim" | "rasp",
};

// ---------- Ảnh hưởng thay đổi (cho fd_registry) ----------
/** Khi nhánh X đổi, các nhánh sau phải chạy lại test. Tự suy từ FLOW_TREE + phần dưới nếu cần. */
export const AFFECTS: Partial<Record<NodeId, NodeId[]>> = {
  "signal": ["process.sampler", "process.baseline", "process.ai", "process.rules", "response"],
  "process.ai": ["process.rules", "response"],
  "flow": ["connect.camera", "connect.esp32", "signal", "process.sampler", "response"],
};
