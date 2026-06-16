#!/bin/bash
# Re-apply permanent firewall drops from ip_security.json after reboot/deploy.
set -euo pipefail
ROOT="/opt/polymarket-bot"
FILE="${IP_SECURITY_PATH:-$ROOT/logs/ip_security.json}"
SCRIPT="$ROOT/scripts/block_ip.sh"
if [ ! -f "$FILE" ] || [ ! -x "$SCRIPT" ]; then
  exit 0
fi
python3 - "$FILE" "$SCRIPT" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

file_path = Path(sys.argv[1])
script = sys.argv[2]
try:
    data = json.loads(file_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
for ip in (data.get("blacklist") or {}):
    subprocess.run(["bash", script, ip], check=False)
PY
