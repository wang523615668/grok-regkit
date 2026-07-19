"""Rotate Mihomo/Clash Meta selector group to change exit IP on 403.

Controller: http://127.0.0.1:9090 (or config mihomo_api)
Secret file: /etc/mihomo/panel.secret
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

LogFn = Callable[[str], None]

_DEFAULT_API = "http://127.0.0.1:9090"
_DEFAULT_SECRET_FILE = "/etc/mihomo/panel.secret"
_DEFAULT_GROUP = "GLOBAL"

_SKIP_KEYWORDS = (
    "剩余",
    "到期",
    "重置",
    "流量",
    "套餐",
    "过期",
    "官网",
    "更新",
    "订阅",
    "DIRECT",
    "REJECT",
    "PASS",
    "COMPATIBLE",
)

_403_nodes: set[str] = set()
_403_ips: set[str] = set()


def _noop(_: str) -> None:
    return None


def load_secret(cfg: Optional[dict] = None) -> str:
    c = cfg or {}
    s = str(c.get("mihomo_secret") or os.environ.get("MIHOMO_SECRET") or "").strip()
    if s:
        return s
    path = str(c.get("mihomo_secret_file") or _DEFAULT_SECRET_FILE).strip()
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _api_base(cfg: Optional[dict] = None) -> str:
    c = cfg or {}
    return str(c.get("mihomo_api") or os.environ.get("MIHOMO_API") or _DEFAULT_API).rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    cfg: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: float = 10.0,
) -> Any:
    base = _api_base(cfg)
    secret = load_secret(cfg)
    url = f"{base}{path}"
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {"status": getattr(resp, "status", 204)}
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {"raw": raw.decode("utf-8", errors="replace")[:500]}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"mihomo {method} {path} HTTP {e.code}: {err}") from e
    except Exception as e:
        raise RuntimeError(f"mihomo {method} {path}: {e}") from e


def get_group(group: str, cfg: Optional[dict] = None) -> dict:
    return _request("GET", f"/proxies/{quote(group, safe='')}", cfg=cfg) or {}


def list_candidates(group: str, cfg: Optional[dict] = None) -> list[str]:
    d = get_group(group, cfg=cfg)
    out: list[str] = []
    for n in list(d.get("all") or []):
        name = str(n or "").strip()
        if not name:
            continue
        up = name.upper()
        if any(k.upper() in up or k in name for k in _SKIP_KEYWORDS):
            continue
        if name in ("GLOBAL", "故障转移", "自动选择", "良心云"):
            continue
        out.append(name)
    return out


def current_node(group: str, cfg: Optional[dict] = None) -> str:
    return str(get_group(group, cfg=cfg).get("now") or "").strip()


def close_connections(cfg: Optional[dict] = None) -> None:
    try:
        _request("DELETE", "/connections", cfg=cfg)
    except Exception:
        pass


def switch_node(group: str, node: str, cfg: Optional[dict] = None) -> bool:
    _request("PUT", f"/proxies/{quote(group, safe='')}", cfg=cfg, body={"name": node})
    # also try sister selector if present (rule mode often uses 良心云)
    if group != "良心云":
        try:
            g2 = get_group("良心云", cfg=cfg)
            if g2 and node in list(g2.get("all") or []):
                _request(
                    "PUT",
                    f"/proxies/{quote('良心云', safe='')}",
                    cfg=cfg,
                    body={"name": node},
                )
        except Exception:
            pass
    close_connections(cfg=cfg)
    return True


def get_exit_ip(proxy: str = "http://127.0.0.1:7890", timeout: float = 12.0) -> str:
    proxy = (proxy or "").strip()
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    urls = (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://api.ip.sb/ip",
        "https://icanhazip.com",
    )
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "grok-regkit/mihomo-rotate"})
            with opener.open(req, timeout=timeout) as resp:
                ip = resp.read().decode("utf-8", errors="replace").strip().split()[0]
                if ip and 6 <= len(ip) <= 45 and "err" not in ip.lower():
                    return ip
        except Exception:
            continue
    return ""


def rotate_exit(
    *,
    cfg: Optional[dict] = None,
    group: Optional[str] = None,
    proxy: str = "http://127.0.0.1:7890",
    avoid_nodes: Optional[set[str]] = None,
    avoid_ips: Optional[set[str]] = None,
    prefer_regions: Optional[list[str]] = None,
    log: Optional[LogFn] = None,
    require_ip_change: bool = True,
    max_tries: int = 12,
    settle_sec: float = 1.5,
) -> dict[str, Any]:
    """Switch selector until exit IP actually changes (when require_ip_change)."""
    lg = log or _noop
    c = cfg or {}
    group = (group or c.get("mihomo_group") or _DEFAULT_GROUP).strip() or _DEFAULT_GROUP
    avoid_n = set(avoid_nodes or set())
    avoid_i = set(avoid_ips or set())
    tried: set[str] = set()

    try:
        cur = current_node(group, cfg=c)
    except Exception as e:
        return {"ok": False, "error": f"read current: {e}", "group": group}

    avoid_n.add(cur)
    tried.add(cur)
    before_ip = get_exit_ip(proxy=proxy)
    if before_ip:
        avoid_i.add(before_ip)
    lg(f"[mihomo] current group={group} node={cur} exit_ip={before_ip or '?'}")

    try:
        candidates = list_candidates(group, cfg=c)
    except Exception as e:
        return {"ok": False, "error": f"list candidates: {e}", "group": group, "from": cur}

    if not candidates:
        return {"ok": False, "error": "no candidates", "group": group, "from": cur}

    prefs = list(
        prefer_regions
        or c.get("mihomo_prefer_regions")
        or ["日本", "美国", "新加坡", "香港", "台湾", "韩国"]
    )
    preferred: list[str] = []
    others: list[str] = []
    for n in candidates:
        if n in avoid_n:
            continue
        if any(p in n for p in prefs):
            preferred.append(n)
        else:
            others.append(n)
    random.shuffle(preferred)
    random.shuffle(others)
    ordered = preferred + others
    if not ordered:
        ordered = [n for n in candidates if n != cur]
        random.shuffle(ordered)

    last_err = ""
    max_tries = max(1, int(max_tries))
    for attempt, node in enumerate(ordered[:max_tries], 1):
        if node in tried:
            continue
        tried.add(node)
        try:
            switch_node(group, node, cfg=c)
        except Exception as e:
            last_err = str(e)
            lg(f"[mihomo] switch try {attempt} -> {node} fail: {e}")
            continue
        time.sleep(max(0.5, float(settle_sec)))
        # force-close again after settle
        close_connections(cfg=c)
        time.sleep(0.3)
        now = current_node(group, cfg=c)
        after_ip = get_exit_ip(proxy=proxy)
        node_changed = now != cur
        ip_changed = bool(after_ip) and after_ip not in avoid_i and after_ip != before_ip
        lg(
            f"[mihomo] try {attempt}: {cur} -> {now} "
            f"exit {before_ip or '?'} -> {after_ip or '?'} "
            f"node_changed={node_changed} ip_changed={ip_changed}"
        )
        if require_ip_change:
            if ip_changed:
                return {
                    "ok": True,
                    "group": group,
                    "from": cur,
                    "to": now,
                    "from_ip": before_ip,
                    "to_ip": after_ip,
                    "ip_changed": True,
                    "attempt": attempt,
                }
            last_err = "exit IP unchanged"
            if after_ip:
                avoid_i.add(after_ip)
            if now:
                avoid_n.add(now)
            continue
        # node-only mode
        if node_changed or ip_changed:
            return {
                "ok": True,
                "group": group,
                "from": cur,
                "to": now,
                "from_ip": before_ip,
                "to_ip": after_ip,
                "ip_changed": ip_changed,
                "attempt": attempt,
            }
        last_err = "node/ip unchanged"

    return {
        "ok": False,
        "error": last_err or "exhausted candidates",
        "group": group,
        "from": cur,
        "tried": list(tried),
        "from_ip": before_ip,
    }


def mark_403_on_current(*, cfg: Optional[dict] = None, proxy: str = "http://127.0.0.1:7890") -> dict:
    c = cfg or {}
    group = str(c.get("mihomo_group") or _DEFAULT_GROUP)
    node = ""
    ip = ""
    try:
        node = current_node(group, cfg=c)
    except Exception:
        pass
    try:
        ip = get_exit_ip(proxy=proxy)
    except Exception:
        pass
    if node:
        _403_nodes.add(node)
    if ip:
        _403_ips.add(ip)
    return {"node": node, "ip": ip, "bad_nodes": list(_403_nodes), "bad_ips": list(_403_ips)}


def rotate_after_403(
    *,
    cfg: Optional[dict] = None,
    proxy: str = "http://127.0.0.1:7890",
    log: Optional[LogFn] = None,
) -> dict[str, Any]:
    """Mark current exit as bad and rotate until a new exit IP is obtained."""
    lg = log or _noop
    c = cfg or {}
    marked = mark_403_on_current(cfg=c, proxy=proxy)
    lg(f"[mihomo] 403 on node={marked.get('node')} ip={marked.get('ip')}; rotating…")
    res = rotate_exit(
        cfg=c,
        proxy=proxy,
        avoid_nodes=set(_403_nodes),
        avoid_ips=set(_403_ips),
        log=lg,
        require_ip_change=bool(c.get("mihomo_require_ip_change", True)),
        max_tries=int(c.get("mihomo_rotate_max_tries", 12) or 12),
        settle_sec=float(c.get("mihomo_switch_settle_sec", 1.5) or 1.5),
    )
    if res.get("ok") and res.get("to"):
        # do not permanently ban the new good node
        pass
    return {**res, "marked": marked}


def reset_403_memory() -> None:
    _403_nodes.clear()
    _403_ips.clear()
