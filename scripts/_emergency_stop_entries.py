#!/usr/bin/env python3
"""Emergency stop: block new LIH entries on VPS."""
import sys
from pathlib import Path
import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import HOST, PROJ, USER, load_password


def ro(c, cmd, t=60):
    _, o, e = c.exec_command(cmd, timeout=t, get_pty=True)
    return (o.read() + e.read()).decode(errors="replace").strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=load_password(), timeout=60)
    try:
        patch = '{"patch":{"RISK_MAX_CONCURRENT_POSITIONS":"0","LIH_PAUSE_AFTER_ROUND":"true"},"control":"pause","user":"emergency-stop"}'
        ro(c, f"grep -E '^(RISK_MAX|LIH_PAUSE|LIVE_LIH)' '{PROJ}/.env'")
        ro(c, f"sed -i 's/^RISK_MAX_CONCURRENT_POSITIONS=.*/RISK_MAX_CONCURRENT_POSITIONS=0/' '{PROJ}/.env'")
        ro(
            c,
            f"grep -q '^LIH_PAUSE_AFTER_ROUND=' '{PROJ}/.env' "
            f"&& sed -i 's/^LIH_PAUSE_AFTER_ROUND=.*/LIH_PAUSE_AFTER_ROUND=true/' '{PROJ}/.env' "
            f"|| echo 'LIH_PAUSE_AFTER_ROUND=true' >> '{PROJ}/.env'",
        )
        ro(c, f"printf '%s' '{patch}' > '{PROJ}/logs/runtime_config.json'")
        import time
        time.sleep(4)
        raw = ro(
            c,
            "curl -s -m 8 http://127.0.0.1:8081/api/config | python3 -c \"import sys,json; "
            "l=json.load(sys.stdin).get('live',{}); print(json.dumps({'riskMax':l.get('riskMaxConcurrentPositions'),"
            "'open':l.get('openCount'),'sess':l.get('lihSessionLegsUsed'),'status':l.get('status'),"
            "'reason':l.get('statusReason'),'ops':l.get('openPositions')}, indent=2))\"",
            t=15,
        )
        print("=== STOPPED ===")
        print(raw)
        print("\n=== recent LIH ===")
        print(ro(c, f"grep -aE 'LIH LIVE|LEG1|HEDGE|CLOSED|entry-wait|session leg' '{PROJ}/bot.log' | tail -20"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
