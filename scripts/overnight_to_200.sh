#!/usr/bin/env bash
# Overnight Grok hybrid register until CPA hot pool reaches TARGET.
# - rotates Mihomo exit often (US/SG/JP)
# - sequential one-shot slots via run_hybrid_n.py
# - salvages mint-success-but-not-hot files periodically
# - cleans browsers between slots
#
# Usage:
#   overnight_to_200.sh
#   overnight_to_200.sh 200
set -u

GROK_REG="${GROK_REG:-/vol1/1000/openzl/grok-regkit}"
CPA_AUTH="${CPA_AUTH:-/vol1/1000/openzl/cpa/auths}"
PY="${GROK_REG}/.venv/bin/python"
RUNNER="${GROK_REG}/run_hybrid_n.py"
LOG="${LOG:-/tmp/grok_overnight_to_200.log}"
STATUS="${STATUS:-/tmp/grok_overnight_to_200.status}"
DISPLAY_VAL="${DISPLAY:-:99}"

TARGET="${1:-${CPA_TARGET_POOL:-200}}"
MIN_AVAIL_MB="${MIN_AVAIL_MB:-500}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-50400}"   # 14h safety
SLOT_TIMEOUT_SEC="${SLOT_TIMEOUT_SEC:-900}"
ROTATE_EVERY="${ROTATE_EVERY:-2}"
SALVAGE_EVERY="${SALVAGE_EVERY:-3}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-4}"
MAX_CONSEC_FAIL="${MAX_CONSEC_FAIL:-8}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

avail_mb() {
  awk '/MemAvailable:/ {printf "%d", $2/1024}' /proc/meminfo
}

auth_count() {
  find "$CPA_AUTH" -maxdepth 1 -name 'xai-*.json' 2>/dev/null | wc -l | tr -d ' '
}

write_status() {
  local n_auth mem ip node
  n_auth=$(auth_count)
  mem=$(avail_mb)
  ip="?"
  node="?"
  if [[ -x "$PY" ]]; then
    # single-line to avoid node names with spaces breaking set --
    IFS='|' read -r ip node < <("$PY" -c 'from mihomo_rotate import get_exit_ip,current_node; print((get_exit_ip() or "?")+"|"+(current_node("GLOBAL") or "?"))' 2>/dev/null || echo '?|?')
    ip="${ip:-?}"
    node="${node:-?}"
  fi
  cat >"$STATUS" <<EOF
updated=$(ts)
hot=$n_auth
target=$TARGET
ok=$OK
fail=$FAIL
consec_fail=$CONSEC_FAIL
slot=$SLOT
avail_mb=$mem
exit_ip=$ip
node=$node
runtime_sec=$(( $(date +%s) - START_TS ))
pid=$$
log=$LOG
EOF
}

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

rotate_ip() {
  local reason="${1:-periodic}"
  log "rotate exit ($reason)"
  (
    cd "$GROK_REG" || exit 0
    "$PY" - <<'PY' >>"$LOG" 2>&1 || true
from mihomo_rotate import rotate_exit, get_exit_ip, current_node

def lg(s):
    print(s, flush=True)

# Prefer US first (often fixes curl_cffi TLS invalid library on JP), then SG/JP/HK
r = rotate_exit(
    prefer_regions=["美国", "新加坡", "日本", "香港", "韩国"],
    require_ip_change=True,
    max_tries=18,
    log=lg,
)
print("ROTATE_RESULT", r, flush=True)
print("NOW", get_exit_ip(), current_node("GLOBAL"), flush=True)
PY
  )
}

