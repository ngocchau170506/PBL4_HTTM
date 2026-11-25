const BASE_URL = (process.env.BASE_URL || 'http://localhost').replace(/\/$/, '');
const IOT_DEVICE_SECRET =
	process.env.IOT_DEVICE_SECRET || 'your_iot_device_secret_token';
const DEVICE_ID = process.env.SIM_DEVICE_ID || 'IOT-DN-02';

// Tuyến mô phỏng dọc cao tốc Đà Nẵng -> Quảng Ngãi (CT01)
const WAYPOINTS = [
	{ lat: 15.9652, lng: 108.2085, speed: 68, co2: 820 },
	{ lat: 15.9445, lng: 108.2212, speed: 72, co2: 960 },
	{ lat: 15.9218, lng: 108.2345, speed: 75, co2: 1120 },
	{ lat: 15.9012, lng: 108.2438, speed: 77, co2: 1260 },
	{ lat: 15.8801, lng: 108.2519, speed: 79, co2: 1360 },
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function postEdge(path, body, extraHeaders = {}) {
	const res = await fetch(`${BASE_URL}${path}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'x-device-token': IOT_DEVICE_SECRET,
			...extraHeaders,
		},
		body: JSON.stringify(body),
	});
	const json = await res.json().catch(() => ({}));
	if (!res.ok) {
		throw new Error(`${path} HTTP ${res.status}: ${json.message || 'Unknown error'}`);
	}
	return json;
}

async function main() {
	console.log(`Simulating Edge Hardware (${DEVICE_ID}) -> ${BASE_URL}`);

	for (let i = 0; i < WAYPOINTS.length; i++) {
		const wp = WAYPOINTS[i];
		await postEdge('/api/v1/edge/telemetry', {
			device_id: DEVICE_ID,
			vi_do_gps: wp.lat,
			kinh_do_gps: wp.lng,
			toc_do_kmh: wp.speed,
			co2_ppm: wp.co2,
		});
		console.log(
			`[${i + 1}/${WAYPOINTS.length}] Telemetry sent: lat=${wp.lat}, lng=${wp.lng}, speed=${wp.speed} km/h, CO2=${wp.co2} ppm`,
		);
		await sleep(1500);
	}

	const key = `SIM_EDGE_${Date.now()}`;
	const vio = await postEdge(
		'/api/v1/edge/violations',
		{
			device_id: DEVICE_ID,
			ma_loai: 'MICROSLEEP',
			ai_confidence_score: 0.93,
			client_event_id: key,
			mo_ta: '[SIM-PI4] Mắt nhắm liên tục 1.9s trên cao tốc CT01 (EAR=0.14)',
		},
		{ 'Idempotency-Key': key },
	);
	console.log(
		`Violation triggered: #${vio.data?.id} (${vio.data?.ma_loai}) -> check Web Map & Mobile App!`,
	);
}

main().catch((err) => {
	console.error('SIMULATION_FAILED:', err.message);
	process.exit(1);
});
