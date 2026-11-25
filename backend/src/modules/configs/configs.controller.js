import * as configService from './configs.service.js';
import { asyncHandler } from '../../common/middleware/index.js';

export const handleGetAiConfig = asyncHandler(async (_req, res) => {
	const config = await configService.getAiConfig();
	res.json({ success: true, data: config });
});

export const handleUpdateAiConfig = asyncHandler(async (req, res) => {
	const updated = await configService.updateAiConfig(req.body, req.user);
	res.json({ success: true, data: updated });
});
