import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { Toaster } from "react-hot-toast";
import MainLayout from "./components/layout/MainLayout";
import LoginPage from "./pages/Login/LoginPage";
import LogsPage from "./pages/Logs/LogsPage";
import DashboardPage from "./pages/Dashboard/DashboardPage";
import DriversPage from "./pages/Drivers/DriversPage";
import TripsPage from "./pages/Trips/TripsPage";
import VehiclesPage from "./pages/Vehicles/VehiclesPage";

// Component tạm để test giao diện
const Placeholder = ({ title }) => (
  <div className="text-olive p-8 text-2xl font-bold">{title}</div>
);

const ProtectedRoute = ({ children, allowedRoles }) => {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" />;
  if (allowedRoles && !allowedRoles.includes(user.role))
    return <Navigate to="/" />;
  return children;
};

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<MainLayout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/drivers" element={<DriversPage />} />
        <Route path="/trips" element={<TripsPage />} />
        <Route path="/vehicles" element={<VehiclesPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route
          path="/admin/ai-config"
          element={
            <ProtectedRoute allowedRoles={["ADMIN"]}>
              <div className="rounded-3xl border border-gray-100 bg-white p-8 shadow-sm">
                <h2 className="text-xl font-bold text-slate-800">Cấu hình tham số AI & Edge Telemetry</h2>
                <p className="mt-1 text-sm text-slate-500">Thiết lập ngưỡng phát hiện buồn ngủ, nồng độ CO2 và nhịp thở của tài xế.</p>
                <div className="mt-6 space-y-4 max-w-lg">
                  <div className="flex items-center justify-between p-4 rounded-xl bg-slate-50 border border-slate-100">
                    <div>
                      <p className="font-semibold text-slate-800 text-sm">Thời gian nhắm mắt báo động</p>
                      <p className="text-xs text-slate-400">Ngưỡng tính ngủ gật (giây)</p>
                    </div>
                    <span className="font-mono font-bold text-brand-blue bg-blue-50 px-3 py-1 rounded-lg">1.5s</span>
                  </div>
                  <div className="flex items-center justify-between p-4 rounded-xl bg-slate-50 border border-slate-100">
                    <div>
                      <p className="font-semibold text-slate-800 text-sm">Ngưỡng CO2 nguy hiểm</p>
                      <p className="text-xs text-slate-400">Nồng độ CO2 kích hoạt còi cabin (ppm)</p>
                    </div>
                    <span className="font-mono font-bold text-alert-warning bg-amber-50 px-3 py-1 rounded-lg">1200 ppm</span>
                  </div>
                  <div className="flex items-center justify-between p-4 rounded-xl bg-slate-50 border border-slate-100">
                    <div>
                      <p className="font-semibold text-slate-800 text-sm">Độ tin cậy AI tối thiểu</p>
                      <p className="text-xs text-slate-400">Ngưỡng tự động gửi bằng chứng video</p>
                    </div>
                    <span className="font-mono font-bold text-emerald-600 bg-emerald-50 px-3 py-1 rounded-lg">70%</span>
                  </div>
                </div>
              </div>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/audit-logs"
          element={
            <ProtectedRoute allowedRoles={["ADMIN"]}>
              <div className="rounded-3xl border border-gray-100 bg-white p-8 shadow-sm">
                <h2 className="text-xl font-bold text-slate-800">Nhật ký thao tác hệ thống</h2>
                <p className="mt-1 text-sm text-slate-500">Ghi nhận lịch sử đăng nhập, phân ca và can thiệp thủ công của ban quản trị.</p>
                <div className="mt-6 divide-y divide-gray-100 text-sm">
                  <div className="py-3 flex justify-between">
                    <span>Quản trị viên đăng nhập hệ thống</span>
                    <span className="text-xs text-slate-400 font-mono">Hôm nay, 08:30</span>
                  </div>
                  <div className="py-3 flex justify-between">
                    <span>Xác nhận vi phạm #{9931} - Tài xế Nguyễn Văn Hùng</span>
                    <span className="text-xs text-slate-400 font-mono">Hôm qua, 15:42</span>
                  </div>
                  <div className="py-3 flex justify-between">
                    <span>Điều phối ca chạy Đà Nẵng - Quy Nhơn</span>
                    <span className="text-xs text-slate-400 font-mono">Hôm qua, 11:20</span>
                  </div>
                </div>
              </div>
            </ProtectedRoute>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
        <Toaster />
      </BrowserRouter>
    </AuthProvider>
  );
}
