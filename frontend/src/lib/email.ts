import nodemailer from "nodemailer";

export async function sendPasswordResetEmail(to: string, token: string) {
  // If SMTP is not configured, we'll log the token (useful for local development without credentials)
  if (!process.env.SMTP_HOST) {
    console.log(`\n==========================================`);
    console.log(`SMTP NOT CONFIGURED! Password reset requested for: ${to}`);
    console.log(`Reset link: ${process.env.NEXTAUTH_URL}/reset-password?token=${token}`);
    console.log(`==========================================\n`);
    return true;
  }

  const transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: parseInt(process.env.SMTP_PORT || "587"),
    auth: {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
    },
  });

  const resetLink = `${process.env.NEXTAUTH_URL}/reset-password?token=${token}`;

  await transporter.sendMail({
    from: process.env.SMTP_FROM || '"Trading Bot" <noreply@example.com>',
    to,
    subject: "Reset your password",
    html: `
      <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Password Reset</h2>
        <p>You requested a password reset for your Trading Bot dashboard.</p>
        <p>Click the link below to reset your password. This link is valid for 1 hour.</p>
        <a href="${resetLink}" style="display: inline-block; padding: 10px 20px; background-color: #000; color: #fff; text-decoration: none; border-radius: 5px;">Reset Password</a>
        <p>If you did not request this, please ignore this email.</p>
      </div>
    `,
  });

  return true;
}