salvage_hot() {
  log "salvage: probe local cpa_auths not yet in hot"
  (
    cd "$GROK_REG" || exit 0
    "$PY" - <<'PY' >>"$LOG" 2>&1 || true
import json
import shutil
from pathlib import Path

from cpa_xai.probe import probe_mini_response
from mihomo_rotate import current_node, get_exit_ip, rotate_exit

proxy = "http://127.0.0.1:7890"
hot = Path("/vol1/1000/openzl/cpa/auths")
local = Path("/vol1/1000/openzl/grok-regkit/cpa_auths")
print("salvage exit", get_exit_ip(), current_node("GLOBAL"), flush=True)
added = 0
checked = 0
for src in sorted(local.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
    dst = hot / src.name
    if dst.exists():
        continue
    checked += 1
    try:
        d = json.loads(src.read_text())
        tok = d.get("access_token") or ""
        if not tok:
            continue
        r = probe_mini_response(tok, proxy=proxy, timeout=35)
        ok = bool(r.get("ok")) and int(r.get("status") or 0) == 200
        print(f"  {src.name} -> {r.get('status')} {r.get('endpoint')} ok={ok}", flush=True)
        if (not ok) and int(r.get("status") or 0) == 403:
            rotate_exit(
                prefer_regions=["日本", "美国", "新加坡"],
                require_ip_change=True,
                max_tries=6,
            )
            r = probe_mini_response(tok, proxy=proxy, timeout=35)
            ok = bool(r.get("ok")) and int(r.get("status") or 0) == 200
            print(f"  retry {src.name} -> {r.get('status')} ok={ok}", flush=True)
        if ok:
            shutil.copy2(src, dst)
            added += 1
            print(f"  HOTLOADED {dst}", flush=True)
    except Exception as e:
        print(f"  err {src.name}: {e}", flush=True)
print(
    f"salvage done checked={checked} added={added} hot={len(list(hot.glob('xai-*.json')))}",
    flush=True,
)
PY
  )
}

run_one_slot() {
  local before after unit waited st
  before=$(auth_count)
  unit="grok-overnight-$$-$(date +%s)"
  log "launch hybrid count=1 unit=$unit hot_before=$before"
  (
    cd "$GROK_REG" || exit 1
    export DISPLAY="$DISPLAY_VAL"
    export PYTHONUNBUFFERED=1
    # keep shell free of env proxies; hybrid uses config proxy=127.0.0.1:7890
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
    if command -v systemd-run >/dev/null 2>&1; then
      systemctl --user reset-failed "$unit" 2>/dev/null || true
      systemd-run --user --collect --unit="$unit" \
        --setenv=DISPLAY="$DISPLAY_VAL" \
        --setenv=PYTHONUNBUFFERED=1 \
        --working-directory="$GROK_REG" \
        "$PY" -u "$RUNNER" 1 >>"$LOG" 2>&1
      waited=0
      while (( waited < SLOT_TIMEOUT_SEC )); do
        st=$(systemctl --user is-active "$unit" 2>/dev/null || echo gone)
        if [[ "$st" != "active" && "$st" != "activating" ]]; then
          break
        fi
        sleep 10
        waited=$((waited + 10))
      done
      systemctl --user stop "$unit" 2>/dev/null || true
      # allow bg postprocess a few more seconds before browser kill
      sleep 12
    else
      timeout "$SLOT_TIMEOUT_SEC" "$PY" -u "$RUNNER" 1 >>"$LOG" 2>&1 || true
      sleep 12
    fi
  )
  kill_browsers
  after=$(auth_count)
  if [[ "$after" -gt "$before" ]]; then
    OK=$((OK + 1))
    CONSEC_FAIL=0
    log "slot ok hot ${before}->${after}"
    return 0
  fi
  FAIL=$((FAIL + 1))
  CONSEC_FAIL=$((CONSEC_FAIL + 1))
  log "slot fail hot ${before}->${after} consec_fail=$CONSEC_FAIL"
  return 1
}

if [[ ! -x "$PY" ]]; then
  echo "ERR missing venv python: $PY" | tee -a "$LOG"
  exit 1
fi
if [[ ! -f "$RUNNER" ]]; then
  echo "ERR missing runner: $RUNNER" | tee -a "$LOG"
  exit 1
fi
if ! [[ "$TARGET" =~ ^[0-9]+$ ]]; then
  TARGET=200
fi
if (( TARGET < 1 )); then
  TARGET=200
fi
if (( TARGET > 500 )); then
  TARGET=500
fi

START_TS=$(date +%s)
OK=0
FAIL=0
CONSEC_FAIL=0
SLOT=0

LOCK=/tmp/grok_overnight_to_200.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  log "skip: another overnight runner holds $LOCK"
  exit 0
fi

: >"$LOG"
ensure_xvfb
kill_browsers
trap 'kill_browsers; write_status; log "trap exit hot=$(auth_count) ok=$OK fail=$FAIL"; flock -u 9' EXIT INT TERM

log "=== overnight start target=$TARGET hot=$(auth_count) avail_mb=$(avail_mb) ==="
rotate_ip "startup"
write_status

while true; do
  now=$(date +%s)
  if (( now - START_TS > MAX_RUNTIME_SEC )); then
    log "stop: max runtime ${MAX_RUNTIME_SEC}s reached"
    break
  fi

  n=$(auth_count)
  mem=$(avail_mb)
  if (( n >= TARGET )); then
    log "done: hot $n >= target $TARGET"
    break
  fi
  if (( mem < MIN_AVAIL_MB )); then
    log "pause: low memory avail_mb=$mem < $MIN_AVAIL_MB; sleep 60"
    kill_browsers
    sleep 60
    continue
  fi
  if (( CONSEC_FAIL >= MAX_CONSEC_FAIL )); then
    log "many consecutive fails ($CONSEC_FAIL); hard rotate + salvage + cool 90s"
    kill_browsers
    rotate_ip "consec_fail"
    salvage_hot
    CONSEC_FAIL=0
    sleep 90
  fi

  SLOT=$((SLOT + 1))
  log "=== slot $SLOT hot=$n/$TARGET mem=$mem ok=$OK fail=$FAIL ==="

  if (( SLOT % ROTATE_EVERY == 1 )); then
    rotate_ip "every_${ROTATE_EVERY}"
  fi

  run_one_slot || true

  if (( SLOT % SALVAGE_EVERY == 0 )); then
    salvage_hot
  fi

  write_status
  sleep "$SLEEP_BETWEEN"
done

salvage_hot
kill_browsers
write_status
log "=== overnight end hot=$(auth_count)/$TARGET ok=$OK fail=$FAIL runtime=$(( $(date +%s) - START_TS ))s ==="
echo "FINAL hot=$(auth_count) ok=$OK fail=$FAIL" | tee -a "$LOG"
exit 0
