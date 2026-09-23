import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
//import axiosClient from '../api/axiosClient';
import toast from 'react-hot-toast';
//import { API_URLS } from '../api/api';

function NotificationBell() {
    const [unreadCount, setUnreadCount] = useState(0);
    const [notifications, setNotifications] = useState([]);
    const [isOpen, setIsOpen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    const dropdownRef = useRef(null); // Dùng để nhận biết khi user click ra ngoài quả chuông
    const navigate = useNavigate();

    // 1. Lấy số lượng thông báo chưa đọc (Cái chấm đỏ) khi vừa vào trang
    useEffect(() => {
        const fetchUnreadCount = async () => {
            try {
                const data = await axiosClient.get(`${API_URLS.NOTIFICATIONS}/me/unread-count`);
                // Tùy C# trả về { count: 5 } hay trả thẳng số 5, mình hứng cho đúng
                setUnreadCount(data.count !== undefined ? data.count : data);
            } catch (error) {
                console.error("Lỗi lấy số thông báo:", error);
            }
        };
        fetchUnreadCount();
    }, []);

    // 2. Xử lý khi click ra ngoài thì đóng hộp thông báo lại
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    // 3. Khi click vào quả chuông: Mở hộp thoại & Tải danh sách chi tiết
    const handleToggleBell = async () => {
        setIsOpen(!isOpen);

        // Nếu đang mở ra và chưa có data thì mới gọi API lấy danh sách
        if (!isOpen) {
            setIsLoading(true);
            try {
                const data = await axiosClient.get(`${API_URLS.NOTIFICATIONS}/me`);
                setNotifications(Array.isArray(data) ? data : (data.items || []));
            } catch (error) {
                toast.error("Không thể tải thông báo lúc này 🌿");
            } finally {
                setIsLoading(false);
            }
        }
    };

    // 4. Đánh dấu đọc tất cả
    const handleMarkAllAsRead = async () => {
        try {
            await axiosClient.patch(`${API_URLS.NOTIFICATIONS}/read-all`);
            setNotifications(prev => prev.map(notif => ({ ...notif, isRead: true })));
            setUnreadCount(0);
            toast.success("Đã đánh dấu đọc tất cả! 🌿");
        } catch (error) {
            toast.error("Có lỗi xảy ra khi cập nhật.");
        }
    };

    // 5. Đánh dấu đọc từng thông báo khi click vào nó
    const handleNotificationClick = async (notif) => {
        const currentId = notif.NotificationId;

        if (!notif.isRead) {
            try {
                // 🌟 Đổi thành API_URLS cho đồng bộ
                await axiosClient.patch(`${API_URLS.NOTIFICATIONS}/${currentId}/read`);
                setNotifications(prev => prev.map(n => (n.notificationId || n.NotificationId || n.id) === currentId ? { ...n, isRead: true } : n));
                setUnreadCount(prev => Math.max(0, prev - 1));
            } catch (error) {
                console.error("Lỗi cập nhật thông báo:", error);
            }
        }

        setIsOpen(false);

        const message = (notif.message || notif.content || "").toLowerCase();
        
        if (message.includes('application') || message.includes('ứng tuyển')) {
            navigate('/history-applied');
        } else if (notif.link) {
            navigate(notif.link);
        } else {
            navigate('/');
        }
    };

    return (
        <div className="relative" ref={dropdownRef}>
            {/* NÚT QUẢ CHUÔNG */}
            <button
                onClick={handleToggleBell}
                className="relative p-2 text-gray-500 hover:text-olive transition-colors rounded-full hover:bg-cream"
            >
                <span className="text-2xl">🔔</span>
                {/* CHẤM ĐỎ: Chỉ hiện khi có thông báo chưa đọc */}
                {unreadCount > 0 && (
                    <span className="absolute top-1 right-1 bg-red-500 text-white text-[10px] font-bold w-5 h-5 flex items-center justify-center rounded-full border-2 border-white shadow-sm animate-bounce">
                        {unreadCount > 99 ? '99+' : unreadCount}
                    </span>
                )}
            </button>

            {/* HỘP THOẠI DROPDOWN (Chỉ hiện khi isOpen = true) */}
            {isOpen && (
                <div className="absolute right-0 mt-3 w-80 md:w-96 bg-white rounded-3xl shadow-2xl border border-gray-100 z-50 overflow-hidden animate-fade-in-up">
                    {/* Header của hộp thoại */}
                    <div className="px-5 py-4 border-b border-gray-50 flex justify-between items-center bg-cream">
                        <h3 className="font-bold text-olive text-lg">Thông báo</h3>
                        {unreadCount > 0 && (
                            <button
                                onClick={handleMarkAllAsRead}
                                className="text-xs font-bold text-earth hover:text-olive transition-colors"
                            >
                                Đánh dấu đọc hết ✔️
                            </button>
                        )}
                    </div>

                    {/* Danh sách thông báo */}
                    <div className="max-h-[400px] overflow-y-auto">
                        {isLoading ? (
                            <div className="p-6 text-center text-gray-400 animate-pulse">Đang tải thông báo... 🌿</div>
                        ) : notifications.length === 0 ? (
                            <div className="p-8 text-center text-gray-400">
                                <span className="text-4xl block mb-2">📭</span>
                                Bạn chưa có thông báo nào.
                            </div>
                        ) : (
                            <div className="flex flex-col">
                                {notifications.map((notif, index) => {
                                    const currentId = notif.NotificationId;

                                    return (
                                        <div
                                            key={currentId || index} // 🌟 Sửa lỗi thiếu Key
                                            onClick={() => handleNotificationClick(notif)} // 🌟 Sửa lỗi undefined
                                            className={`p-4 border-b border-gray-50 cursor-pointer transition-colors hover:bg-gray-50 flex gap-4 ${notif.isRead ? 'opacity-60 bg-white' : 'bg-orange-50/30'}`}
                                        >
                                            <div className="w-10 h-10 rounded-full bg-cream flex items-center justify-center shrink-0 text-lg">
                                                {notif.type === 'Application' ? '📄' : notif.type === 'System' ? '⚙️' : '💬'}
                                            </div>
                                            <div>
                                                <p className={`text-sm ${notif.isRead ? 'text-gray-600' : 'text-textmain font-bold'}`}>
                                                    {notif.message || notif.content}
                                                </p>
                                                <p className="text-xs text-gray-400 mt-1">
                                                    {new Date(notif.createdAt || notif.createdDate).toLocaleString('vi-VN')}
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