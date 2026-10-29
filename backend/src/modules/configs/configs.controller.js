import * as configService from './configs.service.js';

export const handleGetAiConfig = async (req, res, next) => {
  try {
    const config = await configService.getAiConfig();
    res.json({ success: true, data: config });
  } catch (error) {
    next(error);
  }
};

export const handleUpdateAiConfig = async (req, res, next) => {
  try {
    const updated = await configService.updateAiConfig(req.body, req.user);
    res.json({ success: true, data: updated });
  } catch (error) {
    next(error);
  }
};
