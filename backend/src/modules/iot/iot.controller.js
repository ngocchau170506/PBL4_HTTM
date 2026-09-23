import * as iotService from './iot.service.js';

export const handleIngestTelemetry = async (req, res) => {
  try {
    const { tripId, deviceId, kinhDo, viDo, tocDo, co2Level, aiViolationFlag } = req.body;
    if (!deviceId || kinhDo === undefined || viDo === undefined || tocDo === undefined) {
      return res.status(400).json({ message: 'Vui lòng cung cấp deviceId, kinhDo, viDo và tocDo.' });
    }

    const result = await iotService.processIotDataPacket({
      tripId,
      deviceId,
      kinhDo,
      viDo,
      tocDo,
      co2Level,
      aiViolationFlag,
    });
    return res.status(201).json(result);
  } catch (error) {
    return res.status(500).json({ message: error.message });
  }
};
