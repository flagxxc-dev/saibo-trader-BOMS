#!/usr/bin/env python3
import json
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER  # noqa: E402


def main() -> int:
    local = (ROOT / "scripts" / "live_lih_reconcile.py").read_text(encoding="utf-8")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=30)
    sftp = c.open_sftp()
    with sftp.open(f"{PROJ}/scripts/live_lih_reconcile.py", "w") as f:
        f.write(local)
    sftp.close()

    def run(cmd: str) -> str:
        _, o, e = c.exec_command(cmd, timeout=120)
        out = (o.read() + e.read()).decode("utf-8", errors="replace")
        print(out)
        return out

    print("=== reconcile ===")
    run(f"cd '{PROJ}' && .venv/bin/python scripts/live_lih_reconcile.py")
    print("=== reload ===")
    body = json.dumps({"control": "reload_lih_state", "user": "sync-positions"})
    run(
        f"curl -s -X POST http://127.0.0.1:8081/api/control "
        f"-H 'Content-Type: application/json' -d '{body}'"
    )
    print("=== after ===")
    run(
        "curl -s http://127.0.0.1:8081/api/config | python3 -c "
        "'import sys,json; d=json.load(sys.stdin); l=d.get(\"live\",{}); "
        "print(\"openCount\",l.get(\"openCount\")); ops=l.get(\"openPositions\") or []; "
        "print(\"positions\",len(ops)); "
        "[print(p.get(\"asset\"),p.get(\"heldSide\"),p.get(\"yesSize\"),p.get(\"noSize\"),"
        "str(p.get(\"question\",\"\"))[:50]) for p in ops]'"
    )
    print("=== expiry check ===")
    run(
        f"cd '{PROJ}' && .venv/bin/python -c \"import time; "
        "from clob_trades import fetch_user_trades, parse_market_end_ts; "
        "now=time.time(); print('server_now', now); "
        "for x in fetch_user_trades(6): "
        "  title=x.get('title',''); end=parse_market_end_ts(title, ref_ts=now); "
        "  print(x.get('asset'), x.get('outcome'), 'end', end, 'expired', bool(end and now>end+30), title[:45])\""
    )
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
