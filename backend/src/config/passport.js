import passport from 'passport';
import { Strategy as GoogleStrategy } from 'passport-google-oauth20';

const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID;
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET;
const GOOGLE_CALLBACK_URL = process.env.GOOGLE_CALLBACK_URL || 'http://localhost:3000/api/auth/google/callback';

if (GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET) {
	passport.use(
		new GoogleStrategy(
			{
				clientID: GOOGLE_CLIENT_ID,
				clientSecret: GOOGLE_CLIENT_SECRET,
				callbackURL: GOOGLE_CALLBACK_URL,
				scope: ['profile', 'email'],
			},
			// Verify callback — chỉ truyền profile về controller, KHÔNG xử lý DB ở đây
			async (accessToken, refreshToken, profile, done) => {
				try {
					// Trả nguyên profile Google về cho controller xử lý
					const googleProfile = {
						googleId: profile.id,
						email: profile.emails?.[0]?.value || null,
						userName: profile.displayName || null,
						avatar: profile.photos?.[0]?.value || null,
					};
					return done(null, googleProfile);
				} catch (error) {
					return done(error, null);
				}
			},
		),
	);
} else {
	console.warn('[OAuth] GOOGLE_CLIENT_ID hoặc GOOGLE_CLIENT_SECRET chưa được cấu hình. Google Login sẽ không hoạt động.');
}

export default passport;
