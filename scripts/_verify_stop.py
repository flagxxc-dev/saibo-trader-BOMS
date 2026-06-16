#!/usr/bin/env python3
import sys
from pathlib import Path
import paramiko
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=load_password(), timeout=30)
def run(cmd):
    _, o, e = c.exec_command(cmd, timeout=60)
    return (o.read() + e.read()).decode()
print(run("pgrep -af 'trading-core|start_bot|ninja|build.sh' || echo STOPPED"))
print(run(f"grep -E '^PAPER_MODE=|^LIH_ENABLED=' '{PROJ}/.env'"))
print(run(f"test -f '{PROJ}/logs/STOP_TRADING' && echo STOP_TRADING=YES || echo STOP_TRADING=NO"))
c.close()
