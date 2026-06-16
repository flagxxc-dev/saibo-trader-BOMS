/** Strict validation before any DB lookup — defense in depth with Prisma parameter binding. */

const USERNAME_RE = /^[a-zA-Z0-9_.-]{1,64}$/;

const SQL_INJECTION_PATTERNS = [
  /(\b)(union|select|insert|update|delete|drop|alter|exec|execute|sleep|benchmark)(\b)/i,
  /(\b)(or|and)\b\s+['"]?\d/i,
  /(--|#|\/\*|\*\/|;)/,
  /('|"|`)/,
  /(\%27)|(\%22)|(\%3B)/i,
];

function hasSqlInjectionPattern(value: string): boolean {
  return SQL_INJECTION_PATTERNS.some((pattern) => pattern.test(value));
}

export function validateUsername(username: string): string | null {
  const trimmed = username.trim();
  if (!trimmed || trimmed.length > 64) return null;
  if (!USERNAME_RE.test(trimmed)) return null;
  if (hasSqlInjectionPattern(trimmed)) return null;
  return trimmed;
}

export function validatePassword(password: string): string | null {
  if (!password || password.length < 4 || password.length > 128) return null;
  if (/[\x00-\x08\x0B\x0C\x0E-\x1F]/.test(password)) return null;
  return password;
}

export function validateResetToken(token: string): string | null {
  const trimmed = token.trim();
  if (!/^[a-f0-9]{64}$/i.test(trimmed)) return null;
  return trimmed.toLowerCase();
}

export function validateAuditUser(user: string): string {
  const trimmed = (user || "web").trim().slice(0, 64);
  if (!/^[a-zA-Z0-9_.@-]+$/.test(trimmed)) return "web";
  if (hasSqlInjectionPattern(trimmed)) return "web";
  return trimmed;
}

export function validateAuditReason(reason: string): string {
  const trimmed = reason.trim().slice(0, 200);
  if (!trimmed) return "";
  if (hasSqlInjectionPattern(trimmed)) return "";
  return trimmed.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "");
}
