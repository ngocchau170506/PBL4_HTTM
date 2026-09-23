import nodemailer from 'nodemailer';

const createTransporter = () => {
  const host = process.env.SMTP_HOST || 'smtp.gmail.com';
  const port = parseInt(process.env.SMTP_PORT || '587', 10);
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASS;

  if (!user || user === 'your_email@gmail.com' || !pass || pass === 'your_16_char_app_password') {
    return null; // Return null to fallback to console logger
  }

  return nodemailer.createTransport({
    host,
    port,
    secure: process.env.SMTP_SECURE === 'true',
    auth: { user, pass },
  });
};

export const sendViolationEmailAlert = async ({ driverName, driverEmail, bienSo, loaiViPham, diemTru, uyTinConLai, tocDo, videoUrl }) => {
  const transporter = createTransporter();
  const emailFrom = process.env.EMAIL_FROM || '"Hệ Thống Giám Sát PBL4 Fleet" <no-reply@pbl4fleet.vn>';
  const targetEmail = driverEmail || process.env.SMTP_USER;

  const htmlContent = `
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #0b1727; color: #ffffff; padding: 20px; border-radius: 12px; border: 1px solid #1e293b;">
      <h2 style="color: #ef4444; border-bottom: 2px solid #ef4444; padding-bottom: 10px;">
        🚨 THÔNG BÁO CẢNH BÁO SỚM YOLO26 AI & IoT TELEMETRY
      </h2>
      <p style="font-size: 15px; color: #cbd5e1;">Kính gửi Tài xế <strong>${driverName}</strong>,</p>
      <p style="font-size: 14px; color: #94a3b8;">Hệ thống Camera AI YOLO26 (Raspberry Pi 4) & Cảm biến IoT vừa ghi nhận một hành vi vi phạm / dấu hiệu mệt mỏi nguy hiểm đối với chuyến xe do bạn đảm nhận.</p>

      <div style="background-color: #1e293b; padding: 15px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #ef4444;">
        <p style="margin: 5px 0;">🚘 <strong>Biển số xe:</strong> <span style="color: #38bdf8;">${bienSo}</span></p>
        <p style="margin: 5px 0;">👁️ <strong>Loại vi phạm AI:</strong> <span style="color: #f87171; font-weight: bold;">${loaiViPham}</span></p>
        <p style="margin: 5px 0;">📉 <strong>Tốc độ vọt / Vận hành:</strong> <span style="color: #facc15; font-weight: bold;">${tocDo} km/h</span></p>
        <p style="margin: 5px 0;">🔻 <strong>Số điểm bị trừ:</strong> <span style="color: #ef4444; font-weight: bold;">-${diemTru} điểm</span></p>
        <p style="margin: 5px 0;">🛡️ <strong>Quỹ điểm uy tín còn lại:</strong> <span style="color: #4ade80; font-weight: bold;">${uyTinConLai} / 100 điểm</span></p>
      </div>

      ${videoUrl ? `
        <div style="margin-top: 15px; text-align: center;">
          <a href="${videoUrl}" target="_blank" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-weight: bold;">
            📹 Xem Video Bằng Chứng AI YOLO26 / Cloud Storage
          </a>
        </div>
      ` : ''}

      <hr style="border: 0; border-top: 1px solid #334155; margin: 20px 0;" />
      <p style="font-size: 12px; color: #64748b; text-align: center;">
        Thông báo tự động từ Hệ thống Cảnh báo sớm buồn ngủ YOLO26 & Quản lý Vận tải IoT (PBL4). Vui lòng tuân thủ quy định an toàn giao thông.
      </p>
    </div>
  `;

  if (!transporter) {
    console.log('\n================== 📧 MOCK EMAIL SENT (YOLO26 AI ALERT) ==================');
    console.log(`To: ${targetEmail || 'No recipient specified'}`);
    console.log(`Subject: 🚨 YOLO26 AI Alert: Driver ${driverName} committed violation - Deducted ${diemTru} points`);
    console.log(`Violation: ${loaiViPham} (${tocDo} km/h) | Remaining Points: ${uyTinConLai}`);
    console.log('=========================================================================\n');
    return { success: true, mode: 'mock' };
  }

  try {
    const info = await transporter.sendMail({
      from: emailFrom,
      to: targetEmail,
      subject: `🚨 [CẢNH BÁO SỚM YOLO26 AI] Tài xế ${driverName} - Xe ${bienSo} (${loaiViPham})`,
      html: htmlContent,
    });
    console.log('✉️ Real Email sent successfully! MessageId:', info.messageId);
    return { success: true, messageId: info.messageId, mode: 'real' };
  } catch (err) {
    console.error('❌ Failed to send real email via Nodemailer:', err.message);
    return { success: false, error: err.message };
  }
};
