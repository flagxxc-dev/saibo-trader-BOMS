import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth";
import { fetchClobTrades } from "@/lib/botApi";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const session = await requireSession();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const url = new URL(req.url);
    const limit = Number(url.searchParams.get("limit") || "200");
    const data = await fetchClobTrades(Number.isFinite(limit) ? limit : 200);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to load CLOB trades", trades: [] },
      { status: 502 }
    );
  }
}
