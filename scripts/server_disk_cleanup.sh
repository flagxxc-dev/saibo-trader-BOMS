#!/bin/bash
# Free disk on small Vultr instances. Safe for polymarket-bot-only deployments.
set -euo pipefail

echo "=== before ==="
df -h /

# MongoDB (polycopy legacy; C++ bot does not use it)
if systemctl is-active mongod &>/dev/null; then
  systemctl stop mongod || true
fi
systemctl disable mongod 2>/dev/null || true
rm -rf /var/log/mongodb/*
# Optional: drop mongo data if polycopy is retired (frees ~1.4G)
if [ "${PURGE_MONGO_DATA:-0}" = "1" ]; then
  rm -rf /var/lib/mongo/*
fi

# System logs
journalctl --vacuum-size=80M 2>/dev/null || true
find /var/log -name 'messages-*' -mtime +7 -delete 2>/dev/null || true
find /var/log -name 'secure-*' -mtime +14 -delete 2>/dev/null || true

# polycopy legacy backups (often multi-GB)
if [ -d /opt/polycopy/backups ]; then
  find /opt/polycopy/backups -type f -mtime +3 -delete 2>/dev/null || true
fi

# npm / conan caches
rm -rf /root/.npm/_cacache 2>/dev/null || true
rm -rf /root/.conan2/p/b/* 2>/dev/null || true

dnf clean all 2>/dev/null || yum clean all 2>/dev/null || true

echo "=== after ==="
df -h /
du -sh /var/log/mongodb /var/lib/mongo /opt/polycopy/backups 2>/dev/null || true
