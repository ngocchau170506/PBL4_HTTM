import * as disputeService from './disputes.service.js';

export const handleGetDisputes = async (req, res, next) => {
  try {
    const disputes = await disputeService.getAllDisputes(req.query);
    res.json({ success: true, data: disputes });
  } catch (error) {
    next(error);
  }
};

export const handleCreateDispute = async (req, res, next) => {
  try {
    const dispute = await disputeService.createDispute({
      ...req.body,
      driverId: req.body.driverId || req.user.id,
    });
    res.status(201).json({ success: true, data: dispute });
  } catch (error) {
    next(error);
  }
};

export const handleResolveDispute = async (req, res, next) => {
  try {
    const resolved = await disputeService.resolveDispute(req.params.id, req.body, req.user);
    res.json({ success: true, data: resolved });
  } catch (error) {
    next(error);
  }
};
