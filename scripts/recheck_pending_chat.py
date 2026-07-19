#!/usr/bin/env python3
"""Recheck soft-fail (403) Grok OIDC auths and promote chat-ready ones to CPA hotload.

Usage:
  cd /vol1/1000/openzl/grok-regkit
  .venv/bin/python scripts/recheck_pending_chat.py
  .venv/bin/python scripts/recheck_pending_chat.py --also-quarantine --limit 30
  .venv/bin/python scripts/recheck_pending_chat.py --min-age-sec 300

Env / config defaults:
  pending:  /vol1/1000/openzl/cpa/auths_pending
  hotload:  /vol1/1000/openzl/cpa/auths
  quarantine: /vol1/1000/openzl/cpa/auths_quarantine
  proxy:    http://127.0.0.1:7890
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cpa_xai.probe import probe_mini_response  # noqa: E402


def _load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify(ch: dict) -> str:
    if ch.get("ok"):
        return "chat_ok"
    st = int(ch.get("status") or 0)
    err = str(ch.get("error") or "")
    if st == 403 or "permission-denied" in err:
        return "403"
    if st == 429 or "exhausted" in err or "spending" in err.lower():
        return "429"
    if st == 401:
        return "401"
    if st == 0:
        return "network"
    return f"http_{st}"


def main() -> int:
    cfg = _load_config()
    ap = argparse.ArgumentParser(description="Recheck pending/quarantine Grok auths for chat 200")
    ap.add_argument(
        "--pending-dir",
        default=cfg.get("cpa_pending_dir") or "/vol1/1000/openzl/cpa/auths_pending",
    )
    ap.add_argument(
        "--hotload-dir",
        default=cfg.get("cpa_hotload_dir") or "/vol1/1000/openzl/cpa/auths",
    )
    ap.add_argument(
        "--quarantine-dir",
        default=cfg.get("cpa_quarantine_dir") or "/vol1/1000/openzl/cpa/auths_quarantine",
    )
    ap.add_argument(
        "--proxy",
        default=cfg.get("cpa_proxy") or cfg.get("proxy") or "http://127.0.0.1:7890",
    )
    ap.add_argument("--also-quarantine", action="store_true", help="Also scan quarantine for recovered 403s")
    ap.add_argument("--limit", type=int, default=50, help="Max files to probe this run")
    ap.add_argument(
        "--min-age-sec",
        type=int,
        default=int(cfg.get("cpa_pending_min_age_sec", 180) or 180),
        help="Skip files younger than this (seconds)",
    )
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=float(cfg.get("cpa_pending_max_age_hours", 48) or 48),
        help="Older soft-fails move pending→quarantine as dead",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pending = Path(args.pending_dir).expanduser()
    hot = Path(args.hotload_dir).expanduser()
    quar = Path(args.quarantine_dir).expanduser()
    proxy = (args.proxy or "").strip() or None
    now = time.time()

    files: list[tuple[str, Path]] = []
    if pending.is_dir():
        for p in sorted(pending.glob("xai-*.json"), key=lambda x: x.stat().st_mtime):
            files.append(("pending", p))
    if args.also_quarantine and quar.is_dir():
        # Newest first: more likely to recover
        for p in sorted(quar.glob("xai-*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            files.append(("quarantine", p))

    # de-dupe by name prefer pending
    seen = set()
    uniq: list[tuple[str, Path]] = []
    for tag, p in files:
        if p.name in seen:
            continue
        seen.add(p.name)
        uniq.append((tag, p))
    files = uniq[: max(1, args.limit)]

    print(
        f"[recheck] pending={pending} hot={hot} quar={quar} "
        f"files={len(files)} min_age={args.min_age_sec}s proxy={proxy or '(none)'}",
        flush=True,
    )

    stats = {
        "scanned": 0,
        "skipped_young": 0,
        "promoted": 0,
        "still_403": 0,
        "still_429": 0,
        "dead_401": 0,
        "network": 0,
        "moved_old_to_quar": 0,
        "rotated": 0,
        "other": 0,
    }

    if not args.dry_run:
        hot.mkdir(parents=True, exist_ok=True)
        pending.mkdir(parents=True, exist_ok=True)
        quar.mkdir(parents=True, exist_ok=True)

    for tag, path in files:
        age = now - path.stat().st_mtime
        if age < args.min_age_sec:
            stats["skipped_young"] += 1
            print(f"[skip-young] {path.name} age={age:.0f}s", flush=True)
            continue
        stats["scanned"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[parse-err] {path.name} {e}", flush=True)
            stats["other"] += 1
            continue
        token = (data.get("access_token") or "").strip()
        email = data.get("email") or path.stem.removeprefix("xai-")
        if not token:
            print(f"[no-token] {path.name}", flush=True)
            stats["other"] += 1
            continue

        ch = probe_mini_response(token, proxy=proxy, timeout=25)
        bucket = _classify(ch)
        # On 403: rotate exit IP once and re-probe (same account, new IP).
        if bucket == "403" and bool(cfg.get("mihomo_rotate_on_403", True)) and not args.dry_run:
            try:
                from mihomo_rotate import rotate_after_403  # noqa: WPS433

                rot = rotate_after_403(
                    cfg=cfg,
                    proxy=str(proxy or "http://127.0.0.1:7890"),
                    log=lambda m: print(f"  {m}", flush=True),
                )
                stats["rotated"] = int(stats.get("rotated", 0)) + (1 if rot.get("ok") else 0)
                if rot.get("ok"):
                    print(
                        f"  rotated exit {rot.get('from_ip')} -> {rot.get('to_ip')}; re-probe",
                        flush=True,
                    )
                    time.sleep(float(cfg.get("mihomo_rotate_settle_sec", 5) or 5))
                    ch = probe_mini_response(token, proxy=proxy, timeout=25)
                    bucket = _classify(ch)
            except Exception as rot_exc:  # noqa: BLE001
                print(f"  rotate error: {rot_exc}", flush=True)

        print(
            f"[{tag}] {path.name} bucket={bucket} status={ch.get('status')} "
            f"age_h={age/3600:.1f} email={email}",
            flush=True,
        )

        if bucket == "chat_ok":
            stats["promoted"] += 1
            dst = hot / path.name
            if args.dry_run:
                print(f"  would promote -> {dst}", flush=True)
                continue
            shutil.copy2(path, dst)
            os.chmod(dst, 0o600)
            # remove from pending/quarantine after promote
            try:
                path.unlink()
            except OSError:
                pass
            meta = path.with_suffix("").with_name(path.stem + ".meta.json")
            # path.stem is xai-email@domain — meta named xai-email@domain.meta.json
            meta2 = path.parent / (path.stem + ".meta.json")
            for m in (meta, meta2):
                if m.is_file():
                    try:
                        m.unlink()
                    except OSError:
                        pass
            with open(hot / "promoted_from_pending.txt", "a", encoding="utf-8") as f:
                f.write(f"{email}----from={tag}----{int(time.time())}\n")
            print(f"  promoted -> {dst}", flush=True)
            continue

        if bucket == "401":
            stats["dead_401"] += 1
            if tag == "pending" and not args.dry_run:
                dst = quar / path.name
                shutil.move(str(path), str(dst))
                os.chmod(dst, 0o600)
                meta2 = path.parent / (path.stem + ".meta.json")
                if meta2.is_file():
                    try:
                        meta2.unlink()
                    except OSError:
                        pass
                print(f"  dead 401 -> quarantine {dst.name}", flush=True)
            continue

        if bucket == "403":
            stats["still_403"] += 1
        elif bucket == "429":
            stats["still_429"] += 1
        elif bucket == "network":
            stats["network"] += 1
        else:
            stats["other"] += 1

        # Too old soft-fail: park in quarantine for inspection, stop rechecking forever
        max_age = float(args.max_age_hours) * 3600.0
        if tag == "pending" and age >= max_age and bucket in ("403", "429", "other"):
            stats["moved_old_to_quar"] += 1
            if not args.dry_run:
                dst = quar / path.name
                shutil.move(str(path), str(dst))
                os.chmod(dst, 0o600)
                meta2 = path.parent / (path.stem + ".meta.json")
                if meta2.is_file():
                    try:
                        meta2.unlink()
                    except OSError:
                        pass
                print(f"  old soft-fail -> quarantine {dst.name}", flush=True)

    print("[SUMMARY]", json.dumps(stats, ensure_ascii=False), flush=True)
    print(
        f"[POOL] hot={len(list(hot.glob('xai-*.json')))} "
        f"pending={len(list(pending.glob('xai-*.json'))) if pending.is_dir() else 0} "
        f"quar={len(list(quar.glob('xai-*.json'))) if quar.is_dir() else 0}",
        flush=True,
    )
    return 0 if stats["promoted"] or stats["scanned"] >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
