import assert from 'node:assert/strict';

const BASE_URL = (process.env.BASE_URL || 'http://localhost').replace(/\/$/, '');
const IOT_DEVICE_SECRET =
	process.env.IOT_DEVICE_SECRET || 'your_iot_device_secret_token';

const results = [];

async function testCase(name, fn) {
	try {
		await fn();
		results.push({ name, passed: true });
		console.log(`[PASS] ${name}`);
	} catch (err) {
		results.push({ name, passed: false, error: err });
		console.error(`[FAIL] ${name}: ${err?.message || err}`);
		throw err;
	}
}

async function api(path, { method = 'GET', token = null, headers = {}, body } = {}) {
	const reqHeaders = { ...headers };
	if (token) reqHeaders.Authorization = `Bearer ${token}`;
	if (body !== undefined && !reqHeaders['Content-Type']) {
		reqHeaders['Content-Type'] = 'application/json';
	}

	const res = await fetch(`${BASE_URL}${path}`, {
		method,
		headers: reqHeaders,
		body: body !== undefined ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined,
	});
	const text = await res.text();
	let json = null;
	try {
		json = JSON.parse(text);
	} catch {
		json = null;
	}
	return { status: res.status, text, json };
}

async function login(username, password = 'password123') {
	return api('/api/v1/auth/login', {
		method: 'POST',
		body: { username, password },
	});
}

function isoHoursFromNow(hours) {
	return new Date(Date.now() + hours * 3600 * 1000).toISOString();
}

