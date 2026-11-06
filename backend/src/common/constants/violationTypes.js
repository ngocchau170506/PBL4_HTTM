export const VIOLATION_TYPES = {
  BUON_NGU: {
    id: 1,
    code: 'MICROSLEEP',
    legacyCode: 'BUON_NGU',
    name: 'Ngủ gật / Mất tập trung kéo dài (Microsleep)',
    icon: '🚨',
    defaultPoints: 10,
    dieuKien: 'Mắt nhắm liên tục >= 1.5 - 2 giây hoặc góc đầu gục đột ngột',
    xuLyOffline: 'Còi báo động khẩn cấp trong cabin, kích rung ghế, cắt video 10s',
    description: 'Mô hình AI YOLO phát hiện tài xế nhắm mắt liên tục >= 1.5s. Bật chuông báo gắt & trừ 10 điểm uy tín.',
  },
  MET_MOI_NGAP: {
    id: 2,
    code: 'YAWNING_FATIGUE',
    legacyCode: 'MET_MOI_NGAP',
    name: 'Mệt mỏi / Ngáp dài liên tục (Yawning)',
    icon: '🥱',
    defaultPoints: 2,
    canGracePeriod: true, // Cho phép miễn trừ nếu dừng nghỉ kịp thời
    dieuKien: 'Ngáp dài liên tục >= 3 giây hoặc tần suất ngáp >= 3 lần trong 5 phút',
    xuLyOffline: 'Bật quạt gió cabin lấy gió tươi, phát loa nhắc tìm trạm nghỉ gần nhất',
    description: 'Phát hiện ngáp dài liên tục, dấu hiệu thể trạng mệt mỏi. Gợi ý trạm dừng chân và áp dụng cơ chế ân hạn dừng nghỉ an toàn (RESOLVED_SAFE).',
  },
  CO2_CAO: {
    id: 3,
    code: 'HIGH_CO2',
    legacyCode: 'CO2_CAO',
    name: 'Nồng độ CO2 buồng lái cao',
    icon: '⚠️',
    defaultPoints: 2,
    canGracePeriod: true,
    dieuKien: 'Cảm biến MQ-135 đo nồng độ CO2 buồng lái > 1000 - 1200 ppm',
    xuLyOffline: 'Đóng Relay tự động bật quạt hút thông gió cabin',
    description: 'Cảm biến MQ-135 ghi nhận nồng độ CO2 cabin vượt ngưỡng (> 1000 ppm), nguy cơ thiếu oxy gây buồn ngủ.',
  },
  DIEN_THOAI: {
    id: 4,
    code: 'PHONE_DISTRACTION',
    legacyCode: 'DIEN_THOAI',
    name: 'Sử dụng điện thoại khi đang lái xe',
    icon: '📱',
    defaultPoints: 5,
    dieuKien: 'Tay cầm điện thoại áp tai hoặc nhìn xuống thao tác màn hình >= 3 giây',
    xuLyOffline: 'Phát âm thanh nhắc nhở tập trung lái xe, cắt video 10s',
    description: 'Mô hình AI YOLO phát hiện tài xế cầm điện thoại áp tai hoặc nhắn tin khi lái xe.',
  },
  QUA_TOC_DO: {
    id: 5,
    code: 'QUA_TOC_DO',
    legacyCode: 'QUA_TOC_DO',
    name: 'Chạy quá tốc độ an toàn quy định',
    icon: '⚡',
    defaultPoints: 5,
    dieuKien: 'Định vị GPS ghi nhận vận tốc xe vượt quá 80 km/h',
    xuLyOffline: 'Phát âm thanh cảnh báo vượt quá tốc độ cho phép',
    description: 'Hệ thống định vị GPS phát hiện vận tốc xe chạy vượt quá giới hạn 80 km/h quy định.',
  },
  DEVICE_TAMPERING: {
    id: 6,
    code: 'DEVICE_TAMPERING',
    legacyCode: 'DEVICE_TAMPERING',
    name: 'Che / Rút cáp thiết bị / Khuất tài xế',
    icon: '🔌',
    defaultPoints: 15,
    dieuKien: 'Ống kính camera bị che tối đen hoặc tài xế khuất tầm nhìn khi xe đang chạy',
    xuLyOffline: 'Còi báo động khẩn cấp trong cabin, kích hoạt đèn đỏ, cắt video bằng chứng',
    description: 'Phát hiện ống kính camera bị che khuất hoặc rút cáp tín hiệu khi xe đang chạy tốc độ cao.',
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

  // Khớp trực tiếp
  if (VIOLATION_TYPES[upper]) return VIOLATION_TYPES[upper];
  const direct = Object.values(VIOLATION_TYPES).find(
    (t) => t.code === upper || t.legacyCode === upper
  );
  if (direct) return direct;

  // Khớp mềm với các cờ phần cứng hoặc chuỗi cũ
  if (upper.includes('TAMPER') || upper.includes('CHE') || upper.includes('RÚT') || upper.includes('OCCLUD')) {
    return VIOLATION_TYPES.DEVICE_TAMPERING;
  }
  if (upper.includes('MICROSLEEP') || upper.includes('EYES_CLOSED') || upper.includes('NGỦ GẬT') || upper.includes('BUON_NGU')) {
    return VIOLATION_TYPES.BUON_NGU;
  }
  if (upper.includes('PHONE') || upper.includes('ĐIỆN THOẠI') || upper.includes('DIEN_THOAI')) {
    return VIOLATION_TYPES.DIEN_THOAI;
  }
  if (upper.includes('YAWN') || upper.includes('NGÁP') || upper.includes('MET_MOI') || upper.includes('FATIGUE')) {
    return VIOLATION_TYPES.MET_MOI_NGAP;
  }
  if (upper.includes('CO2') || upper.includes('HIGH_CO2')) {
    return VIOLATION_TYPES.CO2_CAO;
  }
  if (upper.includes('SPEED') || upper.includes('TỐC ĐỘ') || upper.includes('QUA_TOC_DO')) {
    return VIOLATION_TYPES.QUA_TOC_DO;
  }

	return null;
};

/**
 * Ánh xạ tín hiệu cảm biến / AI phần cứng sang loại lỗi chuẩn
 */
export const resolveViolationFromFlag = ({ aiViolationFlag, co2, speed, co2Threshold = 1000 }) => {
  if (aiViolationFlag) {
    const resolved = getViolationTypeByCode(aiViolationFlag);
    if (resolved) return resolved;
  }

  if (co2 !== null && co2 !== undefined && co2 >= co2Threshold) {
    return VIOLATION_TYPES.CO2_CAO;
  }

  if (speed !== null && speed !== undefined && speed > 85.0) {
    return VIOLATION_TYPES.QUA_TOC_DO;
  }

  return null;
};

export const getAllViolationTypes = () => Object.values(VIOLATION_TYPES);
