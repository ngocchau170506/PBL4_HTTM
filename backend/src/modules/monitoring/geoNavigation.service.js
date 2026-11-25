const OVERPASS_ENDPOINTS = [
	'https://overpass-api.de/api/interpreter',
	'https://overpass.kumi.systems/api/interpreter',
];
const OSRM_BASE_URL = 'https://router.project-osrm.org/route/v1/driving';

const CACHE_TTL_MS = 10 * 60 * 1000;
const nearbyCache = new Map();

export const haversineKm = (lat1, lng1, lat2, lng2) => {
	const toRad = (deg) => (deg * Math.PI) / 180;
	const R = 6371;
	const dLat = toRad(lat2 - lat1);
	const dLng = toRad(lng2 - lng1);
	const a =
		Math.sin(dLat / 2) ** 2 +
		Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
	return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
};

export const buildGoogleMapsUrl = (lat, lng) => {
	if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
	return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
};

const fetchWithTimeout = async (url, options = {}, timeoutMs = 4500) => {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		return await fetch(url, { ...options, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
};

const classifyOsmStop = (tags = {}) => {
	if (tags.highway === 'rest_area' || tags.highway === 'services') {
		return {
			category: 'REST_AREA',
			categoryLabel: 'Trạm dừng nghỉ',
			priority: 0,
			defaultName: 'Trạm dừng nghỉ tuyến đường',
		};
	}
	if (tags.amenity === 'fuel') {
		return {
			category: 'FUEL',
			categoryLabel: 'Trạm xăng dầu / Dừng đỗ',
			priority: 1,
			defaultName: tags.brand ? `Trạm xăng ${tags.brand}` : 'Trạm xăng dầu',
		};
	}
	return {
		category: 'PARKING',
		categoryLabel: 'Bãi đỗ xe an toàn',
		priority: 2,
		defaultName: 'Bãi đỗ xe',
	};
};

const queryOverpassRadius = async (lat, lng, radiusMeters) => {
	const ql = `[out:json][timeout:4];(nwr(around:${radiusMeters},${lat},${lng})["highway"~"^(rest_area|services)$"];nwr(around:${radiusMeters},${lat},${lng})["amenity"="fuel"];nwr(around:${radiusMeters},${lat},${lng})["amenity"="parking"];);out center 25;`;

	for (const endpoint of OVERPASS_ENDPOINTS) {
		try {
			const res = await fetchWithTimeout(
				endpoint,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
					body: `data=${encodeURIComponent(ql)}`,
				},
				4500,
			);
			if (!res.ok) continue;
			const data = await res.json();
			if (Array.isArray(data?.elements)) {
				return data.elements;
			}
		} catch {
			// Try next Overpass mirror
		}
	}
	return null;
};

const normalizeFallbackStops = (fallbackStops, originLat, originLng) => {
	return (Array.isArray(fallbackStops) ? fallbackStops : [])
		.map((s, idx) => {
			const lat = Number(s.vi_do ?? s.lat);
			const lng = Number(s.kinh_do ?? s.lng);
			if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
			const dist = haversineKm(originLat, originLng, lat, lng);
			return {
				id: `db-${idx}`,
				name: s.ten || s.name || `Trạm dừng #${idx + 1}`,
				category: 'REST_AREA',
				categoryLabel: 'Trạm trên tuyến (DB)',
				lat,
				lng,
				distanceKm: Number(dist.toFixed(1)),
				etaMinutes: Math.max(1, Math.round((dist * 1.25 / 55) * 60)),
				source: 'ROUTE_DB',
				googleMapsUrl: buildGoogleMapsUrl(lat, lng),
			};
		})
		.filter(Boolean)
		.sort((a, b) => a.distanceKm - b.distanceKm);
};

export const findNearbyRestStops = async ({
	lat,
	lng,
	fallbackStops = [],
	limit = 3,
}) => {
	const cacheKey = `${lat.toFixed(2)},${lng.toFixed(2)}:${limit}`;
	const cached = nearbyCache.get(cacheKey);
	if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
		return cached.stops;
	}

	let elements = await queryOverpassRadius(lat, lng, 8000);
	if (Array.isArray(elements) && elements.length === 0) {
		elements = await queryOverpassRadius(lat, lng, 20000);
	}

	const mappedOsm = [];
	if (Array.isArray(elements) && elements.length > 0) {
		for (const el of elements) {
			const sLat = Number(el.lat ?? el.center?.lat);
			const sLng = Number(el.lon ?? el.center?.lon);
			if (!Number.isFinite(sLat) || !Number.isFinite(sLng)) continue;

			const tags = el.tags || {};
			const cls = classifyOsmStop(tags);
			const name = tags.name || tags['name:vi'] || cls.defaultName;
			const dist = haversineKm(lat, lng, sLat, sLng);

			// Deduplicate very close stops (< 150m)
			const duplicate = mappedOsm.some(
				(x) => haversineKm(x.lat, x.lng, sLat, sLng) < 0.15,
			);
			if (duplicate) continue;

			mappedOsm.push({
				id: `osm-${el.type || 'n'}-${el.id}`,
				name,
				category: cls.category,
				categoryLabel: cls.categoryLabel,
				priority: cls.priority,
				lat: Number(sLat.toFixed(6)),
				lng: Number(sLng.toFixed(6)),
				distanceKm: Number(dist.toFixed(1)),
				etaMinutes: Math.max(1, Math.round((dist * 1.25 / 55) * 60)),
				source: 'OSM_OVERPASS',
				googleMapsUrl: buildGoogleMapsUrl(sLat, sLng),
			});
		}
	}

	mappedOsm.sort((a, b) => {
		// Prefer dedicated highway rest_area within 3km extra, otherwise closest distance
		const scoreA = a.distanceKm + a.priority * 1.5;
		const scoreB = b.distanceKm + b.priority * 1.5;
		return scoreA - scoreB;
	});

	let stops = mappedOsm.slice(0, limit).map(({ priority, ...rest }) => rest);

	if (stops.length === 0) {
		stops = normalizeFallbackStops(fallbackStops, lat, lng).slice(0, limit);
	}

	if (stops.length > 0) {
		nearbyCache.set(cacheKey, { ts: Date.now(), stops });
	}

	return stops;
};

