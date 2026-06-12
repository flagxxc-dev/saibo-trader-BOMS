import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth";
import { fetchPreflight } from "@/lib/botApi";

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await requireSession();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const data = await fetchPreflight();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Bot unreachable" }, { status: 502 });
  }
}
