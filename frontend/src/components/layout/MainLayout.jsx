import React from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
//import NotificationBell from "../notification/NotificationBell";

const MainLayout = () => {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <div className="bg-brand-light flex h-screen font-sans">
      {/* SIDEBAR */}
      <aside className="bg-brand-dark flex w-64 flex-col shadow-lg">
        <div className="flex h-20 items-center justify-center border-b border-slate-700">
          <h1
            className="text-alert-safe cursor-pointer text-xl font-bold"
            onClick={() => navigate("/")}
          >
            IoT Driver Safety
          </h1>
        </div>
        <nav className="flex-1 space-y-2 px-4 py-6">
          <button
            onClick={() => navigate("/")}
            className="hover:text-alert-safe flex w-full items-center rounded-xl px-4 py-3 text-gray-300 transition-all hover:bg-slate-800"
          >
            <span className="font-medium"> Giám sát Live</span>
          </button>
          <button
            onClick={() => navigate("/drivers")}
            className="hover:text-alert-safe flex w-full items-center rounded-xl px-4 py-3 text-gray-300 transition-all hover:bg-slate-800"
          >
            <span className="font-medium"> Quản lý Tài xế</span>
          </button>
          <button
            onClick={() => navigate("/trips")}
            className="hover:text-alert-safe flex w-full items-center rounded-xl px-4 py-3 text-gray-300 transition-all hover:bg-slate-800"
          >
            <span className="font-medium"> Ca làm việc</span>
          </button>
          <button
            onClick={() => navigate("/logs")}
            className="hover:text-alert-safe flex w-full items-center rounded-xl px-4 py-3 text-gray-300 transition-all hover:bg-slate-800"
          >
            <span className="font-medium"> Lịch sử Cảnh báo</span>
          </button>
        </nav>
      </aside>

      {/* RIGHT CONTENT */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-20 items-center justify-end bg-white px-8 shadow-sm">
          <div className="flex items-center gap-6">
            <span className="font-medium text-slate-700">
              Chào Quản lý, {user?.name || "Admin"}!
            </span>
            {/* <NotificationBell /> */}
            <button
              onClick={handleLogout}
              className="hover:bg-alert-danger rounded-lg bg-slate-900 px-5 py-2 text-sm text-white transition-all"
            >
              Đăng xuất
            </button>
          </div>
        </header>

        {/* KHU VỰC HIỂN THỊ TRANG CON */}
        <main className="flex-1 overflow-y-auto p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
};
export default MainLayout;
