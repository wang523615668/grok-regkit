#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FastAPI control plane for grok-regkit."""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

# Project root = parent of web/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

import grok_register_ttk as engine  # noqa: E402

ACCESS_PASSWORD = (os.getenv("GROK_REGISTER_ACCESS_PASSWORD") or "").strip()
HOST = (os.getenv("GROK_REGISTER_HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("GROK_REGISTER_PORT") or "8092")

# Optional grok2api / token-pool integration (override via env)
G2A_INTERNAL_BASE = (
    os.getenv("GROK2API_INTERNAL_URL") or "http://127.0.0.1:8010"
).strip().rstrip("/")
G2A_PUBLIC_URL = (
    os.getenv("GROK2API_PUBLIC_URL") or "http://127.0.0.1:8010"
).strip().rstrip("/")

WEB_DIR = Path(__file__).resolve().parent
INDEX_HTML = WEB_DIR / "index.html"

SECRET_FIELDS = {
    "duckmail_api_key",
    "cloudflare_api_key",
    "yyds_api_key",
    "yyds_jwt",
    "grok2api_remote_app_key",
    "proxy",
    "proxy_pass",
    "mailnest_api_key",
    "cloudmail_password",
    "cpa_management_key",
}

# In-memory sessions: token -> expiry ts
_sessions: Dict[str, float] = {}
_SESSION_TTL = 86400 * 7

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None
_controller: Optional[engine.CliStopController] = None
_log_buffer: Deque[str] = collections.deque(maxlen=2000)
_log_seq = 0
_log_cond = threading.Condition()
_job_state: Dict[str, Any] = {
    "running": False,
    "success": 0,
    "fail": 0,
    "target": 0,
    "last_accounts_file": "",
    "started_at": None,
    "finished_at": None,
    "error": "",
}


app = FastAPI(title="Grok Register", version="1.0.0")


def _beijing_hms() -> str:
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt

        return _dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S")
    except Exception:
        # 无 zoneinfo 时退回 UTC+8
        return time.strftime("%H:%M:%S", time.gmtime(time.time() + 8 * 3600))


def _append_log(message: str) -> None:
    global _log_seq
    ts = _beijing_hms()
    line = f"[{ts}] {message}"
    with _log_cond:
        _log_buffer.append(line)
        _log_seq += 1
        _log_cond.notify_all()


def _mask_value(key: str, value: Any) -> Any:
    if key not in SECRET_FIELDS:
        return value
    s = "" if value is None else str(value)
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


def _public_config() -> Dict[str, Any]:
    engine.load_config()
    cfg = dict(engine.config)
    masked = {k: _mask_value(k, v) for k, v in cfg.items()}
    # Keep unmasked non-secrets fully; secret fields show mask + has_* flags
    for key in SECRET_FIELDS:
        raw = cfg.get(key, "")
        masked[f"has_{key}"] = bool(str(raw or "").strip())
    return masked


def _require_auth(x_access_key: Optional[str]) -> None:
    if not ACCESS_PASSWORD:
        return
    key = (x_access_key or "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="access key required")
    # Accept raw password or issued session token
    if key == ACCESS_PASSWORD:
        return
    exp = _sessions.get(key)
    if exp and exp > time.time():
        return
    if exp:
        _sessions.pop(key, None)
    raise HTTPException(status_code=403, detail="invalid access key")


def _issue_token(password: str) -> str:
    raw = f"{password}:{secrets.token_hex(16)}:{time.time()}"
    token = hashlib.sha256(raw.encode()).hexdigest()
    _sessions[token] = time.time() + _SESSION_TTL
    return token


class AuthBody(BaseModel):
    password: str = ""


class StartBody(BaseModel):
    # 单次任务上限（2G 机器仍建议分批；允许 1000 方便面板一次提交）
    count: int = Field(default=1, ge=1, le=1000)


class ConfigBody(BaseModel):
    duckmail_api_key: Optional[str] = None
    cloudflare_api_base: Optional[str] = None
    cloudflare_api_key: Optional[str] = None
    cloudflare_auth_mode: Optional[str] = None
    cloudflare_path_domains: Optional[str] = None
    cloudflare_path_accounts: Optional[str] = None
    cloudflare_path_token: Optional[str] = None
    cloudflare_path_messages: Optional[str] = None
    proxy: Optional[str] = None
    proxy_mode: Optional[str] = None
    proxy_airport_url: Optional[str] = None
    proxy_api_url: Optional[str] = None
    proxy_api_num: Optional[int] = None
    proxy_api_format: Optional[str] = None
    proxy_api_type: Optional[str] = None
    proxy_quality_api: Optional[str] = None
    proxy_host_lookup_api: Optional[str] = None
    proxy_quality_check: Optional[bool] = None
    proxy_check_entry_host: Optional[bool] = None
    proxy_check_exit_ippure: Optional[bool] = None
    proxy_max_fraud_score: Optional[int] = None
    proxy_require_residential: Optional[bool] = None
    proxy_require_country_match: Optional[bool] = None
    proxy_reject_datacenter_org: Optional[bool] = None
    proxy_reject_hosting_flag: Optional[bool] = None
    proxy_quality_max_tries: Optional[int] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[str] = None
    proxy_user: Optional[str] = None
    proxy_pass: Optional[str] = None
    proxy_country: Optional[str] = None
    proxy_delimiter: Optional[str] = None
    proxy_duration: Optional[str] = None
    proxy_user_template: Optional[str] = None
    proxy_session: Optional[str] = None
    enable_nsfw: Optional[bool] = None
    nsfw_async: Optional[bool] = None
    post_success_async: Optional[bool] = None
    register_count: Optional[int] = None
    register_mode: Optional[str] = None
    user_agent: Optional[str] = None
    grok2api_auto_add_local: Optional[bool] = None
    grok2api_local_token_file: Optional[str] = None
    grok2api_pool_name: Optional[str] = None
    grok2api_auto_add_remote: Optional[bool] = None
    grok2api_remote_base: Optional[str] = None
    grok2api_remote_app_key: Optional[str] = None
    defaultDomains: Optional[str] = None
    email_provider: Optional[str] = None
    yyds_api_key: Optional[str] = None
    yyds_jwt: Optional[str] = None
    yyds_default_domain: Optional[str] = None
    duckmail_api_base: Optional[str] = None
    mailnest_api_key: Optional[str] = None
    mailnest_project_code: Optional[str] = None
    cloudmail_url: Optional[str] = None
    cloudmail_admin_email: Optional[str] = None
    cloudmail_password: Optional[str] = None
    cpa_export_enabled: Optional[bool] = None
    cpa_auth_dir: Optional[str] = None
    cpa_copy_to_hotload: Optional[bool] = None
    cpa_hotload_dir: Optional[str] = None
    cpa_remote_url: Optional[str] = None
    cpa_management_key: Optional[str] = None
    cpa_remote_timeout_sec: Optional[float] = None
    cpa_remote_upload_on_chat_fail: Optional[bool] = None
    cpa_probe_chat: Optional[bool] = None
    cpa_chat_required_for_hotload: Optional[bool] = None


@app.get("/api/connectivity")
async def api_connectivity(x_access_key: Optional[str] = Header(None)):
    """代理 / 邮箱 / CPA 本地+远程 Management 连通性检查。"""
    _require_auth(x_access_key)
    engine.load_config()
    from connectivity import format_check_results, run_connectivity_checks

    results = run_connectivity_checks(engine.config, engine.http_get, engine.http_post)
    return {
        "ok": all(ok for _n, ok, _d in results),
        "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "text": format_check_results(results),
    }


def _run_job(count: int) -> None:
    global _controller
    controller = engine.CliStopController()
    with _job_lock:
        _controller = controller
        _job_state["running"] = True
        _job_state["success"] = 0
        _job_state["fail"] = 0
        _job_state["target"] = count
        _job_state["error"] = ""
        _job_state["started_at"] = time.time()
        _job_state["finished_at"] = None

    def log_cb(msg: str) -> None:
        _append_log(str(msg))

    try:
        engine.load_config()
        result = engine.run_registration_job(
            count, log_callback=log_cb, controller=controller
        )
        with _job_lock:
            _job_state["success"] = int(result.get("success") or 0)
            _job_state["fail"] = int(result.get("fail") or 0)
            _job_state["last_accounts_file"] = str(result.get("accounts_file") or "")
    except Exception as exc:
        _append_log(f"[!] job error: {exc}")
        with _job_lock:
            _job_state["error"] = str(exc)
    finally:
        with _job_lock:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _controller = None
        _append_log("[*] web job thread finished")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.head("/", include_in_schema=False)
async def root_head():
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"ok": True, "service": "grok-register"}


@app.get("/monitor/status")
async def monitor_status():
    with _job_lock:
        running = bool(_job_state["running"])
    return {
        "ok": True,
        "service": "grok-register",
        "running_job": running,
    }


def _probe_g2a(app_key: str = "") -> Dict[str, Any]:
    """Check local/public grok2api and optional account count.

    Online = process reachable. Prefer /health (no auth). Do NOT use /v1/models
    alone: it returns 401 without a chat API key and was falsely shown as 离线.
    """
    import urllib.error
    import urllib.request

    result: Dict[str, Any] = {
        "ok": False,
        "internal_base": G2A_INTERNAL_BASE,
        "public_url": G2A_PUBLIC_URL,
        "admin_url": f"{G2A_PUBLIC_URL}/admin/login",
        "account_count": None,
        "error": "",
    }

    def _http_status(url: str, timeout: float = 4.0) -> int:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "grok-register-integration"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            # Any HTTP response means the process is up (401/403 still "online")
            return int(exc.code)
        except Exception:
            raise

    # 1) Liveness — /health is unauthenticated on grok2api
    probe_errors: list[str] = []
    for path in ("/health", "/", "/v1/models"):
        url = f"{G2A_INTERNAL_BASE}{path}"
        try:
            status = _http_status(url, timeout=4.0)
            # reachable if we got any HTTP status (incl. 401/403/404/307)
            if 100 <= status < 600:
                result["ok"] = True
                break
        except Exception as exc:
            probe_errors.append(f"{path}: {exc}")
    if not result["ok"]:
        result["error"] = "; ".join(probe_errors) or "unreachable"
        return result

    # 2) Optional account count via admin API (needs app_key / 管理密码)
    key = (app_key or "").strip()
    if not key:
        engine.load_config()
        key = str(engine.config.get("grok2api_remote_app_key") or "").strip()
    if key and "*" not in key:
        try:
            url = f"{G2A_INTERNAL_BASE}/admin/api/tokens?app_key={urllib.parse.quote(key)}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=6) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                tokens = payload.get("tokens") if isinstance(payload, dict) else None
                if isinstance(tokens, list):
                    result["account_count"] = len(tokens)
                elif isinstance(tokens, dict):
                    # older full-pool shape: { "ssoBasic": [...] }
                    n = 0
                    for v in tokens.values():
                        if isinstance(v, list):
                            n += len(v)
                    result["account_count"] = n
        except Exception as exc:
            result["error"] = f"online; tokens: {exc}"
    return result


@app.get("/api/integration")
async def api_integration(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    engine.load_config()
    cfg = engine.config
    remote_base = str(cfg.get("grok2api_remote_base") or "").strip()
    remote_key = str(cfg.get("grok2api_remote_app_key") or "").strip()
    g2a = _probe_g2a(remote_key)
    linked = bool(cfg.get("grok2api_auto_add_remote")) and bool(remote_base) and bool(remote_key)
    return {
        "ok": True,
        "g2a": g2a,
        "linked": linked,
        "config": {
            "auto_add_remote": bool(cfg.get("grok2api_auto_add_remote")),
            "remote_base": remote_base or g2a["internal_base"],
            "pool_name": cfg.get("grok2api_pool_name") or "ssoBasic",
            "has_app_key": bool(remote_key),
        },
        "defaults": {
            "remote_base": G2A_INTERNAL_BASE,
            "public_url": G2A_PUBLIC_URL,
            "admin_url": f"{G2A_PUBLIC_URL}/admin/login",
            "pool_name": "ssoBasic",
        },
    }


class LinkG2ABody(BaseModel):
    app_key: str = ""
    enable: bool = True
    remote_base: str = ""
    pool_name: str = "ssoBasic"


@app.post("/api/integration/link")
async def api_integration_link(body: LinkG2ABody, x_access_key: Optional[str] = Header(None)):
    """One-click wire register → local grok2api token pool."""
    _require_auth(x_access_key)
    engine.load_config()
    base = (body.remote_base or G2A_INTERNAL_BASE).strip().rstrip("/")
    key = (body.app_key or "").strip()
    if not key or "*" in key:
        # keep existing key if masked / empty and already set
        existing = str(engine.config.get("grok2api_remote_app_key") or "").strip()
        if existing:
            key = existing
        else:
            key = "grok2api"  # default admin password of fresh install
    engine.config["grok2api_remote_base"] = base
    engine.config["grok2api_remote_app_key"] = key
    engine.config["grok2api_pool_name"] = (body.pool_name or "ssoBasic").strip() or "ssoBasic"
    engine.config["grok2api_auto_add_remote"] = bool(body.enable)
    engine.config["grok2api_auto_add_local"] = False
    engine.save_config()
    # probe after link
    g2a = _probe_g2a(key)
    return {
        "ok": True,
        "linked": bool(body.enable),
        "g2a": g2a,
        "config": _public_config(),
    }


@app.post("/api/auth")
async def api_auth(body: AuthBody):
    if not ACCESS_PASSWORD:
        return {"ok": True, "needs_auth": False, "token": ""}
    if (body.password or "").strip() != ACCESS_PASSWORD:
        return JSONResponse({"ok": False, "detail": "invalid password"}, status_code=403)
    token = _issue_token(body.password.strip())
    return {"ok": True, "needs_auth": True, "token": token}


@app.get("/api/config")
async def api_get_config(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    return {"ok": True, "config": _public_config(), "needs_auth": bool(ACCESS_PASSWORD)}


@app.put("/api/config")
async def api_put_config(body: ConfigBody, x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    engine.load_config()
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key in SECRET_FIELDS and isinstance(value, str):
            stripped = value.strip()
            # Empty string clears the secret.
            if stripped == "":
                engine.config[key] = ""
                continue
            # Masked placeholder from GET — keep previous value.
            if "*" in stripped:
                continue
        engine.config[key] = value
    # Server hard-forces proxy quality (env GROK_FORCE_PROXY_QUALITY default on).
    # Cliproxy white: judge EXIT residential via IPPure; entry gateway may be Zenlayer.
    force_q = os.environ.get("GROK_FORCE_PROXY_QUALITY", "1").strip().lower()
    if force_q in ("1", "true", "yes", "on"):
        engine.config["proxy_quality_check"] = True
        engine.config["proxy_check_exit_ippure"] = True
        engine.config["proxy_reject_datacenter_org"] = True
        # Do not force entry hard-reject — white API entry is shared DC by design.
        engine.config["proxy_entry_hard_reject"] = False
    engine.save_config()
    return {"ok": True, "config": _public_config()}


@app.get("/api/status")
async def api_status(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    with _job_lock:
        state = dict(_job_state)
    return {"ok": True, **state}


@app.post("/api/proxy/test")
async def api_proxy_test(x_access_key: Optional[str] = Header(None)):
    """Test current proxy mode (airport / Cliproxy / custom) and probe exit via IPPure."""
    _require_auth(x_access_key)
    engine.load_config()
    logs: List[str] = []

    def _log(msg: str) -> None:
        logs.append(str(msg))

    try:
        mode = str(engine.config.get("proxy_mode") or "").strip().lower()
        if mode in ("cliproxy_white", "cliproxy", "white_api", "api"):
            proxy = engine.fetch_cliproxy_white_proxy(engine.config, log_callback=_log)
        else:
            proxy = engine.resolve_runtime_proxy(
                engine.config, log_callback=_log, fetch_live=True
            )
            if not proxy:
                raise RuntimeError("当前模式无可用代理（直连或未配置）")
            _log(f"[+] 当前代理: {proxy}")
        quality = None
        try:
            quality = engine.probe_proxy_with_ippure(
                proxy,
                quality_api=str(
                    engine.config.get("proxy_quality_api") or "https://my.ippure.com/v1/info"
                ),
            )
            if quality:
                _log(
                    f"[*] 出口 IPPure: ip={quality.get('ip')} "
                    f"country={quality.get('countryCode')} "
                    f"fraud={quality.get('fraudScore')} "
                    f"residential={quality.get('isResidential')} "
                    f"org={quality.get('asOrganization') or quality.get('org') or ''}"
                )
        except Exception as qe:
            logs.append(f"[!] 复检 IPPure 失败: {qe}")
        return {"ok": True, "proxy": proxy, "quality": quality, "logs": logs}
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "detail": str(exc), "logs": logs},
            status_code=400,
        )


@app.post("/api/start")
async def api_start(body: StartBody, x_access_key: Optional[str] = Header(None)):
    global _job_thread
    _require_auth(x_access_key)
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="job already running")
        # clear log for new run but keep last few
        _append_log(f"[*] starting registration count={body.count}")
        t = threading.Thread(target=_run_job, args=(body.count,), daemon=True)
        _job_thread = t
        t.start()
    return {"ok": True, "started": True, "count": body.count}


@app.post("/api/stop")
async def api_stop(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    with _job_lock:
        ctrl = _controller
        running = _job_state["running"]
    if not running or ctrl is None:
        return {"ok": True, "stopped": False, "detail": "no running job"}
    ctrl.stop()
    _append_log("[!] stop requested from web")
    return {"ok": True, "stopped": True}


@app.get("/api/logs")
async def api_logs(
    request: Request,
    x_access_key: Optional[str] = Header(None),
    after: int = Query(0, ge=0),
):
    _require_auth(x_access_key)

    async def event_stream():
        last = after
        while True:
            if await request.is_disconnected():
                break
            with _log_cond:
                # snapshot
                buf = list(_log_buffer)
                seq = _log_seq
            # emit new lines relative to after
            start_idx = max(0, len(buf) - (seq - last)) if seq >= last else 0
            if last == 0:
                start_idx = 0
            else:
                # lines with global indices (seq - len + i)
                start_idx = max(0, len(buf) - (seq - last))
            new_lines = buf[start_idx:]
            for line in new_lines:
                yield f"data: {line}\n\n"
            last = seq
            # wait a bit for more
            await asyncio.sleep(0.5)
            with _log_cond:
                if _log_seq == last and not _job_state["running"]:
                    # keep connection for a short idle then continue
                    pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/logs/snapshot")
async def api_logs_snapshot(
    x_access_key: Optional[str] = Header(None),
    limit: int = Query(200, ge=1, le=2000),
):
    _require_auth(x_access_key)
    with _log_cond:
        lines = list(_log_buffer)[-limit:]
        seq = _log_seq
    return {"ok": True, "seq": seq, "lines": lines}


@app.get("/api/accounts")
async def api_accounts_list(x_access_key: Optional[str] = Header(None)):
    _require_auth(x_access_key)
    files = sorted(ROOT.glob("accounts_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
        }
        for f in files[:50]
    ]
    return {"ok": True, "files": items}


@app.get("/api/accounts/download")
async def api_accounts_download(
    x_access_key: Optional[str] = Header(None),
    name: Optional[str] = Query(None),
):
    _require_auth(x_access_key)
    if name:
        # prevent path traversal
        safe = Path(name).name
        path = ROOT / safe
        if not safe.startswith("accounts_") or not safe.endswith(".txt") or not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
    else:
        files = sorted(ROOT.glob("accounts_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise HTTPException(status_code=404, detail="no accounts file")
        path = files[0]
    return FileResponse(
        path,
        filename=path.name,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "web.server:app",
        host=HOST,
        port=PORT,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
