import * as tripsService from './trips.service.js';

export const getTrips = async (req, res) => {
  try {
    const { driverId, status } = req.query;
    let targetDriverId = driverId;
    if (req.user.role === 'DRIVER') {
      targetDriverId = req.user.driver?.userId || req.user.id;
    }

    const trips = await tripsService.getAllTrips({ driverId: targetDriverId, status });
    res.json({ trips });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const getMyActiveTrip = async (req, res) => {
  try {
    const driverId = req.user.driver?.userId || req.user.id;
    const trip = await tripsService.getDriverActiveTrip(driverId);
    res.json({ trip });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const getTripById = async (req, res) => {
  try {
    const trip = await tripsService.getTripById(req.params.id);
    if (!trip) return res.status(404).json({ message: 'Chuyến xe không tồn tại.' });
    res.json({ trip });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const createTrip = async (req, res) => {
  try {
    const { driverId, vehicleId, routeId } = req.body;
    if (!driverId || !vehicleId || !routeId) {
      return res.status(400).json({ message: 'Vui lòng cung cấp driverId, vehicleId và routeId.' });
    }
    const trip = await tripsService.createTrip({ driverId, vehicleId, routeId });
    res.status(201).json({ trip });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const updateTripStatus = async (req, res) => {
  try {
    const { id } = req.params;
    const { trangThai } = req.body;
    const trip = await tripsService.updateTripStatus(id, trangThai);
    res.json({ trip });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const getPlayback = async (req, res) => {
  try {
    const { id } = req.params;
    const playbackData = await tripsService.getTripPlaybackData(id);
    res.json(playbackData);
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};
