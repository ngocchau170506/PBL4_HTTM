export const VIOLATION_TYPES = {
	MICROSLEEP: {
		id: 1,
		code: 'MICROSLEEP',
		name: 'Ngủ gật (Microsleep)',
		label: 'Ngủ gật (Microsleep)',
		icon: '🚨',
		color: '#ef4444',
		defaultPoints: 10,
		default_penalty: 10,
		muc_do_nghiem_trong: 'CRITICAL',
		need_video: true,
		dieuKien: 'YOLO26 báo eye_closed liên tục >= 1.5s hoặc head_down >= 1.0s',
		xuLyOffline:
			'Kích Relay hú còi khẩn cấp + DFPlayer phát 0001_microsleep.mp3; cắt video 10s',
		description:
			'YOLO26 phát hiện mắt nhắm liên tục >= 1.8s. Kích hoạt còi báo động khẩn cấp.',
	},
	YAWNING_FATIGUE: {
		id: 2,
		code: 'YAWNING_FATIGUE',
		name: 'Ngáp dài mệt mỏi',
		label: 'Ngáp dài mệt mỏi',
		icon: '🥱',
		color: '#f59e0b',
		defaultPoints: 5,
		default_penalty: 5,
		muc_do_nghiem_trong: 'MEDIUM',
		need_video: false,
		dieuKien: 'Cửa sổ trượt: 3 lần yawn (>= 1.5s) trong 5 phút',
		xuLyOffline: 'DFPlayer phát 0002_fatigue_rest.mp3 nhắc tìm trạm nghỉ gần nhất',
		description:
			'Phát hiện ngáp mệt mỏi liên tục trong 5 phút. Gợi ý trạm dừng chân gần nhất.',
	},
	HIGH_CO2: {
		id: 3,
		code: 'HIGH_CO2',
		name: 'Nồng độ CO2 buồng lái cao',
		label: 'CO2 buồng lái cao',
		icon: '⚠️',
		color: '#eab308',
		defaultPoints: 2,
		default_penalty: 2,
		muc_do_nghiem_trong: 'LOW',
		need_video: false,
		dieuKien:
			'Cảm biến MQ-135 đo nồng độ CO2 buồng lái > 1200 ppm kéo dài quá 5 phút',
		xuLyOffline: 'DFPlayer phát 0003_open_window.mp3 nhắc mở cửa lấy gió ngoài',
		description:
			'Cảm biến MQ-135 ghi nhận nồng độ CO2 cabin vượt ngưỡng (> 1200 ppm), nguy cơ gây buồn ngủ.',
	},
	PHONE_DISTRACTION: {
		id: 4,
		code: 'PHONE_DISTRACTION',
		name: 'Dùng điện thoại khi lái xe',
		label: 'Dùng điện thoại',
		icon: '📱',
		color: '#f97316',
		defaultPoints: 5,
		default_penalty: 5,
		muc_do_nghiem_trong: 'MEDIUM',
		need_video: true,
		dieuKien: 'YOLO26 báo phone_use hoặc looking_away liên tục > 3.0s',
		xuLyOffline:
			'DFPlayer phát 0004_focus_driving.mp3 nhắc tập trung lái xe, cắt video 10s',
		description:
			'YOLO26 phát hiện tài xế cầm điện thoại khi đang điều khiển phương tiện.',
	},
	DEVICE_TAMPERING: {
		id: 5,
		code: 'DEVICE_TAMPERING',
		name: 'Che / Rút cáp thiết bị',
		label: 'Che / Rút cáp thiết bị',
		icon: '📷',
		color: '#a855f7',
		defaultPoints: 15,
		default_penalty: 15,
		muc_do_nghiem_trong: 'CRITICAL',
		need_video: false,
		dieuKien:
			'Khung hình tối đen > 15s hoặc mất kết nối Camera USB/UART khi xe chạy > 5km/h',
		xuLyOffline:
			'DFPlayer phát 0005_device_tamper.mp3 hoặc ESP32 tự kích Relay còi cảnh báo',
		description:
			'Phát hiện hành vi che khuất camera hoặc can thiệp phần cứng giám sát khi xe đang chạy.',
	},
};

export const getViolationTypeById = (id) => {
	if (!id) return null;
	const numId = parseInt(id, 10);
	return Object.values(VIOLATION_TYPES).find((t) => t.id === numId) || null;
};

export const getViolationTypeByCode = (code) => {
	if (!code) return null;
	const upper = String(code).toUpperCase().trim();

	if (VIOLATION_TYPES[upper]) return VIOLATION_TYPES[upper];

	if (
		upper.includes('MICROSLEEP') ||
		upper.includes('EYES_CLOSED') ||
		upper.includes('NGỦ GẬT') ||
		upper.includes('BUON_NGU')
	) {
		return VIOLATION_TYPES.MICROSLEEP;
	}
	if (
		upper.includes('PHONE') ||
		upper.includes('ĐIỆN THOẠI') ||
		upper.includes('DIEN_THOAI')
	) {
		return VIOLATION_TYPES.PHONE_DISTRACTION;
	}
	if (
		upper.includes('YAWN') ||
		upper.includes('NGÁP') ||
		upper.includes('MET_MOI') ||
		upper.includes('FATIGUE')
	) {
		return VIOLATION_TYPES.YAWNING_FATIGUE;
	}
	if (upper.includes('CO2')) {
		return VIOLATION_TYPES.HIGH_CO2;
	}
	if (
		upper.includes('TAMPER') ||
		upper.includes('CAMERA') ||
		upper.includes('SPEED') ||
		upper.includes('TỐC ĐỘ') ||
		upper.includes('QUA_TOC_DO')
	) {
		return VIOLATION_TYPES.DEVICE_TAMPERING;
	}

	return null;
};

export const resolveViolationFromFlag = ({
	aiViolationFlag,
	co2,
	speed,
	co2Threshold = 1200,
}) => {
	if (aiViolationFlag) {
		const matched = getViolationTypeByCode(aiViolationFlag);
		if (matched) return matched;
	}
	if (co2 && co2 > co2Threshold && !aiViolationFlag) {
		return VIOLATION_TYPES.HIGH_CO2;
	}
	return null;
};

export const getAllViolationTypes = () => Object.values(VIOLATION_TYPES);
