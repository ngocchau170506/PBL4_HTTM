import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import {
	BadRequestException,
	ForbiddenException,
	NotFoundException,
} from '../../common/exceptions/index.js';
import { sanitizeUser } from '../../common/utils/sanitize.js';
import { getAiConfig } from '../configs/configs.service.js';
import { formatDriver } from '../drivers/drivers.service.js';
import { formatVehicle, formatRoute } from '../assets/assets.service.js';

export const formatShift = (s) => {
	let parsedWarnings = [];
	try {
		parsedWarnings = s.canhBaoKhiTao ? JSON.parse(s.canhBaoKhiTao) : [];
	} catch (e) {
		parsedWarnings = [];
	}

	return {
		...s,
		driver_id: s.driverId,
		vehicle_id: s.vehicleId,
		route_id: s.routeId,
		thoi_gian_bat_dau_du_kien: s.thoiGianBatDauDuKien,
		thoi_gian_ket_thuc_du_kien: s.thoiGianKetThucDuKien,
		trang_thai: s.trangThai,
		ly_do_override: s.lyDoOverride,
		canh_bao_khi_tao: parsedWarnings,
		creator: s.creator ? sanitizeUser(s.creator) : s.creator,
		driver: s.driver ? formatDriver(s.driver) : null,
		vehicle: s.vehicle ? formatVehicle(s.vehicle) : null,
		route: s.route ? formatRoute(s.route) : null,
	};
};

export const getAllShifts = async ({ driverId, status } = {}) => {
	const where = {};
	if (driverId) where.driverId = parseInt(driverId, 10);
	if (status) where.trangThai = status;

	const shifts = await prisma.shift.findMany({
		where,
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
			creator: true,
			trip: true,
		},
		orderBy: { thoiGianBatDauDuKien: 'desc' },
	});

	return shifts.map(formatShift);
};

const parseTimeToMinutes = (timeStr) => {
	const [h, m] = String(timeStr || '')
		.trim()
		.split(':')
		.map((n) => parseInt(n, 10));
	if (Number.isNaN(h)) return null;
	return h * 60 + (Number.isNaN(m) ? 0 : m);
};

const checkWeakWindowOverlap = (startTime, endTime, rawWindows) => {
	let windows = ['13:00-15:00', '01:00-04:00'];
	if (rawWindows) {
		try {
			const parsed =
				typeof rawWindows === 'string' ? JSON.parse(rawWindows) : rawWindows;
			if (Array.isArray(parsed) && parsed.length > 0) {
				windows = parsed;
			}
		} catch {
			// fallback to default windows
		}
	}

	const overlapped = new Set();
	const cursor = new Date(startTime);
	cursor.setHours(0, 0, 0, 0);
	cursor.setDate(cursor.getDate() - 1);

	const endDay = new Date(endTime);
	endDay.setHours(23, 59, 59, 999);

	while (cursor <= endDay) {
		for (const w of windows) {
			const parts = String(w).replace(/\s+/g, '').split('-');
			if (parts.length !== 2) continue;
			const startMin = parseTimeToMinutes(parts[0]);
			const endMin = parseTimeToMinutes(parts[1]);
			if (startMin === null || endMin === null) continue;

			const winStart = new Date(cursor);
			winStart.setHours(Math.floor(startMin / 60), startMin % 60, 0, 0);

			const winEnd = new Date(cursor);
			winEnd.setHours(Math.floor(endMin / 60), endMin % 60, 0, 0);
			if (endMin <= startMin) {
				winEnd.setDate(winEnd.getDate() + 1);
			}

			if (startTime < winEnd && endTime > winStart) {
				overlapped.add(String(w).trim());
			}
		}
		cursor.setDate(cursor.getDate() + 1);
	}

	return Array.from(overlapped);
};

