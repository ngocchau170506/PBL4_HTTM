import prisma from '../../common/prisma.js';

export const getAllTrips = async ({ driverId, status }) => {
  const where = {};
  if (driverId) where.driverId = parseInt(driverId, 10);
  if (status) where.trangThai = status;

  return prisma.trip.findMany({
    where,
    include: {
      driver: {
        include: { user: true },
      },
      vehicle: {
        include: { iotDevice: true },
      },
      route: true,
      violations: true,
    },
    orderBy: { createdAt: 'desc' },
  });
};

export const getDriverActiveTrip = async (driverId) => {
  const id = parseInt(driverId, 10);
  const activeTrip = await prisma.trip.findFirst({
    where: {
      driverId: id,
      trangThai: 'IN_PROGRESS',
    },
    include: {
      driver: { include: { user: true } },
      vehicle: { include: { iotDevice: true } },
      route: true,
      violations: true,
    },
  });

  if (activeTrip) return activeTrip;

  return prisma.trip.findFirst({
    where: {
      driverId: id,
      trangThai: 'PLANNED',
    },
    include: {
      driver: { include: { user: true } },
      vehicle: { include: { iotDevice: true } },
      route: true,
      violations: true,
    },
    orderBy: { createdAt: 'desc' },
  });
};

export const getTripById = async (id) => {
  return prisma.trip.findUnique({
    where: { id: parseInt(id, 10) },
    include: {
      driver: {
        include: { user: true },
      },
      vehicle: {
        include: { iotDevice: true },
      },
      route: true,
      violations: true,
    },
  });
};

export const createTrip = async ({ driverId, vehicleId, routeId }) => {
  const parsedDriverId = parseInt(driverId, 10);
  const parsedVehicleId = parseInt(vehicleId, 10);

  // Check if driver or vehicle is currently on another IN_PROGRESS trip
  const existingDriverTrip = await prisma.trip.findFirst({
    where: { driverId: parsedDriverId, trangThai: 'IN_PROGRESS' },
  });
  if (existingDriverTrip) {
    throw new Error('Tài xế này hiện đang trong một chuyến xe khác chưa hoàn thành.');
  }

  const existingVehicleTrip = await prisma.trip.findFirst({
    where: { vehicleId: parsedVehicleId, trangThai: 'IN_PROGRESS' },
  });
  if (existingVehicleTrip) {
    throw new Error('Phương tiện này hiện đang trong một chuyến xe khác chưa hoàn thành.');
  }

  const trip = await prisma.trip.create({
    data: {
      driverId: parsedDriverId,
      vehicleId: parsedVehicleId,
      routeId: parseInt(routeId, 10),
      trangThai: 'PLANNED',
    },
    include: {
      driver: { include: { user: true } },
      vehicle: true,
      route: true,
    },
  });

  return trip;
};

export const updateTripStatus = async (id, trangThai) => {
  const tripId = parseInt(id, 10);
  const trip = await prisma.trip.findUnique({ where: { id: tripId } });
  if (!trip) throw new Error('Chuyến xe không tồn tại.');

  const data = { trangThai };

  if (trangThai === 'IN_PROGRESS') {
    // Check constraint before starting
    const activeDriverTrip = await prisma.trip.findFirst({
      where: { driverId: trip.driverId, trangThai: 'IN_PROGRESS', id: { not: tripId } },
    });
    if (activeDriverTrip) {
      throw new Error('Tài xế đang có một chuyến xe khác đang chạy (IN_PROGRESS). Vui lòng kết thúc chuyến cũ trước.');
    }

    data.thoiGianBatDau = new Date();
    await prisma.driver.update({ where: { userId: trip.driverId }, data: { trangThaiTaiXe: 'ON_TRIP' } });
    await prisma.vehicle.update({ where: { id: trip.vehicleId }, data: { trangThai: 'IN_USE' } });
  } else if (trangThai === 'COMPLETED' || trangThai === 'CANCELLED') {
    data.thoiGianKetThuc = new Date();
    await prisma.driver.update({ where: { userId: trip.driverId }, data: { trangThaiTaiXe: 'AVAILABLE' } });
    await prisma.vehicle.update({ where: { id: trip.vehicleId }, data: { trangThai: 'AVAILABLE' } });
  }

  return prisma.trip.update({
    where: { id: tripId },
    data,
    include: {
      driver: { include: { user: true } },
      vehicle: true,
      route: true,
    },
  });
};

export const getTripPlaybackData = async (tripId) => {
  const id = parseInt(tripId, 10);
  const trip = await prisma.trip.findUnique({
    where: { id },
    include: {
      route: true,
      vehicle: { include: { iotDevice: true } },
      driver: { include: { user: true } },
      violations: true,
    },
  });

  if (!trip) {
    throw new Error('Không tìm thấy chuyến xe.');
  }

  const logs = await prisma.iotDataLog.findMany({
    where: { tripId: id },
    orderBy: { thoiGianGhiNhan: 'asc' },
  });

  return {
    trip,
    logs,
    violations: trip.violations,
  };
};
