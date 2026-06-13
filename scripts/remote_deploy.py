#!/usr/bin/env python3
"""One-off remote deploy helper. Reads .deploy.local (gitignored). Do not commit secrets."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LOCAL = ROOT / ".deploy.local"
HOST = "70.34.221.132"
USER = "root"
REPO = "https://github.com/TrendHunter/saibo-trader.git"
PROJ = "/opt/polymarket-bot"


def load_password() -> str:
    if not DEPLOY_LOCAL.is_file():
        raise SystemExit(f"Missing {DEPLOY_LOCAL}")
    text = DEPLOY_LOCAL.read_text(encoding="utf-8")
    m = re.search(r'DEPLOY_SSH_PASSWORD\s*=\s*["\']([^"\']+)["\']', text)
    if m:
        return m.group(1)
    return text.strip()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> int:
    print(f"\n>>> {cmd}\n")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    channel = stdout.channel
    buf_out: list[str] = []
    buf_err: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        if channel.recv_ready():
            buf_out.append(channel.recv(65535).decode(errors="replace"))
        if channel.recv_stderr_ready():
            buf_err.append(channel.recv_stderr(65535).decode(errors="replace"))
        if channel.exit_status_ready():
            while channel.recv_ready():
                buf_out.append(channel.recv(65535).decode(errors="replace"))
            while channel.recv_stderr_ready():
                buf_err.append(channel.recv_stderr(65535).decode(errors="replace"))
            break
        if time.monotonic() > deadline:
            print("WARN: command still running on server (local read timeout)", file=sys.stderr)
            return -1
        time.sleep(0.5)
    out = "".join(buf_out)
    err = "".join(buf_err)
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
    return channel.recv_exit_status()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    pw = load_password()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=pw, timeout=30)

    try:
        if mode == "probe":
            for cmd in [
                "hostname; uname -a",
                "ls -la /opt/polycopy 2>/dev/null || ls -la /opt",
                "cd /opt/polycopy 2>/dev/null && git log -1 --oneline && git remote -v | head -2 || echo no_git",
                "cd /opt/polycopy 2>/dev/null && ls -la start_bot.py dashboard_bridge.py build/trading-core 2>&1 | head -5",
                "pgrep -af 'start_bot|dashboard_bridge|trading-core|python' || true",
                "find /opt /root /home -maxdepth 5 \\( -name start_bot.py -o -name trading-core -o -name DumpHedgeDetector.cpp \\) 2>/dev/null | head -20",
                "ls -la /opt/polymarket-bot 2>/dev/null || echo no_polymarket_bot",
                "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true",
                "ss -tlnp | grep -E ':3001|:8080|:8081' || true",
                "curl -s -o /dev/null -w 'bot_api=%{http_code}\n' http://127.0.0.1:8081/health 2>/dev/null || true",
                "curl -s -o /dev/null -w 'web=%{http_code}\n' http://127.0.0.1:3001/login 2>/dev/null || true",
                f"tail -n 8 '{PROJ}/logs/frontend.log' 2>/dev/null || true",
            ]:
                run(client, cmd, timeout=60)
            return 0

        if mode == "setup":
            steps = [
                "command -v git && python3 --version",
                f"test -d '{PROJ}/.git' && echo exists || git clone --branch main '{REPO}' '{PROJ}'",
                f"cd '{PROJ}' && git pull origin main",
                f"test -f '{PROJ}/.env' || cp '{PROJ}/.env.example' '{PROJ}/.env'",
                "dnf install -y gcc gcc-c++ make git openssl-devel python3 python3-pip 2>/dev/null || "
                "yum install -y gcc gcc-c++ make git openssl-devel python3 python3-pip",
                f"cd '{PROJ}' && chmod +x build.sh && ./build.sh",
                f"cd '{PROJ}' && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
                "pkill -f 'start_bot.py' || true; pkill -f 'dashboard_bridge.py' || true; pkill -f 'trading-core' || true; sleep 2",
                f"cd '{PROJ}' && mkdir -p logs && nohup .venv/bin/python start_bot.py >> logs/bridge.log 2>&1 &",
                "sleep 8",
                "pgrep -af 'start_bot|trading-core' || true",
                f"tail -n 25 '{PROJ}/logs/bot.log' 2>/dev/null || tail -n 25 '{PROJ}/logs/bridge.log' 2>/dev/null || true",
            ]
            rc = 0
            for step in steps:
                r = run(client, step, timeout=1800)
                if r != 0 and ("git clone" in step or "./build.sh" in step):
                    return r
                if r != 0:
                    rc = r
            return rc

        if mode == "start":
            start_sh = ROOT / "scripts" / "server_start_bot.sh"
            remote_sh = f"{PROJ}/server_start_bot.sh"
            sftp = client.open_sftp()
            sftp.put(str(start_sh), remote_sh)
            sftp.close()
            steps = [
                f"ls -la '{PROJ}/build/trading-core'",
                f"cd '{PROJ}' && .venv/bin/pip install -q -r requirements.txt",
                f"chmod +x '{remote_sh}' && bash '{remote_sh}'",
                "sleep 8",
                "pgrep -af 'start_bot|trading-core' || true",
                f"tail -n 40 '{PROJ}/logs/bridge.log' 2>/dev/null || true",
                f"tail -n 40 '{PROJ}/logs/bot.log' 2>/dev/null || true",
            ]
            for step in steps:
                run(client, step, timeout=180)
            return 0

        if mode == "disable-mongo":
            script = ROOT / "scripts" / "server_disable_mongo.sh"
            remote = f"{PROJ}/server_disable_mongo.sh"
            sftp = client.open_sftp()
            sftp.put(str(script), remote)
            sftp.close()
            run(client, f"chmod +x '{remote}' && bash '{remote}'", timeout=120)
            return 0

        if mode == "cleanup":
            cleanup_sh = ROOT / "scripts" / "server_disk_cleanup.sh"
            remote = f"{PROJ}/server_disk_cleanup.sh"
            purge = os.environ.get("PURGE_MONGO_DATA", "0")
            sftp = client.open_sftp()
            sftp.put(str(cleanup_sh), remote)
            sftp.close()
            steps = [
                f"chmod +x '{remote}'",
                f"PURGE_MONGO_DATA={purge} bash '{remote}'",
                "du -sh /var/log/mongodb /var/lib/mongo /opt/polycopy/backups 2>/dev/null || true",
            ]
            for step in steps:
                run(client, step, timeout=300)
            return 0

        if mode == "server-paper":
            # Revert server to paper only (does NOT touch local .env).
            bot_sh = ROOT / "scripts" / "server_start_bot.sh"
            remote_bot = f"{PROJ}/server_start_bot.sh"
            sftp = client.open_sftp()
            sftp.put(str(bot_sh), remote_bot)
            sftp.close()
            steps = [
                f"sed -i 's/^PAPER_MODE=.*/PAPER_MODE=true/' '{PROJ}/.env'",
                f"grep -E '^(PAPER_MODE|RISK_MAX)' '{PROJ}/.env' | head -5",
                f"chmod +x '{remote_bot}' && bash '{remote_bot}'",
                "sleep 8",
                "pgrep -af 'start_bot|trading-core' || true",
                f"tail -n 12 '{PROJ}/logs/bridge.log' 2>/dev/null || true",
            ]
            for step in steps:
                run(client, step, timeout=180)
            return 0

        if mode == "go-live-small":
            # Server-only small live: patch server .env (does NOT upload local .env).
            patch_py = f"""import shutil
