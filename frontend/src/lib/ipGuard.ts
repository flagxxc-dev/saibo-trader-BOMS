import fs from "fs";
import path from "path";
import { execFile } from "child_process";
import { promisify } from "util";
import type { NextRequest } from "next/server";

const execFileAsync = promisify(execFile);

export const MAX_LOGIN_FAILURES = 4;

const SECURITY_PATH =
  process.env.IP_SECURITY_PATH || path.resolve(process.cwd(), "../logs/ip_security.json");
const BLOCK_SCRIPT =
  process.env.BLOCK_IP_SCRIPT || path.resolve(process.cwd(), "../scripts/block_ip.sh");

const LOCAL_IPS = new Set(["127.0.0.1", "::1", "localhost", "unknown"]);

type IpSecurityState = {
  blacklist: Record<string, { at: number; reason: string }>;
  attempts: Record<string, { count: number; lastAt: number }>;
};

function emptyState(): IpSecurityState {
  return { blacklist: {}, attempts: {} };
}

function normalizeIp(raw: string | null | undefined): string {
  const value = (raw || "").trim();
  if (!value) return "unknown";
  if (value === "::ffff:127.0.0.1") return "127.0.0.1";
  const first = value.split(",")[0]?.trim() || value;
  return first;
}

export function getClientIpFromHeaders(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  const realIp = headers.get("x-real-ip");
  const cfIp = headers.get("cf-connecting-ip");
  return normalizeIp(forwarded || realIp || cfIp);
}

export function getClientIpFromRequest(req: NextRequest): string {
  return getClientIpFromHeaders(req.headers);
}

function isValidPublicIp(ip: string): boolean {
  if (LOCAL_IPS.has(ip)) return false;
  if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)/.test(ip)) return false;
  return (
    /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) ||
    /^[0-9a-f:]+$/i.test(ip)
  );
}

function ensureParent(filePath: string) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function readState(): IpSecurityState {
  try {
    if (!fs.existsSync(SECURITY_PATH)) return emptyState();
    const parsed = JSON.parse(fs.readFileSync(SECURITY_PATH, "utf-8")) as IpSecurityState;
    return {
      blacklist: parsed.blacklist || {},
      attempts: parsed.attempts || {},
    };
  } catch {
    return emptyState();
  }
}

function writeState(state: IpSecurityState) {
  ensureParent(SECURITY_PATH);
  fs.writeFileSync(SECURITY_PATH, JSON.stringify(state, null, 2), "utf-8");
}

export function isBlacklisted(ip: string): boolean {
  if (LOCAL_IPS.has(ip)) return false;
  const state = readState();
  return Boolean(state.blacklist[ip]);
}

async function applyFirewallBlock(ip: string) {
  if (!isValidPublicIp(ip)) return;
  if (process.platform !== "linux") return;
  if (process.env.APPLY_FIREWALL_BLOCK === "false") return;
  if (!fs.existsSync(BLOCK_SCRIPT)) return;
  try {
    await execFileAsync("bash", [BLOCK_SCRIPT, ip], { timeout: 15000 });
  } catch (err) {
    console.error("[ipGuard] firewall block failed:", err);
  }
}

function addToBlacklist(ip: string, reason: string) {
  if (LOCAL_IPS.has(ip) || !isValidPublicIp(ip)) return false;
  const state = readState();
  if (state.blacklist[ip]) return false;
  state.blacklist[ip] = { at: Date.now(), reason };
  delete state.attempts[ip];
  writeState(state);
  void applyFirewallBlock(ip);
  return true;
}

/** Returns true when the IP was newly blacklisted on this attempt. */
export function recordFailedLogin(ip: string): boolean {
  if (LOCAL_IPS.has(ip) || isBlacklisted(ip)) return true;
  const state = readState();
  const prev = state.attempts[ip]?.count || 0;
  const next = prev + 1;
  state.attempts[ip] = { count: next, lastAt: Date.now() };
  if (next >= MAX_LOGIN_FAILURES) {
    state.blacklist[ip] = { at: Date.now(), reason: "brute_force" };
    delete state.attempts[ip];
    writeState(state);
    void applyFirewallBlock(ip);
    return true;
  }
  writeState(state);
  return false;
}

export function clearLoginFailures(ip: string) {
  if (LOCAL_IPS.has(ip)) return;
  const state = readState();
  if (!state.attempts[ip]) return;
  delete state.attempts[ip];
  writeState(state);
}

export function silentAuthResponse(): Response {
  return new Response(null, { status: 404 });
}

export function shouldSilenceAuth(ip: string): boolean {
  return isBlacklisted(ip);
}
