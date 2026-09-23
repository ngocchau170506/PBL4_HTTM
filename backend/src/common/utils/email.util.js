import nodemailer from 'nodemailer';

let transporter;

const initTransporter = async () => {
	if (transporter) return transporter;

	if (process.env.SMTP_USER && process.env.SMTP_PASS) {
		transporter = nodemailer.createTransport({
			host: process.env.SMTP_HOST || 'smtp.gmail.com',
			port: process.env.SMTP_PORT || 587,
			secure: process.env.SMTP_SECURE === 'true',
			auth: {
				user: process.env.SMTP_USER,
				pass: process.env.SMTP_PASS,
			},
		});
	} else {
		// Dùng tài khoản ảo (Ethereal) để test khi chưa cấu hình email thật
		console.log('Chưa cấu hình SMTP. Tạo tài khoản email ảo để test...');
		const testAccount = await nodemailer.createTestAccount();
		transporter = nodemailer.createTransport({
			host: testAccount.smtp.host,
			port: testAccount.smtp.port,
			secure: testAccount.smtp.secure,
			auth: {
				user: testAccount.user,
				pass: testAccount.pass,
			},
		});
	}
	return transporter;
};

export const sendVerificationEmail = async (to, token) => {
	// Dẫn link trực tiếp về Backend API để user bấm vào là gọi thẳng hàm GET
	const baseUrl = process.env.BACKEND_URL || `http://localhost:${process.env.PORT || 3000}`;
	const verifyUrl = `${baseUrl}/api/auth/verify-email?token=${token}`;

	const mailOptions = {
		from: `"BKMAP Support" <${process.env.SMTP_USER || 'no-reply@bkmap.com'}>`,
		to,
		subject: 'Xác thực tài khoản BKMAP của bạn',
		html: `
			<h2>Chào mừng bạn đến với BKMAP!</h2>
			<p>Vui lòng click vào đường dẫn dưới đây để xác thực địa chỉ email và kích hoạt tài khoản của bạn:</p>
			<a href="${verifyUrl}" style="padding: 10px 20px; background-color: #007bff; color: #fff; text-decoration: none; border-radius: 5px;">Xác thực Email</a>
			<p>Hoặc copy đường dẫn này dán vào trình duyệt:</p>
			<p><a href="${verifyUrl}">${verifyUrl}</a></p>
			<p>Link này sẽ hết hạn trong vòng 24 giờ.</p>

			<br />
			<p>Trân trọng,</p>
			<p>Đội ngũ BKMAP</p>
		`,
	};

	try {
		const mailTransporter = await initTransporter();
		const info = await mailTransporter.sendMail(mailOptions);
		console.log(`Verification email sent to ${to}`);
		
		// In ra đường link xem email preview nếu dùng Ethereal
		if (!process.env.SMTP_USER) {
			console.log('--- TEST EMAIL PREVIEW URL ---');
			console.log(nodemailer.getTestMessageUrl(info));
			console.log('------------------------------');
		}
	} catch (error) {
		console.error('=================== EMAIL ERROR ===================');
		console.error(error);
		console.error('===================================================');
		throw new Error('Không thể gửi email xác thực.');
	}
};

export const sendPasswordResetEmail = async (to, token) => {
  const baseUrl = process.env.FRONTEND_URL || `http://localhost:5173`;
  const resetUrl = `${baseUrl}/reset-password?token=${token}`;

  const mailOptions = {
    from: `"BKMAP Support" <${process.env.SMTP_USER || 'no-reply@bkmap.com'}>`,
    to,
    subject: 'Yêu cầu đặt lại mật khẩu cho tài khoản BKMAP',
    html: `
			<h2>Yêu cầu đặt lại mật khẩu</h2>
			<p>Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản BKMAP của bạn.</p>
			<p>Vui lòng click vào đường dẫn dưới đây để tạo mật khẩu mới:</p>
			<a href="${resetUrl}" style="padding: 10px 20px; background-color: #007bff; color: #fff; text-decoration: none; border-radius: 5px;">Đặt lại mật khẩu</a>
			<p>Hoặc copy đường dẫn này dán vào trình duyệt:</p>
			<p><a href="${resetUrl}">${resetUrl}</a></p>
			<p>Link này sẽ hết hạn trong vòng 1 giờ.</p>
			<p>Nếu bạn không yêu cầu đặt lại mật khẩu, vui lòng bỏ qua email này.</p>

			<br />
			<p>Trân trọng,</p>
			<p>Đội ngũ BKMAP</p>
		`,
  };

  try {
    const mailTransporter = await initTransporter();
    const info = await mailTransporter.sendMail(mailOptions);
    console.log(`Password reset email sent to ${to}`);

    if (!process.env.SMTP_USER) {
      console.log('--- TEST EMAIL PREVIEW URL ---');
      console.log(nodemailer.getTestMessageUrl(info));
      console.log('------------------------------');
    }
  } catch (error) {
    console.error('=================== EMAIL ERROR ===================');
    console.error(error);
    console.error('===================================================');
    throw new Error('Không thể gửi email đặt lại mật khẩu.');
  }
};
