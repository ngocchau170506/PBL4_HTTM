import React, { useState } from "react";
import toast from "react-hot-toast";
import mockShifts from "../../mock/mockShifts.json";

function TripsPage() {
  const [shifts, setShifts] = useState(mockShifts);
  const [filterStatus, setFilterStatus] = useState("ALL");

  const formatDateTime = (isoString) => {
    if (!isoString) return "--:--";
    const date = new Date(isoString);
    return date.toLocaleString("vi-VN", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
    });
  };

  // Hàm tạo nhãn trạng thái ca làm việc
  const getStatusBadge = (status) => {
    switch (status) {
      case "IN_PROGRESS":
        return (
          <span className="flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">
            <span className="h-2 w-2 animate-ping rounded-full bg-emerald-500"></span>
            Đang chạy
          </span>
        );
      case "SCHEDULED":
        return (
          <span className="text-brand-blue rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-bold">
            Đã xếp ca
          </span>
        );
      case "COMPLETED":
        return (
          <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-bold text-gray-600">
            Hoàn thành
          </span>
        );
      case "CANCELLED":
      case "DECLINED":
        return (
          <span className="text-alert-danger rounded-full border border-red-200 bg-red-50 px-3 py-1 text-xs font-bold">
            Đã hủy
          </span>
        );
      default:
        return <span>{status}</span>;
    }
  };

  // Lọc danh sách ca theo tab
  const filteredShifts = shifts.filter((shift) => {
    if (filterStatus === "ALL") return true;
    return shift.trang_thai === filterStatus;
  });

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* HEADER TRANG */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-2xl font-bold text-slate-800">
            Quản lý Ca làm việc
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Lập lịch, điều phối và kiểm soát các chuyến xe vận hành
          </p>
        </div>

        <button className="bg-brand-blue flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-bold text-white shadow-md transition-all hover:-translate-y-0.5 hover:bg-blue-800 hover:shadow-lg">
          <span>➕</span> Lập ca phân công mới
        </button>
      </div>

      {/* KHU VỰC BẢNG DỮ LIỆU */}
      <div className="rounded-3xl border border-gray-100 bg-white p-6 shadow-sm">
        {/* THANH BỘ LỌC TRẠNG THÁI (TABS) */}
        <div className="mb-6 flex gap-2 border-b border-gray-100 pb-4">
          {[
            { key: "ALL", label: "Tất cả ca" },
            { key: "IN_PROGRESS", label: "Đang chạy 🟢" },
            { key: "SCHEDULED", label: "Sắp tới 🗓️" },
            { key: "COMPLETED", label: "Đã hoàn thành ✔️" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilterStatus(tab.key)}
              className={`rounded-xl px-4 py-2 text-xs font-bold transition-all ${
                filterStatus === tab.key
                  ? "bg-slate-900 text-white shadow-sm"
                  : "bg-gray-50 text-slate-600 hover:bg-gray-100"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* BẢNG HIỂN THỊ */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/80 text-xs font-semibold tracking-wider text-slate-500 uppercase">
                <th className="rounded-tl-xl px-4 py-4">Mã ca</th>
                <th className="px-4 py-4">Tài xế & Phương tiện</th>
                <th className="px-4 py-4">Tuyến đường</th>
                <th className="px-4 py-4">Thời gian dự kiến</th>
                <th className="px-4 py-4">Cảnh báo sinh học</th>
                <th className="px-4 py-4">Trạng thái</th>
                <th className="rounded-tr-xl px-4 py-4 text-right">
                  Hành động
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filteredShifts.map((shift) => (
                <tr
                  key={shift.id}
                  className="transition-colors hover:bg-slate-50/60"
                >
                  {/* Mã ca */}
                  <td className="px-4 py-4 font-mono font-bold text-slate-700">
                    #{shift.id}
                  </td>

                  {/* Tài xế & Xe */}
                  <td className="px-4 py-4">
                    <p className="font-bold text-slate-800">
                      {shift.driver.ho_ten}
                    </p>
                    <p className="font-mono text-xs text-slate-500">
                      Xe:{" "}
                      <span className="font-semibold text-slate-700">
                        {shift.vehicle.bien_so}
                      </span>{" "}
                      ({shift.vehicle.loai_xe})
                    </p>
                  </td>

                  {/* Tuyến đường */}
                  <td className="px-4 py-4">
                    <p className="font-semibold text-slate-800">
                      {shift.route.ten_tuyen}
                    </p>
                    <p className="text-xs text-slate-400">
                      {shift.route.khoang_cach_km} km
                    </p>
                  </td>

                  {/* Thời gian */}
                  <td className="px-4 py-4 text-xs text-slate-600">
                    <div>
                      Bắt đầu:{" "}
                      <span className="font-semibold text-slate-800">
                        {formatDateTime(shift.thoi_gian_bat_dau_du_kien)}
                      </span>
                    </div>
                    <div className="mt-0.5">
                      Kết thúc:{" "}
                      <span className="font-semibold text-slate-800">
                        {formatDateTime(shift.thoi_gian_ket_thuc_du_kien)}
                      </span>
                    </div>
                  </td>

                  {/* Cảnh báo sinh học thông minh */}
                  <td className="px-4 py-4">
                    {shift.canh_bao_khi_tao.length > 0 ? (
                      <div className="flex flex-col gap-1">
                        {shift.canh_bao_khi_tao.map((alert, idx) => (
                          <span
                            key={idx}
                            className="inline-flex items-center gap-1 rounded-lg border border-amber-200/60 bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-800"
                          >
                            <span>⚠️</span> {alert.ly_do}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs font-medium text-emerald-600">
                        ✅ Thể trạng tốt
                      </span>
                    )}
                  </td>

                  {/* Trạng thái */}
                  <td className="px-4 py-4">
                    {getStatusBadge(shift.trang_thai)}
                  </td>

                  {/* Hành động */}
                  <td className="px-4 py-4 text-right">
                    <button
                      onClick={() => toast(`Chi tiết ca #${shift.id}: Tuyến ${shift.route.ten_tuyen}`, { icon: 'ℹ️' })}
                      className="text-brand-blue text-xs font-bold hover:underline"
                    >
                      Chi tiết
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default TripsPage;
