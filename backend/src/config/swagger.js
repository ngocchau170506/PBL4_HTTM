import swaggerJsdoc from 'swagger-jsdoc';
import swaggerUi from 'swagger-ui-express';

const swaggerDefinition = {
  openapi: '3.0.0',
  components: {
    securitySchemes: {
      BearerAuth: {
        type: 'http',
        scheme: 'bearer',
        bearerFormat: 'JWT',
        description: 'Nhập JWT Access Token nhận được sau khi đăng nhập (không cần gõ tiền tố Bearer).',
      },
      DeviceTokenAuth: {
        type: 'apiKey',
        in: 'header',
        name: 'x-device-token',
        description: 'Token bí mật phần cứng IoT (ESP32 / Raspberry Pi 4). Mặc định: your_iot_device_secret_token',
      },
    },
    schemas: {
      User: {
        type: 'object',
        properties: {
          id: { type: 'integer', example: 1 },
          username: { type: 'string', example: 'driver01' },
          hoTen: { type: 'string', example: 'Nguyễn Văn Hùng' },
          sdt: { type: 'string', example: '0905123456' },
          role: { type: 'string', enum: ['ADMIN', 'MANAGER', 'DRIVER'], example: 'DRIVER' },
          trangThai: { type: 'string', enum: ['ACTIVE', 'INACTIVE', 'BANNED'], example: 'ACTIVE' },
        },
      },
      ViolationType: {
        type: 'object',
        properties: {
          id: { type: 'integer', example: 1 },
          code: { type: 'string', example: 'BUON_NGU' },
          name: { type: 'string', example: 'Ngủ gật / Mất tập trung kéo dài (Microsleep)' },
          icon: { type: 'string', example: '🚨' },
          defaultPoints: { type: 'integer', example: 10 },
          dieuKien: { type: 'string', example: 'Mắt nhắm liên tục >= 2 giây hoặc góc đầu gục đột ngột' },
          xuLyOffline: { type: 'string', example: 'Còi báo động khẩn cấp trong cabin, kích rung ghế, cắt video 10s' },
          description: { type: 'string', example: 'Mô hình AI YOLO phát hiện tài xế nhắm mắt liên tục >= 2 giây.' },
        },
      },
      Violation: {
        type: 'object',
        properties: {
          id: { type: 'integer', example: 101 },
          driverId: { type: 'integer', example: 1 },
          tripId: { type: 'integer', example: 1 },
          loaiViPham: { type: 'string', example: 'BUON_NGU' },
          violationTypeId: { type: 'integer', example: 1 },
          typeCode: { type: 'string', example: 'BUON_NGU' },
          typeName: { type: 'string', example: 'Ngủ gật / Mất tập trung kéo dài (Microsleep)' },
          typeIcon: { type: 'string', example: '🚨' },
          diemTru: { type: 'integer', example: 10 },
          trangThai: {
            type: 'string',
            enum: ['CONFIRMED', 'PENDING_MANUAL_REVIEW', 'MANUALLY_CONFIRMED', 'MANUALLY_REJECTED', 'REJECTED'],
            example: 'CONFIRMED',
          },
          aiConfidenceScore: { type: 'number', example: 0.95 },
          videoUrl: { type: 'string', example: 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4' },
          thoiGianViPham: { type: 'string', format: 'date-time' },
          moTa: { type: 'string', example: '🚨 Phát hiện nhắm mắt liên tục > 2 giây.' },
        },
      },
      SystemConfig: {
        type: 'object',
        properties: {
          aiConfidenceConfirmThreshold: { type: 'number', example: 0.85 },
          aiConfidenceRejectThreshold: { type: 'number', example: 0.60 },
          co2PpmThreshold: { type: 'number', example: 1000 },
          minRestHours: { type: 'number', example: 8.0 },
          eyeClosedDurationSeconds: { type: 'number', example: 2 },
          yawnDurationSeconds: { type: 'number', example: 3 },
        },
      },
      ApiResponse: {
        type: 'object',
        properties: {
          message: { type: 'string', example: 'Thao tác thành công.' },
        },
      },
    },
  },
  paths: {
    // ==========================================
    // 1. AUTHENTICATION & USERS
    // ==========================================
    '/api/auth/register': {
      post: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Đăng ký tài khoản tài xế mới',
        description: 'Tạo tài khoản tài xế mới. Vai trò luôn mặc định là DRIVER để đảm bảo an toàn.',
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['username', 'password', 'hoTen'],
                properties: {
                  username: { type: 'string', example: 'taixe_hung' },
                  password: { type: 'string', example: 'Password123@' },
                  hoTen: { type: 'string', example: 'Nguyễn Văn Hùng' },
                  sdt: { type: 'string', example: '0905123456' },
                  bangLai: { type: 'string', example: 'FC - 2024' },
                },
              },
            },
          },
        },
        responses: {
          201: { description: 'Đăng ký tài khoản thành công.' },
          400: { description: 'Tên đăng nhập đã tồn tại hoặc thiếu trường bắt buộc.' },
        },
      },
    },
    '/api/auth/login': {
      post: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Đăng nhập hệ thống (Lấy JWT Access Token)',
        description: 'Xác thực tài khoản và trả về JWT Bearer Token (thời hạn 1 ngày).',
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['username', 'password'],
                properties: {
                  username: { type: 'string', example: 'admin' },
                  password: { type: 'string', example: 'password123' },
                },
              },
            },
          },
        },
        responses: {
          200: {
            description: 'Đăng nhập thành công.',
            content: {
              'application/json': {
                schema: {
                  type: 'object',
                  properties: {
                    message: { type: 'string', example: 'Đăng nhập thành công.' },
                    token: { type: 'string', example: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...' },
                    user: { $ref: '#/components/schemas/User' },
                  },
                },
              },
            },
          },
          401: { description: 'Tên đăng nhập hoặc mật khẩu không chính xác.' },
        },
      },
    },
    '/api/auth/me': {
      get: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Lấy thông tin tài khoản hiện tại',
        security: [{ BearerAuth: [] }],
        responses: {
          200: { description: 'Thông tin tài khoản hiện tại.' },
          401: { description: 'Chưa đăng nhập hoặc token hết hạn.' },
        },
      },
    },
    '/api/auth/users': {
      get: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Danh sách tất cả người dùng (Chỉ ADMIN)',
        security: [{ BearerAuth: [] }],
        responses: {
          200: { description: 'Danh sách người dùng hệ thống.' },
          403: { description: 'Yêu cầu vai trò ADMIN.' },
        },
      },
    },
        '/api/auth/users/{userId}/reset-password': {
      post: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Quản trị viên cấp lại mật khẩu cho tài khoản (Chỉ ADMIN)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'userId', in: 'path', required: true, schema: { type: 'integer' }, example: 2 },
        ],
        requestBody: {
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {
                  newPassword: { type: 'string', example: 'Driver@2026' },
                },
              },
            },
          },
        },
        responses: {
          200: { description: 'Cấp lại mật khẩu thành công.' },
        },
      },
    },
    '/api/auth/users/{userId}': {
      patch: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Cập nhật trạng thái / vai trò người dùng (Chỉ ADMIN)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'userId', in: 'path', required: true, schema: { type: 'integer' }, example: 2 },
        ],
        requestBody: {
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {
                  trangThai: { type: 'string', enum: ['ACTIVE', 'INACTIVE', 'BANNED'], example: 'ACTIVE' },
                  role: { type: 'string', enum: ['ADMIN', 'MANAGER', 'DRIVER'], example: 'MANAGER' },
                },
              },
            },
          },
        },
        responses: {
          200: { description: 'Cập nhật thành công.' },
        },
      },
    },
    '/api/auth/config': {
      get: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Xem cấu hình tham số AI & Cảm biến (ADMIN & MANAGER)',
        security: [{ BearerAuth: [] }],
        responses: {
          200: {
            description: 'Cấu hình tham số vận hành hệ thống.',
            content: {
              'application/json': { schema: { properties: { config: { $ref: '#/components/schemas/SystemConfig' } } } },
            },
          },
        },
      },
      put: {
        tags: ['1. Authentication & Tài khoản'],
        summary: 'Cập nhật cấu hình tham số AI & Cảm biến (Chỉ ADMIN)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: { $ref: '#/components/schemas/SystemConfig' },
            },
          },
        },
        responses: {
          200: { description: 'Cập nhật tham số hệ thống thành công.' },
        },
      },
    },

    // ==========================================
    // 2. FLEET ASSETS (VEHICLES, DEVICES, ROUTES)
    // ==========================================
    '/api/assets/vehicles': {
      get: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Danh sách phương tiện vận tải',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Danh sách xe kèm hộp đen IoT gắn liền.' } },
      },
      post: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Thêm phương tiện mới (MANAGER & ADMIN)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['bienSo'],
                properties: {
                  bienSo: { type: 'string', example: '43C-888.99' },
                  loaiXe: { type: 'string', example: 'Xe Tải Container 15 Tấn' },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Tạo phương tiện thành công.' } },
      },
    },
    '/api/assets/vehicles/{id}/status': {
      patch: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Cập nhật trạng thái xe (AVAILABLE / IN_USE / MAINTENANCE)',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {
                  trangThai: { type: 'string', enum: ['AVAILABLE', 'IN_USE', 'MAINTENANCE'], example: 'MAINTENANCE' },
                },
              },
            },
          },
        },
        responses: { 200: { description: 'Cập nhật trạng thái thành công.' } },
      },
    },
    '/api/assets/devices': {
      get: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Danh sách thiết bị IoT',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Danh sách thiết bị hộp đen IoT.' } },
      },
      post: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Đăng ký thiết bị IoT mới (MANAGER & ADMIN)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['macAddress'],
                properties: {
                  macAddress: { type: 'string', example: 'AA:BB:CC:11:22:33' },
                  phienBan: { type: 'string', example: 'v2.1-ESP32-Pi4' },
                  vehicleId: { type: 'integer', example: 1 },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Tạo thiết bị thành công.' } },
      },
    },
    '/api/assets/routes': {
      get: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Danh sách tuyến đường vận tải',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Danh sách các lộ trình.' } },
      },
      post: {
        tags: ['2. Quản lý Đội xe & Thiết bị'],
        summary: 'Tạo lộ trình mới (MANAGER & ADMIN)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['tenTuyen', 'khoangCachKm'],
                properties: {
                  tenTuyen: { type: 'string', example: 'Đà Nẵng - Huế (Qua Đèo Hải Vân)' },
                  khoangCachKm: { type: 'number', example: 105.5 },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Tạo tuyến đường thành công.' } },
      },
    },

    // ==========================================
    // 3. SHIFTS MANAGEMENT
    // ==========================================
    '/api/shifts': {
      get: {
        tags: ['3. Quản lý Ca trực (Shifts)'],
        summary: 'Danh sách ca làm việc',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'driverId', in: 'query', schema: { type: 'integer' }, description: 'Lọc theo tài xế' },
          { name: 'status', in: 'query', schema: { type: 'string' }, description: 'Lọc theo trạng thái ca' },
        ],
        responses: { 200: { description: 'Danh sách ca làm việc.' } },
      },
      post: {
        tags: ['3. Quản lý Ca trực (Shifts)'],
        summary: 'Lập lịch phân ca làm việc mới (Kiểm tra nghỉ 8h & Sinh học)',
        description: 'Tự động kiểm tra khoảng nghỉ tối thiểu 8h, kiểm tra trùng lặp xe/tài xế và đối chiếu khung giờ yếu sinh học.',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['driverId', 'vehicleId', 'routeId', 'thoiGianBatDauDuKien', 'thoiGianKetThucDuKien'],
                properties: {
                  driverId: { type: 'integer', example: 1 },
                  vehicleId: { type: 'integer', example: 1 },
                  routeId: { type: 'integer', example: 1 },
                  thoiGianBatDauDuKien: { type: 'string', format: 'date-time', example: '2026-10-15T08:00:00Z' },
                  thoiGianKetThucDuKien: { type: 'string', format: 'date-time', example: '2026-10-15T12:00:00Z' },
                },
              },
            },
          },
        },
        responses: {
          201: { description: 'Tạo ca làm việc thành công.' },
          400: { description: 'Bị chặn do vi phạm khoảng nghỉ 8h hoặc trùng lịch.' },
        },
      },
    },
    '/api/shifts/{id}/respond': {
      patch: {
        tags: ['3. Quản lý Ca trực (Shifts)'],
        summary: 'Tài xế phản hồi ca trực (ACCEPTED / DECLINED)',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['trangThai'],
                properties: {
                  trangThai: { type: 'string', enum: ['ACCEPTED', 'DECLINED'], example: 'ACCEPTED' },
                  lyDoTuChoi: { type: 'string', example: 'Bận việc gia đình đột xuất' },
                },
              },
            },
          },
        },
        responses: {
          200: { description: 'Phản hồi ca trực thành công.' },
          403: { description: 'Không có quyền phản hồi ca trực của người khác.' },
        },
      },
    },

    // ==========================================
    // 4. TRIPS & LIVE TRACKING
    // ==========================================
    '/api/trips': {
      get: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Danh sách các chuyến xe',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'driverId', in: 'query', schema: { type: 'integer' } },
          { name: 'status', in: 'query', schema: { type: 'string' } },
        ],
        responses: { 200: { description: 'Danh sách các chuyến đi.' } },
      },
      post: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Khởi tạo chuyến xe mới (PLANNED)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['driverId', 'vehicleId', 'routeId'],
                properties: {
                  driverId: { type: 'integer', example: 1 },
                  vehicleId: { type: 'integer', example: 1 },
                  routeId: { type: 'integer', example: 1 },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Khởi tạo chuyến xe thành công.' } },
      },
    },
    '/api/trips/my-active': {
      get: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Lấy chuyến xe đang chạy hoặc sắp chạy của tài xế đăng nhập',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Chuyến xe hiện tại.' } },
      },
    },
    '/api/trips/{id}': {
      get: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Chi tiết một chuyến xe',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        responses: { 200: { description: 'Chi tiết chuyến đi.' } },
      },
    },
    '/api/trips/{id}/status': {
      patch: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Cập nhật trạng thái chuyến xe (Bắt đầu / Kết thúc / Hủy)',
        description: 'Tài xế bấm bắt đầu (IN_PROGRESS) hoặc hoàn thành (COMPLETED). Tự động cập nhật xe, tài xế và tính thưởng an toàn.',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['trangThai'],
                properties: {
                  trangThai: { type: 'string', enum: ['IN_PROGRESS', 'COMPLETED', 'CANCELLED'], example: 'IN_PROGRESS' },
                },
              },
            },
          },
        },
        responses: { 200: { description: 'Cập nhật trạng thái thành công.' } },
      },
    },
    '/api/trips/{id}/playback': {
      get: {
        tags: ['4. Chuyến đi & Giám sát (Trips)'],
        summary: 'Tua lại hành trình (Route Playback)',
        description: 'Truy vấn toàn bộ chuỗi tọa độ GPS và các mốc vi phạm phát sinh để vẽ vệt di chuyển trên bản đồ.',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        responses: { 200: { description: 'Chuỗi tọa độ GPS và các mốc vi phạm của chuyến đi.' } },
      },
    },

    // ==========================================
    // 5. IOT TELEMETRY & HARDWARE INGESTION
    // ==========================================
    '/api/iot/telemetry': {
      post: {
        tags: ['5. IoT Hardware & Telemetry Ingestion'],
        summary: 'Gửi gói tin dữ liệu cảm biến & cờ AI từ phần cứng',
        description: 'Nhận gói tin định kỳ từ Raspberry Pi 4 / ESP32. Tự động kiểm tra cờ AI (nhắm mắt, điện thoại, ngáp, CO2, tốc độ), phát hiện buồn ngủ và trừ điểm uy tín.',
        security: [{ DeviceTokenAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['deviceId', 'kinhDo', 'viDo', 'tocDo'],
                properties: {
                  deviceId: { type: 'integer', example: 1, description: 'ID hộp đen IoT trên xe' },
                  tripId: { type: 'integer', example: 1, description: 'ID chuyến đi (tùy chọn, tự tìm chuyến IN_PROGRESS nếu bỏ trống)' },
                  viDo: { type: 'number', example: 16.0544, description: 'Vĩ độ GPS (Latitude)' },
                  kinhDo: { type: 'number', example: 108.2022, description: 'Kinh độ GPS (Longitude)' },
                  tocDo: { type: 'number', example: 65.5, description: 'Vận tốc xe chạy (km/h)' },
                  co2Level: { type: 'number', example: 750, description: 'Nồng độ CO2 buồng lái từ cảm biến MQ-135 (ppm)' },
                  aiViolationFlag: {
                    type: 'string',
                    enum: ['EYES_CLOSED', 'PHONE', 'YAWN', 'BUON_NGU', 'DIEN_THOAI', 'MET_MOI_NGAP'],
                    example: 'EYES_CLOSED',
                    description: 'Cờ phát hiện từ mô hình AI YOLO Edge trên Raspberry Pi',
                  },
                },
              },
            },
          },
        },
        responses: {
          201: { description: 'Ghi nhận dữ liệu thành công.' },
          401: { description: 'Thiếu hoặc sai x-device-token.' },
        },
      },
    },

    // ==========================================
    // 6. VIOLATIONS & SAFETY LOGS
    // ==========================================
    '/api/violations/types': {
      get: {
        tags: ['6. Vi phạm & Cảnh báo an toàn (Violations)'],
        summary: 'Lấy danh mục chuẩn các loại lỗi (ID, Mã, Điểm trừ, Icon)',
        security: [{ BearerAuth: [] }],
        responses: {
          200: {
            description: 'Danh mục 5 loại lỗi chuẩn hóa.',
            content: {
              'application/json': {
                schema: {
                  type: 'object',
                  properties: {
                    types: { type: 'array', items: { $ref: '#/components/schemas/ViolationType' } },
                  },
                },
              },
            },
          },
        },
      },
    },
    '/api/violations': {
      get: {
        tags: ['6. Vi phạm & Cảnh báo an toàn (Violations)'],
        summary: 'Danh sách lịch sử vi phạm',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'driverId', in: 'query', schema: { type: 'integer' } },
          { name: 'tripId', in: 'query', schema: { type: 'integer' } },
          { name: 'status', in: 'query', schema: { type: 'string' } },
        ],
        responses: { 200: { description: 'Danh sách vi phạm kèm thông tin loại lỗi chuẩn.' } },
      },
      post: {
        tags: ['6. Vi phạm & Cảnh báo an toàn (Violations)'],
        summary: 'Tạo bản ghi vi phạm thủ công (MANAGER & ADMIN)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['driverId', 'tripId'],
                properties: {
                  driverId: { type: 'integer', example: 1 },
                  tripId: { type: 'integer', example: 1 },
                  violationTypeId: { type: 'integer', example: 1, description: 'ID loại lỗi (1: Ngủ gật, 2: Ngáp, 3: CO2, 4: Điện thoại, 5: Tốc độ)' },
                  loaiViPham: { type: 'string', example: 'BUON_NGU' },
                  diemTru: { type: 'integer', example: 10 },
                  videoUrl: { type: 'string', example: 'https://example.com/evidence.mp4' },
                  moTa: { type: 'string', example: 'Phát hiện ngủ gật qua camera giám sát.' },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Khởi tạo vi phạm thành công.' } },
      },
    },
    '/api/violations/drivers-score': {
      get: {
        tags: ['6. Vi phạm & Cảnh báo an toàn (Violations)'],
        summary: 'Bảng xếp hạng quỹ điểm uy tín tài xế',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Danh sách tài xế sắp xếp theo quỹ điểm uy tín.' } },
      },
    },
    '/api/violations/{id}/review': {
      post: {
        tags: ['6. Vi phạm & Cảnh báo an toàn (Violations)'],
        summary: 'Quản lý duyệt vi phạm (CONFIRM hoặc REJECT)',
        description: 'Duyệt thủ công video bằng chứng. Nếu CONFIRM: trừ điểm uy tín. Nếu REJECT một vi phạm đã auto-confirm trước đó: tự động hoàn điểm lại cho tài xế.',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 101 }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['action'],
                properties: {
                  action: { type: 'string', enum: ['CONFIRM', 'REJECT'], example: 'CONFIRM' },
                  lyDo: { type: 'string', example: 'Video ghi nhận rõ tài xế ngủ gật lúc 14:15.' },
                },
              },
            },
          },
        },
        responses: {
          200: { description: 'Xét duyệt thành công.' },
          400: { description: 'Thao tác không hợp lệ hoặc vi phạm đã duyệt.' },
        },
      },
    },

    // ==========================================
    // 7. BIOMETRICS & AI CALIBRATION
    // ==========================================
    '/api/biometrics/profile/{driverId}': {
      get: {
        tags: ['7. Hồ sơ Sinh học & Hiệu chuẩn AI'],
        summary: 'Lấy hồ sơ đặc trưng sinh học của tài xế',
        description: 'Trả về khung giờ yếu sinh học, thời gian lái tối đa khuyến nghị và thống kê 30 ngày.',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'driverId', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        responses: { 200: { description: 'Hồ sơ sinh học tài xế.' } },
      },
    },
    '/api/biometrics/calibration/{driverId}': {
      get: {
        tags: ['7. Hồ sơ Sinh học & Hiệu chuẩn AI'],
        summary: 'Lịch sử hiệu chuẩn khuôn mặt trước ca',
        security: [{ BearerAuth: [] }],
        parameters: [{ name: 'driverId', in: 'path', required: true, schema: { type: 'integer' }, example: 1 }],
        responses: { 200: { description: 'Dữ liệu hiệu chuẩn khuôn mặt.' } },
      },
    },
    '/api/biometrics/calibration': {
      post: {
        tags: ['7. Hồ sơ Sinh học & Hiệu chuẩn AI'],
        summary: 'Lưu dữ liệu hiệu chuẩn khuôn mặt trước khi xuất bến',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['driverId', 'vectorDacTrung'],
                properties: {
                  driverId: { type: 'integer', example: 1 },
                  tripId: { type: 'integer', example: 1 },
                  shiftId: { type: 'integer', example: 1 },
                  vectorDacTrung: {
                    type: 'object',
                    example: { earBaseline: 0.32, marBaseline: 0.15, yawBaseline: 0.05 },
                    description: 'Vector đặc trưng độ mở mắt/miệng cơ sở của tài xế',
                  },
                  ketQua: { type: 'string', enum: ['SUCCESS', 'FAILED'], example: 'SUCCESS' },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Lưu hiệu chuẩn thành công.' } },
      },
    },

    // ==========================================
    // 8. EVIDENCE UPLOAD
    // ==========================================
    '/api/upload/video': {
      post: {
        tags: ['8. Tải lên Bằng chứng (Video Upload)'],
        summary: 'Tải lên video bằng chứng vi phạm (MP4 / WebM)',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'multipart/form-data': {
              schema: {
                type: 'object',
                properties: {
                  video: {
                    type: 'string',
                    format: 'binary',
                    description: 'File video quay từ cabin xe (tối đa 50MB, định dạng mp4, webm, avi)',
                  },
                },
              },
            },
          },
        },
        responses: {
          201: { description: 'Upload video thành công, trả về URL truy cập.' },
          400: { description: 'Không có file hoặc định dạng không hợp lệ.' },
        },
      },
    },

    // ==========================================
    // 10. REAL-TIME MONITORING & REST STOPS
    // ==========================================
    '/api/monitoring/priority-fleet': {
      get: {
        tags: ['10. Giám sát & Điều phối Realtime'],
        summary: 'Lấy danh sách đội xe ưu tiên can thiệp kèm trạm dừng chân và mốc buồn ngủ',
        security: [{ BearerAuth: [] }],
        responses: {
          200: {
            description: 'Danh sách xe kèm trạng thái DANGER/SAFE, khoảng cách trạm dừng chân và mốc bắt đầu buồn ngủ.',
          },
        },
      },
    },
    '/api/monitoring/stats': {
      get: {
        tags: ['10. Giám sát & Điều phối Realtime'],
        summary: 'Thống kê KPI giám sát thời gian thực',
        security: [{ BearerAuth: [] }],
        responses: {
          200: { description: 'Tổng số xe hoạt động, số cảnh báo ngủ gật, mệt mỏi.' },
        },
      },
    },
    '/api/routes/nearest-rest-stop': {
      get: {
        tags: ['10. Giám sát & Điều phối Realtime'],
        summary: 'Tính toán trạm dừng chân gần nhất và khoảng cách từ lúc buồn ngủ (Haversine)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'lat', in: 'query', required: true, schema: { type: 'number' }, example: 16.0544 },
          { name: 'lng', in: 'query', required: true, schema: { type: 'number' }, example: 108.2022 },
          { name: 'speed', in: 'query', schema: { type: 'number' }, example: 60 },
          { name: 'routeId', in: 'query', schema: { type: 'integer' }, example: 1 },
        ],
        responses: {
          200: {
            description: 'Thông tin trạm dừng chân gần nhất, km còn lại, km đã chạy từ lúc buồn ngủ và Google Maps URL.',
          },
        },
      },
    },
    '/api/trips/dispatch-rest-stop': {
      post: {
        tags: ['10. Giám sát & Điều phối Realtime'],
        summary: 'Lệnh điều phối xe vào trạm dừng nghỉ an toàn',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['vehiclePlate', 'restStopName'],
                properties: {
                  vehiclePlate: { type: 'string', example: '43C-123.45' },
                  restStopName: { type: 'string', example: 'Trạm Dừng Nghỉ Nam Hải Vân (Km 22)' },
                },
              },
            },
          },
        },
        responses: { 200: { description: 'Đã gửi lệnh điều hướng tới buồng lái.' } },
      },
    },

    // ==========================================
    // 11. VIOLATIONS EXTRA & SAFE RESOLUTION
    // ==========================================
    '/api/violations/{id}/resolve-safe': {
      post: {
        tags: ['6. Vi phạm & Chấm điểm AI'],
        summary: 'Miễn trừ vi phạm khi tài xế chủ động tấp xe vào trạm nghỉ an toàn (RESOLVED_SAFE)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 },
        ],
        requestBody: {
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {
                  restStopName: { type: 'string', example: 'Trạm Dừng Nghỉ Nam Hải Vân' },
                  notes: { type: 'string', example: 'Xe dừng nghỉ an toàn theo GPS.' },
                },
              },
            },
          },
        },
        responses: { 200: { description: 'Đã miễn trừ vi phạm và hoàn điểm cho tài xế.' } },
      },
    },

    // ==========================================
    // 12. DISPUTES (KHIẾU NẠI)
    // ==========================================
    '/api/disputes': {
      get: {
        tags: ['11. Quản lý Khiếu nại (Disputes)'],
        summary: 'Lấy danh sách các đơn khiếu nại của tài xế',
        security: [{ BearerAuth: [] }],
        responses: { 200: { description: 'Danh sách khiếu nại.' } },
      },
      post: {
        tags: ['11. Quản lý Khiếu nại (Disputes)'],
        summary: 'Tài xế tạo đơn khiếu nại điểm vi phạm',
        security: [{ BearerAuth: [] }],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['violationId', 'reason'],
                properties: {
                  violationId: { type: 'integer', example: 1 },
                  reason: { type: 'string', example: 'Gió bụi bay vào mắt, không phải ngủ gật' },
                },
              },
            },
          },
        },
        responses: { 201: { description: 'Tạo đơn khiếu nại thành công.' } },
      },
    },
    '/api/disputes/{id}/resolve': {
      patch: {
        tags: ['11. Quản lý Khiếu nại (Disputes)'],
        summary: 'Quản trị viên xét duyệt khiếu nại (APPROVED / REJECTED)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'id', in: 'path', required: true, schema: { type: 'integer' }, example: 1 },
        ],
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: {
                type: 'object',
                required: ['trang_thai'],
                properties: {
                  trang_thai: { type: 'string', enum: ['APPROVED', 'REJECTED'], example: 'APPROVED' },
                  phan_hoi: { type: 'string', example: 'Đã xem clip 10s, chấp thuận hoàn điểm.' },
                },
              },
            },
          },
        },
        responses: { 200: { description: 'Xử lý khiếu nại thành công.' } },
      },
    },

    // ==========================================
    // 9. AUDIT LOGS
    // ==========================================
    '/api/audit': {
      get: {
        tags: ['9. Nhật ký Thao tác (Audit Logs)'],
        summary: 'Truy vấn nhật ký can thiệp hệ thống (ADMIN & MANAGER)',
        security: [{ BearerAuth: [] }],
        parameters: [
          { name: 'userId', in: 'query', schema: { type: 'integer' } },
          { name: 'doiTuongLoai', in: 'query', schema: { type: 'string' } },
          { name: 'hanhDong', in: 'query', schema: { type: 'string' } },
          { name: 'limit', in: 'query', schema: { type: 'integer', default: 50 } },
        ],
        responses: { 200: { description: 'Danh sách nhật ký audit log.' } },
      },
    },
  },
};

const swaggerOptions = {
  swaggerDefinition,
  apis: [], // All specs are explicitly and accurately defined in swaggerDefinition.paths
};

export const swaggerSpec = swaggerJsdoc(swaggerOptions);

export const setupSwagger = (app) => {
  // Swagger JSON route
  app.get('/api/docs.json', (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.send(swaggerSpec);
  });

  // Swagger UI route
  app.use(
    '/api/docs',
    swaggerUi.serve,
    swaggerUi.setup(swaggerSpec, {
      customSiteTitle: 'PBL4 Fleet IoT & AI - Swagger API Testing',
      customCss: '.swagger-ui .topbar { display: none }',
      swaggerOptions: {
        persistAuthorization: true,
        docExpansion: 'list',
        filter: true,
      },
    })
  );

  console.log('📖 Swagger API Documentation ready at: http://localhost:3000/api/docs');
};
