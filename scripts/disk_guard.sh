#!/bin/bash
# Disk guard: when root filesystem use >= threshold, prune old build artifacts and logs.
# Safe for running bot — never deletes live_state.json, paper_state.json, or trading-core binary.
#
# Env:
#   DISK_GUARD_THRESHOLD  — trigger percent (default 95)
#   DISK_GUARD_PATH       — mount to check (default /)
#   PROJ                  — repo root (default: parent of scripts/)
#   LOG_MAX_MB            — truncate active logs above this (default 40)
#   LOG_KEEP_DAYS         — delete rotated/old logs older than N days (default 14)
#   BAK_KEEP_COUNT        — keep newest N trading-core.bak-* (default 1)
#   DISK_GUARD_DRY_RUN    — 1 = report only, no deletes
#   DISK_GUARD_FORCE      — 1 = run cleanup even below threshold (for testing)
#
# Cron example (install_disk_guard_cron.sh):
#   15 */4 * * * /opt/polymarket-bot/scripts/disk_guard.sh >> /opt/polymarket-bot/logs/disk_guard.log 2>&1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="${PROJ:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DISK_GUARD_PATH="${DISK_GUARD_PATH:-/}"
DISK_GUARD_THRESHOLD="${DISK_GUARD_THRESHOLD:-95}"
LOG_MAX_MB="${LOG_MAX_MB:-40}"
LOG_KEEP_DAYS="${LOG_KEEP_DAYS:-14}"
BAK_KEEP_COUNT="${BAK_KEEP_COUNT:-1}"
DRY_RUN="${DISK_GUARD_DRY_RUN:-0}"
FORCE="${DISK_GUARD_FORCE:-0}"

log() { echo "[$(date -Iseconds)] $*"; }

disk_use_pct() {
  df -P "$DISK_GUARD_PATH" | awk 'NR==2 {gsub(/%/,"",$5); print $5}'
}

bytes_freed_estimate=0

maybe_rm() {
  local target="$1"
  [[ -e "$target" ]] || return 0
  local sz
  sz=$(du -sb "$target" 2>/dev/null | awk '{print $1}' || echo 0)
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY-RUN delete: $target (${sz} bytes)"
    return 0
  fi
  rm -rf "$target" 2>/dev/null || true
  bytes_freed_estimate=$((bytes_freed_estimate + sz))
  log "deleted: $target (${sz} bytes)"
}

truncate_large_log() {
  local f="$1"
  local max_mb="${2:-$LOG_MAX_MB}"
  [[ -f "$f" ]] || return 0
  local sz max_bytes
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  max_bytes=$((max_mb * 1024 * 1024))
  if (( sz <= max_bytes )); then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY-RUN truncate: $f (${sz} -> ${max_bytes} bytes tail)"
    return 0
  fi
  local tmp="${f}.truncate.$$"
  tail -c "$max_bytes" "$f" > "$tmp" && mv "$tmp" "$f"
  log "truncated: $f (was ${sz} bytes, kept last ${max_mb}MB)"
}

build_running() {
  pgrep -f 'build-lowmem\.sh|build\.sh|cmake --build|/ninja|cc1plus|lto1' >/dev/null 2>&1
}

