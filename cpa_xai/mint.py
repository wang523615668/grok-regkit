"""High-level: mint CPA xai-*.json for one free registered account."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .browser_confirm import mint_with_browser
from .probe import probe_mini_response, probe_models
from .protocol_mint import ProtocolMintError, extract_sso_from_cookies, mint_with_sso_protocol
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def mint_and_export(
    *,
    email: str,
    password: str,
    auth_dir: str | Path,
    page: Any | None = None,
    proxy: str | None = None,
    headless: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    probe: bool = True,
    probe_chat: bool = False,
    browser_timeout_sec: float = 240.0,
    force_standalone: bool = True,
    cookies: Any | None = None,
    sso: str | None = None,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    prefer_protocol: bool = True,
    protocol_only: bool = False,
    protocol_poll_timeout_sec: float = 90.0,
    chat_probe_retries: int = 3,
    chat_probe_retry_delays_sec: list[float] | tuple[float, ...] | None = None,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: (protocol SSO device-flow |) browser device-auth → write CPA → probe.

    Protocol path (curl_cffi + sso cookie) is tried first when prefer_protocol
    and an sso cookie is available. On failure, falls back to browser mint unless
    protocol_only=True.

    Returns dict with keys: ok, path, email, probe, error?, mint_method?
    """
    log = log or _noop
    email = (email or "").strip()
    if not email or not password:
        # Protocol can work with sso alone; password only required for browser fallback
        if not email:
            return {"ok": False, "email": email, "error": "missing email"}
        if not (sso or extract_sso_from_cookies(cookies)):
            return {"ok": False, "email": email, "error": "missing email/password"}

    # Config/explicit proxy wins over shell https_proxy (common 7890 trap).
    # Thread-local pin — safe under concurrent mint workers.
    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    log(f"mint start: {email} proxy={proxy_log_label(resolved) or '(none)'}")

    sso_val = (sso or "").strip() or extract_sso_from_cookies(cookies)
    tokens: dict[str, Any] | None = None
    protocol_err: str | None = None

    if prefer_protocol and sso_val:
        # Retry protocol on rate_limit / slow_down before expensive browser fallback.
        max_proto = 4
        for pi in range(1, max_proto + 1):
            log(f"mint try protocol (SSO HTTP device flow) attempt={pi}/{max_proto}")
            try:
                tokens = mint_with_sso_protocol(
                    sso_cookie=sso_val,
                    email=email,
                    proxy=resolved or None,
                    cookies=cookies,
                    poll_timeout_sec=protocol_poll_timeout_sec,
                    log=log,
                    cancel=cancel,
                )
                log("mint protocol SUCCESS")
                protocol_err = None
                break
            except ProtocolMintError as e:
                protocol_err = str(e)
                log(f"mint protocol failed: {e}")
                el = protocol_err.lower()
                if any(x in el for x in ("slow_down", "rate_limited", "429", "too many")):
                    wait = min(20 * pi, 120)
                    log(f"mint protocol rate-limited, sleep {wait}s before retry")
                    time.sleep(wait)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                protocol_err = str(e)
                log(f"mint protocol exception: {e}")
                break
        if tokens is None:
            if protocol_only:
                return {
                    "ok": False,
                    "email": email,
                    "error": f"protocol_only: {protocol_err}",
                    "mint_method": "protocol",
                }
            log("mint fallback → browser")
    elif prefer_protocol and not sso_val:
        log("mint protocol skipped (no sso cookie) → browser")
        if protocol_only:
            return {
                "ok": False,
                "email": email,
                "error": "protocol_only but no sso cookie",
                "mint_method": "protocol",
            }
    elif not prefer_protocol:
        log("mint protocol disabled → browser")

    if tokens is None:
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": protocol_err or "protocol failed and no password for browser fallback",
                "protocol_error": protocol_err,
            }
        try:
            tokens = mint_with_browser(
                email=email,
                password=password,
                page=None if force_standalone else page,
                proxy=resolved or None,
                headless=headless,
                browser_timeout_sec=browser_timeout_sec,
                force_standalone=force_standalone,
                cookies=cookies,
                reuse_browser=reuse_browser,
                recycle_every=recycle_every,
                poll_log=log,
                cancel=cancel,
            )
            tokens["mint_method"] = "browser"
            if protocol_err:
                tokens["protocol_error"] = protocol_err
        except Exception as e:  # noqa: BLE001
            log(f"mint failed: {e}")
            err = str(e)
            if protocol_err:
                err = f"{err} (protocol: {protocol_err})"
            return {
                "ok": False,
                "email": email,
                "error": err,
                "protocol_error": protocol_err,
            }

    payload = build_cpa_xai_auth(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    log(f"wrote {path}")

    result: dict[str, Any] = {
        "ok": True,
        "email": email,
        "path": str(path),
        "user_code": tokens.get("user_code"),
        "base_url": base_url,
        "proxy": proxy_log_label(resolved),
        "mint_method": tokens.get("mint_method") or "browser",
    }
    if protocol_err and result["mint_method"] != "protocol":
        result["protocol_error"] = protocol_err

    if probe:
        pr = probe_models(tokens["access_token"], base_url=base_url, proxy=resolved or None)
        result["probe_models"] = pr
        log(
            f"probe models: ok={pr.get('ok')} status={pr.get('status')} "
            f"has_grok_45={pr.get('has_grok_45')} ids={pr.get('model_ids')} "
            f"error={str(pr.get('error') or '')[:200]}"
        )
        if not pr.get("has_grok_45"):
            result["ok"] = False
            result["error"] = "token ok but grok-4.5 not listed"
        if probe_chat:
            # Always run chat gate when requested — models list ≠ chat permission.
            # New free accounts often return transient 403 permission-denied right
            # after mint; retry with backoff; on 403 optionally rotate mihomo exit IP.
            delays = list(chat_probe_retry_delays_sec or (15.0, 30.0, 60.0))
            attempts = max(1, int(chat_probe_retries or 1))
            rotate_on_403 = True
            rotate_cfg: dict[str, Any] = {}
            try:
                import json as _json
                from pathlib import Path as _Path

                _cfg_path = _Path(__file__).resolve().parents[1] / "config.json"
                if _cfg_path.is_file():
                    rotate_cfg = dict(_json.loads(_cfg_path.read_text(encoding="utf-8")))
            except Exception:
                rotate_cfg = {}
            try:
                # Overlay live register engine config (if job already loaded it)
                import grok_register_ttk as _eng  # type: ignore

                rotate_cfg = {**rotate_cfg, **dict(getattr(_eng, "config", {}) or {})}
            except Exception:
                pass
            rotate_on_403 = bool(rotate_cfg.get("mihomo_rotate_on_403", True))
            ch: dict[str, Any] = {}
            attempts_log: list[dict[str, Any]] = []
            for attempt in range(1, attempts + 1):
                if cancel and cancel():
                    ch = {"ok": False, "status": 0, "error": "cancelled", "endpoint": "chat/completions"}
                    break
                ch = probe_mini_response(
                    tokens["access_token"], base_url=base_url, proxy=resolved or None
                )
                attempts_log.append(
                    {
                        "attempt": attempt,
                        "ok": bool(ch.get("ok")),
                        "status": ch.get("status"),
                        "error": str(ch.get("error") or "")[:200],
                    }
                )
                log(
                    f"probe chat attempt={attempt}/{attempts}: ok={ch.get('ok')} "
                    f"status={ch.get('status')} endpoint={ch.get('endpoint')} "
                    f"model={ch.get('model')} text={ch.get('text')!r} "
                    f"err={str(ch.get('error') or '')[:160]}"
                )
                if ch.get("ok"):
                    break
                status = int(ch.get("status") or 0)
                err = str(ch.get("error") or "")
                # Only delay-retry soft denials / flaky edge; hard auth fails stop early.
                soft = status in (0, 403, 429, 502, 503, 504) or "permission-denied" in err
                if status == 401 or not soft or attempt >= attempts:
                    # Still rotate once on final 403 so subsequent accounts leave the bad IP.
                    if rotate_on_403 and attempt >= attempts and (status == 403 or "permission-denied" in err):
                        try:
                            from mihomo_rotate import rotate_after_403  # type: ignore

                            rot = rotate_after_403(
                                cfg=rotate_cfg,
                                proxy=str(
                                    resolved
                                    or rotate_cfg.get("cpa_proxy")
                                    or rotate_cfg.get("proxy")
                                    or "http://127.0.0.1:7890"
                                ),
                                log=log,
                            )
                            attempts_log[-1]["rotated_final"] = {
                                "ok": bool(rot.get("ok")),
                                "from_ip": rot.get("from_ip"),
                                "to_ip": rot.get("to_ip"),
                            }
                            if rot.get("ok"):
                                log(
                                    f"probe chat final 403 -> rotated exit "
                                    f"{rot.get('from_ip')} -> {rot.get('to_ip')}"
                                )
                        except Exception as rot_exc:
                            log(f"probe chat final 403 rotate error: {rot_exc}")
                    break

                # User policy: once 403 starts on this IP, switch mihomo exit before retry.
                if rotate_on_403 and (status == 403 or "permission-denied" in err):
                    try:
                        from mihomo_rotate import rotate_after_403  # type: ignore

                        rot = rotate_after_403(
                            cfg=rotate_cfg,
                            proxy=str(
                                resolved
                                or rotate_cfg.get("cpa_proxy")
                                or rotate_cfg.get("proxy")
                                or "http://127.0.0.1:7890"
                            ),
                            log=log,
                        )
                        attempts_log[-1]["rotated"] = {
                            "ok": bool(rot.get("ok")),
                            "from": rot.get("from"),
                            "to": rot.get("to"),
                            "from_ip": rot.get("from_ip"),
                            "to_ip": rot.get("to_ip"),
                        }
                        if rot.get("ok"):
                            log(
                                f"probe chat 403 -> rotated exit "
                                f"{rot.get('from_ip') or rot.get('from')} -> "
                                f"{rot.get('to_ip') or rot.get('to')}"
                            )
                        else:
                            log(f"probe chat 403 rotate failed: {rot.get('error')}")
                    except Exception as rot_exc:
                        log(f"probe chat 403 rotate error: {rot_exc}")
                        attempts_log[-1]["rotate_error"] = str(rot_exc)[:160]

                wait = delays[min(attempt - 1, len(delays) - 1)] if delays else 20.0
                # After IP rotate, shorter wait is enough; keep at least 3s for connection settle.
                if rotate_on_403 and (status == 403 or "permission-denied" in err):
                    wait = min(float(wait), float(rotate_cfg.get("mihomo_rotate_settle_sec", 5) or 5))
                    wait = max(3.0, float(wait))
                log(f"probe chat soft-fail status={status}; wait {wait:.0f}s then retry")
                deadline = time.time() + max(0.0, float(wait))
                while time.time() < deadline:
                    if cancel and cancel():
                        break
                    time.sleep(min(1.0, deadline - time.time()))
            result["probe_chat"] = ch
            result["probe_chat_attempts"] = attempts_log
            if not ch.get("ok"):
                result["ok"] = False
                result["error"] = f"chat probe failed: {ch.get('error') or ch.get('status')}"
                result["chat_soft_fail"] = int(ch.get("status") or 0) in (0, 403, 429)
            else:
                result["chat_soft_fail"] = False
    return result
