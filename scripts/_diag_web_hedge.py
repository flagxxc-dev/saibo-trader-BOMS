#!/usr/bin/env python3
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from remote_deploy import PROJ, load_password, HOST, USER
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=load_password(), timeout=30)

def run(cmd):
    _, o, e = c.exec_command(cmd, timeout=60)
    return (o.read() + e.read()).decode("utf-8", errors="replace")

print("=== status ===")
print(run("curl -s http://127.0.0.1:8081/api/config | python3 -c 'import sys,json; d=json.load(sys.stdin); l=d.get(\"live\",{}); print(json.dumps({k:l.get(k) for k in [\"balance\",\"openCount\",\"status\",\"statusReason\",\"riskMaxConcurrentPositions\",\"lihSessionLegsUsed\",\"lihSessionMaxLegs\"]}, indent=2)); ops=l.get(\"openPositions\") or []; print(\"positions\",len(ops)); [print(p) for p in ops[:5]]'"))
print("=== live_state open ===")
print(run(f"python3 -c \"import json; d=json.load(open('{PROJ}/logs/live_state.json')); print('open',len(d.get('open_lih_positions',{{}}))); import pprint; pprint.pp(list(d.get('open_lih_positions',{{}}).values())[:3])\""))
print("=== chain trades ===")
print(run(f"cd '{PROJ}' && .venv/bin/python -c \"from clob_trades import fetch_user_trades; t=fetch_user_trades(limit=12); [print(x.get('asset'),x.get('outcome'),x.get('size'),x.get('price'),str(x.get('title',''))[:55]) for x in t[:8]]\""))
print("=== hedge/leg1 logs ===")
print(run(f"grep -E 'entry-wait|HEDGE|LEG1|rebalance|PAUSE|other slot|in-flight|no cheap|marginal|hedge skip' '{PROJ}/bot.log' | tail -40"))
c.close()
