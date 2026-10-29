import * as tripsService from './trips.service.js';

export const getTrips = async (req, res) => {
  try {
    const { driverId, status } = req.query;
    let targetDriverId = driverId;
    if (req.user.role === 'DRIVER') {
      targetDriverId = req.user.driver?.userId || req.user.id;
    }

    const trips = await tripsService.getAllTrips({ driverId: targetDriverId, status });
    res.json({ success: true, data: trips, trips });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const getMyActiveTrip = async (req, res) => {
  try {
    const driverId = req.user.driver?.userId || req.user.id;
    const activeTrip = await tripsService.getDriverActiveTrip(driverId);
    res.json({ success: true, data: activeTrip, trip: activeTrip });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const getTripById = async (req, res) => {
  try {
    const trip = await tripsService.getTripById(req.params.id);
    if (!trip) {
      return res.status(404).json({ success: false, message: 'Chuyến xe không tồn tại.' });
    }
    res.json({ success: true, data: trip, trip });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createTrip = async (req, res) => {
  try {
    const trip = await tripsService.createTrip(req.body);
    res.status(201).json({ success: true, data: trip, trip });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

export const updateTripStatus = async (req, res) => {
  try {
    const { id } = req.params;
    const { trangThai, trang_thai } = req.body;
    const status = trangThai || trang_thai;
    if (!status) {
      return res.status(400).json({ success: false, message: 'Vui lòng cung cấp trạng thái mới cho chuyến xe.' });
    }
    const trip = await tripsService.updateTripStatus(id, status);
    res.json({ success: true, data: trip, trip });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

export const getPlayback = async (req, res) => {
  try {
    const playbackData = await tripsService.getTripPlaybackData(req.params.id);
    res.json({ success: true, data: playbackData });
  } catch (error) {
    res.status(404).json({ success: false, message: error.message });
  }
};
