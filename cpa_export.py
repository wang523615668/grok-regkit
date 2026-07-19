"""Register-machine hook: mint CPA xai auth after successful registration.

OIDC package lives at ./cpa_xai (bundled with this project).
Optional override: config `api_reverse_tools` / env `API_REVERSE_TOOLS`
points at a directory that *contains* the `cpa_xai` package.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

_REG_DIR = Path(__file__).resolve().parent
_DEFAULT_OUT = _REG_DIR / "cpa_auths"
_DEFAULT_CPA = Path("")  # empty = do not assume a machine-local CPA path


def _ensure_cpa_xai_on_path(tools_dir: str | Path | None = None) -> Path:
    """Put the parent of `cpa_xai` on sys.path. Default: this project root."""
    if tools_dir:
        tools = Path(tools_dir).expanduser().resolve()
    else:
        env = (os.environ.get("API_REVERSE_TOOLS") or "").strip()
        tools = Path(env).expanduser().resolve() if env else _REG_DIR
    # If user pointed at .../cpa_xai itself, use its parent
    if tools.name == "cpa_xai" and (tools / "__init__.py").is_file():
        tools = tools.parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    return tools


def export_cookies_from_page(page: Any) -> list[dict]:
    """Best-effort export of cookies from a DrissionPage tab/browser."""
    if page is None:
        return []
    cookies = None
    for getter in (
        lambda: page.cookies(all_domains=True, all_info=True),
        lambda: page.cookies(all_domains=True),
        lambda: page.cookies(),
    ):
        try:
            cookies = getter()
            if cookies:
                break
        except TypeError:
            continue
        except Exception:
            continue
    if not cookies:
        try:
            browser = getattr(page, "browser", None)
            if browser is not None:
                cookies = browser.cookies()
        except Exception:
            cookies = None
    if isinstance(cookies, list):
        return [c for c in cookies if isinstance(c, dict)]
    return []


def export_cpa_xai_for_account(
    email: str,
    password: str,
    *,
    page: Any | None = None,
    cookies: Any | None = None,
    sso: str | None = None,
    config: dict | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    """Mint OIDC + write xai-<email>.json under register cpa_auths (and optional CPA auth-dir)."""
    cfg = config or {}
    log = log_callback or (lambda m: print(m, flush=True))

    if not cfg.get("cpa_export_enabled", True):
        log("[cpa] export disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    tools_dir = cfg.get("api_reverse_tools") or cfg.get("cpa_xai_parent") or None
    _ensure_cpa_xai_on_path(tools_dir)

    try:
        from cpa_xai import mint_and_export  # type: ignore
    except Exception as e:  # noqa: BLE001
        log(f"[cpa] import cpa_xai failed: {e}")
        return {"ok": False, "error": f"import: {e}"}

    out_dir = Path(cfg.get("cpa_auth_dir") or _DEFAULT_OUT).expanduser()
    if not out_dir.is_absolute():
        out_dir = (_REG_DIR / out_dir).resolve()

    hotload_raw = (cfg.get("cpa_hotload_dir") or "").strip()
    cpa_dir = Path(hotload_raw).expanduser() if hotload_raw else None
    if cpa_dir and not cpa_dir.is_absolute():
        cpa_dir = (_REG_DIR / cpa_dir).resolve()

    # Priority: cpa_proxy > proxy > env. Config must beat shell https_proxy.
    proxy = (cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip()
    if not proxy:
        proxy = (
            os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("http_proxy")
            or ""
        ).strip()
    # Default headed: headless is frequently Cloudflare-blocked on accounts.x.ai
    headless = bool(cfg.get("cpa_headless", False))
    probe = bool(cfg.get("cpa_probe_after_write", True))
    # Default ON: models-only is not enough; chat 403 must not enter CPA hotload.
    probe_chat = bool(cfg.get("cpa_probe_chat", True))
    # Chat must pass before hotload (mint file under cpa_auths may still be kept).
    chat_required_for_hotload = bool(cfg.get("cpa_chat_required_for_hotload", True))
    quarantine_raw = (cfg.get("cpa_quarantine_dir") or "").strip()
    quarantine_dir = Path(quarantine_raw).expanduser() if quarantine_raw else None
    if quarantine_dir and not quarantine_dir.is_absolute():
        quarantine_dir = (_REG_DIR / quarantine_dir).resolve()
    if quarantine_dir is None and cpa_dir:
        # sibling of hotload: .../cpa/auths -> .../cpa/auths_quarantine
        quarantine_dir = cpa_dir.parent / "auths_quarantine"
    timeout = float(cfg.get("cpa_mint_timeout_sec", 240))
    base_url = cfg.get("cpa_base_url") or "https://cli-chat-proxy.grok.com/v1"
    force_standalone = bool(cfg.get("cpa_force_standalone", True))
    cookie_inject = bool(cfg.get("cpa_mint_cookie_inject", True))
    reuse_browser = bool(cfg.get("cpa_mint_browser_reuse", True))
    recycle_every = int(cfg.get("cpa_mint_browser_recycle_every", 15) or 0)
    # Protocol (pure HTTP SSO device flow) first; browser only on failure.
    prefer_protocol = bool(cfg.get("cpa_prefer_protocol", True))
    protocol_only = bool(cfg.get("cpa_protocol_only", False))
    protocol_poll_timeout = float(cfg.get("cpa_protocol_poll_timeout_sec", 90) or 90)

    # Transient 403 after mint: retry chat probe before quarantine/hotload decision.
    try:
        chat_probe_retries = int(cfg.get("cpa_chat_probe_retries", 3) or 3)
    except (TypeError, ValueError):
        chat_probe_retries = 3
    delays_raw = cfg.get("cpa_chat_probe_retry_delays_sec") or [15, 45, 90]
    chat_probe_delays: list[float] = []
    if isinstance(delays_raw, (list, tuple)):
        for x in delays_raw:
            try:
                chat_probe_delays.append(float(x))
            except (TypeError, ValueError):
                pass
    if not chat_probe_delays:
        chat_probe_delays = [15.0, 45.0, 90.0]

    pending_raw = (cfg.get("cpa_pending_dir") or "").strip()
    pending_dir = Path(pending_raw).expanduser() if pending_raw else None
    if pending_dir and not pending_dir.is_absolute():
        pending_dir = (_REG_DIR / pending_dir).resolve()
    if pending_dir is None and cpa_dir:
        pending_dir = cpa_dir.parent / "auths_pending"

    # cookies: explicit arg > page export > none
    use_cookies = cookies
    if use_cookies is None and cookie_inject and page is not None:
        use_cookies = export_cookies_from_page(page)
    if not cookie_inject:
        use_cookies = None
    else:
        # Always attach SSO cookie clones — register cookies alone often miss accounts.x.ai host
        sso_val = (sso or "").strip()
        if not sso_val and isinstance(use_cookies, list):
            for c in use_cookies:
                if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                    sso_val = str(c.get("value"))
                    break
        if sso_val:
            base = list(use_cookies) if isinstance(use_cookies, list) else []
            for name in ("sso", "sso-rw"):
                for dom in (".x.ai", "accounts.x.ai", ".accounts.x.ai", "auth.x.ai", "grok.com", ".grok.com"):
                    base.append({
                        "name": name,
                        "value": sso_val,
                        "domain": dom,
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    })
            use_cookies = base

    sso_val = (sso or "").strip()
    if not sso_val and isinstance(use_cookies, list):
        for c in use_cookies:
            if isinstance(c, dict) and c.get("name") in ("sso", "sso-rw") and c.get("value"):
                sso_val = str(c.get("value"))
                break

    out_dir.mkdir(parents=True, exist_ok=True)
    log(
        f"[cpa] mint OIDC for {email} -> {out_dir} proxy={proxy or '(none)'} "
        f"cookies={len(use_cookies) if isinstance(use_cookies, list) else (1 if use_cookies else 0)} "
        f"reuse={reuse_browser} protocol={prefer_protocol}"
        f"{' only' if protocol_only else ''} sso={'yes' if sso_val else 'no'}"
    )

    def _log(msg: str) -> None:
        log(f"[cpa] {msg}")

    result = mint_and_export(
        email=email,
        password=password,
        auth_dir=out_dir,
        page=None if force_standalone else page,
        proxy=proxy or None,
        headless=headless,
        base_url=base_url,
        probe=probe,
        probe_chat=probe_chat,
        browser_timeout_sec=timeout,
        force_standalone=force_standalone,
        cookies=use_cookies,
        sso=sso_val or None,
        reuse_browser=reuse_browser,
        recycle_every=recycle_every,
        prefer_protocol=prefer_protocol,
        protocol_only=protocol_only,
        protocol_poll_timeout_sec=protocol_poll_timeout,
        chat_probe_retries=chat_probe_retries,
        chat_probe_retry_delays_sec=chat_probe_delays,
        log=_log,
    )
    if result.get("mint_method"):
        log(f"[cpa] mint_method={result.get('mint_method')}")

    # By default, a failed post-write *models* probe is only a warning: the local
    # CPA auth file under cpa_auths has already been minted. Chat probe is harder:
    # when cpa_chat_required_for_hotload=true (default), chat failure blocks hotload
    # and optionally quarantines the file so dead 403s never enter CPA rotation.
    if (
        not result.get("ok")
        and result.get("path")
        and str(result.get("error") or "").startswith("token ok but grok-4.5 not listed")
        and not cfg.get("cpa_probe_required", False)
    ):
        result["ok"] = True
        result["probe_warning"] = result.pop("error", "probe failed")
        log(f"[cpa] probe warning ignored (file already written): {result.get('probe_warning')}")

    chat = result.get("probe_chat") if isinstance(result.get("probe_chat"), dict) else None
    chat_ok = bool(chat and chat.get("ok"))
    if probe_chat and chat is not None:
        result["chat_ok"] = chat_ok
        if not chat_ok:
            result["chat_status"] = chat.get("status")
            result["chat_error"] = chat.get("error") or chat.get("status")
            log(
                f"[cpa] chat gate FAIL status={chat.get('status')} "
                f"err={str(chat.get('error') or '')[:180]}"
            )
        else:
            log(
                f"[cpa] chat gate OK endpoint={chat.get('endpoint')} "
                f"model={chat.get('model')} text={str(chat.get('text') or '')[:40]!r}"
            )

    allow_hotload = True
    if chat_required_for_hotload and probe_chat:
        # If chat was requested but missing from result (mint skipped probe), deny hotload.
        allow_hotload = chat_ok
        if not allow_hotload:
            result["hotload_skipped"] = True
            result["hotload_skip_reason"] = "chat_probe_failed_or_missing"
            # Keep mint as local artifact, but mark export not fully OK for pool purposes.
            if result.get("ok") and not chat_ok:
                result["ok"] = False
                if not result.get("error"):
                    result["error"] = (
                        f"chat probe failed: {result.get('chat_error') or result.get('chat_status') or 'missing'}"
                    )

    if (
        result.get("path")
        and cfg.get("cpa_copy_to_hotload", False)
        and cpa_dir
        and allow_hotload
        and (result.get("ok") or result.get("path"))
    ):
        # only hotload when chat gate allows
        if allow_hotload and (not chat_required_for_hotload or chat_ok or not probe_chat):
            try:
                cpa_dir.mkdir(parents=True, exist_ok=True)
                src = Path(result["path"])
                dst = cpa_dir / src.name
                shutil.copy2(src, dst)
                os.chmod(dst, 0o600)
                result["cpa_path"] = str(dst)
                log(f"[cpa] hotload copy -> {dst}")
            except Exception as e:  # noqa: BLE001
                log(f"[cpa] hotload copy failed: {e}")
                result["cpa_copy_error"] = str(e)

    # Soft chat fails (transient 403/429 right after mint) go to pending, not dead quarantine.
    # Hard fails (401 / permanent) still go to quarantine so ops can inspect.
    chat_status = int((chat or {}).get("status") or result.get("chat_status") or 0)
    err_txt = str(result.get("chat_error") or result.get("error") or "")
    soft_fail = bool(result.get("chat_soft_fail")) or (
        not chat_ok
        and (
            chat_status in (0, 403, 429)
            or "permission-denied" in err_txt
            or "free-usage-exhausted" in err_txt
        )
        and chat_status != 401
    )
    result["chat_soft_fail"] = soft_fail

    if (
        result.get("path")
        and soft_fail
        and pending_dir
        and (not allow_hotload or (probe_chat and chat is not None and not chat_ok))
    ):
        try:
            pending_dir.mkdir(parents=True, exist_ok=True)
            src = Path(result["path"])
            pdst = pending_dir / src.name
            shutil.copy2(src, pdst)
            os.chmod(pdst, 0o600)
            # sidecar for recheck scheduler
            meta = {
                "email": email,
                "first_fail_ts": int(time.time()),
                "chat_status": chat_status,
                "chat_error": err_txt[:300],
                "probe_chat_attempts": result.get("probe_chat_attempts") or [],
                "source": str(src),
            }
            (pending_dir / (src.stem + ".meta.json")).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            result["pending_path"] = str(pdst)
            if cpa_dir:
                stale = cpa_dir / src.name
                if stale.is_file():
                    try:
                        stale.unlink()
                        result["hotload_removed"] = str(stale)
                        log(f"[cpa] removed stale hotload {stale}")
                    except OSError as e:
                        log(f"[cpa] remove stale hotload failed: {e}")
            log(f"[cpa] pending (soft chat fail, will recheck) -> {pdst}")
            with open(pending_dir / "chat_pending.txt", "a", encoding="utf-8") as f:
                f.write(
                    f"{email}----{chat_status or result.get('error') or 'chat_soft_fail'}"
                    f"----{int(time.time())}\n"
                )
        except Exception as e:  # noqa: BLE001
            log(f"[cpa] pending copy failed: {e}")
            result["pending_error"] = str(e)
    elif (
        result.get("path")
        and quarantine_dir
        and (not allow_hotload or (probe_chat and chat is not None and not chat_ok))
        and not soft_fail
    ):
        # Quarantine hard failures only
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            src = Path(result["path"])
            qdst = quarantine_dir / src.name
            shutil.copy2(src, qdst)
            os.chmod(qdst, 0o600)
            result["quarantine_path"] = str(qdst)
            # ensure not lingering in hotload from a previous run of same email
            if cpa_dir:
                stale = cpa_dir / src.name
                if stale.is_file():
                    try:
                        stale.unlink()
                        result["hotload_removed"] = str(stale)
                        log(f"[cpa] removed stale hotload {stale}")
                    except OSError as e:
                        log(f"[cpa] remove stale hotload failed: {e}")
            log(f"[cpa] quarantine copy -> {qdst}")
            with open(quarantine_dir / "chat_failed.txt", "a", encoding="utf-8") as f:
                f.write(
                    f"{email}----{result.get('chat_status') or result.get('error') or 'chat_fail'}"
                    f"----{int(time.time())}\n"
                )
        except Exception as e:  # noqa: BLE001
            log(f"[cpa] quarantine copy failed: {e}")
            result["quarantine_error"] = str(e)

    # failure log under register dir
    if not result.get("ok"):
        fail_path = out_dir / "cpa_auth_failed.txt"
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(f"{email}----{result.get('error') or 'unknown'}----{int(time.time())}\n")
        # Do NOT raise on chat-only failure unless explicitly required — SSO/OIDC still useful.
        if cfg.get("cpa_mint_required", False) and not result.get("path"):
            raise RuntimeError(f"CPA mint required but failed: {result.get('error')}")
        if cfg.get("cpa_chat_required", False) and not chat_ok:
            raise RuntimeError(f"CPA chat required but failed: {result.get('error')}")

    return result
