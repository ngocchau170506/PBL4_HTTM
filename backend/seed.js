import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcrypt';

const prisma = new PrismaClient();

async function main() {
  console.log('🌱 Seeding PBL4 Fleet Database Matching Frontend Schema & SRS Specification...');

  // 1. Clean existing tables in order
  await prisma.auditLog.deleteMany();
  await prisma.dispute.deleteMany();
  await prisma.driverCalibrationData.deleteMany();
  await prisma.driverBiometricProfile.deleteMany();
  await prisma.driverScoreLog.deleteMany();
  await prisma.violation.deleteMany();
  await prisma.iotDataLog.deleteMany();
  await prisma.trip.deleteMany();
  await prisma.shift.deleteMany();
  await prisma.iotDevice.deleteMany();
  await prisma.vehicle.deleteMany();
  await prisma.route.deleteMany();
  await prisma.driver.deleteMany();
  await prisma.user.deleteMany();
  await prisma.systemConfig.deleteMany();

  const saltRounds = 10;
  const commonPassword = await bcrypt.hash('password123', saltRounds);

  // 2. System Config
  console.log('⚙️ Creating System Config...');
  await prisma.systemConfig.create({
    data: {
      fleetScope: 'GLOBAL',
      microsleepEyeClosedSec: 1.5,
      headDownSec: 1.0,
      yawnCountThreshold: 3,
      phoneUseSec: 3.0,
      co2WarningThresholdPpm: 1200.0,
      mq135WarmupSec: 180,
      autoConfirmMin: 0.85,
      manualReviewMin: 0.60,
      weightLandmarks: 0.4,
      weightCnn: 0.4,
      weightEdge: 0.2,
      penaltyCapPerTrip: 30,
      penaltyCapPerDay: 50,
      manualReviewSlaHours: 24,
      disputeWindowHours: 48,
      minRestHours: 8.0,
      timezone: 'Asia/Ho_Chi_Minh',
    },
  });

  // 3. Users (Admin, Dispatcher & Drivers)
  console.log('👥 Creating Users (Admin, Dispatcher & Drivers)...');
  const adminUser = await prisma.user.create({
    data: {
      username: 'admin',
      passwordHash: commonPassword,
      hoTen: 'Nguyễn Quản Trị',
      sdt: '0905111222',
      role: 'ADMIN',
      trangThai: 'ACTIVE',
    },
  });

  const dispatcherUser = await prisma.user.create({
    data: {
      username: 'dieuphoi',
      passwordHash: commonPassword,
      hoTen: 'Trần Điều Phối',
      sdt: '0905222333',
      role: 'DISPATCHER',
      trangThai: 'ACTIVE',
    },
  });

  // Driver 1: Lê Minh Tuấn (An toàn - Xanh 95đ)
  const driver1User = await prisma.user.create({
    data: {
      username: 'tx_minhtuan',
      passwordHash: commonPassword,
      hoTen: 'Lê Minh Tuấn',
      sdt: '0905333444',
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: 'FC-790012345',
          hangBangLai: 'FC',
          uyTin: 95,
          capCanhBao: 'NORMAL',
          trangThaiTaiXe: 'ON_TRIP',
        },
      },
    },
    include: { driver: true },
  });

  // Driver 2: Phạm Hoàng Nam (Cảnh cáo vàng 58đ)
  const driver2User = await prisma.user.create({
    data: {
      username: 'tx_hoangnam',
      passwordHash: commonPassword,
      hoTen: 'Phạm Hoàng Nam',
      sdt: '0905444555',
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: 'C-430054321',
          hangBangLai: 'C',
          uyTin: 58,
          capCanhBao: 'WARNING_YELLOW',
          trangThaiTaiXe: 'ON_TRIP',
        },
      },
    },
    include: { driver: true },
  });

  // Driver 3: Đặng Văn Hùng (Báo động đỏ 15đ - Đình chỉ)
  const driver3User = await prisma.user.create({
    data: {
      username: 'tx_vanhung',
      passwordHash: commonPassword,
      hoTen: 'Đặng Văn Hùng',
      sdt: '0905555666',
      role: 'DRIVER',
      trangThai: 'LOCKED',
      driver: {
        create: {
          bangLai: 'FC-430098765',
          hangBangLai: 'FC',
          uyTin: 15,
          capCanhBao: 'SUSPENDED_RED',
          trangThaiTaiXe: 'OFF_DUTY',
        },
      },
    },
    include: { driver: true },
  });

  // Driver 4: Võ Quốc Bảo (Bình thường 85đ)
  const driver4User = await prisma.user.create({
    data: {
      username: 'tx_quocbao',
      passwordHash: commonPassword,
      hoTen: 'Võ Quốc Bảo',
      sdt: '0905666777',
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: 'C-920011223',
          hangBangLai: 'C',
          uyTin: 85,
          capCanhBao: 'NORMAL',
          trangThaiTaiXe: 'AVAILABLE',
        },
      },
    },
    include: { driver: true },
  });

  // 4. Vehicles & IoT Devices
  console.log('🚚 Creating Vehicles & IoT Devices...');
  const vehicle1 = await prisma.vehicle.create({
    data: {
      bienSo: '43C-123.45',
      loaiXe: 'Đầu kéo Container 40ft',
      taiTrongTan: 32.5,
      trangThai: 'IN_USE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:3C:4D:5E',
          maThietBi: 'IOT-DN-01',
          cpuSerial: '10000000a1b2c3d4',
          phienBan: 'fw-v1.2.0',
          aiModelVersion: 'yolo26n-v1.1',
          trangThaiKetNoi: 'ONLINE',
          cpuTempC: 64.5,
          freeStorageMb: 24500,
          lastHeartbeatAt: new Date(),
        },
      },
    },
    include: { iotDevice: true },
  });

  const vehicle2 = await prisma.vehicle.create({
    data: {
      bienSo: '43H-567.89',
      loaiXe: 'Xe tải thùng 8 tấn',
      taiTrongTan: 8.0,
      trangThai: 'IN_USE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:3C:99:88',
          maThietBi: 'IOT-DN-02',
          cpuSerial: '10000000e5f6a7b8',
          phienBan: 'fw-v1.2.0',
          aiModelVersion: 'yolo26n-v1.1',
          trangThaiKetNoi: 'ONLINE',
          cpuTempC: 73.2,
          freeStorageMb: 18200,
          lastHeartbeatAt: new Date(),
        },
      },
    },
    include: { iotDevice: true },
  });

  const vehicle3 = await prisma.vehicle.create({
    data: {
      bienSo: '92C-888.99',
      loaiXe: 'Xe tải ISUZU 5 tấn',
      taiTrongTan: 5.0,
      trangThai: 'AVAILABLE',
      iotDevice: {
        create: {
          macAddress: '00:1A:2B:3C:77:66',
          maThietBi: 'IOT-QN-01',
          cpuSerial: '100000009c8b7a6f',
          phienBan: 'fw-v1.1.5',
          aiModelVersion: 'yolo26n-v1.0',
          trangThaiKetNoi: 'OFFLINE',
          cpuTempC: 55.0,
          freeStorageMb: 28900,
          lastHeartbeatAt: new Date(Date.now() - 3600 * 1000 * 10),
        },
      },
    },
    include: { iotDevice: true },
  });

  const vehicle4 = await prisma.vehicle.create({
    data: {
      bienSo: '43C-999.00',
      loaiXe: 'Đầu kéo Hyundai Xcient',
      taiTrongTan: 30.0,
      trangThai: 'MAINTENANCE', // Đang bảo dưỡng
    },
  });

  // 5. Routes
  console.log('🛣️ Creating Routes...');
  const route1 = await prisma.route.create({
    data: {
      tenTuyen: 'Cảng Tiên Sa (Đà Nẵng) - KCN Phú Bài (Huế)',
      khoangCachKm: 95.5,
      danhSachTramDungChan: JSON.stringify([
        { ten: 'Trạm dừng nghỉ Nam Hải Vân (Km 22)', vi_do: 16.1485, kinh_do: 108.1322, km_marker: 22 },
        { ten: 'Trạm dừng nghỉ Bắc Hải Vân (Lăng Cô)', vi_do: 16.2331, kinh_do: 108.0789, km_marker: 38 },
        { ten: 'Trạm dịch vụ Hòa Nhơn', vi_do: 16.3210, kinh_do: 107.8215, km_marker: 65 },
      ]),
    },
  });

  const route2 = await prisma.route.create({
    data: {
      tenTuyen: 'KCN Hòa Khánh (Đà Nẵng) - KCN VSIP (Quảng Ngãi)',
      khoangCachKm: 138.0,
      danhSachTramDungChan: JSON.stringify([
        { ten: 'Trạm dừng nghỉ Cao tốc CT01 (Km 36)', vi_do: 15.7921, kinh_do: 108.3105, km_marker: 36 },
        { ten: 'Trạm dịch vụ Tam Kỳ (Km 72)', vi_do: 15.5642, kinh_do: 108.4812, km_marker: 72 },
      ]),
    },
  });

  // 6. Shifts & Trips
  console.log('📅 Creating Shifts & Trips...');
  const shift1 = await prisma.shift.create({
    data: {
      driverId: driver1User.id,
      vehicleId: vehicle1.id,
      routeId: route1.id,
      thoiGianBatDauDuKien: new Date(Date.now() - 3600 * 1000 * 3),
      thoiGianKetThucDuKien: new Date(Date.now() + 3600 * 1000 * 1),
      trangThai: 'IN_PROGRESS',
      createdBy: dispatcherUser.id,
      canhBaoKhiTao: JSON.stringify([]),
    },
  });

  const shift2 = await prisma.shift.create({
    data: {
      driverId: driver2User.id,
      vehicleId: vehicle2.id,
      routeId: route2.id,
      thoiGianBatDauDuKien: new Date(Date.now() - 3600 * 1000 * 2),
      thoiGianKetThucDuKien: new Date(Date.now() + 3600 * 1000 * 3),
      trangThai: 'IN_PROGRESS',
      createdBy: dispatcherUser.id,
      lyDoOverride: 'Tài xế cam kết dừng nghỉ 20 phút tại trạm dừng Tam Kỳ.',
      canhBaoKhiTao: JSON.stringify([
        'Thời lượng ca vượt mức khuyến nghị của Hồ sơ sinh học (180 phút).',
        'Ca chạy giao thoa với khung giờ yếu sinh học (13:00 - 15:00).',
      ]),
    },
  });

  const trip1 = await prisma.trip.create({
    data: {
      shiftId: shift1.id,
      driverId: driver1User.id,
      vehicleId: vehicle1.id,
      routeId: route1.id,
      thoiGianBatDau: new Date(Date.now() - 3600 * 1000 * 3),
      trangThai: 'IN_PROGRESS',
    },
  });

  const trip2 = await prisma.trip.create({
    data: {
      shiftId: shift2.id,
      driverId: driver2User.id,
      vehicleId: vehicle2.id,
      routeId: route2.id,
      thoiGianBatDau: new Date(Date.now() - 3600 * 1000 * 2),
      trangThai: 'IN_PROGRESS',
    },
  });

  // 7. IoT Telemetry Logs
  console.log('📡 Generating Telemetry Logs...');
  await prisma.iotDataLog.createMany({
    data: [
      { tripId: trip1.id, deviceId: vehicle1.iotDevice.id, kinhDo: 108.1284, viDo: 16.1652, tocDo: 42.5, co2Level: 680.0, thoiGianGhiNhan: new Date() },
      { tripId: trip2.id, deviceId: vehicle2.iotDevice.id, kinhDo: 108.2519, viDo: 15.8801, tocDo: 78.0, co2Level: 1340.0, thoiGianGhiNhan: new Date() },
    ],
  });

  // 8. Violations
  console.log('🚨 Creating Violations with Full Lifecycle & Video...');
  const v1 = await prisma.violation.create({
    data: {
      clientEventId: '550e8400-e29b-41d4-a716-446655440601',
      tripId: trip2.id,
      driverId: driver2User.id,
      loaiViPham: 'MICROSLEEP',
      maLoai: 'MICROSLEEP',
      tenLoai: 'Ngủ gật (Microsleep)',
      diemTru: 10,
      videoUrl: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
      videoDuration: 10,
      trangThai: 'PENDING_MANUAL_REVIEW',
      trangThaiDiem: 'NOT_APPLIED',
      nguonQuyetDinh: null,
      aiConfidenceScore: 0.76,
      moTa: 'YOLO26 phát hiện mắt nhắm liên tục 1.8s. Điểm EAR Cloud = 0.14. Cần Điều phối viên xác nhận qua video.',
    },
  });

  const v2 = await prisma.violation.create({
    data: {
      clientEventId: '550e8400-e29b-41d4-a716-446655440602',
      tripId: trip2.id,
      driverId: driver2User.id,
      loaiViPham: 'PHONE_DISTRACTION',
      maLoai: 'PHONE_DISTRACTION',
      tenLoai: 'Dùng điện thoại khi lái xe',
      diemTru: 5,
      videoUrl: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4',
      videoDuration: 10,
      trangThai: 'CONFIRMED',
      trangThaiDiem: 'APPLIED',
      nguonQuyetDinh: 'AI',
      reviewedBy: dispatcherUser.id,
      reviewedAt: new Date(Date.now() - 3600 * 1000 * 1),
      aiConfidenceScore: 0.92,
      moTa: 'Phát hiện tay cầm điện thoại áp tai liên tục 4.2 giây khi xe chạy 75 km/h.',
    },
  });

  const v3 = await prisma.violation.create({
    data: {
      clientEventId: '550e8400-e29b-41d4-a716-446655440604',
      tripId: trip2.id,
      driverId: driver2User.id,
      loaiViPham: 'HIGH_CO2',
      maLoai: 'HIGH_CO2',
      tenLoai: 'Nồng độ CO2 buồng lái cao',
      diemTru: 2,
      videoUrl: null,
      trangThai: 'CONFIRMED',
      trangThaiDiem: 'APPLIED',
      nguonQuyetDinh: 'SYSTEM_RULE',
      aiConfidenceScore: 1.0,
      moTa: 'Cảm biến buồng lái ghi nhận CO2 đạt 1,340 ppm kéo dài quá 5 phút.',
    },
  });

  // 9. Disputes (SRS 4.1 Four-Eyes Principle)
  console.log('⚖️ Creating Disputes for Mobile App...');
  await prisma.dispute.create({
    data: {
      violationId: v2.id,
      driverId: driver2User.id,
      maLyDo: 'FALSE_DETECTION',
      tenLyDo: 'AI nhận diện nhầm hành vi',
      noiDungGiaiTrinh: 'Lúc 03:30 xe đi qua trạm thu phí, tôi đưa tay lên gãi tai và chỉnh lại tai nghe đàm thoại bộ đàm đội xe chứ không hề cầm điện thoại cá nhân. Mong điều phối viên xem kỹ lại video giúp tôi.',
      trangThai: 'OPEN',
    },
  });

  await prisma.dispute.create({
    data: {
      violationId: v3.id,
      driverId: driver2User.id,
      maLyDo: 'SENSOR_ERROR',
      tenLyDo: 'Nhiễu cảm biến môi trường',
      noiDungGiaiTrinh: 'Thời điểm đó xe đang dừng chờ xếp hàng sau một xe đầu kéo xả khói đen ở trạm cân nên khói lọt vào cabin làm cảm biến MQ-135 nhảy vọt, sau đó tôi đã bật quạt hút gió ngay.',
      trangThai: 'OPEN',
    },
  });

  // 10. Driver Score Logs
  console.log('📈 Creating Driver Score Logs...');
  await prisma.driverScoreLog.createMany({
    data: [
      {
        driverId: driver1User.id,
        loaiThayDoi: 'CONG_DIEM_THUONG',
        soDiemThayDoi: 5,
        diemTruoc: 90,
        diemSau: 95,
        lyDo: 'Thưởng hoàn thành chuyến đi an toàn (> 50km, không có lỗi vi phạm)',
        tripId: trip1.id,
        createdAt: new Date(Date.now() - 3600 * 1000 * 24),
      },
      {
        driverId: driver2User.id,
        loaiThayDoi: 'TRU_DIEM',
        soDiemThayDoi: -5,
        diemTruoc: 65,
        diemSau: 60,
        lyDo: 'Vi phạm PHONE_DISTRACTION: Dùng điện thoại khi lái xe',
        violationId: v2.id,
        tripId: trip2.id,
        createdAt: new Date(Date.now() - 3600 * 1000 * 1),
      },
      {
        driverId: driver2User.id,
        loaiThayDoi: 'TRU_DIEM',
        soDiemThayDoi: -2,
        diemTruoc: 60,
        diemSau: 58,
        lyDo: 'Vi phạm HIGH_CO2: Cảm biến CO2 buồng lái cao (1,340 ppm)',
        violationId: v3.id,
        tripId: trip2.id,
        createdAt: new Date(),
      },
    ],
  });

  // 11. Biometric Profiles
  console.log('🧬 Creating 30-Day Biometric Profiles...');
  await prisma.driverBiometricProfile.create({
    data: {
      driverId: driver1User.id,
      khungGioYeuSinhHoc: JSON.stringify(['02:00 - 04:00']),
      thoiGianLaiToiDaKhuyenNghi: 240,
      thoiGianLaiTrungBinhTruocMetMoi: 215,
      tanSuatViPham30Ngay: JSON.stringify({ MICROSLEEP: 0, YAWNING_FATIGUE: 1, HIGH_CO2: 1, PHONE_DISTRACTION: 0, DEVICE_TAMPERING: 0 }),
      danhGiaChung: 'Thể trạng xuất sắc, nhịp sinh học ổn định. Thích hợp phân bổ các ca đêm đường dài xuyên tỉnh.',
      kyTinhToanTu: new Date(Date.now() - 30 * 24 * 3600 * 1000),
      kyTinhToanDen: new Date(),
    },
  });

  await prisma.driverBiometricProfile.create({
    data: {
      driverId: driver2User.id,
      khungGioYeuSinhHoc: JSON.stringify(['13:00 - 15:00', '01:00 - 04:00']),
      thoiGianLaiToiDaKhuyenNghi: 180,
      thoiGianLaiTrungBinhTruocMetMoi: 145,
      tanSuatViPham30Ngay: JSON.stringify({ MICROSLEEP: 2, YAWNING_FATIGUE: 5, HIGH_CO2: 3, PHONE_DISTRACTION: 2, DEVICE_TAMPERING: 0 }),
      danhGiaChung: 'Điểm uy tín 58 (Cảnh cáo vàng). Nhịp sinh học giảm rõ rệt đầu giờ chiều. Đang bị giảm 30% tần suất ca đêm.',
      kyTinhToanTu: new Date(Date.now() - 30 * 24 * 3600 * 1000),
      kyTinhToanDen: new Date(),
    },
  });

  // 12. Audit Logs
  console.log('📜 Creating Audit Logs...');
  await prisma.auditLog.createMany({
    data: [
      {
        userId: dispatcherUser.id,
        userName: 'Trần Điều Phối',
        actorType: 'USER',
        hanhDong: 'REVIEW_VIOLATION',
        doiTuongLoai: 'violation',
        doiTuongId: String(v2.id),
        giaTriTruoc: JSON.stringify({ trang_thai_xac_thuc: 'PENDING_MANUAL_REVIEW' }),
        giaTriSau: JSON.stringify({ trang_thai_xac_thuc: 'CONFIRMED', diem_bi_tru: 5 }),
        lyDo: 'Xác nhận tài xế dùng điện thoại áp tai qua video camera buồng lái.',
      },
      {
        userId: adminUser.id,
        userName: 'Nguyễn Quản Trị',
        actorType: 'USER',
        hanhDong: 'UPDATE_SYSTEM_CONFIG',
        doiTuongLoai: 'system_configs',
        doiTuongId: 'GLOBAL',
        giaTriTruoc: JSON.stringify({ co2_warning_threshold_ppm: 1000 }),
        giaTriSau: JSON.stringify({ co2_warning_threshold_ppm: 1200 }),
        lyDo: 'Nâng ngưỡng cảnh báo CO2 mùa hè theo khuyến cáo kỹ thuật',
      },
    ],
  });

  console.log('🎉 Seed completed successfully! Database now matches Frontend 100%!');
}

main()
  .catch((e) => {
    console.error('❌ Error during seed:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
