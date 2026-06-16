#!/bin/bash
# Permanently drop traffic from an IPv4 address (firewalld).
set -euo pipefail
IP="${1:-}"
if [[ -z "$IP" ]]; then
  exit 0
fi
if [[ ! "$IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  exit 0
fi
RULE="rule family=\"ipv4\" source address=\"${IP}\" drop"
if command -v firewall-cmd >/dev/null 2>&1; then
  if firewall-cmd --permanent --query-rich-rule="$RULE" >/dev/null 2>&1; then
    exit 0
  fi
  firewall-cmd --permanent --add-rich-rule="$RULE"
  firewall-cmd --reload
  exit 0
fi
if command -v iptables >/dev/null 2>&1; then
  if iptables -C INPUT -s "$IP" -j DROP >/dev/null 2>&1; then
    exit 0
  fi
  iptables -I INPUT -s "$IP" -j DROP
fi
