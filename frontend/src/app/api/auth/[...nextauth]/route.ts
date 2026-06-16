import NextAuth, { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { headers } from "next/headers";
import type { NextRequest } from "next/server";
import { prisma } from "@/lib/prisma";
import bcrypt from "bcryptjs";
import { validatePassword, validateUsername } from "@/lib/inputSecurity";
import {
  clearLoginFailures,
  getClientIpFromHeaders,
  getClientIpFromRequest,
  recordFailedLogin,
  shouldSilenceAuth,
  silentAuthResponse,
} from "@/lib/ipGuard";

async function verifyCredentials(username: string, password: string) {
  const safeUser = validateUsername(username);
  const safePass = validatePassword(password);
  if (!safeUser || !safePass) return null;

  const user = await prisma.user.findUnique({
    where: { email: safeUser },
  });

  if (!user) return null;

  const ok = await bcrypt.compare(safePass, user.password);
  if (!ok) return null;

  return { id: user.id, email: user.email };
}

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        username: { label: "账号", type: "text" },
        password: { label: "密码", type: "password" },
      },
      async authorize(credentials) {
        const headerList = await headers();
        const ip = getClientIpFromHeaders(headerList);
        if (shouldSilenceAuth(ip)) return null;

        const username = credentials?.username?.trim();
        const password = credentials?.password ?? "";
        if (!username || !password) {
          recordFailedLogin(ip);
          return null;
        }

        const user = await verifyCredentials(username, password);
        if (!user) {
          recordFailedLogin(ip);
          return null;
        }

        clearLoginFailures(ip);
        return user;
      },
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60,
  },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id;
      }
      return token;
    },
    async session({ session, token }) {
      if (token && session.user) {
        session.user.email = token.email;
        // @ts-expect-error extended session user
        session.user.id = token.id;
      }
      return session;
    },
  },
  secret: process.env.NEXTAUTH_SECRET,
};

const handler = NextAuth(authOptions);

function isCredentialsCallback(req: NextRequest) {
  return req.nextUrl.pathname.includes("/callback/credentials");
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ nextauth: string[] }> }) {
  const ip = getClientIpFromRequest(req);
  if (shouldSilenceAuth(ip)) return silentAuthResponse();
  return handler(req, ctx);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ nextauth: string[] }> }) {
  const ip = getClientIpFromRequest(req);
  if (shouldSilenceAuth(ip)) return silentAuthResponse();
  const response = await handler(req, ctx);
  if (isCredentialsCallback(req) && shouldSilenceAuth(ip)) {
    return silentAuthResponse();
  }
  return response;
}
