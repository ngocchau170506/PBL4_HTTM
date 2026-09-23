import React from 'react';

// Khuôn đúc cho Ô nhập liệu (Text, Email, Password...)
function InputField({ 
    label,          // Tên hiển thị (VD: "Email của bạn")
    name,           // Tên biến để lưu data
    type = "text",  // Loại input (text, email, password, date...)
    value,          // Giá trị hiện tại
    onChange,       // Hàm xử lý khi gõ phím
    placeholder,    // Chữ mờ gợi ý
    required,       // Bắt buộc nhập không?
    disabled        // Có bị khóa không?
}) {
    return (
        <div className="w-full">
            {/* Nếu có truyền label vào thì mới hiển thị */}
            {label && (
                <label className="block text-sm font-bold text-gray-700 mb-2">
                    {label} {required && <span className="text-red-500">*</span>}
                </label>
            )}
            
            <input 
                type={type}
                name={name}
                value={value}
                onChange={onChange}
                placeholder={placeholder}
                required={required}
                disabled={disabled}
                className="w-full px-4 py-2.5 rounded-xl border border-gray-200 focus:border-earth focus:ring-2 focus:ring-earth focus:ring-opacity-20 outline-none transition-all bg-gray-50 focus:bg-white disabled:bg-gray-100 disabled:cursor-not-allowed"
            />
        </div>
    );
}

export default InputField;