const validateShiftConstraints = async ({
	driverId,
	vehicleId,
	startTime,
	endTime,
	lyDoOverride,
	excludeShiftId = null,
	skipTimeChecks = false,
}) => {
	if (isNaN(startTime.getTime()) || !endTime || isNaN(endTime.getTime())) {
		throw new BadRequestException('Thời gian ca làm việc không hợp lệ.');
	}

	if (startTime >= endTime) {
		throw new BadRequestException(
			'Thời gian bắt đầu ca phải diễn ra trước thời gian kết thúc.',
		);
	}

	const idExcludeFilter = excludeShiftId ? { id: { not: excludeShiftId } } : {};

	// 1. Kiểm tra Tài xế đình chỉ
	const driver = await prisma.driver.findUnique({ where: { userId: driverId } });
	if (driver && (driver.uyTin <= 20 || driver.capCanhBao === 'SUSPENDED_RED')) {
		throw new BadRequestException(
			'CHẶN: Tài xế này đang bị đình chỉ công tác (Điểm uy tín <= 20 hoặc Báo động đỏ). Không thể xếp ca.',
		);
	}

	// 2. Kiểm tra Xe bảo dưỡng
	const vehicle = await prisma.vehicle.findUnique({ where: { id: vehicleId } });
	if (vehicle && vehicle.trangThai === 'MAINTENANCE') {
		throw new BadRequestException(
			'CHẶN: Phương tiện này đang trong xưởng bảo dưỡng. Không thể xếp ca.',
		);
	}

	// 3. Kiểm tra trùng lặp lịch phương tiện
	const overlappingVehicleShift = await prisma.shift.findFirst({
		where: {
			...idExcludeFilter,
			vehicleId,
			trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
			thoiGianBatDauDuKien: { lt: endTime },
			thoiGianKetThucDuKien: { gt: startTime },
		},
	});
	if (overlappingVehicleShift) {
		throw new BadRequestException(
			'CHẶN: Phương tiện này đã được xếp vào một ca làm việc khác trong khoảng thời gian đã chọn.',
		);
	}

	const warnings = [];
	if (!skipTimeChecks) {
		// 4. Lấy cấu hình hệ thống động
		const config = await getAiConfig();
		const minRestHours = config.min_rest_hours ?? 10.0;

		// 5. Kiểm tra trùng lặp ca trực của Tài xế
		const overlappingDriverShift = await prisma.shift.findFirst({
			where: {
				...idExcludeFilter,
				driverId,
				trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
				thoiGianBatDauDuKien: { lt: endTime },
				thoiGianKetThucDuKien: { gt: startTime },
			},
		});
		if (overlappingDriverShift) {
			throw new BadRequestException(
				'CHẶN: Tài xế này đã có một ca làm việc khác trùng với khoảng thời gian đã chọn.',
			);
		}

		// 6. Kiểm tra khoảng thời gian nghỉ tối thiểu trước & sau ca
		const prevShift = await prisma.shift.findFirst({
			where: {
				...idExcludeFilter,
				driverId,
				trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
				thoiGianKetThucDuKien: { lte: startTime },
			},
			orderBy: { thoiGianKetThucDuKien: 'desc' },
		});

		if (prevShift) {
			const restHoursBefore =
				(startTime.getTime() -
					new Date(prevShift.thoiGianKetThucDuKien).getTime()) /
				(1000 * 3600);
			if (restHoursBefore < minRestHours) {
				throw new BadRequestException(
					`CHẶN: Khoảng thời gian nghỉ giữa ca trước và ca này chỉ có ${restHoursBefore.toFixed(1)}h (< ${minRestHours}h tối thiểu theo quy định). Không thể xếp ca.`,
				);
			}
		}

		const nextShift = await prisma.shift.findFirst({
			where: {
				...idExcludeFilter,
				driverId,
				trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
				thoiGianBatDauDuKien: { gte: endTime },
			},
			orderBy: { thoiGianBatDauDuKien: 'asc' },
		});

		if (nextShift) {
			const restHoursAfter =
				(new Date(nextShift.thoiGianBatDauDuKien).getTime() -
					endTime.getTime()) /
				(1000 * 3600);
			if (restHoursAfter < minRestHours) {
				throw new BadRequestException(
					`CHẶN: Khoảng thời gian nghỉ giữa ca này và ca sau chỉ có ${restHoursAfter.toFixed(1)}h (< ${minRestHours}h tối thiểu theo quy định). Không thể xếp ca.`,
				);
			}
		}

		// 7. Kiểm tra Hồ sơ Sinh học động & cấp cảnh báo tài xế
		const profile = await prisma.driverBiometricProfile.findFirst({
			where: { driverId },
			orderBy: { createdAt: 'desc' },
		});

		const shiftMinutes = (endTime.getTime() - startTime.getTime()) / (1000 * 60);
		const maxDriveMinutes = profile?.thoiGianLaiToiDaKhuyenNghi || 240;
		if (shiftMinutes > maxDriveMinutes) {
			warnings.push(
				`CẢNH BÁO: Tổng thời gian lái ca dự kiến (${(shiftMinutes / 60).toFixed(1)}h) vượt mức khuyến nghị sinh học (${(maxDriveMinutes / 60).toFixed(1)}h).`,
			);
		}

		const overlappedWeakWindows = checkWeakWindowOverlap(
			startTime,
			endTime,
			profile?.khungGioYeuSinhHoc,
		);
		if (overlappedWeakWindows.length > 0) {
			warnings.push(
				`LƯU Ý: Ca làm việc giao thoa với khung giờ yếu sinh học của tài xế (${overlappedWeakWindows.join(', ')}).`,
			);
		}

		if (driver?.capCanhBao === 'WARNING_YELLOW') {
			warnings.push(
				`CẢNH BÁO: Tài xế đang ở mức Cảnh cáo vàng (Uy tín: ${driver.uyTin}/100).`,
			);
		}

		if (warnings.length > 0 && !String(lyDoOverride || '').trim()) {
			throw new BadRequestException(
				`CẢNH BÁO SINH HỌC (Yêu cầu nhập lý do ghi đè): ${warnings.join(' ')}`,
			);
		}
	}

	return { warnings };
};

