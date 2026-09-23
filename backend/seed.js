import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcrypt';

const prisma = new PrismaClient();

async function main() {
  console.log('🌱 Starting Seeding PBL4 Fleet Database...');

  // 1. Clean existing records
  await prisma.violation.deleteMany();
  await prisma.iotDataLog.deleteMany();
  await prisma.trip.deleteMany();
  await prisma.iotDevice.deleteMany();
  await prisma.vehicle.deleteMany();
  await prisma.route.deleteMany();
  await prisma.driver.deleteMany();
  await prisma.manager.deleteMany();
  await prisma.user.deleteMany();

  const saltRounds = 10;
  const commonPassword = await bcrypt.hash('password123', saltRounds);

  // 2. Seed Users & Roles
  console.log('👥 Creating Users (Manager & Drivers)...');
  const adminUser = await prisma.user.create({
    data: {
      username: 'admin',
      passwordHash: commonPassword,
      hoTen: 'Nguyễn Văn Quản Lý',
      sdt: '0905123456',
      role: 'MANAGER',
      trangThai: 'ACTIVE',
      manager: {
        create: {
          vaiTro: 'ADMIN',
        },
      },
    },
  });

  const driver1User = await prisma.user.create({
    data: {
      username: 'driver1',
      passwordHash: commonPassword,
      hoTen: 'Trần Văn Tài (Tài Xế 1)',
      sdt: '0914888999',
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: 'B2 - 88991122',
          uyTin: 95,
          trangThaiTaiXe: 'ON_TRIP',
        },
      },
    },
  });

  const driver2User = await prisma.user.create({
    data: {
      username: 'driver2',
      passwordHash: commonPassword,
      hoTen: 'Lê Hoàng Long (Tài Xế 2)',
      sdt: '0987654321',
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: 'FC - 99001133',
          uyTin: 100,
          trangThaiTaiXe: 'AVAILABLE',
        },
      },
    },
  });

  // 3. Seed Vehicles & IoT Devices
  console.log('🚚 Creating Vehicles & IoT Devices...');
  const vehicle1 = await prisma.vehicle.create({
    data: {
      bienSo: '43C-123.45',
      loaiXe: 'Xe Tải 5 Tấn',
      trangThai: 'IN_USE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:3C:4D:5E',
          phienBan: 'v2.1-GPS-4G',
        },
      },
    },
    include: { iotDevice: true },
  });

  const vehicle2 = await prisma.vehicle.create({
    data: {
      bienSo: '92C-678.90',
      loaiXe: 'Xe Container 40ft',
      trangThai: 'AVAILABLE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:3C:99:88',
          phienBan: 'v2.2-GPS-5G',
        },
      },
    },
    include: { iotDevice: true },
  });

  const vehicle3 = await prisma.vehicle.create({
    data: {
      bienSo: '29H-543.21',
      loaiXe: 'Xe Bán Tải D-Max',
      trangThai: 'MAINTENANCE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:77:88:99',
          phienBan: 'v1.8-GPS',
        },
      },
    },
    include: { iotDevice: true },
  });

  // 4. Seed Routes
  console.log('🛣️ Creating Routes...');
  const route1 = await prisma.route.create({
    data: {
      tenTuyen: 'Tuyến Đà Nẵng ➔ Huế (Qua hầm Hải Vân)',
      khoangCachKm: 105.5,
    },
  });

  const route2 = await prisma.route.create({
    data: {
      tenTuyen: 'Tuyến Đà Nẵng ➔ Quy Nhơn (QL1A)',
      khoangCachKm: 302.0,
    },
  });

  const route3 = await prisma.route.create({
    data: {
      tenTuyen: 'Tuyến TP. Hồ Chí Minh ➔ Đà Lạt',
      khoangCachKm: 310.0,
    },
  });

  // 5. Seed Trips
  console.log('📦 Creating Operations Trips...');
  const trip1 = await prisma.trip.create({
    data: {
      driverId: driver1User.id,
      vehicleId: vehicle1.id,
      routeId: route1.id,
      thoiGianBatDau: new Date(Date.now() - 3600 * 1000 * 2), // 2 hours ago
      trangThai: 'IN_PROGRESS',
    },
  });

  const trip2 = await prisma.trip.create({
    data: {
      driverId: driver2User.id,
      vehicleId: vehicle2.id,
      routeId: route2.id,
      thoiGianBatDau: new Date(Date.now() - 3600 * 1000 * 24),
      thoiGianKetThuc: new Date(Date.now() - 3600 * 1000 * 18),
      trangThai: 'COMPLETED',
    },
  });

  // 6. Seed IoT Data Logs for Route Playback (Route 1 Đà Nẵng ➔ Huế)
  console.log('📡 Generating IoT Data Logs for Route Playback...');
  const playbackCoords = [
    { lat: 16.0544, lng: 108.2022, speed: 45 }, // Đà Nẵng Center
    { lat: 16.0700, lng: 108.1950, speed: 60 },
    { lat: 16.1000, lng: 108.1500, speed: 75 },
    { lat: 16.1350, lng: 108.1150, speed: 92 }, // Over-speed segment (92 km/h)
    { lat: 16.1700, lng: 108.0800, speed: 88 }, // Over-speed segment
    { lat: 16.2000, lng: 108.0500, speed: 50 }, // Hầm Hải Vân
    { lat: 16.2500, lng: 108.0000, speed: 65 },
    { lat: 16.3500, lng: 107.8500, speed: 70 },
    { lat: 16.4637, lng: 107.5909, speed: 40 }, // Huế City Center
  ];

  let logTime = new Date(Date.now() - 3600 * 1000 * 2);
  for (const pt of playbackCoords) {
    await prisma.iotDataLog.create({
      data: {
        tripId: trip1.id,
        deviceId: vehicle1.iotDevice.id,
        kinhDo: pt.lng,
        viDo: pt.lat,
        tocDo: pt.speed,
        thoiGianGhiNhan: new Date(logTime),
      },
    });
    logTime = new Date(logTime.getTime() + 10 * 60 * 1000); // 10 minutes interval
  }

  // 7. Seed Violation with Video Evidence Stream URL
  console.log('🚨 Creating Sample Violation Record...');
  await prisma.violation.create({
    data: {
      driverId: driver1User.id,
      tripId: trip1.id,
      loaiViPham: 'QUÁ TỐC ĐỘ (92 km/h - Giới hạn 80 km/h)',
      diemTru: 5,
      videoUrl: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
      videoDuration: 15,
      thoiGianViPham: new Date(Date.now() - 3600 * 1000 * 1.5),
      moTa: 'Phát hiện thiết bị IoT ghi nhận tốc độ 92.4 km/h tại đoạn đường Hải Vân - Huế. Hệ thống đã tự động trừ 5 điểm uy tín.',
    },
  });

  console.log('✅ Database Seeding Completed Successfully!');
  console.log('--------------------------------------------------');
  console.log('🔑 TEST ACCOUNTS CREATED:');
  console.log('👉 MANAGER: username = admin    | password = password123');
  console.log('👉 DRIVER 1: username = driver1  | password = password123');
  console.log('👉 DRIVER 2: username = driver2  | password = password123');
  console.log('--------------------------------------------------');
}

main()
  .catch((e) => {
    console.error('❌ Seeding Error:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
