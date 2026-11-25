import { Toaster } from "react-hot-toast";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import ErrorBoundary from "./components/common/ErrorBoundary";
import MainLayout from "./components/layout/MainLayout";
import { AuthProvider, useAuth } from "./context/AuthContext";
import AdminAiConfigPage from "./pages/Admin/AdminAiConfigPage";
import AdminAuditLogsPage from "./pages/Admin/AdminAuditLogsPage";
import DashboardPage from "./pages/Dashboard/DashboardPage";
import DriversPage from "./pages/Drivers/DriversPage";
import LoginPage from "./pages/Login/LoginPage";
import LogsPage from "./pages/Logs/LogsPage";
import MapPage from "./pages/Map/MapPage";
import TripsPage from "./pages/Trips/TripsPage";
import VehiclesPage from "./pages/Vehicles/VehiclesPage";

const ProtectedRoute = ({ children, allowedRoles }) => {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" />;
  if (
    allowedRoles &&
    !allowedRoles.map((r) => r.toUpperCase()).includes(user.role?.toUpperCase())
  ) {
    return <Navigate to="/" />;
  }
  return children;
};

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/map" element={<MapPage />} />
        <Route path="/drivers" element={<DriversPage />} />
        <Route path="/trips" element={<TripsPage />} />
        <Route path="/vehicles" element={<VehiclesPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route
          path="/admin/ai-config"
          element={
            <ProtectedRoute allowedRoles={["ADMIN"]}>
              <AdminAiConfigPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/audit-logs"
          element={
            <ProtectedRoute allowedRoles={["ADMIN"]}>
              <AdminAuditLogsPage />
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
    <ErrorBoundary>
      <AuthProvider>
        <BrowserRouter>
          <AppRoutes />
          <Toaster />
        </BrowserRouter>
      </AuthProvider>
    </ErrorBoundary>
  );
}
