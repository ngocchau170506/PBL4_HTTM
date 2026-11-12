import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code,
  Copy,
  FileDiff,
  Layers,
  MinusCircle,
  PlusCircle,
} from "lucide-react";
import React, { useState } from "react";
import toast from "react-hot-toast";

function safeParse(val) {
  if (!val) return {};
  if (typeof val === "object") return val;
  try {
    return JSON.parse(val);
  } catch {
    return { raw: String(val) };
  }
}

export default function JsonDiffViewer({ before, after, title = "Chi tiết Biến động Dữ liệu" }) {
  const [viewMode, setViewMode] = useState("VISUAL"); // "VISUAL" | "SIDE_BY_SIDE" | "RAW"
  const [showUnchanged, setShowUnchanged] = useState(false);

  const beforeObj = safeParse(before);
  const afterObj = safeParse(after);

  const allKeys = Array.from(
    new Set([...Object.keys(beforeObj || {}), ...Object.keys(afterObj || {})])
  );

  const diffItems = allKeys.map((key) => {
    const hasBefore = Object.prototype.hasOwnProperty.call(beforeObj, key);
    const hasAfter = Object.prototype.hasOwnProperty.call(afterObj, key);

    const valBefore = beforeObj[key];
    const valAfter = afterObj[key];

    let type = "UNCHANGED";
    if (hasBefore && !hasAfter) {
      type = "REMOVED";
    } else if (!hasBefore && hasAfter) {
      type = "ADDED";
    } else if (JSON.stringify(valBefore) !== JSON.stringify(valAfter)) {
      type = "MODIFIED";
    }

    return {
      key,
      type,
      before: valBefore,
      after: valAfter,
    };
  });

  const modifiedCount = diffItems.filter((i) => i.type === "MODIFIED").length;
  const addedCount = diffItems.filter((i) => i.type === "ADDED").length;
  const removedCount = diffItems.filter((i) => i.type === "REMOVED").length;
  const unchangedCount = diffItems.filter((i) => i.type === "UNCHANGED").length;

  const displayItems = showUnchanged
    ? diffItems
    : diffItems.filter((i) => i.type !== "UNCHANGED");

  const copyToClipboard = (data, label) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success(`Đã sao chép ${label} vào bộ nhớ tạm!`, { icon: "📋" });
  };

  const formatValue = (v) => {
    if (v === null) return "null";
    if (v === undefined) return "undefined";
    if (typeof v === "object") return JSON.stringify(v);
    if (typeof v === "boolean") return v ? "true" : "false";
    return String(v);
  };

  return (
    <div className="space-y-4">
      {/* TOOLBAR & VIEW MODE SELECTOR */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-brand-border-dark pb-3">
        <div className="flex items-center gap-2">
          <FileDiff className="h-4 w-4 text-brand-blue-sky" />
          <span className="text-xs font-bold text-brand-text-primary">{title}</span>
        </div>

        <div className="flex items-center gap-1 rounded-xl border border-brand-border-dark bg-brand-dark/80 p-1">
          <button
            type="button"
            onClick={() => setViewMode("VISUAL")}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === "VISUAL"
                ? "bg-brand-primary text-white shadow-sm"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            <Layers className="h-3 w-3" />
            <span>Trường thay đổi</span>
          </button>

          <button
            type="button"
            onClick={() => setViewMode("SIDE_BY_SIDE")}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === "SIDE_BY_SIDE"
                ? "bg-brand-primary text-white shadow-sm"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            <Code className="h-3 w-3" />
            <span>So sánh 2 cột</span>
          </button>

          <button
            type="button"
            onClick={() => setViewMode("RAW")}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-bold transition-all ${
              viewMode === "RAW"
                ? "bg-brand-primary text-white shadow-sm"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            <span>Raw JSON</span>
          </button>
        </div>
      </div>

      {/* STATS BADGES */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {modifiedCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-950/40 px-2 py-0.5 font-bold text-amber-300">
            ~ {modifiedCount} chỉnh sửa
          </span>
        )}
        {addedCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-950/40 px-2 py-0.5 font-bold text-emerald-300">
            + {addedCount} trường mới
          </span>
        )}
        {removedCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 bg-rose-950/40 px-2 py-0.5 font-bold text-rose-300">
            - {removedCount} đã xóa
          </span>
        )}
        {modifiedCount === 0 && addedCount === 0 && removedCount === 0 && (
          <span className="inline-flex items-center gap-1 rounded-md border border-brand-border-dark bg-brand-surface px-2 py-0.5 text-brand-text-muted">
            <CheckCircle2 className="h-3 w-3 text-emerald-400" /> Không có thay đổi
          </span>
        )}

        {unchangedCount > 0 && (
          <button
            type="button"
            onClick={() => setShowUnchanged(!showUnchanged)}
            className="ml-auto text-[10px] text-brand-text-subtle underline hover:text-brand-blue-sky"
          >
            {showUnchanged ? "Ẩn trường không đổi" : `Hiện ${unchangedCount} trường giữ nguyên`}
          </button>
        )}
      </div>

      {/* VIEW 1: VISUAL FIELD DIFF (RECOMMENDED FOR QUICK AUDIT) */}
      {viewMode === "VISUAL" && (
        <div className="space-y-2">
          {displayItems.length === 0 ? (
            <div className="rounded-xl border border-brand-border-dark bg-brand-dark/40 py-8 text-center text-xs text-brand-text-subtle">
              Hai đối tượng dữ liệu hoàn toàn đồng nhất, không phát sinh sai lệch.
            </div>
          ) : (
            displayItems.map((item) => {
              if (item.type === "MODIFIED") {
                return (
                  <div
                    key={item.key}
                    className="rounded-xl border border-amber-500/30 bg-amber-950/15 p-3 text-xs"
                  >
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="font-mono font-bold text-amber-300">
                        {item.key}
                      </span>
                      <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-400">
                        THAY ĐỔI
                      </span>
                    </div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <div className="rounded-lg border border-rose-500/20 bg-rose-950/30 p-2">
                        <span className="block text-[10px] font-semibold text-rose-400 uppercase">
                          Trước
                        </span>
                        <code className="font-mono text-rose-200">
                          {formatValue(item.before)}
                        </code>
                      </div>
                      <div className="rounded-lg border border-emerald-500/20 bg-emerald-950/30 p-2">
                        <span className="block text-[10px] font-semibold text-emerald-400 uppercase">
                          Sau
                        </span>
                        <code className="font-mono font-bold text-emerald-200">
                          {formatValue(item.after)}
                        </code>
                      </div>
                    </div>
                  </div>
                );
              }

              if (item.type === "ADDED") {
                return (
                  <div
                    key={item.key}
                    className="rounded-xl border border-emerald-500/30 bg-emerald-950/15 p-3 text-xs"
                  >
                    <div className="mb-1 flex items-center justify-between">
                      <span className="font-mono font-bold text-emerald-300">
                        + {item.key}
                      </span>
                      <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-400">
                        THÊM MỚI
                      </span>
                    </div>
                    <code className="font-mono text-emerald-200">
                      {formatValue(item.after)}
                    </code>
                  </div>
                );
              }

              if (item.type === "REMOVED") {
                return (
                  <div
                    key={item.key}
                    className="rounded-xl border border-rose-500/30 bg-rose-950/15 p-3 text-xs"
                  >
                    <div className="mb-1 flex items-center justify-between">
                      <span className="font-mono font-bold text-rose-300">
                        - {item.key}
                      </span>
                      <span className="rounded bg-rose-500/20 px-1.5 py-0.5 text-[10px] font-bold text-rose-400">
                        ĐÃ XÓA
                      </span>
                    </div>
                    <code className="font-mono text-rose-200 line-through">
                      {formatValue(item.before)}
                    </code>
                  </div>
                );
              }

              // UNCHANGED
              return (
                <div
                  key={item.key}
                  className="flex items-center justify-between rounded-xl border border-brand-border-dark bg-brand-dark/40 px-3 py-2 text-xs opacity-60"
                >
                  <span className="font-mono text-brand-text-muted">{item.key}</span>
                  <code className="font-mono text-brand-text-subtle">
                    {formatValue(item.before)}
                  </code>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* VIEW 2: SIDE-BY-SIDE 2-COLUMN VIEW */}
      {viewMode === "SIDE_BY_SIDE" && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {/* CỘT TRƯỚC */}
          <div className="rounded-2xl border border-rose-500/30 bg-rose-950/20 p-3.5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase text-rose-400">
                Giá trị trước thay đổi
              </span>
              <button
                type="button"
                onClick={() => copyToClipboard(beforeObj, "Giá trị trước")}
                className="text-rose-400 hover:text-white"
                title="Sao chép"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <pre className="max-h-80 overflow-auto font-mono text-[11px] leading-relaxed text-rose-200">
              {JSON.stringify(beforeObj, null, 2)}
            </pre>
          </div>

          {/* CỘT SAU */}
          <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-3.5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase text-emerald-400">
                Giá trị sau thay đổi
              </span>
              <button
                type="button"
                onClick={() => copyToClipboard(afterObj, "Giá trị sau")}
                className="text-emerald-400 hover:text-white"
                title="Sao chép"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <pre className="max-h-80 overflow-auto font-mono text-[11px] leading-relaxed text-emerald-200">
              {JSON.stringify(afterObj, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* VIEW 3: RAW JSON CODE */}
      {viewMode === "RAW" && (
        <div className="space-y-3">
          <div className="rounded-2xl border border-brand-border-dark bg-brand-dark/80 p-3.5">
            <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-brand-text-subtle">
              Full Object Diff JSON
            </p>
            <pre className="max-h-80 overflow-auto font-mono text-[11px] text-brand-blue-light">
              {JSON.stringify(
                {
                  _diff_summary: {
                    modified: modifiedCount,
                    added: addedCount,
                    removed: removedCount,
                  },
                  before: beforeObj,
                  after: afterObj,
                },
                null,
                2
              )}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