cleanup_build_artifacts() {
  local aggressive="${1:-0}"
  log "=== phase: build artifacts (aggressive=${aggressive}) ==="
  local bak_dir="$PROJ/build"
  if [[ -d "$bak_dir" ]]; then
    mapfile -t baks < <(ls -1t "$bak_dir"/trading-core.bak-* 2>/dev/null || true)
    if ((${#baks[@]} > BAK_KEEP_COUNT)); then
      for ((i = BAK_KEEP_COUNT; i < ${#baks[@]}; i++)); do
        maybe_rm "${baks[$i]}"
      done
    fi
    for f in "$bak_dir/build.log" "$PROJ/logs/build_bg.log"; do
      truncate_large_log "$f" 10
    done
  fi

  maybe_rm /root/.npm/_cacache
  if [[ -d /root/.conan2/p/b ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      find /root/.conan2/p/b -mindepth 1 -maxdepth 1 -mtime +3 -print 2>/dev/null | while read -r d; do
        log "DRY-RUN delete: $d"
      done
    else
      find /root/.conan2/p/b -mindepth 1 -maxdepth 1 -mtime +3 -exec rm -rf {} + 2>/dev/null || true
      log "pruned conan2 package build cache older than 3d"
    fi
  fi

  maybe_rm "$PROJ/frontend/.next/cache"

  if [[ "$aggressive" != "1" ]]; then
    return 0
  fi

  if build_running; then
    log "skip aggressive build/ cleanup — compile in progress"
    return 0
  fi

  if [[ -d "$PROJ/build/CMakeFiles" ]]; then
    maybe_rm "$PROJ/build/CMakeFiles"
  fi
  if [[ -d "$PROJ/build/_deps" ]]; then
    maybe_rm "$PROJ/build/_deps"
  fi
  if [[ "$DRY_RUN" != "1" ]]; then
    rm -f "$PROJ/build/.ninja_deps" "$PROJ/build/.ninja_log" 2>/dev/null || true
  fi
}

cleanup_logs() {
  log "=== phase: logs ==="
  mkdir -p "$PROJ/logs"

  # Never touch state / runtime config
  local keep_names="live_state.json paper_state.json runtime_config.json preflight.json"

  for f in \
    "$PROJ/logs/bridge.log" \
    "$PROJ/logs/bot.log" \
    "$PROJ/logs/frontend.log" \
    "$PROJ/logs/disk_guard.log" \
    "$PROJ/serverbot.log" \
    "$PROJ/bot.log"; do
    truncate_large_log "$f"
  done

  # Rotated / dated logs
  find "$PROJ/logs" -maxdepth 1 -type f \
    \( -name '*.log.*' -o -name '*.log.gz' -o -name '*.log.old' \) \
    -mtime +"$LOG_KEEP_DAYS" -print -delete 2>/dev/null || true

  # Old state backups (keep recent)
  find "$PROJ/logs" -maxdepth 1 -type f \
    \( -name 'live_state.json.bak.*' -o -name 'paper_state.json.*.bak' -o -name '*.pre-live*.bak' \) \
    -mtime +"$LOG_KEEP_DAYS" -print 2>/dev/null | while read -r f; do
    maybe_rm "$f"
  done

  # Large misc logs in repo root
  find "$PROJ" -maxdepth 1 -type f -name '*.log' -size +50M -print 2>/dev/null | while read -r f; do
    truncate_large_log "$f" 20
  done
}

cleanup_system_light() {
  log "=== phase: system (light) ==="
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY-RUN journalctl --vacuum-size=80M"
  else
    journalctl --vacuum-size=80M 2>/dev/null || true
  fi
  if [[ -d /opt/polycopy/backups ]]; then
    find /opt/polycopy/backups -type f -mtime +7 -print -delete 2>/dev/null || true
  fi
  dnf clean all 2>/dev/null || yum clean all 2>/dev/null || true
}

main() {
  local used before after
  used=$(disk_use_pct)
  before=$(df -hP "$DISK_GUARD_PATH" | awk 'NR==2 {print $3"/"$2" ("$5")"}')
  log "disk check: ${before} threshold=${DISK_GUARD_THRESHOLD}%"

  if [[ "$FORCE" == "1" ]]; then
    log "FORCE mode — running cleanup (current ${used}%)"
  elif (( used < DISK_GUARD_THRESHOLD )); then
    log "below threshold — nothing to do"
    exit 0
  else
    log "ATTENTION: disk at ${used}% >= ${DISK_GUARD_THRESHOLD}% — starting cleanup"
  fi
  cleanup_logs
  cleanup_build_artifacts 0

  used=$(disk_use_pct)
  log "after light cleanup: $(df -hP "$DISK_GUARD_PATH" | awk 'NR==2 {print $5}') used"

  if (( used >= DISK_GUARD_THRESHOLD )); then
    cleanup_build_artifacts 1
    cleanup_system_light
  fi

  after=$(df -hP "$DISK_GUARD_PATH" | awk 'NR==2 {print $3"/"$2" ("$5")"}')
  used=$(disk_use_pct)
  log "done: ${after} (freed ~${bytes_freed_estimate} bytes reported)"
  if (( used >= DISK_GUARD_THRESHOLD )); then
    log "WARN: still at ${used}% — consider PURGE_MONGO_DATA=1 bash scripts/server_disk_cleanup.sh"
    exit 2
  fi
  exit 0
}

main "$@"