from pathlib import Path
P = Path("{PROJ}/.env")
updates = {{
    "PAPER_MODE": "false",
    "RISK_MAX_POSITION_FRACTION": "0.35",
    "RISK_MAX_CONCURRENT_POSITIONS": "1",
    "MIN_ORDER_SIZE": "5.0",
    "DH_ENABLE_5M": "true",
    "DH_ENABLE_15M": "false",
    "DH_ENABLE_5M_BTC": "true",
    "DH_ENABLE_5M_ETH": "false",
    "DH_ENABLE_5M_SOL": "false",
    "DH_ENABLE_15M_BTC": "false",
    "DH_ENABLE_15M_ETH": "false",
    "AUTO_REDEEM": "true",
    "LIVE_DH_DRY_RUN": "true",
    "PAPER_STATE_PERSIST": "false",
    "LIVE_STARTING_BALANCE": "21.077149",
}}
shutil.copy2(P, str(P) + ".pre-live-small.bak")
lines = P.read_text(encoding="utf-8").splitlines()
done = set()
out = []
for line in lines:
    s = line.strip()
    if s and not s.startswith("#") and "=" in line:
        k = line.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{{k}}={{updates[k]}}")
            done.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in done:
        out.append(f"{{k}}={{v}}")
P.write_text("\\n".join(out) + "\\n", encoding="utf-8")
print("patched", len(updates), "keys")
"""
            bot_sh = ROOT / "scripts" / "server_start_bot.sh"
            remote_bot = f"{PROJ}/server_start_bot.sh"
            sftp = client.open_sftp()
            sftp.put(str(bot_sh), remote_bot)
            with sftp.file(f"{PROJ}/_patch_live_small.py", "w") as f:
                f.write(patch_py)
            sftp.close()
            steps = [
                f"test -f '{PROJ}/logs/paper_state.json' && "
                f"cp '{PROJ}/logs/paper_state.json' '{PROJ}/logs/paper_state.json.pre-live.bak' || true",
                f"cd '{PROJ}' && .venv/bin/python _patch_live_small.py",
                f"grep -E '^(PAPER_MODE|RISK_|MIN_ORDER|DH_ENABLE|AUTO_REDEEM)' '{PROJ}/.env'",
                f"cd '{PROJ}' && .venv/bin/python derive_and_update_keys.py",
                f"grep -E '^POLY_API' '{PROJ}/.env' | sed "
                "'s/POLY_API_SECRET=.*/POLY_API_SECRET=***MASKED***/;"
                "s/POLY_PASSPHRASE=.*/POLY_PASSPHRASE=***MASKED***/;"
                "s/POLY_API_KEY=.*/POLY_API_KEY=***SET***/'",
                f"cd '{PROJ}' && .venv/bin/python fetch_balance.py",
                f"cd '{PROJ}' && .venv/bin/python start_bot.py --preflight-only",
                f"chmod +x '{remote_bot}' && bash '{remote_bot}'",
                "sleep 10",
                "pgrep -af 'start_bot|trading-core' || true",
                f"tail -n 30 '{PROJ}/logs/bot.log' 2>/dev/null || tail -n 30 '{PROJ}/logs/bridge.log'",
            ]
            rc = 0
            for step in steps:
                r = run(client, step, timeout=300)
                if r != 0 and "derive_and_update_keys" in step:
                    rc = r
                if r != 0 and "preflight-only" in step:
                    return r
            return rc

        if mode == "shadow-dh":
            # Pull latest, enable LIVE_DH_DRY_RUN, rebuild core, restart (no real orders).
            bot_sh = ROOT / "scripts" / "server_start_bot.sh"
            remote_bot = f"{PROJ}/server_start_bot.sh"
            sftp = client.open_sftp()
            sftp.put(str(bot_sh), remote_bot)
            sftp.close()
            steps = [
                f"cd '{PROJ}' && git stash push -m deploy-sync || true",
                f"cd '{PROJ}' && git pull origin main",
                f"grep -q '^LIVE_DH_DRY_RUN=' '{PROJ}/.env' && "
                f"sed -i 's/^LIVE_DH_DRY_RUN=.*/LIVE_DH_DRY_RUN=true/' '{PROJ}/.env' || "
                f"echo 'LIVE_DH_DRY_RUN=true' >> '{PROJ}/.env'",
                f"grep -E '^(PAPER_MODE|LIVE_DH_DRY_RUN|RISK_MAX)' '{PROJ}/.env' | head -6",
                f"cd '{PROJ}' && chmod +x build.sh && ./build.sh",
                f"chmod +x '{remote_bot}' && bash '{remote_bot}'",
                "sleep 10",
                "pgrep -af 'start_bot|trading-core' || true",
                f"grep -E 'LIVE DH|SHADOW|Dry-run|Mode: LIVE' '{PROJ}/bot.log' | tail -20",
            ]
            rc = 0
            for step in steps:
                r = run(client, step, timeout=3600)
                if r != 0 and "git pull" in step:
                    rc = r
                if r != 0 and "./build.sh" in step:
                    return r
            return rc

        if mode == "sync-env":
            local_env = ROOT / ".env"
            if not local_env.is_file():
                print(f"ERROR: missing {local_env}", file=sys.stderr)
                return 1
            remote_env = f"{PROJ}/.env"
            sftp = client.open_sftp()
            try:
                sftp.rename(remote_env, f"{remote_env}.bak")
            except OSError:
                pass
            sftp.put(str(local_env), remote_env)
            sftp.close()
            keys = [
                line.split("=", 1)[0].strip()
                for line in local_env.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#") and "=" in line
            ]
            print(f"Uploaded .env ({len(keys)} keys)", file=sys.stderr)
            bot_sh = ROOT / "scripts" / "server_start_bot.sh"
            remote_bot = f"{PROJ}/server_start_bot.sh"
            sftp = client.open_sftp()
            sftp.put(str(bot_sh), remote_bot)
            sftp.close()
            run(client, f"chmod +x '{remote_bot}' && bash '{remote_bot}'", timeout=120)
            run(
                client,
                f"grep -E '^(PAPER_MODE|DH_SUM_TARGET|DH_MIN_DISCOUNT|RISK_)' '{remote_env}' | head -10",
                timeout=30,
            )
            run(client, "pgrep -af 'start_bot|trading-core' || true", timeout=15)
            return 0

        if mode == "set-auth":
            import secrets

            user = sys.argv[2] if len(sys.argv) > 2 else "zhan"
            password = sys.argv[3] if len(sys.argv) > 3 else "qilai"
            secret = secrets.token_hex(32)
            web_env = (
                f"AUTH_USERNAME={user}\n"
                f"AUTH_PASSWORD={password}\n"
                f"NEXTAUTH_URL=http://{HOST}:3001\n"
                f"NEXTAUTH_SECRET={secret}\n"
                f"AUTH_TRUST_HOST=true\n"
            )
            sftp = client.open_sftp()
            with sftp.file(f"{PROJ}/web.env", "w") as f:
                f.write(web_env)
            sftp.close()
            restart_sh = ROOT / "scripts" / "server_restart_web.sh"
            remote = f"{PROJ}/server_restart_web.sh"
            sftp = client.open_sftp()
            sftp.put(str(restart_sh), remote)
            sftp.close()
            run(client, f"chmod +x '{remote}' && bash '{remote}'", timeout=300)
            print(f"Web login set to {user} / (password updated)", file=sys.stderr)
            return 0

        if mode == "web":
            web_sh = ROOT / "scripts" / "server_start_web.sh"
            remote_web = f"{PROJ}/server_start_web.sh"
            sftp = client.open_sftp()
            sftp.put(str(web_sh), remote_web)
            sftp.close()
            steps = [
                "command -v node && node --version || "
                "(curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - && dnf install -y nodejs)",
                f"chmod +x '{remote_web}'",
                f"export NEXTAUTH_URL=http://{HOST}:3001 && bash '{remote_web}'",
                "firewall-cmd --permanent --add-port=3001/tcp 2>/dev/null || true",
                "firewall-cmd --reload 2>/dev/null || true",
                "ss -tlnp | grep -E ':3001|:8080|:8081' || true",
                f"tail -n 25 '{PROJ}/logs/frontend.log' 2>/dev/null || true",
            ]
            for step in steps:
                run(client, step, timeout=1800)
            return 0

        if mode == "deploy":
            proj = PROJ
            _, stdout, _ = client.exec_command(f"test -d '{proj}/.git' && echo ok", timeout=15)
            if "ok" not in stdout.read().decode():
                print(f"ERROR: {proj} missing — run: python scripts/remote_deploy.py setup", file=sys.stderr)
                return 1
            bot_sh = ROOT / "scripts" / "server_start_bot.sh"
            web_sh = ROOT / "scripts" / "server_start_web.sh"
            remote_bot = f"{proj}/server_start_bot.sh"
            remote_web = f"{proj}/server_start_web.sh"
            sftp = client.open_sftp()
            sftp.put(str(bot_sh), remote_bot)
            sftp.put(str(web_sh), remote_web)
            sftp.close()
            steps = [
                f"cd '{proj}' && git pull origin main",
                f"cd '{proj}' && .venv/bin/pip install -q -r requirements.txt",
                f"chmod +x '{remote_bot}' '{remote_web}' && bash '{remote_bot}'",
                "command -v node && node --version || "
                "(curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - && dnf install -y nodejs)",
                f"export NEXTAUTH_URL=http://{HOST}:3001 && bash '{remote_web}'",
                "firewall-cmd --permanent --add-port=3001/tcp 2>/dev/null || true",
                "firewall-cmd --reload 2>/dev/null || true",
                "ss -tlnp | grep -E ':3001|:8080|:8081' || true",
                "pgrep -af 'start_bot|trading-core|next-server' || true",
            ]
            rc = 0
            for step in steps:
                r = run(client, step, timeout=1800)
                if r != 0 and "git pull" in step:
                    rc = r
            return rc

        print(f"Unknown mode: {mode}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
