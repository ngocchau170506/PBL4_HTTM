import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { useLocation, useNavigate } from "react-router-dom";
import { useFleetStore } from "../../store/useFleetStore";
import formatDateTime from "../../utils/formatDateTime";

function NotificationBell() {
  const { violations, disputes, syncFleetData } = useFleetStore();
  const [readIds, setReadIds] = useState([]);
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    syncFleetData();
  }, [location.pathname, isOpen, syncFleetData]);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const pendingViolations = (violations || [])
    .filter((v) => v.trang_thai_xac_thuc === "PENDING_MANUAL_REVIEW")
    .map((v) => ({
      id: `v-${v.id}`,
      type: v.ma_loai === "MICROSLEEP" ? "DANGER" : "WARNING",
      message: `[Chờ duyệt AI] Tài xế ${
        v.driver?.user?.ho_ten || "Phạm Hoàng Nam"
      }: Phát hiện ${v.ten_loai || v.ma_loai} (#${v.id})`,
      createdAt: v.thoi_gian_chot_vi_pham || new Date().toISOString(),
      link: "/logs",
    }));

  const openDisputes = (disputes || [])
    .filter((d) => d.trang_thai === "OPEN")
    .map((d) => ({
      id: `d-${d.id}`,
      type: "WARNING",
      message: `[Khiếu nại #KN-${d.id}] Tài xế ${
        d.driver?.user?.ho_ten || "Phạm Hoàng Nam"
      } yêu cầu xem xét lại lỗi #${d.violation_id}`,
      createdAt: d.khieu_nai_luc || new Date().toISOString(),
      link: "/logs",
    }));

  const notifications = [...pendingViolations, ...openDisputes];
  const unreadCount = notifications.filter(
    (n) => !readIds.includes(n.id),
  ).length;

  const handleToggleBell = () => setIsOpen(!isOpen);

  const handleMarkAllAsRead = () => {
    setReadIds(notifications.map((n) => n.id));
    toast.success("Đã đánh dấu đã đọc tất cả thông báo!");
  };

  const handleNotificationClick = (notif) => {
    if (!readIds.includes(notif.id)) {
      setReadIds((prev) => [...prev, notif.id]);
    }
    setIsOpen(false);
    if (notif.link) navigate(notif.link);
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={handleToggleBell}
        className="text-brand-text-muted hover:bg-brand-surface hover:text-brand-text-primary relative cursor-pointer rounded-xl p-2.5 transition-colors"
      >
        <span className="text-xl">🔔</span>
        {unreadCount > 0 && (
          <span className="bg-alert-danger border-brand-dark absolute top-1.5 right-1.5 flex h-4.5 w-4.5 items-center justify-center rounded-full border text-[10px] font-bold text-white shadow-sm">
            {unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="border-brand-border bg-brand-dark/95 absolute right-0 z-50 mt-3 w-80 overflow-hidden rounded-3xl border shadow-2xl backdrop-blur-xl md:w-96">
          <div className="border-brand-border-dark bg-brand-surface/80 flex items-center justify-between border-b px-5 py-3.5">
            <h3 className="text-brand-text-primary text-sm font-bold">
              Thông báo Cần Xử lý ({unreadCount})
            </h3>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllAsRead}
                className="text-brand-blue-sky cursor-pointer text-xs font-bold hover:text-white hover:underline"
              >
                Đánh dấu đã đọc
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="text-brand-text-subtle p-8 text-center text-xs">
                🎉 Tất cả vi phạm và khiếu nại đã được xử lý xong!
              </div>
            ) : (
              <div className="divide-brand-border-dark/60 divide-y">
                {notifications.map((notif) => {
                  const isRead = readIds.includes(notif.id);
                  return (
                    <div
                      key={notif.id}
                      onClick={() => handleNotificationClick(notif)}
                      className={`hover:bg-brand-surface/70 flex cursor-pointer gap-3.5 p-4 transition-colors ${
                        isRead
                          ? "bg-transparent opacity-60"
                          : "bg-brand-blue-night/25"
                      }`}
                    >
                      <div className="shrink-0 text-xl">
                        {notif.type === "DANGER" ? "🚨" : "⚖️"}
                      </div>
                      <div>
                        <p
                          className={`text-xs leading-relaxed ${
                            isRead
                              ? "text-brand-text-muted"
                              : "text-brand-text-primary font-bold"
                          }`}
                        >
                          {notif.message}
                        </p>
                        <p className="text-brand-text-subtle mt-1.5 font-mono text-[10px]">
                          {formatDateTime(notif.createdAt)}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationBell;
