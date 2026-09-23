import React, { useState, useEffect } from "react";
import mockDrivers from "../../../mock/mockDrivers";

function DashboardPage() {
  const [fleetStatus, setFleetStatus] = useState([]);
  useEffect(() => {
    const activeFleet = mockDrivers.map((driver) => ({
      ...driver,
      kinh_do: 106.6881 + Math.random() * 0.01,
      vi_do: 20.8449 + Math.random() * 0.01,
    }));
    setFleetStatus(activeFleet);
  }, []);

  const totalVehicles = fleetStatus.length;
  const safeVehicles = fleetStatus.filter(
    (v) => v.cap_canh_bao === "NORMAL",
  ).length;
  const warningVehicles = fleetStatus.filter(
    (v) => v.cap_canh_bao === "CANH_CAO_VANG",
  ).length;
  const dangerVehicles = fleetStatus.filter(
    (v) => v.cap_canh_bao === "BAO_DONG_DO",
  ).length;

  const getStatusColor = (status) => {
    if (status === "NORMAL") return "bg-alert-safe text-white";
    if (status === "CANH_CAO_VANG") return "bg-alert-warning text-white";
    if (status === "BAO_DONG_DO") return "bg-alert-danger text-white";
    return "bg-gray-200 text-gray-800";
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* HEADER */}
      <div>
        <h2 className="text-brand-dark text-2xl font-bold">
          Giám sát thời gian thực
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Theo dõi trạng thái an toàn của toàn bộ đội xe
        </p>
      </div>
      {/* SUMMARY CARDS */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-4">
        <div className="flex items-center gap-4 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-100 text-2xl">
            🚚
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">
              Tổng xe đang chạy
            </p>
            <p className="text-brand-dark text-2xl font-bold">
              {totalVehicles}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-100 text-2xl">
            ✅
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">An toàn</p>
            <p className="text-alert-safe text-2xl font-bold">{safeVehicles}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-yellow-100 text-2xl">
            ⚠️
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">
              Cảnh báo (Ngáp/CO2)
            </p>
            <p className="text-alert-warning text-2xl font-bold">
              {warningVehicles}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-2xl">
            🚨
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">
              Báo động (Ngủ gật)
            </p>
            <p className="text-alert-danger text-2xl font-bold">
              {dangerVehicles}
            </p>
          </div>
        </div>
      </div>
      <div className="grid h-[500px] grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="flex flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-sm lg:col-span-2">
          <div className="border-b border-gray-100 bg-gray-50 p-4">
            <h3 className="text-brand-dark font-bold">
              Bản đồ Định vị (Live Map)
            </h3>
          </div>
          <div className="relative flex flex-1 items-center justify-center bg-slate-200">
            <p className="flex flex-col items-center gap-2 font-medium text-slate-500">
              <span className="text-4xl">🗺️</span>
              Khu vực tích hợp Google Maps / Leaflet
            </p>
            <div className="bg-alert-safe absolute top-1/4 left-1/3 h-4 w-4 animate-pulse rounded-full border-2 border-white shadow-lg"></div>
            <div className="bg-alert-danger absolute top-1/2 left-2/3 h-4 w-4 animate-bounce rounded-full border-2 border-white shadow-lg"></div>
          </div>
        </div>
        <div className="flex flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50 p-4">
            <h3 className="text-brand-dark font-bold">Trạng thái Đội xe</h3>
            <span className="animate-pulse rounded-full bg-green-100 px-2 py-1 text-xs font-bold text-green-700">
              Live 🟢
            </span>
          </div>

          <div className="flex-1 overflow-y-auto p-2">
            {fleetStatus.map((vehicle) => (
              <div
                key={vehicle.id}
                className="mb-2 flex cursor-pointer items-center justify-between rounded-xl border border-transparent p-3 transition-colors hover:border-gray-100 hover:bg-gray-50"
              >
                <div>
                  <p className="text-brand-dark font-bold">
                    {vehicle.xe_hien_tai || "Chưa xếp xe"}
                  </p>
                  <p className="text-xs text-gray-500">{vehicle.ho_ten}</p>
                </div>
                <div
                  className={`rounded-full px-3 py-1 text-xs font-bold ${getStatusColor(vehicle.cap_canh_bao)}`}
                >
                  {vehicle.cap_canh_bao === "NORMAL"
                    ? "An toàn"
                    : vehicle.cap_canh_bao === "CANH_CAO_VANG"
                      ? "Cảnh báo"
                      : "Nguy hiểm"}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default DashboardPage;