export const computeDrivingRoute = async ({
	fromLat,
	fromLng,
	toLat,
	toLng,
}) => {
	const straightKm = haversineKm(fromLat, fromLng, toLat, toLng);
	const fallbackDistKm = Number(Math.max(0.2, straightKm * 1.2).toFixed(1));
	const fallbackEta = Math.max(1, Math.round((fallbackDistKm / 55) * 60));
	const fallbackResult = {
		distanceKm: fallbackDistKm,
		etaMinutes: fallbackEta,
		geometry: [
			[fromLat, fromLng],
			[toLat, toLng],
		],
		source: 'STRAIGHT_LINE_FALLBACK',
	};

	try {
		const url = `${OSRM_BASE_URL}/${fromLng},${fromLat};${toLng},${toLat}?overview=full&geometries=geojson`;
		const res = await fetchWithTimeout(url, {}, 4500);
		if (!res.ok) return fallbackResult;

		const data = await res.json();
		const route = data?.routes?.[0];
		const coords = route?.geometry?.coordinates;
		if (!route || !Array.isArray(coords) || coords.length < 2) {
			return fallbackResult;
		}

		return {
			distanceKm: Number(((route.distance || 0) / 1000).toFixed(1)),
			etaMinutes: Math.max(1, Math.round((route.duration || 60) / 60)),
			geometry: coords.map(([lng, lat]) => [lat, lng]),
			source: 'OSRM',
		};
	} catch {
		return fallbackResult;
	}
};
