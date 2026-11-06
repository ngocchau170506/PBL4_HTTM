import {
  AlertOctagon,
  BarChart3,
  CalendarClock,
  LayoutDashboard,
  LogOut,
  Map,
  PhoneCall,
  Radio,
  Search,
  ShieldAlert,
  Sliders,
  Truck,
  Users,
  X,
  Zap,
} from "lucide-react";
import { useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import logoImg from "../../assets/Logo.png";
import { useAuth } from "../../context/AuthContext";
import socketClient from "../../services/socketClient";
import { useFleetStore } from "../../store/useFleetStore";
import NotificationBell from "../notification/NotificationBell";

const MainLayout = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  const {
    globalSearchQuery,
    setGlobalSearchQuery,
    socketStatus,
    activeAlert,
    dismissAlert,
    syncFleetData,
    setSocketStatus,
    handleTelemetrySocket,
    handleNewViolationSocket,
    handleDeviceHealthSocket,
    tickRealtimeTelemetry,
  } = useFleetStore();

  useEffect(() => {
    syncFleetData();
    const token = localStorage.getItem("token");
    socketClient.connect(token, {
      setSocketStatus,
      handleTelemetrySocket,
      handleNewViolationSocket,
      handleDeviceHealthSocket,
      tickRealtimeTelemetry,
      syncFleetData,
    });

    return () => {
      socketClient.disconnect();
    };
  }, [
    syncFleetData,
    setSocketStatus,
    handleTelemetrySocket,
    handleNewViolationSocket,
    handleDeviceHealthSocket,
    tickRealtimeTelemetry,
  ]);

  const handleLogout = () => {
    socketClient.disconnect();
    logout();
    navigate("/login");
  };

  const handleSimulateSocketAlert = () => {
    socketClient.emitSimulatedViolation({
      handleNewViolationSocket,
    });
  };

  const isActive = (path) => {
    if (path === "/" && location.pathname === "/") return true;
    if (path === "/map" && location.pathname === "/map") return true;
    if (path !== "/" && path !== "/map" && location.pathname.startsWith(path))
      return true;
    return false;
  };

  const primaryNavItems = [
    {
      id: "overview",
      path: "/",
      label: "Tổng quan",
      tag: "Dashboard",
      icon: LayoutDashboard,
    },
    {
      id: "gps-map",
      path: "/map",
      label: "Bản đồ GPS",
      tag: "Live Tracking",
      icon: Map,
    },
    {
      id: "fleet",
      path: "/vehicles",
      label: "Quản lý đội xe",
      tag: "Fleet",
      icon: Truck,
    },
    {
      id: "violations",
      path: "/logs",
      label: "Xác thực Vi phạm AI",
      tag: "Violation",
      hasBadgeDot: true,
      icon: ShieldAlert,
    },
    {
      id: "drivers",
      path: "/drivers",
      label: "Quản lý tài xế",
      tag: "Biometrics",
      icon: Users,
    },
    {
      id: "shifts",
      path: "/trips",
      label: "Lịch Phân ca",
      tag: "Schedule",
      icon: CalendarClock,
    },
  ];

  const adminNavItems = [
    {
      id: "audit",
      path: "/admin/audit-logs",
      label: "Báo cáo & Audit Log",
      tag: "Reports",
      icon: BarChart3,
    },
    {
      id: "ai-config",
      path: "/admin/ai-config",
      label: "Cài đặt Ngưỡng AI",
      tag: "Settings",
      icon: Sliders,
    },
  ];

  return (
    <div className="text-brand-text-secondary flex h-screen overflow-hidden bg-transparent font-sans">
      {/* SIDEBAR NAVIGATION */}
      <aside className="bg-brand-dark/90 border-brand-border-dark z-20 flex w-20 shrink-0 flex-col items-center border-r py-4 shadow-xl backdrop-blur-md">
        <button
          onClick={() => navigate("/")}
          className="hover:bg-brand-surface/80 mb-5 flex h-12 w-12 cursor-pointer items-center justify-center rounded-2xl transition-all"
          title="Trang chủ FleetGuard"
        >
          <img
            src={logoImg}
            alt="Logo"
            className="h-9 w-9 object-contain drop-shadow"
          />
        </button>

        <nav className="flex w-full flex-1 flex-col items-center gap-2.5 overflow-x-hidden overflow-y-auto px-3">
          {primaryNavItems.map((item) => {
            const active = isActive(item.path);
            const Icon = item.icon;
            return (
              <div key={item.id} className="group relative flex justify-center">
                <button
                  onClick={() => navigate(item.path)}
                  className={`relative flex h-12 w-12 cursor-pointer items-center justify-center rounded-2xl transition-all ${
                    active
                      ? "border-brand-border-active from-brand-primary to-brand-primary-dark border bg-linear-to-br text-white shadow-lg shadow-blue-500/35"
                      : "text-brand-text-muted hover:bg-brand-surface/80 hover:text-brand-text-primary"
                  }`}
                  aria-label={item.label}
                >
                  <Icon className="h-5 w-5" />
                  {item.hasBadgeDot && (
                    <span className="absolute top-2 right-2 flex h-2.5 w-2.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75"></span>
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500"></span>
                    </span>
                  )}
                </button>
                <div className="pointer-events-none absolute top-1/2 left-full z-50 ml-3.5 hidden -translate-y-1/2 items-center group-hover:flex">
                  <div className="border-brand-border bg-brand-dark text-brand-text-primary flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold whitespace-nowrap shadow-2xl">
                    <span>{item.label}</span>
                    {item.tag && (
                      <span className="border-brand-primary/30 bg-brand-blue-night text-brand-blue-light rounded-md border px-1.5 py-0.5 text-[10px] font-bold">
                        {item.tag}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          <div className="bg-brand-border-dark my-1 h-px w-8" />

          {user?.role?.toLowerCase() === "admin" &&
            adminNavItems.map((item) => {
              const active = isActive(item.path);
              const Icon = item.icon;
              return (
                <div
                  key={item.id}
                  className="group relative flex justify-center"
                >
                  <button
                    onClick={() => navigate(item.path)}
                    className={`relative flex h-12 w-12 cursor-pointer items-center justify-center rounded-2xl transition-all ${
                      active
                        ? "border-brand-border-active from-brand-primary to-brand-primary-dark border bg-linear-to-br text-white shadow-lg shadow-blue-500/35"
                        : "text-brand-text-muted hover:bg-brand-surface/80 hover:text-brand-text-primary"
                    }`}
                    aria-label={item.label}
                  >
                    <Icon className="h-5 w-5" />
                  </button>
                  <div className="pointer-events-none absolute top-1/2 left-full z-50 ml-3.5 hidden -translate-y-1/2 items-center group-hover:flex">
                    <div className="border-brand-border bg-brand-dark text-brand-text-primary flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold whitespace-nowrap shadow-2xl">
                      <span>{item.label}</span>
                      {item.tag && (
                        <span className="border-brand-border bg-brand-surface text-brand-text-muted rounded-md border px-1.5 py-0.5 text-[10px] font-bold">
                          {item.tag}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
        </nav>

        <div className="border-brand-border-dark flex w-full flex-col items-center gap-2.5 border-t px-3 pt-3">
          <div className="group relative flex justify-center">
            <a
              href="tel:19008899"
              className="flex h-11 w-11 items-center justify-center rounded-2xl border border-rose-500/30 bg-rose-500/15 text-rose-400 shadow-sm transition-all hover:bg-rose-500 hover:text-white"
              aria-label="Hotline 1900 8899"
            >
              <PhoneCall className="h-4.5 w-4.5" />
            </a>
          </div>
          <div className="group relative flex justify-center">
            <button
              onClick={handleLogout}
              className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-2xl text-slate-500 transition-all hover:bg-rose-950/30 hover:text-rose-400"
              aria-label="Đăng xuất"
            >
              <LogOut className="h-4.5 w-4.5" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex flex-1 flex-col overflow-hidden bg-transparent">
        <header className="bg-brand-dark/90 border-brand-border-dark relative z-20 flex h-18 shrink-0 items-center justify-between border-b px-8 shadow-md backdrop-blur-md">
          <div className="mr-4 max-w-md flex-1">
            <div className="relative">
              <Search className="text-brand-text-subtle pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Tra cứu nhanh biển số xe, tài xế..."
                value={globalSearchQuery}
                onChange={(e) => setGlobalSearchQuery(e.target.value)}
                className="border-brand-border bg-brand-dark/90 text-brand-text-primary placeholder:text-brand-text-subtle focus:border-brand-primary focus:bg-brand-surface focus:ring-brand-primary w-full rounded-lg border py-2 pr-4 pl-9 text-xs transition-all outline-none focus:ring-1"
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* NHÃN TRẠNG THÁI SOCKET.IO-CLIENT */}
            <div className="hidden items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-emerald-950/30 px-3 py-1.5 text-[11px] font-bold text-emerald-400 md:flex">
              <Radio className="h-3.5 w-3.5 animate-pulse" />
              <span>Socket.io: {socketStatus}</span>
            </div>

            {/* NÚT BẤM GIẢ LẬP SỰ KIỆN SOCKET `violation:new` KHI DEMO */}
            <button
              onClick={handleSimulateSocketAlert}
              className="flex cursor-pointer items-center gap-1.5 rounded-xl border border-rose-500/40 bg-rose-950/40 px-3 py-1.5 text-xs font-bold text-rose-300 shadow-sm transition-all hover:bg-rose-600 hover:text-white active:scale-95"
              title="Bắn thử sự kiện Socket.io violation:new"
            >
              <Zap className="h-3.5 w-3.5 text-amber-400" />
              <span className="hidden sm:inline">Test Báo động Socket</span>
            </button>

            <NotificationBell />

            <div className="border-brand-border-dark flex items-center gap-3 border-l pl-3">
              <div className="from-brand-primary to-brand-primary-dark ring-brand-primary/30 relative flex h-9 w-9 items-center justify-center rounded-full bg-linear-to-br text-xs font-bold text-white shadow-md ring-2">
                {user?.name ? user.name.charAt(0) : "TN"}
              </div>
              <div className="hidden text-left leading-tight sm:block">
                <p className="text-brand-text-primary text-xs font-bold">
                  {user?.name}
                </p>
                <p className="flex items-center gap-1 text-[11px] font-semibold text-emerald-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
                  {user?.role?.toLowerCase() === "admin"
                    ? "Quản trị viên"
                    : "Điều phối viên"}
                </p>
              </div>
            </div>
          </div>
        </header>

        {/* BANNER BÁO ĐỘNG ĐỎ TOÀN CỤC TỪ ZUSTAND (`activeAlert`) */}
        {activeAlert && (
          <div className="border-alert-danger/50 flex items-center justify-between border-b bg-rose-950/90 px-8 py-2.5 text-xs text-white shadow-lg backdrop-blur-md">
            <div className="flex items-center gap-2.5">
              <AlertOctagon className="h-4 w-4 shrink-0 animate-bounce text-rose-400" />
              <span>
                <b>[CẢNH BÁO SOCKET.IO KHẨN CẤP]</b> Xe{" "}
                <b>{activeAlert.bien_so_xe}</b> ({activeAlert.driver_name}) vừa
                phát hiện lỗi <b>{activeAlert.ten_loai}</b>!
              </span>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={() => {
                  dismissAlert();
                  navigate("/logs");
                }}
                className="cursor-pointer rounded-lg bg-white px-2.5 py-1 text-[11px] font-black text-rose-950 hover:bg-rose-100"
              >
                Thẩm định ngay &rarr;
              </button>
              <button
                onClick={dismissAlert}
                className="cursor-pointer text-rose-300 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        <main className="text-brand-text-secondary flex-1 overflow-y-auto bg-slate-950/15 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default MainLayout;
