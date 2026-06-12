import asyncio
import json
import subprocess
import websockets
import threading
import sys
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from bot_config import (
    public_config,
    update_env,
    append_audit,
    read_audit,
    write_runtime_config,
)

# Configuration
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8080"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8081"))
PREFLIGHT_PATH = Path(os.getenv("PREFLIGHT_PATH", "logs/preflight.json"))
CORE_CMD = ["./build/trading-core.exe"] if os.name == "nt" else ["./build/trading-core"]

clients = set()
latest_data = "{}"
_core_ready_printed = False


def _print_startup_banner() -> None:
    cfg = public_config()
    pre = _read_preflight()
    mode = (pre.get("mode") or ("paper" if os.getenv("PAPER_MODE", "true").lower() != "false" else "live")).upper()
    ok = pre.get("ok", True)
    mark = "✅" if ok else "⚠️"
    print(f"\n{mark} Bridge 就绪 | 模式 {mode} | WS :{WS_PORT} | API :{HTTP_PORT}", file=sys.stderr)
    if pre.get("wallet", {}).get("funder"):
        w = pre["wallet"]
        print(
            f"   钱包 funder={w.get('funder')} signer={w.get('signer')} sigType={w.get('signature_type')}",
            file=sys.stderr,
        )
    if cfg:
        print(
            f"   DH sum≤{cfg.get('DH_SUM_TARGET', '?')}  "
            f"5m={cfg.get('DH_ENABLE_5M', '?')}  15m={cfg.get('DH_ENABLE_15M', '?')}",
            file=sys.stderr,
        )
    print("", file=sys.stderr)


def _maybe_print_core_ready(line: str) -> None:
    global _core_ready_printed
    if _core_ready_printed:
        return
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return
    if "strategy" not in d or "balance" not in d:
        return
    _core_ready_printed = True
    paper = d.get("isPaperMode", True)
    bal = float(d.get("balance") or 0)
    open_n = d.get("openCount", 0)
    status = d.get("statusReason") or d.get("status", 0)
    assets_5m = [
        a for a, k in (("BTC", "dhEnable5mBtc"), ("ETH", "dhEnable5mEth"), ("SOL", "dhEnable5mSol"))
        if d.get(k, True) and d.get("dhEnable5m", True)
    ]
    assets_15m = [
        a for a, k in (("BTC", "dhEnable15mBtc"), ("ETH", "dhEnable15mEth"))
        if d.get(k, True) and d.get("dhEnable15m", True)
    ]
    fee = "动态" if d.get("useDynamicFees") else "扁平"
    print(
        f"[CORE 就绪] {'纸面' if paper else '实盘'} | 余额 ${bal:.2f} | 持仓 {open_n} | 状态 {status}",
        file=sys.stderr,
    )
    print(
        f"            5m[{' '.join(assets_5m) or '关'}]  "
        f"15m[{' '.join(assets_15m) or '关'}]  "
        f"费率={fee}  市场={d.get('marketsScanned', 0)}",
        file=sys.stderr,
    )


def _read_preflight() -> dict:
    if not PREFLIGHT_PATH.is_file():
        return {}
    try:
        return json.loads(PREFLIGHT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_preflight() -> None:
    if os.getenv("PREFLIGHT_SKIP", "").strip().lower() in ("1", "true", "yes"):
        return
    script = Path(__file__).resolve().parent / "live_preflight.py"
    if not script.is_file():
        return
    try:
        subprocess.run([sys.executable, str(script)], check=False)
    except Exception as exc:
        print(f"[preflight] skipped: {exc}", file=sys.stderr)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if status >= 400:
        err = payload.get("error", payload)
        print(f"[HTTP] ERROR {status} {err}", file=sys.stderr)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc


class ConfigHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} {fmt % args}", file=sys.stderr)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                _json_response(self, 200, {"ok": True})
            elif path == "/api/config":
                _json_response(self, 200, {"config": public_config(), "live": json.loads(latest_data or "{}")})
            elif path == "/api/audit":
                _json_response(self, 200, {"events": read_audit()})
            elif path == "/api/preflight":
                report = _read_preflight()
                if not report:
                    _json_response(self, 404, {"error": "preflight not run yet"})
                else:
                    _json_response(self, 200, {"preflight": report})
            else:
                _json_response(self, 404, {"error": "not found"})
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = _read_body(self)
            user = str(body.get("user") or "web")

            if path == "/api/config":
                patch = body.get("patch") or {}
                if not isinstance(patch, dict) or not patch:
                    _json_response(self, 400, {"error": "patch object required"})
                    return
                applied = update_env(patch)
                append_audit({"type": "config", "user": user, "patch": applied})
                write_runtime_config({"patch": applied, "user": user})
                _json_response(self, 200, {"ok": True, "applied": applied})

            elif path == "/api/control":
                action = str(body.get("action") or "").lower()
                if action not in ("pause", "resume", "reset_kill"):
                    _json_response(self, 400, {"error": "action must be pause|resume|reset_kill"})
                    return
                reason = str(body.get("reason") or f"Manual {action} via web")
                append_audit({"type": "control", "user": user, "action": action, "reason": reason})
                write_runtime_config({"control": action, "reason": reason, "user": user})
                _json_response(self, 200, {"ok": True, "action": action})

            else:
                _json_response(self, 404, {"error": "not found"})
        except ValueError as exc:
            _json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            _json_response(self, 500, {"error": str(exc)})


def run_http_server():
    server = ThreadingHTTPServer((WS_HOST, HTTP_PORT), ConfigHTTPHandler)
    print(f"Config API on http://{WS_HOST}:{HTTP_PORT}", file=sys.stderr)
    server.serve_forever()


async def broadcast(message):
    if clients:
        await asyncio.gather(*[client.send(message) for client in clients])


async def handler(websocket):
    global latest_data
    clients.add(websocket)
    try:
        await websocket.send(latest_data)
        async for _message in websocket:
            pass
    finally:
        clients.remove(websocket)


def run_core():
    global latest_data
    process = subprocess.Popen(
        CORE_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=os.getcwd(),
    )

    def log_stderr():
        for line in process.stderr:
            print(f"[CORE LOG] {line.strip()}", file=sys.stderr)

    threading.Thread(target=log_stderr, daemon=True).start()

    for line in process.stdout:
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            latest_data = line
            _maybe_print_core_ready(line)
            asyncio.run_coroutine_threadsafe(broadcast(line), loop)
        else:
            print(f"[CORE INFO] {line}", file=sys.stderr)


async def main():
    global loop
    loop = asyncio.get_running_loop()

    _run_preflight()
    _print_startup_banner()
    threading.Thread(target=run_http_server, daemon=True).start()

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        print(f"Bridge started on ws://{WS_HOST}:{WS_PORT}", file=sys.stderr)
        threading.Thread(target=run_core, daemon=True).start()
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down bridge...")