export const createShift = async ({
	driverId,
	driver_id,
	vehicleId,
	vehicle_id,
	routeId,
	route_id,
	thoiGianBatDauDuKien,
	thoi_gian_bat_dau_du_kien,
	startTime: rawStart,
	thoiGianKetThucDuKien,
	thoi_gian_ket_thuc_du_kien,
	endTime: rawEnd,
	durationHours,
	ly_do_override,
	createdBy,
}) => {
	const pDriverId = parseInt(driverId || driver_id, 10);
	const pVehicleId = parseInt(vehicleId || vehicle_id, 10);
	const pRouteId = parseInt(routeId || route_id, 10);
	const pCreatedBy = parseInt(createdBy || 1, 10);

	const startStr = thoiGianBatDauDuKien || thoi_gian_bat_dau_du_kien || rawStart;
	let endStr = thoiGianKetThucDuKien || thoi_gian_ket_thuc_du_kien || rawEnd;

	const startTime = new Date(startStr);
	let endTime = endStr ? new Date(endStr) : null;
	if (!endTime && durationHours) {
		endTime = new Date(startTime.getTime() + Number(durationHours) * 3600000);
	}

	const { warnings } = await validateShiftConstraints({
		driverId: pDriverId,
		vehicleId: pVehicleId,
		startTime,
		endTime,
		lyDoOverride: ly_do_override,
	});

	const shift = await prisma.shift.create({
		data: {
			driverId: pDriverId,
			vehicleId: pVehicleId,
			routeId: pRouteId,
			thoiGianBatDauDuKien: startTime,
			thoiGianKetThucDuKien: endTime,
			trangThai: 'SCHEDULED',
			createdBy: pCreatedBy,
			lyDoOverride: ly_do_override || null,
			canhBaoKhiTao: JSON.stringify(warnings),
		},
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
		},
	});

	const hasOverride = Boolean(ly_do_override?.trim());
	logAuditAction({
		userId: pCreatedBy,
		hanhDong: hasOverride ? 'CREATE_SHIFT_OVERRIDE' : 'CREATE_SHIFT',
		doiTuongLoai: 'SHIFT',
		doiTuongId: String(shift.id),
		giaTriSau: shift,
		lyDo: hasOverride
			? `Ghi đè cảnh báo Smart Scheduling: "${ly_do_override}"`
			: warnings.length > 0
				? warnings.join(' | ')
				: 'Tạo ca làm việc mới.',
	});

	return formatShift(shift);
};

