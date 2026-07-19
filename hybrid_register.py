"""Hybrid Grok registration: protocol RPC + browser tokens.

Used by Web/CLI when config register_mode == "hybrid".
"""
from __future__ import annotations

import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent

from browser.token_harvester import BrowserTokenSession  # noqa: E402
from protocol.grpc_client import AuthManagementClient  # noqa: E402
from protocol.session import ProtocolSession  # noqa: E402


def load_next_action_from_capture() -> str:
    rpc = ROOT / "capture_out" / "rpc"
    for name in ("03_SignUpSubmit.req.headers.json",):
        p = rpc / name
        if p.is_file():
            try:
                import json

                h = json.loads(p.read_text(encoding="utf-8"))
                return h.get("next-action") or h.get("Next-Action") or ""
            except Exception:
                pass
    if rpc.is_dir():
        import json

        for f in rpc.glob("*.req.headers.json"):
            try:
                h = json.loads(f.read_text(encoding="utf-8"))
                if h.get("next-action"):
                    return h["next-action"]
            except Exception:
                pass
    return ""


def _maybe_rotate_on_reg_403(status: int, raw_hint: str, log: Callable[[str], None], proxy: str = "") -> bool:
    """If register-stage HTTP looks like CF/IP ban 403, rotate mihomo exit IP.

    Returns True if a rotate was attempted (ok or fail). Caller usually fails this account.
    """
    st = int(status or 0)
    hint = (raw_hint or "").lower()
    is_ban = st == 403 or "cloudflare" in hint or "<!doctype html>" in hint[:80]
    if not is_ban:
        return False
    try:
        from grok_register_ttk import config as _cfg
        from mihomo_rotate import rotate_after_403

        cfg = dict(_cfg or {}) if isinstance(_cfg, dict) else {}
        if not bool(cfg.get("mihomo_rotate_on_403", True)):
            return False
        px = (
            proxy
            or cfg.get("cpa_proxy")
            or cfg.get("proxy")
            or "http://127.0.0.1:7890"
        )
        log(f"[hybrid] register-stage 403/CF ban -> rotate exit IP…")
        rot = rotate_after_403(cfg=cfg, proxy=str(px), log=log)
        if rot.get("ok"):
            log(
                f"[hybrid] rotated exit {rot.get('from_ip')} -> {rot.get('to_ip')} "
                f"node={rot.get('to')}"
            )
        else:
            log(f"[hybrid] rotate failed: {rot.get('error')}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"[hybrid] rotate error: {exc}")
        return False


def register_one_hybrid(
    *,
    log: Callable[[str], None],
    proxy: str = "",
    user_agent: str = "",
    next_action: str = "",
    accounts_file: Path,
    should_stop: Optional[Callable[[], bool]] = None,
    post_success: bool = True,
) -> bool:
    """Register one account via hybrid path. Returns True on SSO success."""
    from grok_register_ttk import (
        build_profile,
        get_email_and_token,
        get_oai_code,
        schedule_post_registration,
        config as _cfg,
    )

    stop = should_stop or (lambda: False)
    t0 = time.time()
    action = (next_action or load_next_action_from_capture() or "").strip()

    try:
        with BrowserTokenSession(log=log) as browser:
            if stop():
                return False
            # Install hooks before navigation so CreateEmail body is always captured
            browser.start()  # no-op if already started by context
            browser.install_network_hook()
            browser.open_signup()
            # Re-install after signup SPA settle (fetch may be rebound)
            browser.install_network_hook()
            action = action or browser.scrape_next_action() or action

            email, mail_token = get_email_and_token()
            log(f"[hybrid] email={email}")
            if stop():
                return False

            # Browser UI submit triggers native CreateEmail (passes CF). Capture castle from that request.
            castle = browser.harvest_castle_via_email_submit(email, timeout=45)
            browser_cookies = browser.export_cookies()
            if not castle or len(castle) < 1000 or not str(castle).startswith("IBYIll"):
                log(
                    f"[hybrid] bad castle len={len(castle or '')} head={(castle or '')[:24]}"
                )
                return False

            ua = browser.browser_user_agent() or user_agent or ""
            sess = ProtocolSession(
                proxy=(proxy or "").strip(),
                user_agent=ua,
                impersonate="chrome131",
            )
            # Prefer fresh signup cookies; strip old sso so server doesn't treat as logged-in.
            jar = dict(browser_cookies or {})
            for stale in ("sso", "sso-rw"):
                jar.pop(stale, None)
            sess.set_cookies(jar)
            client = AuthManagementClient(sess)
            if action:
                client.next_action = action

            browser_sent = browser.create_email_sent_via_browser()
            if browser_sent:
                log(f"[hybrid] CreateEmail via browser OK (skip protocol) castle_len={len(castle)}")
            else:
                r1 = client.create_email_validation_code(email, castle)
                log(f"[hybrid] CreateEmail status={r1['status']} castle_len={len(castle)}")
                if r1["status"] >= 400:
                    body_hint = ""
                    raw_txt = ""
                    try:
                        raw = r1.get("raw") or b""
                        raw_txt = raw[:500].decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)[:500]
                        if b"cloudflare" in raw[:500].lower() or b"<!DOCTYPE" in raw[:200]:
                            body_hint = " (Cloudflare block)"
                    except Exception:
                        pass
                    log(f"[hybrid] CreateEmail fail{body_hint} strings={r1.get('strings')[:2]}")
                    _maybe_rotate_on_reg_403(r1["status"], raw_txt, log, proxy=proxy)
                    return False
            if stop():
                return False

            _poll = float(_cfg.get("mail_poll_interval", 2) or 2) if isinstance(_cfg, dict) else 2
            code = get_oai_code(mail_token, email, log_callback=log, poll_interval=_poll)
            clean = str(code or "").replace("-", "").strip()
            if not clean:
                log("[hybrid] no mail code")
                return False
            log(f"[hybrid] code={clean}")

            # Align with 07:16 success path:
            # protocol VerifyEmail → inject Turnstile → createUser (no full browser-finish).
            # Full browser-finish burns OTP and often dies on Turnstile without createUser.
            r2 = None
            for v_try in range(1, 4):
                try:
                    r2 = client.verify_email_validation_code(email, clean)
                    break
                except Exception as exc:
                    msg = str(exc)
                    log(f"[hybrid] VerifyEmail attempt={v_try} err={msg[:160]}")
                    if "TLS" in msg or "invalid library" in msg or "curl:" in msg:
                        time.sleep(1.0 * v_try)
                        continue
                    raise
            if r2 is None:
                return False
            log(f"[hybrid] VerifyEmail status={r2['status']}")
            if r2["status"] >= 400:
                raw_txt = ""
                try:
                    raw = r2.get("raw") or b""
                    raw_txt = (
                        raw[:500].decode("utf-8", errors="ignore")
                        if isinstance(raw, (bytes, bytearray))
                        else str(raw)[:500]
                    )
                except Exception:
                    raw_txt = str(r2.get("strings") or "")[:300]
                log(f"[hybrid] VerifyEmail fail {r2.get('strings')[:5]}")
                if "already" not in raw_txt.lower() and "used" not in raw_txt.lower():
                    _maybe_rotate_on_reg_403(r2["status"], raw_txt, log, proxy=proxy)
                    return False
            if stop():
                return False

            given, family, password = build_profile()
            try:
                client.validate_password(email, password)
            except Exception:
                pass

            sso = ""
            body_txt = ""
            r3 = {"status": 0, "cookies": {}, "text": ""}

            # Prefer inject path (sitekey + turnstilePatch) — same as 07:16 success.
            # NOTE: OTP is already protocol-verified; do NOT burn time on full browser-finish
            # (that path reuses dead OTP form and rarely yields createUser).
            turnstile = browser.get_turnstile_token(timeout=90, inject=True)
            if len(turnstile) < 80:
                log(f"[hybrid] turnstile short len={len(turnstile)}; retry once after brief wait")
                time.sleep(1.5)
                turnstile = browser.get_turnstile_token(timeout=60, inject=True)
            if len(turnstile) < 80:
                log(f"[hybrid] turnstile short len={len(turnstile)}")
                return False

            if not sso:
                castle2 = browser.read_captured_castle() or castle
                if len(castle2) < 1000:
                    castle2 = castle
                browser_cookies = browser.export_cookies()
                jar2 = dict(browser_cookies or {})
                for stale in ("sso", "sso-rw"):
                    jar2.pop(stale, None)
                sess.set_cookies(jar2)
                action = (
                    action
                    or browser.scrape_next_action()
                    or load_next_action_from_capture()
                )
                if not action:
                    # Force re-discover from signup chunks (not unrelated CSR hashes)
                    client.next_action = ""
                    action = client.discover_next_action(timeout=60)
                known = "7f50061dd2f5b389a530e4a048d5fdf0c48d1d9259"
                if not action:
                    action = known
                    log(f"[hybrid] next-action fallback={action[:16]}...")
                elif action != known:
                    log(
                        f"[hybrid] next-action discovered={action[:20]}... "
                        f"known={known[:16]}..."
                    )
                else:
                    log(f"[hybrid] next-action={action[:20]}...")
                client.next_action = action
                if stop():
                    return False

                def _do_signup(act: str):
                    return client.create_user_via_server_action(
                        email=email,
                        code=clean,
                        given_name=given,
                        family_name=family,
                        password=password,
                        turnstile_token=turnstile,
                        castle_token=castle2,
                        next_action=act,
                        conversion_id=str(uuid.uuid4()),
                    )

                # Retry protocol signup on transient curl TLS errors
                r3 = None
                for attempt in range(1, 4):
                    try:
                        r3 = _do_signup(action)
                        break
                    except Exception as exc:
                        msg = str(exc)
                        log(f"[hybrid] createUser attempt={attempt} err={msg[:160]}")
                        if "TLS" in msg or "invalid library" in msg or "curl:" in msg:
                            time.sleep(1.0 * attempt)
                            # rebuild session cookies/proxy path
                            try:
                                jar_retry = dict(browser.export_cookies() or {})
                                for stale in ("sso", "sso-rw"):
                                    jar_retry.pop(stale, None)
                                sess.set_cookies(jar_retry)
                            except Exception:
                                pass
                            continue
                        raise
                if r3 is None:
                    return False
                sso = r3.get("sso") or ""
                if not sso:
                    sso = (r3.get("cookies") or {}).get("sso") or (r3.get("cookies") or {}).get("sso-rw") or ""
                body_txt = str(r3.get("text") or "")
                known = "7f50061dd2f5b389a530e4a048d5fdf0c48d1d9259"
                if (not sso) and action != known and (
                    "isLoggedInWithSSO" in body_txt or r3.get("status") == 200
                ):
                    log(f"[hybrid] retry sign-up with known next-action={known[:16]}...")
                    jar3 = dict(browser.export_cookies() or {})
                    for stale in ("sso", "sso-rw"):
                        jar3.pop(stale, None)
                    sess.set_cookies(jar3)
                    r3 = _do_signup(known)
                    sso = r3.get("sso") or ""
                    if not sso:
                        sso = (r3.get("cookies") or {}).get("sso") or (r3.get("cookies") or {}).get("sso-rw") or ""
                    body_txt = str(r3.get("text") or "")

            log(
                f"[hybrid] sign-up status={r3.get('status') if r3 else 0} sso_len={len(sso)} "
                f"elapsed={time.time() - t0:.1f}s"
            )
            if not sso:
                log(
                    f"[hybrid] no sso cookies={list((r3.get('cookies') or {}).keys())[:12]} "
                    f"body={body_txt[:240]}"
                )
                _maybe_rotate_on_reg_403(int(r3.get("status") or 0), body_txt, log, proxy=proxy)
                return False

            # Hybrid often gets set-cookie *wrapper* JWT (~2k). CPA needs real session sso (~150).
            try:
                from protocol.sso_util import (
                    is_session_sso,
                    is_wrapper_sso,
                    materialize_sso_via_browser,
                    materialize_sso_via_http,
                )

                if is_wrapper_sso(sso) or not is_session_sso(sso):
                    log(f"[hybrid] sso looks like set-cookie wrapper len={len(sso)}; materialize…")
                    from grok_register_ttk import _get_page

                    page = _get_page()
                    sess_sso = ""
                    if page is not None:
                        sess_sso = materialize_sso_via_browser(
                            page, sso, log=log, timeout=40
                        )
                    if not sess_sso or not is_session_sso(sess_sso):
                        jar = dict(browser.export_cookies() or {})
                        sess_sso = materialize_sso_via_http(
                            sso,
                            proxy=(proxy or "").strip(),
                            extra_cookies=jar,
                            log=log,
                        ) or sess_sso
                    if sess_sso and is_session_sso(sess_sso):
                        log(f"[hybrid] session sso ready len={len(sess_sso)}")
                        sso = sess_sso
                    else:
                        log(
                            f"[hybrid] WARN still wrapper/non-session sso len={len(sso)}; "
                            f"CPA mint may fail until browser path works"
                        )
            except Exception as e:
                log(f"[hybrid] sso materialize: {e}")

            line = f"{email}----{password}----{sso}\n"
            try:
                with accounts_file.open("a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                log(f"[hybrid] save file fail: {e}")

            log(f"[hybrid][+] OK {email}")
            if post_success:
                try:
                    # Export full browser jar (cf_clearance + sso) for CPA protocol mint
                    jar_full = dict(browser.export_cookies() or {})
                    if sso:
                        jar_full["sso"] = sso
                        jar_full["sso-rw"] = jar_full.get("sso-rw") or sso
                    cookie_list = [
                        {"name": k, "value": v, "domain": ".x.ai", "path": "/"}
                        for k, v in jar_full.items()
                        if k and v is not None
                    ]
                    log(f"[hybrid] post cookies={len(cookie_list)} for CPA/g2a")
                    schedule_post_registration(
                        email,
                        password,
                        sso,
                        page=None,
                        cookies=cookie_list,
                        log_callback=log,
                    )
                except Exception as e:
                    log(f"[hybrid] post_success: {e}")
            return True
    except Exception as e:
        log(f"[hybrid] exception: {e}")
        try:
            log(traceback.format_exc().splitlines()[-3])
        except Exception:
            pass
        return False


def run_hybrid_registration_job(count, log_callback=None, controller=None):
    """Web/CLI entry compatible with run_registration_job return shape."""
    import grok_register_ttk as engine

    log = log_callback or engine.cli_log
    if controller is None:
        controller = engine.CliStopController()

    success_count = 0
    fail_count = 0
    accounts_output_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"accounts_hybrid_{engine.now_beijing('%Y%m%d_%H%M%S')}.txt",
    )
    log(f"[*] 混合模式启动，目标数量: {count}")
    log(f"[*] 成功账号将实时保存到: {accounts_output_file}")

    mode = str(engine.config.get("proxy_mode", "direct") or "direct")
    try:
        resolved_proxy = engine.apply_resolved_proxy_to_config(
            log_callback=log, fetch_live=True
        )
    except Exception as proxy_exc:
        log(f"[!] 获取/解析代理失败: {proxy_exc}")
        raise

    if resolved_proxy:
        safe = resolved_proxy
        try:
            import urllib.parse

            parsed = urllib.parse.urlparse(resolved_proxy)
            if parsed.password:
                safe = resolved_proxy.replace(":" + parsed.password + "@", ":****@")
        except Exception:
            pass
        log(f"[*] 代理模式: {mode} | {safe}")
    else:
        log(f"[*] 代理模式: {mode or 'direct'}（直连）")

    next_action = load_next_action_from_capture()
    ua = str(engine.config.get("user_agent") or "")
    proxy = str(engine.config.get("proxy") or resolved_proxy or "")

    try:
        i = 0
        while i < count:
            if controller.should_stop():
                break
            log(f"--- [hybrid] 开始第 {i + 1}/{count} 个账号 ---")
            ok = register_one_hybrid(
                log=log,
                proxy=proxy,
                user_agent=ua,
                next_action=next_action,
                accounts_file=Path(accounts_output_file),
                should_stop=controller.should_stop,
                post_success=True,
            )
            if ok:
                success_count += 1
            else:
                fail_count += 1
            i += 1
            log(f"[*] 当前统计: 成功 {success_count} | 失败 {fail_count}")
            if controller.should_stop():
                break
            engine.sleep_with_cancel(1, controller.should_stop)
    except KeyboardInterrupt:
        controller.stop()
        log("[!] 收到 Ctrl+C，正在停止")
    except Exception as exc:
        log(f"[!] 混合任务异常: {exc}")
    finally:
        # Don't block job end for long CPA browser mint (SSO already saved).
        engine.wait_post_success_queue(timeout=45, log_callback=log)
        try:
            engine.cleanup_runtime_memory(log_callback=log, reason="混合任务结束")
        except Exception:
            pass
        log(f"[*] 混合任务结束。成功 {success_count} | 失败 {fail_count}")

    return {
        "success": success_count,
        "fail": fail_count,
        "accounts_file": accounts_output_file,
        "stopped": bool(controller.should_stop()),
    }
