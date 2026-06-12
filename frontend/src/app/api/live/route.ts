import { WebSocket } from "ws";
import { requireSession } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const session = await requireSession();
  if (!session) {
    return new Response("Unauthorized", { status: 401 });
  }

  const wsUrl = process.env.BOT_WS_URL || "ws://127.0.0.1:8080";

  const stream = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      let ws: WebSocket | null = null;
      let closed = false;

      const keepAlive = setInterval(() => {
        if (!closed) controller.enqueue(encoder.encode(": keepalive\n\n"));
      }, 15000);

      const connect = () => {
        if (closed) return;
        ws = new WebSocket(wsUrl);

        ws.on("open", () => console.log("[SSE] Connected to bot WebSocket"));

        ws.on("message", (data) => {
          if (closed) return;
          try {
            const parsed = JSON.parse(data.toString());
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`));
          } catch (err) {
            console.error("[SSE] Invalid JSON:", err);
          }
        });

        ws.on("close", () => {
          if (!closed) setTimeout(connect, 2000);
        });

        ws.on("error", (err) => console.error("[SSE] WebSocket error:", err));
      };

      connect();

      req.signal.addEventListener("abort", () => {
        closed = true;
        clearInterval(keepAlive);
        ws?.close();
        try { controller.close(); } catch { /* noop */ }
      });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
