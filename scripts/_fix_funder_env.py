#!/usr/bin/env python3
from pathlib import Path

PROJ = Path("/opt/polymarket-bot")
FUNDER = "0x645ae230aea42f760a17fc96d5407ed42931ab1c"
p = PROJ / ".env"
lines = p.read_text(encoding="utf-8").splitlines()
out = []
seen = False
for line in lines:
    if line.startswith("POLYMARKET_FUNDER=") or line.startswith("#POLYMARKET_FUNDER="):
        if not seen:
            out.append(f"POLYMARKET_FUNDER={FUNDER}")
            seen = True
        continue
    out.append(line)
if not seen:
    out.append(f"POLYMARKET_FUNDER={FUNDER}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("POLYMARKET_FUNDER fixed:", FUNDER)
