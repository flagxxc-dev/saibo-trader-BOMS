import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth";
import { fetchBotConfig, updateBotConfig, botControl, fetchAuditEvents } from "@/lib/botApi";

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await requireSession();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const data = await fetchBotConfig();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Bot unreachable" }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const session = await requireSession();
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await req.json();
    const user = session.user?.email || "web";
    if (body.action) {
      const result = await botControl(body.action, user, body.reason);
      return NextResponse.json(result);
    }
    if (body.patch) {
      const result = await updateBotConfig(body.patch, user);
      return NextResponse.json(result);
    }
    return NextResponse.json({ error: "patch or action required" }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Update failed" }, { status: 502 });
  }
}