// --- BỔ SUNG HÀM CẬP NHẬT CA (PATCH /shifts/:id)  ---
export const updateShift = async (shiftId, updatedData, actorUser) => {
	const id = parseInt(shiftId, 10);
	const oldShift = await prisma.shift.findUnique({ where: { id } });
	if (!oldShift) {
		throw new NotFoundException('Không tìm thấy ca phân công!');
	}

	if (oldShift.trangThai === 'IN_PROGRESS') {
		throw new BadRequestException(
			'Không thể chỉnh sửa ca đang trong hành trình lăn bánh!',
		);
	}

	const data = {};
	if (updatedData.driverId || updatedData.driver_id) {
		data.driverId = parseInt(updatedData.driverId || updatedData.driver_id, 10);
	}
	if (updatedData.vehicleId || updatedData.vehicle_id) {
		data.vehicleId = parseInt(updatedData.vehicleId || updatedData.vehicle_id, 10);
	}
	if (updatedData.routeId || updatedData.route_id) {
		data.routeId = parseInt(updatedData.routeId || updatedData.route_id, 10);
	}
	if (updatedData.thoiGianBatDauDuKien || updatedData.thoi_gian_bat_dau_du_kien) {
		data.thoiGianBatDauDuKien = new Date(
			updatedData.thoiGianBatDauDuKien || updatedData.thoi_gian_bat_dau_du_kien,
		);
	}
	if (updatedData.thoiGianKetThucDuKien || updatedData.thoi_gian_ket_thuc_du_kien) {
		data.thoiGianKetThucDuKien = new Date(
			updatedData.thoiGianKetThucDuKien || updatedData.thoi_gian_ket_thuc_du_kien,
		);
	}
	if (updatedData.trangThai || updatedData.trang_thai) {
		data.trangThai = updatedData.trangThai || updatedData.trang_thai;
	}
	if (
		updatedData.lyDoOverride !== undefined ||
		updatedData.ly_do_override !== undefined
	) {
		data.lyDoOverride = updatedData.lyDoOverride ?? updatedData.ly_do_override;
	}

	const targetDriverId = data.driverId ?? oldShift.driverId;
	const targetVehicleId = data.vehicleId ?? oldShift.vehicleId;
	const targetStart = data.thoiGianBatDauDuKien ?? oldShift.thoiGianBatDauDuKien;
	const targetEnd = data.thoiGianKetThucDuKien ?? oldShift.thoiGianKetThucDuKien;
	const targetStatus = data.trangThai ?? oldShift.trangThai;
	const targetOverride =
		data.lyDoOverride !== undefined ? data.lyDoOverride : oldShift.lyDoOverride;

	if (!['CANCELLED', 'DECLINED', 'COMPLETED'].includes(targetStatus)) {
		const skipTimeChecks = oldShift.trangThai === 'ACCEPTED';
		const { warnings } = await validateShiftConstraints({
			driverId: targetDriverId,
			vehicleId: targetVehicleId,
			startTime: new Date(targetStart),
			endTime: new Date(targetEnd),
			lyDoOverride: targetOverride,
			excludeShiftId: id,
			skipTimeChecks,
		});
		if (!skipTimeChecks) {
			data.canhBaoKhiTao = JSON.stringify(warnings);
		}
	}

	const updatedShift = await prisma.shift.update({
		where: { id },
		data,
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
		},
	});

	logAuditAction({
		userId: actorUser?.id || 2,
		hanhDong: 'UPDATE_SHIFT',
		doiTuongLoai: 'SHIFT',
		doiTuongId: String(id),
		giaTriTruoc: {
			driver_id: oldShift.driverId,
			vehicle_id: oldShift.vehicleId,
			thoi_gian_bat_dau_du_kien: oldShift.thoiGianBatDauDuKien,
			trang_thai: oldShift.trangThai,
		},
		giaTriSau: {
			driver_id: updatedShift.driverId,
			vehicle_id: updatedShift.vehicleId,
			thoi_gian_bat_dau_du_kien: updatedShift.thoiGianBatDauDuKien,
			trang_thai: updatedShift.trangThai,
		},
		lyDo: updatedData.ly_do_override || `Cập nhật thông tin ca phân công #${id}`,
	});

	return formatShift(updatedShift);
};

