import { getToken } from "next-auth/jwt";
import { NextFetchEvent, NextRequest, NextResponse } from "next/server";
import { getClientIpFromRequest, shouldSilenceAuth } from "@/lib/ipGuard";

const protectedMatchers = [
  /^\/dashboard(?:\/|$)/,
  /^\/strategies(?:\/|$)/,
  /^\/risk(?:\/|$)/,
  /^\/history(?:\/|$)/,
  /^\/audit(?:\/|$)/,
  /^\/api\/live(?:\/|$)/,
  /^\/api\/bot(?:\/|$)/,
];

function isProtectedPath(pathname: string) {
  return protectedMatchers.some((pattern) => pattern.test(pathname));
}

export default async function proxy(req: NextRequest, _event: NextFetchEvent) {
  const ip = getClientIpFromRequest(req);
  const pathname = req.nextUrl.pathname;

  if (shouldSilenceAuth(ip)) {
    if (pathname.startsWith("/api/auth")) {
      return new NextResponse(null, { status: 404 });
    }
    if (isProtectedPath(pathname)) {
      return NextResponse.redirect(new URL("/login", req.url));
    }
    return NextResponse.next();
  }

  if (isProtectedPath(pathname)) {
    const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
    if (!token) {
      return NextResponse.redirect(new URL("/login", req.url));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/api/auth/:path*",
    "/dashboard/:path*",
    "/strategies/:path*",
    "/risk/:path*",
    "/history/:path*",
    "/audit/:path*",
    "/api/live/:path*",
    "/api/bot/:path*",
  ],
};
