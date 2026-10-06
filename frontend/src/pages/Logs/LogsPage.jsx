import React, { useState } from "react";
import toast from "react-hot-toast";
import mockViolations from "../../mock/mockViolations.json";

function LogsPage() {
  const [violations, setViolations] = useState(mockViolations);
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedViolation, setSelectedViolation] = useState(null);

  const getViolationBadge = (type) => {
    switch (type) {
      case "BUON_NGU":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-bold text-red-700">
            <span>🚨</span> Buồn ngủ gật
          </span>
        );
      case "CO2_CAO":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-bold text-amber-700">
            <span>⚠️</span> CO2 vượt ngưỡng
          </span>
        );
      case "DIEN_THOAI":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-bold text-blue-700">
            <span>📱</span> Dùng điện thoại
          </span>
        );
      case "MET_MOI_NGAP":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-purple-200 bg-purple-50 px-3 py-1 text-xs font-bold text-purple-700">
            <span>🥱</span> Mệt mỏi / Ngáp
          </span>
        );
      default:
        return (
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-bold text-gray-700">
            {type}
          </span>
        );
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case "PENDING_MANUAL_REVIEW":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-bold text-amber-700">
            <span className="h-2 w-2 animate-ping rounded-full bg-amber-500"></span>
            Chờ duyệt
          </span>
        );
      case "CONFIRMED":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">
            <span>✔️</span> Đã xác nhận
          </span>
        );
      case "REJECTED":
        return (
          <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-100 px-3 py-1 text-xs font-bold text-gray-500">
            <span>❌</span> Đã từ chối
          </span>
        );
      default:
        return <span>{status}</span>;
    }
  };

  const handleUpdateStatus = (id, newStatus) => {
    setViolations((prev) =>
      prev.map((v) => (v.id === id ? { ...v, status: newStatus, trang_thai: newStatus } : v))
    );
    if (selectedViolation && selectedViolation.id === id) {
      setSelectedViolation((prev) => ({ ...prev, trang_thai: newStatus }));
    }
    const message =
      newStatus === "CONFIRMED"
        ? `Đã xác nhận sự kiện vi phạm #${id}!`
        : `Đã bác bỏ sự kiện vi phạm #${id}!`;
    toast.success(message);
  };

  const filteredViolations = violations.filter((v) => {
    if (statusFilter !== "ALL" && v.trang_thai !== statusFilter) return false;
    if (searchTerm) {
      const query = searchTerm.toLowerCase();
      const matchName = v.driver_name?.toLowerCase().includes(query);
      const matchPlate = v.vehicle_plate?.toLowerCase().includes(query);
      const matchId = String(v.id).includes(query);
      return matchName || matchPlate || matchId;
    }
    return true;
  });

  const pendingCount = violations.filter((v) => v.trang_thai === "PENDING_MANUAL_REVIEW").length;
  const confirmedCount = violations.filter((v) => v.trang_thai === "CONFIRMED").length;
  const rejectedCount = violations.filter((v) => v.trang_thai === "REJECTED").length;

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* HEADER */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-2xl font-bold text-slate-800">
            Lịch sử Cảnh báo & Vi phạm IoT
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Giám sát cảnh báo cảm biến Edge AI, bằng chứng video và duyệt vi phạm lái xe
          </p>
        </div>
      </div>

      {/* STATS CARDS */}
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Tổng sự kiện</p>
          <p className="mt-2 text-3xl font-extrabold text-slate-800">{violations.length}</p>
          <p className="mt-1 text-xs text-slate-500">Ghi nhận từ các camera AI</p>
        </div>
        <div className="rounded-2xl border border-amber-100 bg-amber-50/50 p-5 shadow-sm">
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider">Chờ duyệt thủ công</p>
          <p className="mt-2 text-3xl font-extrabold text-amber-600">{pendingCount}</p>
          <p className="mt-1 text-xs text-amber-600/80">Cần người điều hành xác minh</p>
        </div>
        <div className="rounded-2xl border border-emerald-100 bg-emerald-50/50 p-5 shadow-sm">
          <p className="text-xs font-semibold text-emerald-700 uppercase tracking-wider">Đã xác nhận</p>
          <p className="mt-2 text-3xl font-extrabold text-emerald-600">{confirmedCount}</p>
          <p className="mt-1 text-xs text-emerald-600/80">Trừ điểm uy tín tự động</p>
        </div>
        <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Bác bỏ (False Alarm)</p>
          <p className="mt-2 text-3xl font-extrabold text-slate-600">{rejectedCount}</p>
          <p className="mt-1 text-xs text-slate-500">Không tính điểm phạt</p>
        </div>
      </div>

      {/* MAIN CARD */}
      <div className="rounded-3xl border border-gray-100 bg-white p-6 shadow-sm">
        {/* FILTERS & SEARCH */}
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-gray-100 pb-4">
          <div className="flex flex-wrap gap-2">
            {[
              { key: "ALL", label: "Tất cả" },
              { key: "PENDING_MANUAL_REVIEW", label: `Chờ duyệt (${pendingCount}) ⏳` },
              { key: "CONFIRMED", label: `Đã xác nhận (${confirmedCount}) ✔️` },
              { key: "REJECTED", label: `Đã từ chối (${rejectedCount})` },
            ].map((tab) => (
              <button
                key={tab.key}
                onClick={() => setStatusFilter(tab.key)}
                className={`rounded-xl px-4 py-2 text-xs font-bold transition-all ${
                  statusFilter === tab.key
                    ? "bg-slate-900 text-white shadow-sm"
                    : "bg-gray-50 text-slate-600 hover:bg-gray-100"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="w-full sm:w-64">
            <input
              type="text"
              placeholder="🔍 Tìm tài xế, biển số..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-2 text-xs outline-none focus:border-brand-blue focus:bg-white focus:ring-1 focus:ring-brand-blue"
            />
          </div>
        </div>

        {/* TABLE */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/80 text-xs font-semibold tracking-wider text-slate-500 uppercase">
                <th className="rounded-tl-xl px-4 py-4">Mã sự kiện</th>
                <th className="px-4 py-4">Thời gian</th>
                <th className="px-4 py-4">Tài xế & Xe</th>
                <th className="px-4 py-4">Hành vi phát hiện</th>
                <th className="px-4 py-4 text-center">Độ tin cậy AI</th>
                <th className="px-4 py-4 text-center">Điểm trừ</th>
                <th className="px-4 py-4">Trạng thái</th>
                <th className="rounded-tr-xl px-4 py-4 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filteredViolations.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-400">
                    Không tìm thấy bản ghi cảnh báo nào.
                  </td>
                </tr>
              ) : (
                filteredViolations.map((v) => (
                  <tr key={v.id} className="transition-colors hover:bg-slate-50/60">
                    {/* ID */}
                    <td className="px-4 py-4 font-mono font-bold text-slate-700">
                      #{v.id}
                    </td>

                    {/* Time */}
                    <td className="px-4 py-4 text-xs text-slate-600">
                      <div>{new Date(v.thoi_gian_vi_pham).toLocaleDateString("vi-VN")}</div>
                      <div className="text-slate-400 font-mono">
                        {new Date(v.thoi_gian_vi_pham).toLocaleTimeString("vi-VN")}
                      </div>
                    </td>

                    {/* Driver & Vehicle */}
                    <td className="px-4 py-4">
                      <p className="font-bold text-slate-800">{v.driver_name}</p>
                      <p className="font-mono text-xs text-slate-500">
                        {v.vehicle_plate} • Ca #{v.trip_id}
                      </p>
                    </td>

                    {/* Violation Type */}
                    <td className="px-4 py-4">
                      {getViolationBadge(v.loai_vi_pham)}
                    </td>

                    {/* Confidence */}
                    <td className="px-4 py-4 text-center">
                      <div className="inline-block font-mono text-xs font-bold text-slate-700">
                        {(v.ai_confidence_score * 100).toFixed(0)}%
                      </div>
                      <div className="mx-auto mt-1 h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={`h-full ${
                            v.ai_confidence_score > 0.8
                              ? "bg-emerald-500"
                              : v.ai_confidence_score > 0.6
                                ? "bg-amber-500"
                                : "bg-red-400"
                          }`}
                          style={{ width: `${v.ai_confidence_score * 100}%` }}
                        ></div>
                      </div>
                    </td>

                    {/* Penalty */}
                    <td className="px-4 py-4 text-center font-bold text-alert-danger">
                      -{v.diem_tru}đ
                    </td>

                    {/* Status */}
                    <td className="px-4 py-4">
                      {getStatusBadge(v.trang_thai)}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-4 text-right">
                      <button
                        onClick={() => setSelectedViolation(v)}
                        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-brand-blue shadow-sm hover:bg-blue-50 hover:border-blue-200 transition-all"
                      >
                        Chi tiết / Video
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* DETAIL / REVIEW MODAL */}
      {selectedViolation && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs">
          <div className="w-full max-w-2xl overflow-hidden rounded-3xl bg-white shadow-2xl animate-fade-in-up">
            <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-6 py-4">
              <div>
                <h3 className="text-lg font-bold text-slate-800">
                  Chi tiết Cảnh báo #{selectedViolation.id}
                </h3>
                <p className="text-xs text-slate-500">
                  Tài xế: {selectedViolation.driver_name} • Xe: {selectedViolation.vehicle_plate}
                </p>
              </div>
              <button
                onClick={() => setSelectedViolation(null)}
                className="rounded-full p-2 text-slate-400 hover:bg-gray-200 hover:text-slate-600 transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="p-6 space-y-4 max-h-[75vh] overflow-y-auto">
              {/* VIDEO CLIP OR SENSOR NOTE */}
              {selectedViolation.video_url ? (
                <div className="overflow-hidden rounded-2xl bg-black">
                  <video
                    src={selectedViolation.video_url}
                    controls
                    className="w-full max-h-72 object-contain"
                  />
                  <div className="bg-slate-900 p-2 text-center text-xs text-slate-300">
                    📹 Đoạn cắt bằng chứng camera buồng lái ({selectedViolation.video_duration}s)
                  </div>
                </div>
              ) : (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-center">
                  <span className="text-2xl">📡</span>
                  <p className="mt-1 text-sm font-bold text-amber-800">Cảnh báo Cảm biến Môi trường (CO2/Khí độc)</p>
                  <p className="text-xs text-amber-700">Dữ liệu telemetry ghi nhận từ cảm biến IoT buồng lái, không có video đính kèm.</p>
                </div>
              )}

              {/* DETAILS GRID */}
              <div className="grid grid-cols-2 gap-4 rounded-2xl border border-gray-100 bg-slate-50/60 p-4 text-sm">
                <div>
                  <span className="text-xs text-slate-400">Loại vi phạm:</span>
                  <div className="mt-1">{getViolationBadge(selectedViolation.loai_vi_pham)}</div>
                </div>
                <div>
                  <span className="text-xs text-slate-400">Trạng thái hiện tại:</span>
                  <div className="mt-1">{getStatusBadge(selectedViolation.trang_thai)}</div>
                </div>
                <div>
                  <span className="text-xs text-slate-400">Thời điểm ghi nhận:</span>
                  <p className="font-semibold text-slate-700 mt-1">
                    {new Date(selectedViolation.thoi_gian_vi_pham).toLocaleString("vi-VN")}
                  </p>
                </div>
                <div>
                  <span className="text-xs text-slate-400">Điểm trừ quy định:</span>
                  <p className="font-bold text-alert-danger mt-1">
                    -{selectedViolation.diem_tru} điểm
                  </p>
                </div>
              </div>

              {/* DESCRIPTION */}
              <div className="rounded-2xl border border-blue-50 bg-blue-50/40 p-4">
                <p className="text-xs font-bold text-brand-blue uppercase tracking-wider">
                  Phân tích từ mô hình Edge AI (Độ tin cậy {(selectedViolation.ai_confidence_score * 100).toFixed(0)}%)
                </p>
                <p className="mt-1.5 text-sm text-slate-700 leading-relaxed">
                  {selectedViolation.mo_ta}
                </p>
              </div>
            </div>

            {/* MODAL FOOTER */}
            <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50 px-6 py-4">
              <button
                onClick={() => setSelectedViolation(null)}
                className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-xs font-bold text-slate-600 hover:bg-gray-100"
              >
                Đóng
              </button>

              <div className="flex gap-2">
                <button
                  onClick={() => handleUpdateStatus(selectedViolation.id, "REJECTED")}
                  disabled={selectedViolation.trang_thai === "REJECTED"}
                  className="rounded-xl border border-red-200 bg-white px-4 py-2 text-xs font-bold text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
                >
                  Bác bỏ (False Alarm)
                </button>
                <button
                  onClick={() => handleUpdateStatus(selectedViolation.id, "CONFIRMED")}
                  disabled={selectedViolation.trang_thai === "CONFIRMED"}
                  className="rounded-xl bg-brand-blue px-4 py-2 text-xs font-bold text-white shadow-md hover:bg-blue-800 disabled:opacity-50 transition-all"
                >
                  Xác nhận phạt tài xế
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default LogsPage;
