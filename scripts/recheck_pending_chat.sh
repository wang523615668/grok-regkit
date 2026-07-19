#!/usr/bin/env bash
# Recheck soft-fail Grok auths (pending 403) and promote chat-ready to CPA hotload.
set -u
GROK_REG="${GROK_REG:-/vol1/1000/openzl/grok-regkit}"
PY="${GROK_REG}/.venv/bin/python"
LOG="${LOG:-/tmp/grok_recheck_pending.log}"
ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

if [[ ! -x "$PY" ]]; then
  log "ERR missing $PY"
  exit 1
fi

log "recheck start"
cd "$GROK_REG" || exit 1
"$PY" -u scripts/recheck_pending_chat.py --limit "${LIMIT:-40}" --min-age-sec "${MIN_AGE:-180}" "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
log "recheck end rc=$rc"
exit "$rc"
