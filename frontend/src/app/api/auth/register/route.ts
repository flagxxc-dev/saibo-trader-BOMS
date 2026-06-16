import { NextResponse } from "next/server";

/** Single-account dashboard: registration disabled (use seed / web.env). */
export async function POST() {
  return NextResponse.json({ error: "Registration is disabled" }, { status: 403 });
}
