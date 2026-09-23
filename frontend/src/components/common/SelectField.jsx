import React from 'react';

// Khuôn đúc cho Ô xổ xuống (Dropdown)
function SelectField({ 
    label, 
    name, 
    value, 
    onChange, 
    options = [], // Danh sách các lựa chọn (Mảng)
    required, 
    disabled 
}) {
    return (
        <div className="w-full">
            {label && (
                <label className="block text-sm font-bold text-gray-700 mb-2">
                    {label} {required && <span className="text-red-500">*</span>}
                </label>
            )}
            
            <select 
                name={name}
                value={value}
                onChange={onChange}
                required={required}
                disabled={disabled}
                className="w-full px-4 py-2.5 rounded-xl border border-gray-200 focus:border-earth focus:ring-2 focus:ring-earth focus:ring-opacity-20 outline-none transition-all bg-gray-50 focus:bg-white cursor-pointer disabled:bg-gray-100 disabled:cursor-not-allowed"
            >
                <option value="">-- Vui lòng chọn --</option>
                {/* Duyệt qua mảng options để in ra các thẻ <option> */}
                {options.map((opt, index) => (
                    <option key={index} value={opt.value}>
                        {opt.label}
                    </option>
                ))}
            </select>
        </div>
    );
}

export default SelectField;