/**
 * Tiện ích Địa lý & Tính toán Tọa độ GPS (PBL4 Fleet)
 */

// Bán kính Trái Đất theo km
const EARTH_RADIUS_KM = 6371;

/**
 * Tính khoảng cách đường chim bay giữa 2 tọa độ GPS bằng công thức Haversine (km)
 */
export const calculateDistanceKm = (lat1, lon1, lat2, lon2) => {
  const toRad = (angle) => (angle * Math.PI) / 180;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);

  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);

  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

  return parseFloat((EARTH_RADIUS_KM * c).toFixed(2));
};

/**
 * Tìm trạm dừng chân gần nhất từ tọa độ hiện tại của xe và danh sách trạm
 */
export const findNearestRestStop = ({
  currentLat,
  currentLng,
  restStops = [],
  currentSpeedKmH = 60,
  onsetLat = null,
  onsetLng = null,
  onsetTimestamp = null,
}) => {
  const effectiveSpeed = Math.max(30, currentSpeedKmH || 60);

  if (!restStops || restStops.length === 0) {
    const destLat = 16.1485;
    const destLng = 108.1322;
    const stopName = 'Trạm Dừng Nghỉ Nam Hải Vân (Km 22)';
    const kmRemaining = calculateDistanceKm(currentLat, currentLng, destLat, destLng);
    const minutesRemaining = Math.max(1, Math.round((kmRemaining / effectiveSpeed) * 60));

    const kmAtOnset = (onsetLat && onsetLng)
      ? calculateDistanceKm(onsetLat, onsetLng, destLat, destLng)
      : parseFloat((kmRemaining + 1.2).toFixed(2));
    const kmTraveled = (onsetLat && onsetLng)
      ? calculateDistanceKm(onsetLat, onsetLng, currentLat, currentLng)
      : 1.2;

    return {
      tenTram: stopName,
      name: stopName,
      ten: stopName,
      kmConLai: kmRemaining,
      kmRemaining: kmRemaining,
      kmAtOnset,
      kmBanDau: kmAtOnset,
      kmTraveledSinceOnset: kmTraveled,
      kmDaDi: kmTraveled,
      onsetGps: onsetLat && onsetLng ? { lat: onsetLat, lng: onsetLng, timestamp: onsetTimestamp } : null,
      phutConLai: minutesRemaining,
      etaMinutes: minutesRemaining,
      vi_do: destLat,
      kinh_do: destLng,
      lat: destLat,
      lng: destLng,
      googleMapsUrl: `https://www.google.com/maps/dir/?api=1&destination=${destLat},${destLng}`,
      thongBaoGiongNoi: `Cảnh báo an toàn! Còn khoảng ${kmRemaining} km (${minutesRemaining} phút) nữa sẽ đến ${stopName}.`,
    };
  }

  let nearest = null;
  let minDistance = Infinity;

  for (const stop of restStops) {
    const stopLat = stop.vi_do || stop.lat || stop.viDo;
    const stopLng = stop.kinh_do || stop.lng || stop.kinhDo;

    if (stopLat !== undefined && stopLng !== undefined) {
      const dist = calculateDistanceKm(currentLat, currentLng, stopLat, stopLng);
      if (dist < minDistance) {
        minDistance = dist;
        nearest = { ...stop, stopLat, stopLng };
      }
    }
  }

  if (!nearest) {
    nearest = restStops[0];
    minDistance = nearest.kmMarker || 5.0;
  }

  const kmRemaining = parseFloat(minDistance.toFixed(1));
  const minutesRemaining = Math.max(1, Math.round((kmRemaining / effectiveSpeed) * 60));
  const stopName = nearest.ten || nearest.tenTram || 'Trạm dừng chân an toàn';
  const destLat = nearest.stopLat || nearest.vi_do || 16.1485;
  const destLng = nearest.stopLng || nearest.kinh_do || 108.1322;

  const kmAtOnset = (onsetLat && onsetLng)
    ? calculateDistanceKm(onsetLat, onsetLng, destLat, destLng)
    : parseFloat((kmRemaining + 1.2).toFixed(2));
  const kmTraveled = (onsetLat && onsetLng)
    ? calculateDistanceKm(onsetLat, onsetLng, currentLat, currentLng)
    : 1.2;

  return {
    tenTram: stopName,
    name: stopName,
    ten: stopName,
    kmConLai: kmRemaining,
    kmRemaining: kmRemaining,
    kmAtOnset,
    kmBanDau: kmAtOnset,
    kmTraveledSinceOnset: kmTraveled,
    kmDaDi: kmTraveled,
    onsetGps: onsetLat && onsetLng ? { lat: onsetLat, lng: onsetLng, timestamp: onsetTimestamp } : null,
    km_marker: nearest.km_marker || nearest.kmMarker,
    phutConLai: minutesRemaining,
    etaMinutes: minutesRemaining,
    vi_do: destLat,
    kinh_do: destLng,
    lat: destLat,
    lng: destLng,
    googleMapsUrl: `https://www.google.com/maps/dir/?api=1&destination=${destLat},${destLng}`,
    thongBaoGiongNoi: `Cảnh báo an toàn! Còn ${kmRemaining} km (khoảng ${minutesRemaining} phút) nữa sẽ đến ${stopName}. Vui lòng giảm tốc độ để dừng xe nghỉ ngơi hoặc đổi ca lái!`,
  };
};
