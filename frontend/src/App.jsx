import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { Toaster } from "react-hot-toast";
import MainLayout from "./components/layout/MainLayout";
import LoginPage from "./pages/Login/LoginPage";
import LogsPage from "./pages/Logs/components/LogsPage";
import DashboardPage from "./pages/Dashboard/components/DashboardPage";

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
        <Route
          path="/drivers"
          element={<Placeholder title=" Quản lý Tài xế & Điểm uy tín" />}
        />
        <Route
          path="/trips"
          element={<Placeholder title=" Quản lý Ca làm (Trips)" />}
        />
        <Route path="/logs" element={<LogsPage />} />
      </Route>
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