// --- BỔ SUNG HÀM XÓA CA (DELETE /shifts/:id)  ---
export const deleteShift = async (shiftId, reason, actorUser) => {
	const id = parseInt(shiftId, 10);
	const target = await prisma.shift.findUnique({
		where: { id },
		include: {
			driver: { include: { user: true } },
			vehicle: true,
		},
	});

	if (!target) {
		throw new NotFoundException('Không tìm thấy ca phân công!');
	}
	if (target.trangThai === 'IN_PROGRESS') {
		throw new BadRequestException('Không thể xóa ca đang thực hiện!');
	}

	await prisma.shift.delete({ where: { id } });

	logAuditAction({
		userId: actorUser?.id || 2,
		hanhDong: 'DELETE_SHIFT',
		doiTuongLoai: 'SHIFT',
		doiTuongId: String(id),
		giaTriTruoc: {
			driver_name: target.driver?.user?.hoTen,
			bien_so_xe: target.vehicle?.bienSo,
			trang_thai: target.trangThai,
		},
		giaTriSau: { deleted: true },
		lyDo: reason || 'Hủy ca phân công do điều chỉnh kế hoạch',
	});

	return { success: true, id };
};

export const respondToShift = async (
	shiftId,
	{ trangThai, trang_thai, lyDoTuChoi, ly_do_tu_choi },
	currentUser = null,
) => {
	const id = parseInt(shiftId, 10);
	const shift = await prisma.shift.findUnique({ where: { id } });
	if (!shift) throw new NotFoundException('Ca làm việc không tồn tại.');

	if (
		currentUser?.role?.toUpperCase() === 'DRIVER' &&
		Number(shift.driverId) !== Number(currentUser.driver?.userId || currentUser.id)
	) {
		throw new ForbiddenException(
			'Bạn không có quyền phản hồi ca làm việc của tài xế khác.',
		);
	}

	if (shift.trangThai === 'IN_PROGRESS' || shift.trangThai === 'COMPLETED') {
		throw new BadRequestException(
			'Không thể chỉnh sửa ca làm việc khi đã bắt đầu hoặc hoàn thành.',
		);
	}

	const newStatus = String(trangThai || trang_thai || '').toUpperCase();
	if (!['ACCEPTED', 'DECLINED'].includes(newStatus)) {
		throw new BadRequestException(
			'Trạng thái phản hồi ca chỉ hợp lệ với ACCEPTED hoặc DECLINED.',
		);
	}
	const reason = lyDoTuChoi || ly_do_tu_choi;

	const updatedShift = await prisma.shift.update({
		where: { id },
		data: {
			trangThai: newStatus,
			lyDoTuChoi: reason || null,
		},
		include: {
			driver: { include: { user: true } },
			vehicle: true,
			route: true,
		},
	});

	return formatShift(updatedShift);
};
