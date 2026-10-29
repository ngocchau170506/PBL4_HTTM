import * as shiftsService from './shifts.service.js';

export const getShifts = async (req, res) => {
  try {
    const { driverId, status } = req.query;
    let targetDriverId = driverId;
    if (req.user.role === 'DRIVER') {
      targetDriverId = req.user.driver?.userId || req.user.id;
    }

    const shifts = await shiftsService.getAllShifts({ driverId: targetDriverId, status });
    res.json({ success: true, data: shifts, shifts });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createShift = async (req, res) => {
  try {
    const shift = await shiftsService.createShift({
      ...req.body,
      createdBy: req.user.id,
    });
    res.status(201).json({ success: true, data: shift });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

export const respondToShift = async (req, res) => {
  try {
    const { id } = req.params;
    const shift = await shiftsService.respondToShift(id, req.body);
    res.json({ success: true, data: shift });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};
