import prisma from '../../common/prisma.js';
import { sendViolationEmailAlert } from '../../common/email.service.js';

export const getAllViolations = async ({ driverId, tripId }) => {
  const where = {};
  if (driverId) where.driverId = parseInt(driverId, 10);
  if (tripId) where.tripId = parseInt(tripId, 10);

  return prisma.violation.findMany({
    where,
    include: {
      driver: {
        include: { user: true },
      },
      trip: {
        include: {
          vehicle: true,
          route: true,
        },
      },
    },
    orderBy: { thoiGianViPham: 'desc' },
  });
};

export const createViolationRecord = async ({ driverId, tripId, loaiViPham, diemTru, videoUrl, videoDuration, moTa }) => {
  const parsedDriverId = parseInt(driverId, 10);
  const parsedTripId = parseInt(tripId, 10);
  const ptsToDeduct = parseInt(diemTru || 5, 10);

  // 1. Transaction to insert violation & deduct points
  const [violation, updatedDriver] = await prisma.$transaction([
    prisma.violation.create({
      data: {
        driverId: parsedDriverId,
        tripId: parsedTripId,
        loaiViPham,
        diemTru: ptsToDeduct,
        videoUrl: videoUrl || null,
        videoDuration: videoDuration ? parseInt(videoDuration, 10) : null,
        moTa: moTa || 'Ghi nhận vi phạm thủ công bởi cấp quản lý.',
      },
      include: {
        driver: { include: { user: true } },
        trip: { include: { vehicle: true, route: true } },
      },
    }),
    prisma.driver.update({
      where: { userId: parsedDriverId },
      data: {
        uyTin: {
          decrement: ptsToDeduct,
        },
      },
    }),
  ]);

  // Send Email Alert
  sendViolationEmailAlert({
    driverName: violation.driver.user.hoTen,
    driverEmail: violation.driver.user.username + '@gmail.com',
    bienSo: violation.trip.vehicle.bienSo,
    loaiViPham: violation.loaiViPham,
    diemTru: ptsToDeduct,
    uyTinConLai: updatedDriver.uyTin,
    tocDo: 'N/A',
    videoUrl: violation.videoUrl,
  }).catch((e) => console.error(e));

  return { violation, updatedDriver };
};

export const getDriverRankings = async () => {
  return prisma.driver.findMany({
    include: {
      user: true,
      violations: {
        orderBy: { thoiGianViPham: 'desc' },
      },
    },
    orderBy: { uyTin: 'desc' },
  });
};
