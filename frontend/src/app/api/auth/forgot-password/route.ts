import { NextResponse } from "next/server";

/** Fixed-account mode: password reset via email is disabled. */
export async function POST() {
  return NextResponse.json({ error: "Password reset is disabled" }, { status: 403 });
}
