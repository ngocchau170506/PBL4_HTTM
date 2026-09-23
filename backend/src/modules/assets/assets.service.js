import prisma from '../../common/prisma.js';

// Vehicles
export const getAllVehicles = async () => {
  return prisma.vehicle.findMany({
    include: {
      iotDevice: true,
    },
    orderBy: { id: 'desc' },
  });
};

export const createVehicle = async ({ bienSo, loaiXe }) => {
  return prisma.vehicle.create({
    data: { bienSo, loaiXe, trangThai: 'AVAILABLE' },
  });
};

export const updateVehicleStatus = async (id, trangThai) => {
  return prisma.vehicle.update({
    where: { id: parseInt(id, 10) },
    data: { trangThai },
  });
};

// IoT Devices
export const getAllIotDevices = async () => {
  return prisma.iotDevice.findMany({
    include: {
      vehicle: true,
    },
    orderBy: { id: 'desc' },
  });
};

export const createIotDevice = async ({ macAddress, phienBan, vehicleId }) => {
  return prisma.iotDevice.create({
    data: {
      macAddress,
      phienBan,
      vehicleId: vehicleId ? parseInt(vehicleId, 10) : null,
    },
  });
};

export const assignDeviceToVehicle = async (deviceId, vehicleId) => {
  return prisma.iotDevice.update({
    where: { id: parseInt(deviceId, 10) },
    data: { vehicleId: vehicleId ? parseInt(vehicleId, 10) : null },
  });
};

// Routes
export const getAllRoutes = async () => {
  return prisma.route.findMany({
    orderBy: { id: 'desc' },
  });
};

export const createRoute = async ({ tenTuyen, khoangCachKm }) => {
  return prisma.route.create({
    data: {
      tenTuyen,
      khoangCachKm: khoangCachKm ? parseFloat(khoangCachKm) : 0,
    },
  });
};
