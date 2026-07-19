#!/bin/bash
# lightweight progress watcher for hybrid 100 batch
LOG=${1:-/tmp/grok_reg_100.log}
ACC_GLOB=/vol1/1000/openzl/grok-regkit/accounts_hybrid_*.txt
CPA_DIR=/vol1/1000/openzl/cpa/auths
echo "[$(date '+%H:%M:%S')] watching $LOG"
if [ -f "$LOG" ]; then
  ok=$(rg -c 'hybrid\]\[\+\] OK' "$LOG" 2>/dev/null || echo 0)
  fail=$(rg -c '当前统计: 成功' "$LOG" 2>/dev/null | tail -1)
  last=$(rg 'hybrid\]\[\+\] OK|turnstile short|VerifyEmail|no sso|exception|RESULT|当前统计' "$LOG" 2>/dev/null | tail -8)
  echo "ok_markers=$ok"
  echo "last_lines:"
  echo "$last"
fi
newest=$(ls -t $ACC_GLOB 2>/dev/null | head -1)
if [ -n "$newest" ]; then
  lines=$(wc -l < "$newest")
  echo "accounts_file=$newest lines=$lines"
fi
echo "cpa_auths=$(ls $CPA_DIR/xai-*.json 2>/dev/null | wc -l)"
echo "chromium=$(pgrep -c chromium || echo 0) mem_avail_mb=$(free -m | awk '/Mem:/{print $7}')"
