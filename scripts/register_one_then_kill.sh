#!/usr/bin/env bash
# Register N Grok accounts via grok-regkit hybrid (one slot = 1 account), then kill browsers.
# Replaces legacy /vol1/1000/openzl/grok_reg/scripts/register_one_then_kill.sh
#
# Usage:
#   register_one_then_kill.sh              # 1 account
#   register_one_then_kill.sh 3            # 3 sequential one-shots
#   register_one_then_kill.sh 5 --force    # ignore free-mem / pool gates
set -u

GROK_REG="${GROK_REG:-/vol1/1000/openzl/grok-regkit}"
CPA_AUTH="${CPA_AUTH:-/vol1/1000/openzl/cpa/auths}"
CPA_SECRETS="${CPA_SECRETS:-/vol1/1000/openzl/cpa/.secrets.env}"
# Load CPA_MGMT_KEY for remote Management upload (optional)
if [[ -f "$CPA_SECRETS" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  . "$CPA_SECRETS"
  set +a
fi
PY="${GROK_REG}/.venv/bin/python"
RUNNER="${GROK_REG}/run_hybrid_n.py"
LOG="${LOG:-/tmp/grok_register_one_kill.log}"
DISPLAY_VAL="${DISPLAY:-:99}"

MIN_AVAIL_MB="${MIN_AVAIL_MB:-600}"
TARGET_POOL="${CPA_TARGET_POOL:-100}"
MAX_SLOTS="${1:-1}"
FORCE=0
if [[ "${2:-}" == "--force" ]] || [[ "${1:-}" == "--force" ]]; then
  FORCE=1
  [[ "${1:-}" == "--force" ]] && MAX_SLOTS=1
fi
if ! [[ "${MAX_SLOTS}" =~ ^[0-9]+$ ]]; then
  MAX_SLOTS=1
fi
# User batch runs may request up to 100 accounts; hard-cap avoids accidental 1000+
MAX_SLOTS=$(( MAX_SLOTS < 1 ? 1 : (MAX_SLOTS > 100 ? 100 : MAX_SLOTS) ))

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

kill_browsers() {
  pkill -9 -f 'user-data-dir=/tmp/DrissionPage/autoPortData' 2>/dev/null || true
  pkill -9 -f 'turnstilePatch' 2>/dev/null || true
  pkill -9 -f '/usr/lib/chromium/chromium.*DrissionPage' 2>/dev/null || true
  pkill -9 -f "${GROK_REG}/.chrome-data/" 2>/dev/null || true
  pkill -9 -f "${GROK_REG}/run_hybrid" 2>/dev/null || true
  pkill -9 -f "${GROK_REG}/hybrid_register.py" 2>/dev/null || true
  sleep 1
}

ensure_xvfb() {
  export DISPLAY="$DISPLAY_VAL"
  if ! xdpyinfo -display "$DISPLAY_VAL" >/dev/null 2>&1; then
    log "start Xvfb $DISPLAY_VAL"
    Xvfb "$DISPLAY_VAL" -screen 0 1920x1080x24 -ac >/tmp/Xvfb99.log 2>&1 &
    sleep 1
  fi
}

avail_mb() {
  awk '/MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo
}

auth_count() {
  find "$CPA_AUTH" -maxdepth 1 -name 'xai-*.json' 2>/dev/null | wc -l | tr -d ' '
}

already_running() {
  pgrep -f "${GROK_REG}/run_hybrid" >/dev/null 2>&1 \
    || pgrep -f "${GROK_REG}/hybrid_register.py" >/dev/null 2>&1 \
    || pgrep -f "${GROK_REG}/grok_register_ttk.py" >/dev/null 2>&1
}

if [[ ! -x "$PY" ]]; then
  log "ERR missing venv python: $PY"
  exit 1
fi
if [[ ! -f "$RUNNER" ]]; then
  log "ERR missing runner: $RUNNER"
  exit 1
fi

if already_running && [[ "$FORCE" -ne 1 ]]; then
  log "skip: hybrid register already running"
  exit 0
fi

ensure_xvfb
kill_browsers
trap 'kill_browsers; log "trap: browsers killed on exit"' EXIT INT TERM

ok=0
fail=0
for ((i=1; i<=MAX_SLOTS; i++)); do
  n_auth=$(auth_count)
  mem=$(avail_mb)
  log "slot $i/$MAX_SLOTS auths=${n_auth} target=${TARGET_POOL} avail_mb=${mem}"

  if [[ "$FORCE" -ne 1 ]] && [[ "$n_auth" -ge "$TARGET_POOL" ]]; then
    log "done: pool full ($n_auth >= $TARGET_POOL)"
    break
  fi
  if [[ "$FORCE" -ne 1 ]] && [[ "$mem" -lt "$MIN_AVAIL_MB" ]]; then
    log "skip: low memory avail_mb=${mem} < ${MIN_AVAIL_MB}"
    break
  fi
  if already_running && [[ "$FORCE" -ne 1 ]]; then
    log "skip: another register appeared"
    break
  fi

  before=$(auth_count)
  log "launch hybrid register count=1"
  (
    cd "$GROK_REG" || exit 1
    export DISPLAY="$DISPLAY_VAL"
    export PYTHONUNBUFFERED=1
    if command -v systemd-run >/dev/null 2>&1; then
      unit="grok-regkit-slot-$$-$i"
      systemctl --user reset-failed "$unit" 2>/dev/null || true
      systemd-run --user --collect --unit="$unit" \
        --setenv=DISPLAY="$DISPLAY_VAL" \
        --working-directory="$GROK_REG" \
        "$PY" -u "$RUNNER" 1 >>"$LOG" 2>&1
        for _ in $(seq 1 108); do
        st=$(systemctl --user is-active "$unit" 2>/dev/null || echo gone)
        if [[ "$st" != "active" && "$st" != "activating" ]]; then
          break
        fi
        sleep 10
      done
      systemctl --user stop "$unit" 2>/dev/null || true
    else
      timeout 720 "$PY" -u "$RUNNER" 1 >>"$LOG" 2>&1
    fi
  )
  rc=$?
  kill_browsers
  after=$(auth_count)
  mem2=$(avail_mb)
  if [[ "$after" -gt "$before" ]]; then
    ok=$((ok+1))
    log "slot $i ok rc=$rc auths ${before}->${after} avail_mb=${mem2}"
  else
    fail=$((fail+1))
    log "slot $i fail rc=$rc auths ${before}->${after} avail_mb=${mem2}"
  fi
  sleep 3
done

kill_browsers
log "summary ok=${ok} fail=${fail} auths=$(auth_count) avail_mb=$(avail_mb)"
exit 0