async function main() {
	console.log(`Running PBL4 Business Logic E2E Suite against ${BASE_URL}`);

	// Shared state resolved dynamically from seeded data
	const tokens = {};
	let deviceToken = null;
	const state = {
		drivers: {},
		vehicles: {},
		routes: [],
		shifts: [],
		trips: [],
		violations: [],
		disputes: [],
	};

	// =========================================================================
	// GROUP 1: Auth, RBAC & Device Token
	// =========================================================================
	await testCase('1.1 Login seeded accounts (admin, dieuphoi, drivers)', async () => {
		for (const u of ['admin', 'dieuphoi', 'tx_minhtuan', 'tx_hoangnam', 'tx_quocbao']) {
			const r = await login(u);
			assert.equal(r.status, 200, `Login ${u} failed with ${r.status}`);
			tokens[u] = r.json?.token || r.json?.data?.token;
			assert.ok(tokens[u], `Missing token for ${u}`);
		}
	});

	await testCase('1.2 Reject login for LOCKED suspended driver (tx_vanhung)', async () => {
		const r = await login('tx_vanhung');
		assert.equal(r.status, 403);
		assert.equal(r.json?.error?.code, 'FORBIDDEN');
	});

	await testCase('1.3 Enforce RBAC on admin/dispatcher endpoints', async () => {
		const checks = [
			['GET', '/api/v1/auth/users'],
			['PUT', '/api/v1/configs', { min_rest_hours: 9 }],
			['GET', '/api/v1/audit-logs'],
			['POST', '/api/v1/shifts', {}],
		];
		for (const [method, path, body] of checks) {
			const r = await api(path, { method, token: tokens.tx_minhtuan, body });
			assert.equal(r.status, 403, `Expected 403 for DRIVER ${method} ${path}, got ${r.status}`);
			assert.equal(r.json?.error?.code, 'FORBIDDEN');
		}
	});

	await testCase('1.4 Hydrate seeded IDs dynamically & check driver self-only privacy', async () => {
		const [drvRes, vehRes, rteRes, shfRes, trpRes, vioRes, dspRes] = await Promise.all([
			api('/api/v1/drivers', { token: tokens.admin }),
			api('/api/v1/vehicles', { token: tokens.admin }),
			api('/api/v1/routes', { token: tokens.admin }),
			api('/api/v1/shifts', { token: tokens.admin }),
			api('/api/v1/trips', { token: tokens.admin }),
			api('/api/v1/violations', { token: tokens.admin }),
			api('/api/v1/disputes', { token: tokens.admin }),
		]);

		for (const d of drvRes.json.data) {
			if (d.user?.username) state.drivers[d.user.username] = d;
		}
		for (const v of vehRes.json.data) {
			state.vehicles[v.bienSo || v.bien_so_xe] = v;
		}
		state.routes = rteRes.json.data;
		state.shifts = shfRes.json.data;
		state.trips = trpRes.json.data;
		state.violations = vioRes.json.data;
		state.disputes = dspRes.json.data;

		assert.ok(state.drivers.tx_minhtuan, 'Seeded driver tx_minhtuan missing');
		assert.ok(state.drivers.tx_hoangnam, 'Seeded driver tx_hoangnam missing');
		assert.ok(state.drivers.tx_vanhung, 'Seeded driver tx_vanhung missing');
		assert.ok(state.drivers.tx_quocbao, 'Seeded driver tx_quocbao missing');
		assert.ok(state.vehicles['43C-123.45'], 'Seeded vehicle 43C-123.45 missing');
		assert.ok(state.vehicles['92C-888.99'], 'Seeded vehicle 92C-888.99 missing');
		assert.ok(state.vehicles['43C-999.00'], 'Seeded maintenance vehicle 43C-999.00 missing');

		const otherId = state.drivers.tx_hoangnam.id;
		const ownId = state.drivers.tx_minhtuan.id;

		for (const suffix of ['', '/score-logs', '/biometrics']) {
			const denied = await api(`/api/v1/drivers/${otherId}${suffix}`, { token: tokens.tx_minhtuan });
			assert.equal(denied.status, 403, `Expected 403 reading other driver ${suffix}`);
			const allowed = await api(`/api/v1/drivers/${ownId}${suffix}`, { token: tokens.tx_minhtuan });
			assert.equal(allowed.status, 200, `Expected 200 reading own driver ${suffix}`);
		}
	});

	await testCase('1.5 Issue device_token & verify centralized 401 on invalid IoT auth', async () => {
		const badTok = await api('/api/v1/auth/device-token', {
			method: 'POST',
			body: { ma_thiet_bi: 'IOT-DN-01', device_secret: 'wrong_secret' },
		});
		assert.equal(badTok.status, 401);
		assert.equal(badTok.json?.error?.code, 'UNAUTHORIZED');

		const goodTok = await api('/api/v1/auth/device-token', {
			method: 'POST',
			body: { ma_thiet_bi: 'IOT-DN-01', device_secret: IOT_DEVICE_SECRET },
		});
		assert.equal(goodTok.status, 200);
		deviceToken = goodTok.json?.device_token || goodTok.json?.data?.device_token;
		assert.ok(deviceToken, 'Expected device_token JWT');

		const badHb = await api('/api/v1/edge/heartbeat', {
			method: 'POST',
			headers: { 'x-device-token': 'invalid-token' },
			body: { ma_thiet_bi: 'IOT-DN-01', trang_thai_ket_noi: 'ONLINE' },
		});
		assert.equal(badHb.status, 401);
		assert.equal(badHb.json?.success, false);
		assert.equal(badHb.json?.error?.code, 'UNAUTHORIZED');
	});

	// =========================================================================
	// GROUP 2: Smart Scheduling Constraints
	// =========================================================================
	let createdShiftId = null;

	await testCase('2.1 Block shift for SUSPENDED_RED driver (tx_vanhung)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_vanhung.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(24),
				thoiGianKetThucDuKien: isoHoursFromNow(27),
			},
		});
		assert.equal(r.status, 400);
		assert.match(r.json?.message || '', /đình chỉ/i);
	});

	await testCase('2.2 Block shift for MAINTENANCE vehicle (43C-999.00)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_quocbao.id,
				vehicleId: state.vehicles['43C-999.00'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(24),
				thoiGianKetThucDuKien: isoHoursFromNow(27),
			},
		});
		assert.equal(r.status, 400);
		assert.match(r.json?.message || '', /bảo dưỡng/i);
	});

	await testCase('2.3 Block invalid shift window (startTime >= endTime)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_quocbao.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(28),
				thoiGianKetThucDuKien: isoHoursFromNow(26),
			},
		});
		assert.equal(r.status, 400);
	});

	await testCase('2.4 Block overlapping vehicle schedule (43C-123.45)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_quocbao.id,
				vehicleId: state.vehicles['43C-123.45'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(-1),
				thoiGianKetThucDuKien: isoHoursFromNow(0.5),
			},
		});
		assert.equal(r.status, 400);
	});

	await testCase('2.5 Block overlapping driver schedule (tx_minhtuan)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_minhtuan.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(-1),
				thoiGianKetThucDuKien: isoHoursFromNow(0.5),
			},
		});
		assert.equal(r.status, 400);
	});

	await testCase('2.6 Block shift violating minimum rest hours (< 8h after shift1)', async () => {
		const r = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_minhtuan.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: isoHoursFromNow(3), // shift1 ends at +1h -> 2h rest < 8h
				thoiGianKetThucDuKien: isoHoursFromNow(6),
			},
		});
		assert.equal(r.status, 400);
		assert.match(r.json?.message || '', /nghỉ/i);
	});

	await testCase('2.7 Require ly_do_override on biometric warning, allow with override & audit', async () => {
		const tomorrow = new Date(Date.now() + 24 * 3600 * 1000);
		tomorrow.setUTCHours(12, 30, 0, 0);
		const startIso = tomorrow.toISOString();
		const endIso = new Date(tomorrow.getTime() + 5 * 3600 * 1000).toISOString(); // 5h > 4h max

		const blocked = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_quocbao.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: startIso,
				thoiGianKetThucDuKien: endIso,
			},
		});
		assert.equal(blocked.status, 400);
		assert.match(blocked.json?.message || '', /CẢNH BÁO SINH HỌC/);

		const created = await api('/api/v1/shifts', {
			method: 'POST',
			token: tokens.dieuphoi,
			body: {
				driverId: state.drivers.tx_quocbao.id,
				vehicleId: state.vehicles['92C-888.99'].id,
				routeId: state.routes[0].id,
				thoiGianBatDauDuKien: startIso,
				thoiGianKetThucDuKien: endIso,
				ly_do_override: 'Tài xế nghỉ đủ giấc, tăng cường chạy tuyến ngắn.',
			},
		});
		assert.equal(created.status, 201);
		createdShiftId = created.json.data.id;
		assert.ok(created.json.data.canh_bao_khi_tao.length > 0);

		const auditRes = await api('/api/v1/audit-logs?hanhDong=CREATE_SHIFT_OVERRIDE', {
			token: tokens.admin,
		});
		assert.equal(auditRes.status, 200);
		assert.ok(
			auditRes.json.data.some((a) => String(a.doiTuongId) === String(createdShiftId)),
			'Expected CREATE_SHIFT_OVERRIDE audit log',
		);
	});

	await testCase('2.8 Enforce driver ownership on shift respond (403 other, 200 owner)', async () => {
		const denied = await api(`/api/v1/shifts/${createdShiftId}/respond`, {
			method: 'PATCH',
			token: tokens.tx_minhtuan,
			body: { trang_thai: 'ACCEPTED' },
		});
		assert.equal(denied.status, 403);

		const ok = await api(`/api/v1/shifts/${createdShiftId}/respond`, {
			method: 'PATCH',
			token: tokens.tx_quocbao,
			body: { trang_thai: 'ACCEPTED' },
		});
		assert.equal(ok.status, 200);
		assert.equal(ok.json.data.trang_thai, 'ACCEPTED');
	});

	// =========================================================================
	// GROUP 3: Trip Lifecycle, Co-Driver Handover & Safe Trip Reward Guards
	// =========================================================================
	let startedTripId = null;

	await testCase('3.1 Start trip via /trips/start & enforce driver trip privacy', async () => {
		const startRes = await api('/api/v1/trips/start', {
			method: 'POST',
			token: tokens.tx_quocbao,
			body: { shiftId: createdShiftId },
		});
		assert.equal(startRes.status, 200);
		startedTripId = startRes.json.data.id;
		assert.equal(startRes.json.data.trang_thai, 'IN_PROGRESS');

		const otherView = await api(`/api/v1/trips/${startedTripId}`, {
			token: tokens.tx_minhtuan,
		});
		assert.equal(otherView.status, 403);
	});

	await testCase('3.2 Co-driver switch blocks suspended driver & logs valid switch', async () => {
		const blocked = await api(`/api/v1/trips/${startedTripId}/switch-driver`, {
			method: 'POST',
			token: tokens.tx_quocbao,
			body: { newDriverId: state.drivers.tx_vanhung.id },
		});
		assert.equal(blocked.status, 400);

		const ok = await api(`/api/v1/trips/${startedTripId}/switch-driver`, {
			method: 'POST',
			token: tokens.tx_quocbao,
			body: { newDriverId: state.drivers.tx_quocbao.id },
		});
		assert.equal(ok.status, 200);

		const auditRes = await api('/api/v1/audit-logs?hanhDong=SWITCH_CO_DRIVER', {
			token: tokens.admin,
		});
		assert.ok(auditRes.json.data.some((a) => String(a.doiTuongId) === String(startedTripId)));
	});

	await testCase('3.3 Complete trip with empty body (< 30s anti-cheat guard) & no double reward on trip1', async () => {
		const compQuick = await api(`/api/v1/trips/${startedTripId}/complete`, {
			method: 'POST',
			token: tokens.tx_quocbao,
		});
		assert.equal(compQuick.status, 200);
		assert.equal(compQuick.json.data.trang_thai, 'COMPLETED');

		const qbAfter = await api(`/api/v1/drivers/${state.drivers.tx_quocbao.id}`, {
			token: tokens.admin,
		});
		assert.equal(qbAfter.json.data.diem_uy_tin, 85, 'Trip < 30s must not grant safe trip reward');

		const vehAfter = await api('/api/v1/vehicles', { token: tokens.admin });
		const v92 = vehAfter.json.data.find((v) => v.bienSo === '92C-888.99');
		assert.equal(v92.trang_thai, 'AVAILABLE');

		// Complete seeded trip1 (already has CONG_DIEM_THUONG in seed -> must not grant twice)
		const trip1 = state.trips.find((t) => Number(t.driverId) === Number(state.drivers.tx_minhtuan.id));
		assert.ok(trip1, 'Seeded trip1 missing');
		const compTrip1 = await api(`/api/v1/trips/${trip1.id}/complete`, {
			method: 'POST',
			token: tokens.tx_minhtuan,
		});
		assert.equal(compTrip1.status, 200);
		assert.equal(compTrip1.json.data.trang_thai, 'COMPLETED');

		const mtAfter = await api(`/api/v1/drivers/${state.drivers.tx_minhtuan.id}`, {
			token: tokens.admin,
		});
		assert.equal(mtAfter.json.data.diem_uy_tin, 95, 'Already rewarded trip must not grant reward twice');
	});

	// =========================================================================
	// GROUP 4: Edge Violations, Idempotency, AI Thresholds, Capping & Review
	// =========================================================================
	let trip2Id = null;
	let autoConfirmedViolationId = null;

	await testCase('4.1 Edge violations: thresholds (>=0.85, 0.6-0.85, <0.6) & Idempotency-Key', async () => {
		const trip2 = state.trips.find((t) => Number(t.driverId) === Number(state.drivers.tx_hoangnam.id));
		assert.ok(trip2, 'Seeded trip2 missing');
		trip2Id = trip2.id;

		const key1 = `E2E_EVT_${Date.now()}_A`;
		const r1 = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken, 'Idempotency-Key': key1 },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.92,
				client_event_id: key1,
			},
		});
		assert.equal(r1.status, 201);
		assert.equal(r1.json.data.trang_thai_xac_thuc, 'CONFIRMED');
		autoConfirmedViolationId = r1.json.data.id;

		// Resend exact same client_event_id -> 200 Idempotent, no duplicate deduction
		const r1Dup = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken, 'Idempotency-Key': key1 },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.92,
				client_event_id: key1,
			},
		});
		assert.equal(r1Dup.status, 200);
		assert.equal(r1Dup.json.data.id, autoConfirmedViolationId);

		// Medium confidence -> PENDING_MANUAL_REVIEW
		const rMed = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.72,
				client_event_id: `E2E_EVT_${Date.now()}_MED`,
			},
		});
		assert.equal(rMed.status, 201);
		assert.equal(rMed.json.data.trang_thai_xac_thuc, 'PENDING_MANUAL_REVIEW');
		assert.equal(rMed.json.data.trang_thai_diem, 'NOT_APPLIED');

		// Low confidence -> REJECTED
		const rLow = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.40,
				client_event_id: `E2E_EVT_${Date.now()}_LOW`,
			},
		});
		assert.equal(rLow.status, 201);
		assert.equal(rLow.json.data.trang_thai_xac_thuc, 'REJECTED');

		const hn = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}`, {
			token: tokens.admin,
		});
		assert.equal(hn.json.data.diem_uy_tin, 48, 'Expected 58 - 10 = 48 (idempotency + pending/rejected untouched)');
	});

	await testCase('4.2 Penalty Capping (penalty_cap_per_trip = 30)', async () => {
		// Seed already deducted 7 on trip2 + 10 in test 4.1 = 17.
		// Next MICROSLEEP (-10) -> total 27 (score 38)
		const r2 = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.95,
				client_event_id: `E2E_EVT_${Date.now()}_CAP1`,
			},
		});
		assert.equal(r2.status, 201);
		assert.equal(r2.json.data.diem_bi_tru, 10);
		assert.equal(r2.json.data.trang_thai_diem, 'APPLIED');

		// Next MICROSLEEP (raw 10) -> capped to 3 (total 30, score 35)
		const r3 = await api('/api/v1/edge/violations', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken },
			body: {
				trip_id: trip2Id,
				ma_loai: 'MICROSLEEP',
				ai_confidence_score: 0.95,
				client_event_id: `E2E_EVT_${Date.now()}_CAP2`,
			},
		});
		assert.equal(r3.status, 201);
		assert.equal(r3.json.data.diem_bi_tru, 3);
		assert.equal(r3.json.data.trang_thai_diem, 'CAPPED');

		const hn = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}`, {
			token: tokens.admin,
		});
		assert.equal(hn.json.data.diem_uy_tin, 35);
	});

	await testCase('4.3 Dispatcher manual review: reject false alarm refunds +10đ & confirm pending', async () => {
		const rej = await api(`/api/v1/violations/${autoConfirmedViolationId}/review`, {
			method: 'PATCH',
			token: tokens.dieuphoi,
			body: {
				trang_thai_xac_thuc: 'REJECTED',
				ghi_chu_duyet: 'Xác nhận báo động giả trong E2E test.',
			},
		});
		assert.equal(rej.status, 200);
		assert.equal(rej.json.data.trang_thai_xac_thuc, 'REJECTED');

		const hnAfterReject = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}`, {
			token: tokens.admin,
		});
		assert.equal(hnAfterReject.json.data.diem_uy_tin, 45);

		const v1 = state.violations.find((v) => v.clientEventId === '550e8400-e29b-41d4-a716-446655440601');
		assert.ok(v1, 'Seeded PENDING_MANUAL_REVIEW violation v1 missing');
		const conf = await api(`/api/v1/violations/${v1.id}/review`, {
			method: 'PATCH',
			token: tokens.dieuphoi,
			body: {
				trang_thai_xac_thuc: 'CONFIRMED',
				ghi_chu_duyet: 'Xác nhận thủ công qua video E2E.',
			},
		});
		assert.equal(conf.status, 200);
		assert.equal(conf.json.data.trang_thai_xac_thuc, 'CONFIRMED');
	});

	// =========================================================================
	// GROUP 5: Disputes & Four-Eyes Principle
	// =========================================================================
	await testCase('5.1 Reject duplicate dispute & cross-driver dispute', async () => {
		const v2 = state.violations.find((v) => v.clientEventId === '550e8400-e29b-41d4-a716-446655440602');
		assert.ok(v2, 'Seeded v2 missing');

		const dup = await api('/api/v1/disputes', {
			method: 'POST',
			token: tokens.tx_hoangnam,
			body: {
				violationId: v2.id,
				maLyDo: 'FALSE_DETECTION',
				noiDungGiaiTrinh: 'Gửi trùng lần 2',
			},
		});
		assert.equal(dup.status, 400);

		const cross = await api('/api/v1/disputes', {
			method: 'POST',
			token: tokens.tx_minhtuan,
			body: {
				violationId: v2.id,
				maLyDo: 'FALSE_DETECTION',
				noiDungGiaiTrinh: 'Khiếu nại hộ người khác',
			},
		});
		assert.equal(cross.status, 403);
	});

	await testCase('5.2 Enforce Four-Eyes Principle (reviewer cannot resolve own dispute; Admin approves & refunds)', async () => {
		const v2 = state.violations.find((v) => v.clientEventId === '550e8400-e29b-41d4-a716-446655440602');
		const d1 = state.disputes.find((d) => Number(d.violationId) === Number(v2.id));
		assert.ok(d1, 'Seeded dispute d1 on v2 missing');

		const hnBefore = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}`, {
			token: tokens.admin,
		});
		const scoreBefore = hnBefore.json.data.diem_uy_tin;

		// dieuphoi reviewed v2 in seed -> blocked by Four-Eyes
		const fourEyesBlock = await api(`/api/v1/disputes/${d1.id}/resolve`, {
			method: 'PATCH',
			token: tokens.dieuphoi,
			body: {
				trang_thai: 'APPROVED',
				phan_hoi: 'Thử tự duyệt lại khiếu nại',
			},
		});
		assert.equal(fourEyesBlock.status, 403);
		assert.match(fourEyesBlock.json?.message || '', /Four-Eyes/i);

		// admin approves & refunds +5đ
		const approved = await api(`/api/v1/disputes/${d1.id}/resolve`, {
			method: 'PATCH',
			token: tokens.admin,
			body: {
				trang_thai: 'APPROVED',
				phan_hoi: 'Admin duyệt hoàn điểm theo Four-Eyes.',
			},
		});
		assert.equal(approved.status, 200);
		assert.equal(approved.json.data.trang_thai, 'APPROVED');

		const hnAfter = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}`, {
			token: tokens.admin,
		});
		assert.equal(hnAfter.json.data.diem_uy_tin, Math.min(100, scoreBefore + 5));

		const logsRes = await api(`/api/v1/drivers/${state.drivers.tx_hoangnam.id}/score-logs`, {
			token: tokens.admin,
		});
		assert.ok(
			logsRes.json.data.some((l) => l.loai_thay_doi === 'HOAN_DIEM_KHIEU_NAI'),
			'Expected HOAN_DIEM_KHIEU_NAI in driver score logs',
		);

		const reResolve = await api(`/api/v1/disputes/${d1.id}/resolve`, {
			method: 'PATCH',
			token: tokens.admin,
			body: { trang_thai: 'REJECTED', phan_hoi: 'Không được xử lý lần 2' },
		});
		assert.equal(reResolve.status, 400);
	});

	// =========================================================================
	// GROUP 6: Anti-Tampering Heartbeat, Driver Lock/Unlock & Dynamic AI Config
	// =========================================================================
	await testCase('6.1 ERROR_CAMERA heartbeat auto-creates DEVICE_TAMPERING violation', async () => {
		const hb = await api('/api/v1/edge/heartbeat', {
			method: 'POST',
			headers: { 'x-device-token': deviceToken },
			body: {
				ma_thiet_bi: 'IOT-DN-02',
				trang_thai_ket_noi: 'ERROR_CAMERA',
				cpu_temp_c: 68.4,
				free_storage_mb: 19000,
			},
		});
		assert.equal(hb.status, 200);

		const vioRes = await api(`/api/v1/violations?tripId=${trip2Id}`, {
			token: tokens.admin,
		});
		assert.ok(
			vioRes.json.data.some((v) => v.ma_loai === 'DEVICE_TAMPERING'),
			'Expected auto-created DEVICE_TAMPERING violation on active trip2',
		);
	});

	await testCase('6.2 Admin lock (SUSPENDED_RED) and unlock (NORMAL) driver with AuditLog', async () => {
		const qbId = state.drivers.tx_quocbao.id;
		const locked = await api(`/api/v1/drivers/${qbId}/status`, {
			method: 'PATCH',
			token: tokens.admin,
			body: { cap_canh_bao: 'SUSPENDED_RED', ly_do: 'E2E test lock' },
		});
		assert.equal(locked.status, 200);
		assert.ok(locked.json.data.diem_uy_tin <= 15);
		assert.equal(locked.json.data.cap_canh_bao, 'SUSPENDED_RED');

		const unlocked = await api(`/api/v1/drivers/${qbId}/status`, {
			method: 'PATCH',
			token: tokens.admin,
			body: { cap_canh_bao: 'NORMAL', ly_do: 'E2E test unlock' },
		});
		assert.equal(unlocked.status, 200);
		assert.equal(unlocked.json.data.diem_uy_tin, 85);

		const auditRes = await api('/api/v1/audit-logs', { token: tokens.admin });
		assert.ok(auditRes.json.data.some((a) => a.hanhDong === 'SUSPEND_DRIVER'));
		assert.ok(auditRes.json.data.some((a) => a.hanhDong === 'UNLOCK_DRIVER'));
	});

	await testCase('6.3 Dynamic AI config update & restore', async () => {
		const upd = await api('/api/v1/configs', {
			method: 'PUT',
			token: tokens.admin,
			body: { min_rest_hours: 9.5 },
		});
		assert.equal(upd.status, 200);
		assert.equal(upd.json.data.min_rest_hours, 9.5);

		const getCfg = await api('/api/v1/configs', { token: tokens.admin });
		assert.equal(getCfg.status, 200);
		assert.equal(getCfg.json.data.min_rest_hours, 9.5);

		const restore = await api('/api/v1/configs', {
			method: 'PUT',
			token: tokens.admin,
			body: { min_rest_hours: 8.0 },
		});
		assert.equal(restore.status, 200);
		assert.equal(restore.json.data.min_rest_hours, 8.0);
	});

	// =========================================================================
	// GROUP 7: Post-Mutation Security Hygiene
	// =========================================================================
	await testCase('7.1 Zero passwordHash leakage after all mutations', async () => {
		for (const ep of [
			'/api/v1/drivers',
			'/api/v1/violations',
			'/api/v1/disputes',
			'/api/v1/audit-logs',
			'/api/v1/shifts',
			'/api/v1/trips',
			'/api/v1/vehicles',
		]) {
			const r = await api(ep, { token: tokens.admin });
			assert.equal(r.status, 200);
			assert.ok(!r.text.includes('passwordHash'), `passwordHash leaked in ${ep}`);
		}
	});

	const passedCount = results.filter((r) => r.passed).length;
	console.log(`\nSUMMARY: PASSED ${passedCount}/${results.length} business-logic E2E cases.`);
}

main().catch((err) => {
	console.error('\nE2E_SUITE_FAILED:', err);
	process.exit(1);
});
