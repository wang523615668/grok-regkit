#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import threading
import datetime
import time
import os
import sys
import gc
import queue
import secrets
import struct
import random
import re
import string
import json
import base64
import select
import socket
import socketserver
import ssl
import urllib.parse
from zoneinfo import ZoneInfo

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

# 运行日志统一北京时间（与服务器 UTC 无关）
_BJ_TZ = ZoneInfo("Asia/Shanghai")


def now_beijing(fmt: str = "%H:%M:%S") -> str:
    return datetime.datetime.now(_BJ_TZ).strftime(fmt)

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    HAS_TK = True
except Exception:
    tk = None  # type: ignore
    ttk = None  # type: ignore
    messagebox = None  # type: ignore
    scrolledtext = None  # type: ignore
    HAS_TK = False

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "",
    # proxy_mode: direct | custom | whitelist | cliproxy_white | airport
    "proxy_mode": "airport",
    # 机场(Mihomo)本地 HTTP 入口：订阅在 mihomo 的 REGISTER-RESIDENTIAL 组
    "proxy_airport_url": "http://127.0.0.1:7893",
    # Cliproxy 白名单 API：返回 ip:port 文本
    # 例: https://api.cliproxy.io/white/api?region=US&num=1&time=10&format=n&type=txt
    "proxy_api_url": "https://api.cliproxy.io/white/api",
    "proxy_api_num": 5,
    "proxy_api_format": "n",
    "proxy_api_type": "txt",
    # IP 质量检测
    # 1) 先查「入口 IP」(Cliproxy 返回的 host) —— 不走代理，省家宽
    # 2) 可选再经代理查出口 IP（IPPure）
    "proxy_quality_api": "https://my.ippure.com/v1/info",
    "proxy_host_lookup_api": "http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,org,as,hosting,proxy,mobile,query,isp",
    "proxy_quality_check": True,
    # Cliproxy white API returns shared DC gateway host:port; real residential is EXIT via proxy.
    # Entry host check is informational only by default (do not hard-reject Zenlayer gateways).
    "proxy_check_entry_host": False,
    "proxy_check_exit_ippure": True,
    "proxy_max_fraud_score": 40,
    "proxy_require_residential": True,
    "proxy_require_country_match": True,
    "proxy_reject_datacenter_org": True,
    "proxy_reject_hosting_flag": True,
    "proxy_quality_max_tries": 8,
    # whitelist / 代理组（用户名带国家，旧模式）
    "proxy_host": "",
    "proxy_port": "",
    "proxy_user": "",
    "proxy_pass": "",
    # 国家/地区：Cliproxy 用 region，建议 US
    "proxy_country": "US",
    # 用户名拼装分隔符，如 - 或 _
    "proxy_delimiter": "-",
    # 轮转/粘性时长（分钟）：Cliproxy 对应 time 参数
    "proxy_duration": "10",
    # 模板变量: {user} {pass} {host} {port} {country} {delimiter} {session} {duration}
    "proxy_user_template": "{user}{delimiter}region{delimiter}{country}",
    "proxy_session": "",
    "enable_nsfw": True,
    # True=NSFW 后台执行（功能仍做）；False=拿 sso 后立刻同步开 NSFW
    "nsfw_async": True,
    "register_count": 1,
    # register_mode: browser (full UI) | hybrid (protocol + short browser tokens)
    "register_mode": "browser",
    # browser engine for hybrid token harvest: "nodriver" (preferred) or "drission"
    "browser_engine": "nodriver",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "grok2api_auto_add_local": True,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
    # ===== CPA / free Grok 4.5 (OIDC via Grok Build, NOT SSO) =====
    # SSO → web model pool; OIDC → CLIProxyAPI → cli-chat-proxy → grok-4.5
    "cpa_export_enabled": True,
    "cpa_auth_dir": "./cpa_auths",
    "cpa_copy_to_hotload": True,
    "cpa_hotload_dir": "",  # set to CPA auth-dir on server, e.g. /opt/cliproxyapi/auths
    "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
    "cpa_proxy": "",  # empty = fall back to runtime proxy / airport
    # Remote CPA Management API (optional; from grokRegister-cpa)
    # POST {cpa_remote_url}/v0/management/auth-files?name=xai-*.json
    "cpa_remote_url": "",  # e.g. http://127.0.0.1:8317
    "cpa_management_key": "",  # remote-management.secret-key / CPA_MGMT_KEY
    "cpa_remote_timeout_sec": 30,
    # only upload when chat gate allows hotload (default); true=also upload soft/hard fails
    "cpa_remote_upload_on_chat_fail": False,
    # Protocol mint needs no browser; fallback browser MUST be headed (Xvfb) on servers.
    "cpa_headless": False,
    "cpa_force_standalone": True,
    "cpa_mint_timeout_sec": 300,
    "cpa_mint_required": False,
    "cpa_probe_after_write": True,
    "cpa_probe_required": False,
    "cpa_probe_chat": False,
    "cpa_prefer_protocol": True,
    "cpa_protocol_only": False,
    "cpa_protocol_poll_timeout_sec": 90,
    "cpa_mint_cookie_inject": True,
    "cpa_gui_close_mint_browser": True,
    "cpa_mint_browser_reuse": False,
    "cpa_mint_browser_recycle_every": 15,
    # Gap between CPA mints to avoid auth.x.ai device-code 429/slow_down
    "cpa_mint_gap_sec": 25,
    # 注册主路径提速：sso 落盘后，g2a 入池 + CPA mint 进后台队列（功能都保留）
    "post_success_async": True,
    # 后台入池：浏览器松了再多试几次，比注册高峰硬等 502 更划算
    "grok2api_bg_max_http_tries": 6,
    "grok2api_bg_http_timeout_sec": 15,
    # Extra email providers (MailNest / CloudMail) — optional
    "duckmail_api_base": "https://api.duckmail.sbs",
    "mailnest_api_key": "",
    "mailnest_project_code": "x-ai001",
    "cloudmail_url": "",
    "cloudmail_admin_email": "",
    "cloudmail_password": "",
    "defaultDomains": "",
    "email_provider": "duckmail",
    "yyds_api_key": "",
    "yyds_jwt": "",
    "yyds_default_domain": "",
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0
_cpa_export_lock = threading.Lock()
_cpa_last_mint_ts = 0.0  # wall clock; serialize + gap between mints
_post_success_q = queue.Queue()
_post_success_worker_lock = threading.Lock()
_post_success_worker_started = False
_post_success_pending = 0
_post_success_pending_lock = threading.Lock()


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置失败: {e}")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def _proxy_quote(part: str) -> str:
    return urllib.parse.quote(str(part or ""), safe="")


def _cliproxy_build_url(c, region, num, duration, fmt, typ) -> str:
    base = str(c.get("proxy_api_url", "") or "https://api.cliproxy.io/white/api").strip()
    if not base:
        raise ValueError("未配置 proxy_api_url")
    if "?" in base:
        return (
            base.replace("{region}", region)
            .replace("{country}", region)
            .replace("{num}", str(num))
            .replace("{time}", duration)
            .replace("{duration}", duration)
            .replace("{format}", fmt)
            .replace("{type}", typ)
        )
    qs = urllib.parse.urlencode(
        {
            "region": region,
            "num": str(num),
            "time": duration,
            "format": fmt,
            "type": typ,
        }
    )
    return f"{base.rstrip('/')}?{qs}"


def _parse_proxy_hostports(text: str) -> list:
    """Parse ip:port lines from Cliproxy txt/json response."""
    text = (text or "").strip()
    if not text:
        return []
    lines = []
    # try full JSON first
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                raw_list = data.get("data") or data.get("list") or data.get("proxies") or []
                if isinstance(raw_list, str):
                    text = raw_list
                elif isinstance(raw_list, list):
                    for item in raw_list:
                        if isinstance(item, str):
                            lines.append(item)
                        elif isinstance(item, dict):
                            lines.append(
                                str(item.get("proxy") or item.get("ip") or item.get("addr") or "")
                            )
                else:
                    one = str(data.get("proxy") or data.get("ip") or "")
                    if one:
                        lines.append(one)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        lines.append(item)
                    elif isinstance(item, dict):
                        lines.append(str(item.get("proxy") or item.get("ip") or ""))
        except Exception:
            pass
    if not lines:
        lines = text.replace("\r\n", "\n").replace(",", "\n").split("\n")

    out = []
    seen = set()
    for raw in lines:
        cand = str(raw or "").strip()
        if not cand or cand.startswith("#"):
            continue
        cand = cand.replace("http://", "").replace("https://", "").strip()
        if ":" not in cand:
            continue
        host, port = cand.rsplit(":", 1)
        host = host.strip().strip("[]")
        port = port.strip()
        if not host or not port.isdigit():
            continue
        item = f"{host}:{port}"
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


_DATACENTER_ORG_KEYWORDS = (
    "amazon",
    "aws",
    "google cloud",
    "google llc",
    "microsoft",
    "azure",
    "digitalocean",
    "linode",
    "akamai",
    "cloudflare",
    "ovh",
    "hetzner",
    "vultr",
    "contabo",
    "choopa",
    "leaseweb",
    "colocrossing",
    "psychz",
    "quadranet",
    "m247",
    "datacamp",
    "zenlayer",
    "server",
    "hosting",
    "vps",
    "dedicated",
    "data center",
    "datacenter",
    "colocation",
    "colo ",
)


def _normalize_quality_info(info: dict) -> dict:
    """Normalize IPPure / ip-api style payloads into one shape."""
    info = dict(info or {})
    # ip-api.com fields -> common
    if not info.get("ip") and info.get("query"):
        info["ip"] = info.get("query")
    if not info.get("countryCode") and info.get("countryCode") is None:
        # already ok
        pass
    if not info.get("asOrganization"):
        info["asOrganization"] = (
            info.get("asOrganization")
            or info.get("org")
            or info.get("isp")
            or info.get("as")
            or ""
        )
    if "isResidential" not in info or info.get("isResidential") is None:
        # ip-api: hosting/proxy/mobile
        if "hosting" in info or "mobile" in info:
            hosting = bool(info.get("hosting"))
            mobile = bool(info.get("mobile"))
            if hosting:
                info["isResidential"] = False
            elif mobile:
                info["isResidential"] = True
    if info.get("hosting") is True and info.get("isResidential") is None:
        info["isResidential"] = False
    return info


def lookup_entry_ip_quality(ip: str, cfg=None, timeout: int = 12) -> dict:
    """Lookup Cliproxy *entry host* IP quality WITHOUT going through proxy.

    Saves residential bandwidth. Uses ip-api.com free endpoint by default.
    """
    c = cfg if isinstance(cfg, dict) else config
    ip = str(ip or "").strip()
    if not ip:
        raise ValueError("empty ip")
    template = str(
        c.get("proxy_host_lookup_api")
        or "http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,org,as,hosting,proxy,mobile,query,isp"
    ).strip()
    url = template.replace("{ip}", urllib.parse.quote(ip))
    # direct, no proxy
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "grok-register/1.0"})
    if resp.status_code >= 400:
        raise RuntimeError(f"入口IP查询 HTTP {resp.status_code}: {(resp.text or '')[:160]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"入口IP查询非JSON: {(resp.text or '')[:160]}")
    if not isinstance(data, dict):
        raise RuntimeError("入口IP查询格式错误")
    if str(data.get("status", "")).lower() == "fail":
        raise RuntimeError(f"入口IP查询失败: {data.get('message') or data}")
    data = _normalize_quality_info(data)
    data["ip"] = data.get("ip") or ip
    data["_source"] = "entry-host"
    return data


def probe_proxy_with_ippure(proxy_url: str, quality_api: str = "", timeout: int = 15) -> dict:
    """Call IPPure *through proxy* to get exit IP quality info.

    Docs: https://my.ippure.com/v1/info  (returns exit IP of the proxy path)
    Note: free responses often omit fraudScore/isResidential.
    """
    api = (quality_api or "https://my.ippure.com/v1/info").strip()
    proxies = {"http": proxy_url, "https": proxy_url}
    resp = requests.get(
        api,
        proxies=proxies,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"IPPure HTTP {resp.status_code}: {(resp.text or '')[:160]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"IPPure 返回非 JSON: {(resp.text or '')[:160]}")
    if not isinstance(data, dict):
        raise RuntimeError(f"IPPure 返回格式错误: {type(data)}")
    data = _normalize_quality_info(data)
    data["_source"] = "exit-ippure"
    return data


def evaluate_proxy_quality(info: dict, cfg=None, *, stage: str = "exit") -> tuple:
    """Return (ok: bool, reason: str, summary: str).

    stage: entry|exit — entry is Cliproxy host IP; exit is path after proxy.
    """
    c = cfg if isinstance(cfg, dict) else config
    info = _normalize_quality_info(info or {})
    fraud = info.get("fraudScore")
    try:
        fraud_i = int(fraud) if fraud is not None and str(fraud) != "" else None
    except Exception:
        fraud_i = None
    is_res = info.get("isResidential")
    is_broadcast = bool(info.get("isBroadcast"))
    hosting_flag = info.get("hosting")
    country_code = str(info.get("countryCode") or "").strip().upper()
    ip = str(info.get("ip") or "").strip()
    org = str(info.get("asOrganization") or info.get("org") or info.get("isp") or "").strip()
    org_l = org.lower()
    expected = str(c.get("proxy_country", "US") or "US").strip().upper()
    if expected == "RAND":
        expected = ""

    max_fraud = int(c.get("proxy_max_fraud_score", 40) or 40)
    require_res = bool(c.get("proxy_require_residential", True))
    require_country = bool(c.get("proxy_require_country_match", True))
    reject_dc_org = bool(c.get("proxy_reject_datacenter_org", True))
    reject_hosting = bool(c.get("proxy_reject_hosting_flag", True))

    summary = (
        f"[{stage}] ip={ip or '?'} country={country_code or '?'} "
        f"fraud={fraud_i if fraud_i is not None else '?'} "
        f"residential={is_res} hosting={hosting_flag} org={org or '?'}"
    )

    # High risk score (IPPure full plan / web)
    if fraud_i is not None:
        if fraud_i >= 70:
            return False, f"极度风险 fraudScore={fraud_i}", summary
        if fraud_i > max_fraud:
            return False, f"风险分过高 fraudScore={fraud_i}>{max_fraud}", summary

    if is_broadcast:
        return False, "广播/异常 IP (isBroadcast)", summary

    # Explicit datacenter flags
    if reject_hosting and hosting_flag is True:
        return False, "机房IP (hosting=true)", summary
    if require_res and is_res is False:
        return False, "非住宅 IP (isResidential=false)", summary

    if require_country and expected and country_code and country_code != expected:
        return False, f"国家不匹配 want={expected} got={country_code}", summary

    # ASN / org heuristics (Zenlayer etc.)
    if reject_dc_org and org_l and any(k in org_l for k in _DATACENTER_ORG_KEYWORDS):
        return False, f"疑似机房/云厂商 ASN: {org}", summary

    # Entry host: if no residential/fraud fields, do NOT soft-pass when org empty either
    if stage == "entry":
        if require_res and is_res is None and fraud_i is None and hosting_flag is None:
            # only org-based pass; if org missing, reject to be safe
            if not org_l:
                return False, "入口IP信息不足，无法确认非机房", summary
        return True, "ok", summary

    # Exit path via IPPure free API often lacks fraud/residential
    if require_res and is_res is None and fraud_i is None:
        # require org not datacenter already checked; still warn-level ok only if org present
        if not org_l:
            return False, "出口IP信息不足(无fraud/residential/org)", summary
        return True, "ok(出口字段不完整，已按国家+ASN判断)", summary
    return True, "ok", summary


# Cache entry-host quality within one process run: host -> (ok, reason, summary)
_entry_host_quality_cache = {}


def quality_check_cliproxy_hostport(hostport: str, cfg=None, log_callback=None) -> tuple:
    """Full quality gate for one Cliproxy host:port.

    Cliproxy white API returns a shared DC gateway (e.g. 107.151.x.x:port). The real
    residential IP is the *exit* seen when traffic goes through that port — different
    ports on the same gateway often map to different residential exits.

    1) Optional entry host note (informational; NOT a hard reject by default)
    2) IPPure via proxy for exit IP (authoritative for residential / country / fraud)
    Returns (ok, proxy_url, detail)
    """
    global _entry_host_quality_cache
    c = cfg if isinstance(cfg, dict) else config
    hostport = str(hostport or "").strip()
    host, port = hostport.rsplit(":", 1)
    proxy_url = f"http://{hostport}"
    # Default OFF: entry is almost always Zenlayer/VpsQuan gateway for white API.
    check_entry = bool(c.get("proxy_check_entry_host", False))
    # Hard reject on entry only if user explicitly opts in (legacy / non-Cliproxy).
    entry_hard = bool(c.get("proxy_entry_hard_reject", False))
    check_exit = bool(c.get("proxy_check_exit_ippure", True))
    quality_api = str(c.get("proxy_quality_api") or "https://my.ippure.com/v1/info").strip()

    if check_entry:
        cached = _entry_host_quality_cache.get(host)
        if cached is not None:
            ok, reason, summary = cached
            if log_callback:
                mark = "[+]" if ok else ("[-]" if entry_hard else "[*]")
                log_callback(
                    f"{mark} 入口(缓存) host={host} | {reason}"
                    + ("" if ok or entry_hard else "（网关可忽略，以出口为准）")
                )
            if not ok and entry_hard:
                return False, proxy_url, reason
        else:
            try:
                entry = lookup_entry_ip_quality(host, c)
                ok, reason, summary = evaluate_proxy_quality(entry, c, stage="entry")
                _entry_host_quality_cache[host] = (ok, reason, summary)
                if log_callback:
                    if ok:
                        log_callback(f"[+] 入口检测 {summary} | {reason}")
                    elif entry_hard:
                        log_callback(f"[-] 入口检测 {summary} | {reason}")
                    else:
                        log_callback(
                            f"[*] 入口网关 {summary} | {reason} "
                            f"（Cliproxy 共享入口可忽略，以出口家宽为准）"
                        )
                if not ok and entry_hard:
                    return False, proxy_url, reason
            except Exception as exc:
                if log_callback:
                    log_callback(f"[*] 入口检测跳过 {host}: {exc}")
                _entry_host_quality_cache[host] = (True, f"入口查询失败已忽略: {exc}", "")

    exit_info = None
    if check_exit:
        try:
            exit_info = probe_proxy_with_ippure(proxy_url, quality_api=quality_api, timeout=15)
            ok, reason, summary = evaluate_proxy_quality(exit_info, c, stage="exit")
            if log_callback:
                mark = "[+]" if ok else "[-]"
                log_callback(f"{mark} 出口检测 {summary} | {reason}")
            if not ok:
                return False, proxy_url, reason
        except Exception as exc:
            if log_callback:
                log_callback(f"[-] 出口 IPPure 检测失败 {hostport}: {exc}")
            return False, proxy_url, f"出口检测异常: {exc}"
    else:
        if log_callback:
            log_callback("[!] 出口检测已关闭，无法确认是否家宽，不建议用于注册")

    # Attach last exit meta for logging (not part of public return contract)
    detail = "ok"
    if isinstance(exit_info, dict):
        exit_ip = str(exit_info.get("ip") or "").strip()
        exit_org = str(
            exit_info.get("asOrganization")
            or exit_info.get("org")
            or exit_info.get("isp")
            or ""
        ).strip()
        res = exit_info.get("isResidential")
        fraud = exit_info.get("fraudScore")
        detail = (
            f"ok | 入口网关={host}:{port} → 出口家宽={exit_ip or '?'} "
            f"org={exit_org or '?'} residential={res} fraud={fraud if fraud is not None else '?'}"
        )
        try:
            c["_last_proxy_exit"] = {
                "gateway": hostport,
                "exit_ip": exit_ip,
                "exit_org": exit_org,
                "isResidential": res,
                "fraudScore": fraud,
                "countryCode": exit_info.get("countryCode"),
            }
        except Exception:
            pass
    return True, proxy_url, detail


def fetch_cliproxy_white_proxy(cfg=None, log_callback=None) -> str:
    """Call Cliproxy white API, quality-check via IPPure, return http://ip:port.

    Cliproxy:
      https://api.cliproxy.io/white/api?region=US&num=5&time=10&format=n&type=txt
    IPPure (through proxy):
      https://my.ippure.com/v1/info
    """
    c = cfg if isinstance(cfg, dict) else config
    region = str(c.get("proxy_country", "US") or "US").strip() or "US"
    if region.upper() == "RAND":
        region = "Rand"
    duration = str(c.get("proxy_duration", "10") or "10").strip()
    if duration.lower().startswith("t-"):
        duration = duration[2:]
    duration = "".join(ch for ch in duration if ch.isdigit()) or "10"
    fmt = str(c.get("proxy_api_format", "n") or "n").strip() or "n"
    typ = str(c.get("proxy_api_type", "txt") or "txt").strip() or "txt"
    # Quality check is ON by default; env GROK_FORCE_PROXY_QUALITY=1 hard-forces it.
    quality_on = bool(c.get("proxy_quality_check", True))
    if os.environ.get("GROK_FORCE_PROXY_QUALITY", "1").strip() in ("1", "true", "TRUE", "yes", "YES"):
        quality_on = True
    max_tries = int(c.get("proxy_quality_max_tries", 8) or 8)
    batch = int(c.get("proxy_api_num", 5) or 5)
    if quality_on:
        batch = max(batch, 3)
    check_entry = bool(c.get("proxy_check_entry_host", False))
    check_exit = bool(c.get("proxy_check_exit_ippure", True))
    if log_callback:
        log_callback(
            f"[*] 代理质量检测: {'开启' if quality_on else '关闭'} "
            f"(入口备注={'开' if check_entry else '关'} / "
            f"出口IPPure={'开' if check_exit else '关'}；"
            f"Cliproxy 以出口家宽为准，同网关不同端口=不同出口)"
        )

    tested = 0
    last_err = ""
    # Track rejected host:port only — same gateway host can have good/bad exits per port.
    rejected_ports = set()
    for attempt in range(1, max_tries + 1):
        url = _cliproxy_build_url(c, region, batch, duration, fmt, typ)
        if log_callback:
            log_callback(
                f"[*] 请求 Cliproxy 白名单 IP: region={region} time={duration}m "
                f"num={batch} (第{attempt}/{max_tries}批)"
            )
        try:
            resp = requests.get(url, timeout=20)
            text = (resp.text or "").strip()
            if resp.status_code >= 400:
                raise RuntimeError(f"Cliproxy API HTTP {resp.status_code}: {text[:200]}")
            if not text:
                raise RuntimeError("Cliproxy API 返回为空")
        except Exception as exc:
            last_err = str(exc)
            if log_callback:
                log_callback(f"[!] Cliproxy 提取失败: {exc}")
            time.sleep(1)
            continue

        hostports = _parse_proxy_hostports(text)
        if not hostports:
            last_err = f"无法解析 IP: {text[:160]}"
            if log_callback:
                log_callback(f"[!] {last_err}")
            continue

        unique_hosts = sorted({hp.rsplit(":", 1)[0] for hp in hostports})
        fresh = [hp for hp in hostports if hp not in rejected_ports]
        if log_callback:
            log_callback(
                f"[*] 本批 {len(hostports)} 条，网关入口 {len(unique_hosts)} 个: "
                f"{', '.join(unique_hosts[:8])}{'...' if len(unique_hosts) > 8 else ''}；"
                f"待测端口 {len(fresh)}"
            )
            if len(unique_hosts) == 1:
                log_callback(
                    f"[*] 提示: 同一入口 {unique_hosts[0]} 的不同端口通常对应不同出口家宽，"
                    "将逐端口做出口 IPPure 检测"
                )
        if not fresh:
            if log_callback:
                log_callback("[*] 本批端口均已测过不合格，继续下一批")
            continue

        for hp in fresh:
            tested += 1
            if not quality_on:
                proxy_url = f"http://{hp}"
                if log_callback:
                    log_callback(
                        f"[!] 警告: 质量检测已关闭，直接使用 {hp}（未验证出口家宽）"
                    )
                return proxy_url
            ok, proxy_url, reason = quality_check_cliproxy_hostport(
                hp, c, log_callback=log_callback
            )
            if ok:
                if log_callback:
                    # reason already embeds gateway→exit when quality ran
                    if reason and reason != "ok" and "出口" in str(reason):
                        log_callback(f"[+] 选用合格代理: {hp}")
                        log_callback(f"[+] {reason}")
                        log_callback(
                            "[*] 说明: 日志里的 107.x/128.x 是 Cliproxy「入口网关」，"
                            "网站/Cloudflare 看到的是上面的「出口家宽 IP」，不是机房 IP。"
                        )
                    else:
                        log_callback(f"[+] 选用合格代理: {hp}（出口检测通过）")
                return proxy_url
            last_err = reason
            rejected_ports.add(hp)
            continue

    raise RuntimeError(
        f"未找到合格代理（已检测约 {tested} 个端口出口）。最后原因: {last_err or '无'}。\n"
        f"Cliproxy white 返回的是共享网关:端口；真实质量看「经代理出口」。\n"
        f"可调高 region=US、proxy_quality_max_tries，或放宽 fraud / 国家匹配。"
    )


def build_whitelist_proxy_url(cfg=None) -> str:
    """Build whitelist proxy URL with country/region and delimiter.

    Typical vendor username: user-region-US  (delimiter="-")
    Full URL: http://user-region-US:pass@host:port
    """
    c = cfg if isinstance(cfg, dict) else config
    host = str(c.get("proxy_host", "") or "").strip()
    port = str(c.get("proxy_port", "") or "").strip()
    user = str(c.get("proxy_user", "") or "").strip()
    password = str(c.get("proxy_pass", "") or "")
    country = str(c.get("proxy_country", "US") or "US").strip().upper()
    delim = str(c.get("proxy_delimiter", "-") or "-")
    duration = str(c.get("proxy_duration", "120") or "120").strip()
    # allow "120" or "t-120"
    if duration.lower().startswith("t-"):
        duration = duration[2:]
    duration = "".join(ch for ch in duration if ch.isdigit()) or "120"
    session = str(c.get("proxy_session", "") or "").strip()
    if not session:
        session = secrets.token_hex(4)
    template = str(
        c.get("proxy_user_template", "{user}{delimiter}region{delimiter}{country}")
        or "{user}{delimiter}region{delimiter}{country}"
    ).strip()
    if not host or not port:
        return ""
    username = template.format(
        user=user,
        pass_=password,
        password=password,
        host=host,
        port=port,
        country=country,
        delimiter=delim,
        session=session,
        duration=duration,
        t=f"t-{duration}",
    )
    # If no username, allow IP-whitelist-only host:port
    if username:
        auth = f"{_proxy_quote(username)}:{_proxy_quote(password)}@"
    else:
        auth = ""
    return f"http://{auth}{host}:{port}"


def resolve_airport_proxy(cfg=None, log_callback=None) -> str:
    """Local Mihomo mixed port backed by airport subscription (hysteria2/vless)."""
    c = cfg if isinstance(cfg, dict) else config
    url = str(
        c.get("proxy_airport_url")
        or c.get("proxy")
        or "http://127.0.0.1:7893"
    ).strip()
    if not url:
        url = "http://127.0.0.1:7893"
    if log_callback:
        log_callback(
            f"[*] 代理模式: 机场(Mihomo) | {url} "
            f"（订阅节点组 REGISTER-RESIDENTIAL，不是 Cliproxy ip:port）"
        )
    return url


def resolve_runtime_proxy(cfg=None, log_callback=None, fetch_live=True) -> str:
    """Resolve effective proxy URL from mode + API / whitelist group / custom."""
    c = cfg if isinstance(cfg, dict) else config
    mode = str(c.get("proxy_mode", "") or "").strip().lower()
    custom = str(c.get("proxy", "") or "").strip()
    if not mode:
        return custom
    if mode in ("direct", "none", "off"):
        return ""
    if mode in ("airport", "mihomo", "kunlun", "airport_mihomo"):
        return resolve_airport_proxy(c, log_callback=log_callback)
    if mode in ("cliproxy_white", "cliproxy", "white_api", "api"):
        if not fetch_live:
            return custom
        return fetch_cliproxy_white_proxy(c, log_callback=log_callback)
    if mode in ("whitelist", "group", "proxy_group"):
        return build_whitelist_proxy_url(c)
    return custom


def apply_resolved_proxy_to_config(log_callback=None, fetch_live=True):
    """Write resolved proxy into config['proxy'] for existing get_proxies/browser code."""
    global config
    resolved = resolve_runtime_proxy(config, log_callback=log_callback, fetch_live=fetch_live)
    config["proxy"] = resolved
    return resolved


def get_configured_proxy():
    # After job start, cliproxy mode stores resolved ip:port into config['proxy'].
    # Do not re-fetch API on every request.
    mode = str(config.get("proxy_mode", "") or "").strip().lower()
    if mode in ("cliproxy_white", "cliproxy", "white_api", "api"):
        return str(config.get("proxy", "") or "").strip()
    if mode in ("airport", "mihomo", "kunlun", "airport_mihomo"):
        return str(
            config.get("proxy")
            or config.get("proxy_airport_url")
            or "http://127.0.0.1:7893"
        ).strip()
    if mode in ("whitelist", "group", "proxy_group"):
        return build_whitelist_proxy_url(config)
    if mode in ("direct", "none", "off"):
        return ""
    if mode:
        return str(config.get("proxy", "") or "").strip()
    return str(config.get("proxy", "") or "").strip()


def get_proxies():
    proxy = get_configured_proxy()
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def _parse_proxy_url(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return urllib.parse.urlsplit(raw)
    except Exception:
        return None


def _safe_proxy_port(parsed):
    try:
        return parsed.port
    except Exception:
        return None


def _proxy_has_auth(proxy):
    parsed = _parse_proxy_url(proxy)
    return bool(parsed and parsed.hostname and (parsed.username is not None or parsed.password is not None))


def _strip_proxy_auth(proxy):
    raw = str(proxy or "").strip()
    parsed = _parse_proxy_url(raw)
    if not parsed or not parsed.hostname:
        return raw
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = _safe_proxy_port(parsed)
    netloc = f"{host}:{port}" if port else host
    stripped = urllib.parse.urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))
    if "://" not in raw:
        return stripped.split("://", 1)[1]
    return stripped


def _proxy_endpoint_terms(proxy=None):
    parsed = _parse_proxy_url(proxy or get_configured_proxy())
    if not parsed or not parsed.hostname:
        return []
    terms = [parsed.hostname]
    port = _safe_proxy_port(parsed)
    if port:
        terms.append(f"{parsed.hostname}:{port}")
        terms.append(f"port {port}")
    return [x.lower() for x in terms if x]


def is_proxy_connection_error(exc):
    if not get_configured_proxy():
        return False
    err = str(exc or "").lower()
    if not err:
        return False
    if any(x in err for x in ("proxy", "tunnel", "socks")):
        return True
    connect_markers = (
        "could not connect",
        "failed to connect",
        "connection refused",
        "connection reset",
        "connect error",
        "timed out",
        "timeout",
    )
    if any(x in err for x in connect_markers):
        terms = _proxy_endpoint_terms()
        if not terms or any(t in err for t in terms):
            return True
    return False


def page_has_proxy_error(page_obj):
    try:
        url = str(getattr(page_obj, "url", "") or "")
        title = str(page_obj.run_js("return document.title || ''") or "")
        body = str(page_obj.run_js("return document.body ? document.body.innerText.slice(0, 2000) : ''") or "")
    except Exception:
        return False
    text = f"{url}\n{title}\n{body}".lower()
    return any(
        marker in text
        for marker in (
            "err_proxy",
            "proxy connection failed",
            "proxy server",
            "proxy authentication",
            "tunnel connection failed",
            "无法连接到代理服务器",
            "代理服务器",
        )
    )


class _ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _proxy_recv_until_headers(sock, timeout=20, limit=65536):
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _proxy_relay(left, right, timeout=60):
    left.settimeout(timeout)
    right.settimeout(timeout)
    sockets = [left, right]
    while True:
        readable, _, _ = select.select(sockets, [], [], timeout)
        if not readable:
            return
        for sock in readable:
            data = sock.recv(65536)
            if not data:
                return
            peer = right if sock is left else left
            peer.sendall(data)


class _LocalAuthProxyBridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        bridge = self.server.bridge
        upstream = None
        try:
            initial = _proxy_recv_until_headers(self.request, timeout=bridge.timeout)
            if not initial:
                return
            first_line = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
            if first_line.upper().startswith("CONNECT "):
                target = first_line.split()[1]
                upstream = bridge.open_upstream()
                req = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
                if bridge.auth_header:
                    req.append(f"Proxy-Authorization: Basic {bridge.auth_header}")
                upstream.sendall(("\r\n".join(req) + "\r\n\r\n").encode("latin1"))
                response = _proxy_recv_until_headers(upstream, timeout=bridge.timeout)
                if response:
                    self.request.sendall(response)
                status = response.split(b"\r\n", 1)[0]
                if b" 200 " not in status:
                    return
                _proxy_relay(self.request, upstream, timeout=bridge.relay_timeout)
            else:
                upstream = bridge.open_upstream()
                upstream.sendall(bridge.inject_proxy_auth(initial))
                _proxy_relay(self.request, upstream, timeout=bridge.relay_timeout)
        except Exception:
            return
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


class LocalAuthProxyBridge:
    def __init__(self, proxy_url):
        parsed = _parse_proxy_url(proxy_url)
        if not parsed or not parsed.hostname:
            raise ValueError("认证代理地址格式无效")
        if (parsed.scheme or "http").lower() not in ("http", "https"):
            raise ValueError("Chromium 本地认证代理桥仅支持 http/https 上游代理")
        self.upstream_scheme = (parsed.scheme or "http").lower()
        self.upstream_host = parsed.hostname
        self.upstream_port = _safe_proxy_port(parsed) or (443 if self.upstream_scheme == "https" else 80)
        username = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        raw_auth = f"{username}:{password}".encode("utf-8")
        self.auth_header = base64.b64encode(raw_auth).decode("ascii") if (username or password) else ""
        self.timeout = 20
        self.relay_timeout = 90
        self.server = None
        self.thread = None
        self.local_proxy = ""

    def open_upstream(self):
        sock = socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)
        if self.upstream_scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.upstream_host)
        sock.settimeout(self.timeout)
        return sock

    def inject_proxy_auth(self, data):
        if not self.auth_header or b"\r\n\r\n" not in data:
            return data
        if b"\r\nproxy-authorization:" in data.lower():
            return data
        head, body = data.split(b"\r\n\r\n", 1)
        auth_line = f"Proxy-Authorization: Basic {self.auth_header}".encode("latin1")
        return head + b"\r\n" + auth_line + b"\r\n\r\n" + body

    def start(self):
        self.server = _ReusableThreadingTCPServer(("127.0.0.1", 0), _LocalAuthProxyBridgeHandler)
        self.server.bridge = self
        port = self.server.server_address[1]
        self.local_proxy = f"http://127.0.0.1:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.local_proxy

    def stop(self):
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        self.server = None
        self.thread = None
        self.local_proxy = ""


def stop_browser_proxy_bridge():
    global browser_proxy_bridge
    if browser_proxy_bridge is not None:
        try:
            browser_proxy_bridge.stop()
        except Exception:
            pass
    browser_proxy_bridge = None


def prepare_browser_proxy(use_proxy=True, log_callback=None):
    proxy = get_configured_proxy()
    if not use_proxy or not proxy:
        return "", None
    if _proxy_has_auth(proxy):
        parsed = _parse_proxy_url(proxy)
        scheme = (parsed.scheme or "http").lower() if parsed else ""
        if scheme in ("http", "https"):
            bridge = LocalAuthProxyBridge(proxy)
            browser_proxy = bridge.start()
            if log_callback:
                log_callback(f"[*] 已为 Chromium 启动本地认证代理桥: {browser_proxy}")
            return browser_proxy, bridge
        stripped = _strip_proxy_auth(proxy)
        if log_callback:
            log_callback("[!] Chromium 暂不直接支持该认证代理协议，已使用去认证代理地址，失败将回退直连")
        return stripped, None
    return proxy, None


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    """按配置轮换选择 Cloudflare 临时邮箱域名。"""
    global _cf_domain_index
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    domain = domains[_cf_domain_index % len(domains)]
    _cf_domain_index += 1
    return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口并兼容 admin 创建模式。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = {"Content-Type": "application/json"}
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    token_file = resolve_grok2api_local_token_file()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip()
    if not pool_name:
        pool_name = "ssoBasic"
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    data = {}
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    pool = data.get(pool_name)
    if not isinstance(pool, list):
        pool = []
    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(_normalize_sso_token(item))
        elif isinstance(item, dict):
            existing.add(_normalize_sso_token(item.get("token", "")))
    if token in existing:
        if log_callback:
            log_callback(f"[*] 号池本地已存在 token: {pool_name}")
        return True
    entry = {"token": token, "tags": ["auto-register"], "note": email}
    pool.append(entry)
    data[pool_name] = pool
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if log_callback:
        log_callback(f"[+] 已写入号池本地: {pool_name} ({token_file})")
    return True


def get_grok2api_remote_api_bases(base):
    """生成号池管理 API 候选根路径。

    参数:
      - base str: 用户配置的号池远端地址

    返回:
      - list[str]: 依次尝试的管理 API 根路径
    """
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    lower = normalized.lower()
    candidates = [normalized]
    if lower.endswith("/admin/api"):
        return candidates
    if lower.endswith("/admin"):
        candidates.append(f"{normalized}/api")
    else:
        candidates.append(f"{normalized}/admin/api")
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def add_token_to_grok2api_remote_pool(
    raw_token,
    email="",
    log_callback=None,
    *,
    max_http_tries=None,
    http_timeout=None,
):
    token = _normalize_sso_token(raw_token)
    if not token:
        return False
    base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
    app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
    pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
    if not base or not app_key:
        if log_callback:
            log_callback("[Debug] 号池远端未配置 base/app_key，跳过")
        return False
    headers = {"Content-Type": "application/json"}
    query = {"app_key": app_key}
    pool_map = {"ssoBasic": "basic", "ssoSuper": "super"}
    remote_pool = pool_map.get(pool_name, "basic")
    api_bases = get_grok2api_remote_api_bases(base)
    add_errors = []
    # 优先使用 add 接口，避免全量覆盖远端池
    add_payload = {"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]}
    # Retry on 502/503 when pool API is temporarily overloaded (low-RAM hosts + Chromium)
    if max_http_tries is None:
        max_http_tries = 4
    try:
        max_http_tries = int(max_http_tries)
    except (TypeError, ValueError):
        max_http_tries = 4
    max_http_tries = max(1, min(max_http_tries, 10))
    if http_timeout is None:
        http_timeout = 30
    try:
        http_timeout = float(http_timeout)
    except (TypeError, ValueError):
        http_timeout = 30.0
    http_timeout = max(3.0, min(http_timeout, 60.0))
    for api_base in api_bases:
        endpoint = f"{api_base}/tokens/add"
        for attempt in range(1, max_http_tries + 1):
            try:
                resp_add = http_post(
                    endpoint,
                    headers=headers,
                    params=query,
                    json=add_payload,
                    timeout=http_timeout,
                )
                resp_add.raise_for_status()
                if log_callback:
                    log_callback(f"[+] 已写入号池远端: {pool_name} ({endpoint})")
                return True
            except Exception as add_exc:
                err_s = str(add_exc)
                add_errors.append(f"{endpoint}#{attempt}: {add_exc}")
                status_code = getattr(getattr(add_exc, "response", None), "status_code", None)
                if status_code is None:
                    # DummyResponse / requests-style raise_for_status strings
                    for code in (502, 503, 504, 429):
                        if str(code) in err_s:
                            status_code = code
                            break
                retryable = status_code in (429, 502, 503, 504) or any(
                    x in err_s.lower() for x in ("502", "503", "504", "429", "bad gateway", "timeout", "connection")
                )
                if retryable and attempt < max_http_tries:
                    wait = min(2 * attempt, 8)
                    if log_callback:
                        log_callback(
                            f"[Debug] 号池 /tokens/add 暂失败 ({err_s[:120]})，"
                            f"{wait}s 后重试 {attempt}/{max_http_tries}"
                        )
                    time.sleep(wait)
                    continue
                # Non-retryable for this base (e.g. 404 wrong path) → next api_base
                break
    if log_callback:
        log_callback(f"[Debug] /tokens/add 写入失败，尝试 /tokens 全量模式: {'; '.join(add_errors)}")

    # 兜底：旧版全量保存接口
    current = {}
    fallback_base = api_bases[0] if api_bases else base
    for api_base in api_bases or [base]:
        try:
            resp = http_get(
                f"{api_base}/tokens",
                headers=headers,
                params=query,
                timeout=min(http_timeout, 20),
            )
            if resp.status_code == 200:
                payload = resp.json()
                current = payload.get("tokens", {}) if isinstance(payload, dict) else {}
                fallback_base = api_base
                break
        except Exception:
            continue
    if not isinstance(current, dict):
        current = {}
    pool = current.get(pool_name)
    if not isinstance(pool, list):
        pool = []
    existing = set()
    for item in pool:
        if isinstance(item, str):
            existing.add(_normalize_sso_token(item))
        elif isinstance(item, dict):
            existing.add(_normalize_sso_token(item.get("token", "")))
    if token not in existing:
        pool.append({"token": token, "tags": ["auto-register"], "note": email})
    current[pool_name] = pool
    save_errors = []
    save_bases = []
    for item in [fallback_base, *(api_bases or [base])]:
        if item and item not in save_bases:
            save_bases.append(item)
    for api_base in save_bases:
        try:
            resp2 = http_post(
                f"{api_base}/tokens",
                headers=headers,
                params=query,
                json=current,
                timeout=http_timeout,
            )
            resp2.raise_for_status()
            if log_callback:
                log_callback(f"[+] 已写入号池远端: {pool_name} ({api_base}/tokens)")
            return True
        except Exception as save_exc:
            save_errors.append(f"{api_base}/tokens: {save_exc}")
    raise RuntimeError(f"号池远端 /tokens 全量模式写入失败: {'; '.join(save_errors)}")


def add_token_to_grok2api_pools(
    raw_token,
    email="",
    log_callback=None,
    *,
    max_http_tries=None,
    http_timeout=None,
):
    if config.get("grok2api_auto_add_local", True):
        try:
            add_token_to_grok2api_local_pool(raw_token, email=email, log_callback=log_callback)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入号池本地失败: {exc}")
    if config.get("grok2api_auto_add_remote", False):
        try:
            add_token_to_grok2api_remote_pool(
                raw_token,
                email=email,
                log_callback=log_callback,
                max_http_tries=max_http_tries,
                http_timeout=http_timeout,
            )
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 写入号池远端失败: {exc}")


def _config_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on", "y"):
        return True
    if s in ("0", "false", "no", "off", "n", ""):
        return False
    return bool(default)


def _post_success_worker_loop():
    """Background worker: NSFW / g2a 入池 / CPA mint（不阻塞下一号浏览器注册）。"""
    while True:
        job = _post_success_q.get()
        if job is None:
            _post_success_q.task_done()
            break
        log = job.get("log") or (lambda m: print(m, flush=True))
        email = job.get("email") or ""
        sso = job.get("sso") or ""
        try:
            log(f"[bg] 后处理开始: {email}")
            if job.get("do_nsfw"):
                log(f"[bg] 开启 NSFW: {email}")
                try:
                    nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log)
                    if nsfw_ok:
                        log(f"[bg] NSFW 开启成功: {nsfw_msg}")
                    else:
                        log(f"[bg] NSFW 未开启: {nsfw_msg}")
                except Exception as nsfw_exc:
                    log(f"[bg] NSFW 异常: {nsfw_exc}")
            if job.get("do_g2a"):
                try:
                    bg_tries = int(config.get("grok2api_bg_max_http_tries", 6) or 6)
                except (TypeError, ValueError):
                    bg_tries = 6
                try:
                    bg_timeout = float(config.get("grok2api_bg_http_timeout_sec", 15) or 15)
                except (TypeError, ValueError):
                    bg_timeout = 15.0
                add_token_to_grok2api_pools(
                    sso,
                    email=email,
                    log_callback=log,
                    max_http_tries=bg_tries,
                    http_timeout=bg_timeout,
                )
            if job.get("do_cpa") and config.get("cpa_export_enabled", True):
                try:
                    export_cpa_after_success(
                        email,
                        job.get("password") or "",
                        sso,
                        page=None,
                        cookies=job.get("cookies") or [],
                        log_callback=log,
                    )
                except Exception as cpa_exc:
                    log(f"[bg] CPA 导出未成功: {cpa_exc}")
            log(f"[bg] 后处理完成: {email}")
        except Exception as exc:
            log(f"[bg] 后处理异常 {email}: {exc}")
        finally:
            global _post_success_pending
            with _post_success_pending_lock:
                _post_success_pending = max(0, _post_success_pending - 1)
            _post_success_q.task_done()


def ensure_post_success_worker(log_callback=None):
    global _post_success_worker_started
    with _post_success_worker_lock:
        if _post_success_worker_started:
            return
        t = threading.Thread(
            target=_post_success_worker_loop,
            name="post-success-worker",
            daemon=True,
        )
        t.start()
        _post_success_worker_started = True
        if log_callback:
            log_callback("[*] 后处理后台线程已启动（g2a/CPA/NSFW 可异步）")


def wait_post_success_queue(timeout=300, log_callback=None):
    """Wait until background post-success jobs drain (call at job end)."""
    log = log_callback or (lambda m: None)
    deadline = time.time() + max(0.0, float(timeout or 0))
    last_log = 0.0
    while True:
        with _post_success_pending_lock:
            pending = _post_success_pending
        unfinished = getattr(_post_success_q, "unfinished_tasks", 0)
        if pending <= 0 and unfinished <= 0:
            log("[*] 后处理队列已清空")
            return True
        if time.time() >= deadline:
            log(f"[!] 后处理队列仍有约 {pending} 个未完成（超时返回，后台会继续）")
            return False
        now = time.time()
        # Log every 10s only — avoid flooding Web console every second
        if pending > 0 and (now - last_log) >= 10.0:
            log(f"[*] 等待后处理队列… 剩余约 {pending}（CPA/NSFW 后台中）")
            last_log = now
        time.sleep(1.0)


def schedule_post_registration(
    email, password, sso, page=None, cookies=None, log_callback=None
):
    """After sso saved: NSFW + g2a + CPA. Prefer async so next account starts sooner.

    - enable_nsfw + nsfw_async=False → NSFW 同步（你需要立刻开时）
    - post_success_async=True → g2a / CPA（及 async NSFW）进后台
    - cookies: optional pre-exported jar (hybrid path has no live page)
    """
    log = log_callback or (lambda m: print(m, flush=True))
    out_cookies = []
    if isinstance(cookies, list) and cookies:
        out_cookies = [c for c in cookies if isinstance(c, dict)]
        if out_cookies:
            log(f"[cpa] 使用调用方 cookie {len(out_cookies)} 条（供后台 OIDC mint）")
    try:
        import cpa_export

        if not out_cookies and page is not None:
            out_cookies = cpa_export.export_cookies_from_page(page) or []
            if out_cookies:
                log(f"[cpa] 已预导出 cookie {len(out_cookies)} 条（供后台 OIDC mint）")
    except Exception as exc:
        log(f"[cpa] cookie 预导出失败(仍可用 sso): {exc}")
        if not out_cookies:
            out_cookies = []
    cookies = out_cookies

    do_nsfw = bool(config.get("enable_nsfw", True))
    nsfw_async = _config_bool(config.get("nsfw_async", True), default=True)
    post_async = _config_bool(config.get("post_success_async", True), default=True)
    do_g2a = bool(config.get("grok2api_auto_add_remote") or config.get("grok2api_auto_add_local"))
    do_cpa = bool(config.get("cpa_export_enabled", True))

    # Optional sync NSFW before queueing the rest
    if do_nsfw and not nsfw_async:
        log("[*] 6. 开启 NSFW（同步）")
        try:
            nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log)
            if nsfw_ok:
                log(f"[+] NSFW 开启成功: {nsfw_msg}")
            else:
                log(f"[!] NSFW 未开启，继续: {nsfw_msg}")
        except Exception as nsfw_exc:
            log(f"[!] NSFW 异常，继续: {nsfw_exc}")
        do_nsfw = False  # already done

    need_queue = do_g2a or do_cpa or do_nsfw
    if not need_queue:
        return {"async": False, "queued": False}

    if not post_async:
        # Fully synchronous path (old behavior)
        if do_nsfw:
            log("[*] 6. 开启 NSFW")
            try:
                nsfw_ok, nsfw_msg = enable_nsfw_for_token(sso, log_callback=log)
                if nsfw_ok:
                    log(f"[+] NSFW 开启成功: {nsfw_msg}")
                else:
                    log(f"[!] NSFW 未开启，继续: {nsfw_msg}")
            except Exception as nsfw_exc:
                log(f"[!] NSFW 异常，继续: {nsfw_exc}")
        if do_g2a:
            add_token_to_grok2api_pools(sso, email=email, log_callback=log)
        if do_cpa:
            try:
                export_cpa_after_success(
                    email,
                    password or "",
                    sso,
                    page=None,
                    cookies=cookies,
                    log_callback=log,
                )
            except Exception as cpa_exc:
                log(f"[cpa] 导出未成功（SSO 仍已保存）: {cpa_exc}")
        return {"async": False, "queued": False}

    ensure_post_success_worker(log_callback=log)
    global _post_success_pending
    with _post_success_pending_lock:
        _post_success_pending += 1
    _post_success_q.put(
        {
            "email": email,
            "password": password or "",
            "sso": sso,
            "cookies": cookies,
            "do_nsfw": do_nsfw,
            "do_g2a": do_g2a,
            "do_cpa": do_cpa,
            "log": log,
        }
    )
    parts = []
    if do_nsfw:
        parts.append("NSFW")
    if do_g2a:
        parts.append("g2a入池")
    if do_cpa:
        parts.append("CPA")
    log(f"[*] 后处理已入队后台: {'+'.join(parts) or '无'} → 立即开下一号")
    return {"async": True, "queued": True}


def export_cpa_after_success(email, password, sso, page=None, cookies=None, log_callback=None):
    """After successful registration: mint OIDC for free Grok 4.5 (CPA / Build path).

    SSO alone powers web pool models (4.20/4.3). Free grok-4.5 needs OIDC
    via accounts.x.ai device-flow → cpa_auths/xai-*.json → CLIProxyAPI.
    """
    log = log_callback or (lambda m: print(m, flush=True))
    if not config.get("cpa_export_enabled", True):
        log("[cpa] export disabled, skip")
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if not email:
        log("[cpa] 缺少 email，跳过 CPA 导出")
        return {"ok": False, "error": "missing email"}
    # protocol path only needs sso; password needed for browser fallback
    if not password and not sso:
        log("[cpa] 缺少 password/sso，跳过 CPA 导出")
        return {"ok": False, "error": "missing password/sso"}
    try:
        import cpa_export
    except Exception as exc:
        log(f"[cpa] 导入 cpa_export 失败: {exc}")
        return {"ok": False, "error": f"import: {exc}"}

    if cookies is None:
        cookies = []
        try:
            cookies = cpa_export.export_cookies_from_page(page) if page is not None else []
        except Exception as exc:
            log(f"[cpa] cookie 导出失败，继续用 sso/协议 mint: {exc}")
            cookies = []
    if cookies:
        log(f"[cpa] 已导出 cookie {len(cookies)} 条供 OIDC mint")

    cpa_cfg = dict(config)
    # Prefer airport/local proxy for mint if cpa_proxy empty
    if not str(cpa_cfg.get("cpa_proxy") or "").strip():
        mode = str(cpa_cfg.get("proxy_mode") or "").strip().lower()
        if mode in ("airport", "mihomo", "kunlun", "airport_mihomo"):
            cpa_cfg["cpa_proxy"] = str(
                cpa_cfg.get("proxy_airport_url")
                or cpa_cfg.get("proxy")
                or "http://127.0.0.1:7893"
            ).strip()
        elif str(cpa_cfg.get("proxy") or "").strip():
            cpa_cfg["cpa_proxy"] = str(cpa_cfg.get("proxy")).strip()
    if _config_bool(config.get("cpa_gui_close_mint_browser", True), default=True):
        cpa_cfg["cpa_mint_browser_reuse"] = False

    with _cpa_export_lock:
        # Space out device-code mints — auth.x.ai rate-limits bursts (429/slow_down)
        global _cpa_last_mint_ts
        try:
            gap = float(config.get("cpa_mint_gap_sec", 25) or 0)
        except (TypeError, ValueError):
            gap = 25.0
        if gap > 0 and _cpa_last_mint_ts > 0:
            wait = gap - (time.time() - _cpa_last_mint_ts)
            if wait > 0.5:
                log(f"[cpa] mint 间隔保护: 等待 {wait:.1f}s (gap={gap}s)")
                time.sleep(wait)
        try:
            result = cpa_export.export_cpa_xai_for_account(
                email,
                password or "",
                page=page,
                cookies=cookies,
                sso=sso,
                config=cpa_cfg,
                log_callback=log,
            )
        except Exception as exc:
            _cpa_last_mint_ts = time.time()
            log(f"[cpa] CPA 导出异常: {exc}")
            if config.get("cpa_mint_required", False):
                raise
            return {"ok": False, "error": str(exc)}
        _cpa_last_mint_ts = time.time()

    if result.get("ok"):
        log(f"[cpa] CPA/OIDC 已导出: {result.get('path')}")
        if result.get("probe"):
            log(f"[cpa] probe: {result.get('probe')}")
    else:
        log(f"[cpa] CPA 导出失败: {result.get('error') or result}")
    return result


def apply_browser_proxy_option(options, proxy):
    if not proxy:
        return
    if hasattr(options, "set_proxy"):
        try:
            options.set_proxy(proxy)
            return
        except Exception:
            pass
    if not hasattr(options, "set_argument"):
        raise AttributeError("当前 DrissionPage ChromiumOptions 不支持设置浏览器代理")
    try:
        options.set_argument(f"--proxy-server={proxy}")
    except TypeError:
        options.set_argument("--proxy-server", proxy)


def _set_browser_argument(options, arg, value=None):
    if not hasattr(options, "set_argument"):
        return
    try:
        if value is None:
            options.set_argument(arg)
        else:
            options.set_argument(arg, value)
    except TypeError:
        if value is None:
            options.set_argument(arg)
        else:
            options.set_argument(f"{arg}={value}")


def _detect_linux_browser_path():
    """Prefer real Chromium binaries over snap wrapper (/snap/bin/chromium).

    DrissionPage fails to connect CDP when launched via the snap wrapper script,
    but works with .../usr/lib/chromium-browser/chrome.
    """
    env = os.environ.get("GROK_REGISTER_BROWSER_PATH", "").strip()
    # If user pointed at snap wrapper, rewrite to real binary when present.
    snap_real = "/snap/chromium/current/usr/lib/chromium-browser/chrome"
    if env in ("/snap/bin/chromium", "chromium") and os.path.exists(snap_real):
        env = snap_real
    candidates = [
        env,
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        snap_real,
        # versioned snap fallback
        "/snap/chromium/current/usr/lib/chromium-browser/chrome",
        "/snap/bin/chromium",
    ]
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return ""


def _linux_display_socket_ok(display: str = "") -> bool:
    display = (display or os.environ.get("DISPLAY", "") or "").strip()
    if not display:
        return False
    try:
        num = display.split(":")[-1].split(".")[0]
        if not num.isdigit():
            return False
        return os.path.exists(f"/tmp/.X11-unix/X{num}")
    except Exception:
        return False


def _ensure_xvfb(log_callback=None) -> bool:
    """Ensure Xvfb is up for DISPLAY (default :99). Returns True if socket ready."""
    if sys.platform == "win32":
        return False
    display = os.environ.get("DISPLAY", "").strip() or ":99"
    os.environ["DISPLAY"] = display
    if _linux_display_socket_ok(display):
        return True
    # Only manage classic :N displays
    try:
        num = display.split(":")[-1].split(".")[0]
        if not num.isdigit():
            return False
    except Exception:
        return False
    if log_callback:
        log_callback(f"[*] DISPLAY={display} 无 X socket，尝试启动 Xvfb...")
    try:
        import subprocess

        log_path = "/var/log/xvfb-99.log" if num == "99" else f"/tmp/xvfb-{num}.log"
        with open(log_path, "a", encoding="utf-8", errors="ignore") as lf:
            subprocess.Popen(
                [
                    "Xvfb",
                    display,
                    "-screen",
                    "0",
                    "1920x1080x24",
                    "-ac",
                    "+extension",
                    "GLX",
                    "+render",
                    "-noreset",
                ],
                stdout=lf,
                stderr=lf,
                start_new_session=True,
            )
        for _ in range(20):
            time.sleep(0.25)
            if _linux_display_socket_ok(display):
                if log_callback:
                    log_callback(f"[+] Xvfb 已就绪 DISPLAY={display}")
                return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[!] 启动 Xvfb 失败: {exc}")
    if log_callback:
        log_callback(f"[!] Xvfb 仍不可用 DISPLAY={display}")
    return False


def _linux_should_headless():
    """Prefer headed Chromium under Xvfb when DISPLAY works.

    Pure headless is heavily flagged by Cloudflare on accounts.x.ai.
    GROK_REGISTER_HEADLESS=1 forces headless.
    GROK_REGISTER_HEADLESS=0 prefers headed, but falls back to headless if no X.
    """
    flag = os.environ.get("GROK_REGISTER_HEADLESS", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    # Prefer headed when X is available (auto-start Xvfb if needed)
    if _ensure_xvfb():
        if flag in ("0", "false", "no", "off"):
            return False
        return False
    # No display: must headless even if user asked for headed
    return True


def _pick_free_local_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        try:
            sock.close()
        except Exception:
            pass


def create_browser_options(browser_proxy="", force_headless=None):
    options = ChromiumOptions()
    # DrissionPage auto_port() may leave address empty on some versions;
    # always pin an explicit local debugging port.
    try:
        options.auto_port()
    except Exception:
        pass
    if not getattr(options, "address", None) or ":" not in str(options.address):
        port = _pick_free_local_port()
        if hasattr(options, "set_local_port"):
            options.set_local_port(port)
        else:
            try:
                options._address = f"127.0.0.1:{port}"
            except Exception:
                pass
    # Give Chromium more time to open remote-debugging port (snap is slow)
    try:
        options.set_timeouts(base=3, page_load=30, script=20)
    except TypeError:
        try:
            options.set_timeouts(base=3)
        except Exception:
            pass
    apply_browser_proxy_option(options, browser_proxy)
    if sys.platform != "win32":
        browser_path = _detect_linux_browser_path()
        if browser_path and hasattr(options, "set_browser_path"):
            try:
                options.set_browser_path(browser_path)
            except Exception:
                pass
        # Fresh user-data dir per launch avoids SingletonLock / zombie chrome conflicts
        base_data = os.environ.get("GROK_REGISTER_USER_DATA", "").strip() or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".chrome-data"
        )
        user_data = os.path.join(
            base_data, f"run-{os.getpid()}-{int(time.time())}-{secrets.token_hex(2)}"
        )
        try:
            os.makedirs(user_data, exist_ok=True)
            if hasattr(options, "set_user_data_path"):
                options.set_user_data_path(user_data)
            elif hasattr(options, "set_paths"):
                options.set_paths(user_data_path=user_data)
        except Exception:
            pass
        _set_browser_argument(options, "--no-sandbox")
        _set_browser_argument(options, "--disable-setuid-sandbox")
        _set_browser_argument(options, "--disable-dev-shm-usage")
        _set_browser_argument(options, "--no-first-run")
        _set_browser_argument(options, "--no-default-browser-check")
        _set_browser_argument(options, "--window-size=1280,900")
        # Snap + Xvfb: disable GPU to avoid ANGLE/XCB init failures
        _set_browser_argument(options, "--disable-gpu")
        _set_browser_argument(options, "--disable-software-rasterizer")
        _set_browser_argument(options, "--disable-features=TranslateUI,BlinkGenPropertyTrees")
        # Reduce automation fingerprints (helps Cloudflare / Turnstile)
        _set_browser_argument(options, "--disable-blink-features=AutomationControlled")
        _set_browser_argument(options, "--lang=en-US")
        # Note: do not pass invalid --excludeSwitches=... as a bare chromium flag
        try:
            if hasattr(options, "set_pref"):
                options.set_pref("credentials_enable_service", False)
                options.set_pref("profile.password_manager_enabled", False)
        except Exception:
            pass
        if force_headless is None:
            headless = _linux_should_headless()
        else:
            headless = bool(force_headless)
        if headless:
            try:
                if hasattr(options, "headless"):
                    options.headless(True)
            except Exception:
                pass
            _set_browser_argument(options, "--headless=new")
        if os.path.exists(EXTENSION_PATH) and not headless:
            options.add_extension(EXTENSION_PATH)
        return options
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.get(url, **request_kwargs)
    except Exception as exc:
        if request_kwargs.get("proxies") and is_proxy_connection_error(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.post(url, **request_kwargs)
    except Exception as exc:
        if request_kwargs.get("proxies") and is_proxy_connection_error(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_delete(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.delete(url, **request_kwargs)
    except Exception as exc:
        if request_kwargs.get("proxies") and is_proxy_connection_error(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.delete(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("鐢ㄦ埛鍋滄娉ㄥ唽")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = {"Authorization": f"Bearer {token}"}
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鍒涘缓閭澶辫触: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 鑾峰彇token澶辫触: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鑾峰彇閭欢璇︽儏澶辫触: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("鑾峰彇 YYDS token 澶辫触")
    print(f"[*] 宸插垱寤?YYDS 閭: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def get_email_provider():
    return config.get("email_provider", "duckmail")


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "mailnest":
        from email_providers import mailnest as mailnest_provider

        key = str(config.get("mailnest_api_key") or "").strip()
        project = str(config.get("mailnest_project_code") or "x-ai001").strip()
        address = mailnest_provider.buy_email(http_post, key, project)
        # MailNest 用邮箱本身当收信句柄，token 占位
        return address, "mailnest"
    if provider == "cloudmail":
        from email_providers import cloudmail as cloudmail_provider

        url = str(config.get("cloudmail_url") or "").rstrip("/")
        admin_email = str(
            config.get("cloudmail_admin_email")
            or os.environ.get("CLOUDMAIL_ADMIN_EMAIL")
            or ""
        ).strip()
        admin_password = str(
            config.get("cloudmail_password")
            or os.environ.get("CLOUDMAIL_PASSWORD")
            or ""
        ).strip()
        domains = [
            x.strip()
            for x in str(config.get("defaultDomains", "") or "").split(",")
            if x.strip()
        ]
        return cloudmail_provider.create_mailbox(
            http_post, url, admin_email, admin_password, domains
        )
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 兜底回退到 Mail.tm 风格
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("获取 DuckMail token 失败")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "mailnest":
        from email_providers import mailnest as mailnest_provider

        key = str(config.get("mailnest_api_key") or "").strip()
        return mailnest_provider.wait_for_code(
            http_post,
            key,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            raise_if_cancelled=raise_if_cancelled,
            sleep_with_cancel=sleep_with_cancel,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
        )
    if provider == "cloudmail":
        from email_providers import cloudmail as cloudmail_provider

        url = str(config.get("cloudmail_url") or "").rstrip("/")
        admin_email = str(
            config.get("cloudmail_admin_email")
            or os.environ.get("CLOUDMAIL_ADMIN_EMAIL")
            or ""
        ).strip()
        admin_password = str(
            config.get("cloudmail_password")
            or os.environ.get("CLOUDMAIL_PASSWORD")
            or ""
        ).strip()
        return cloudmail_provider.wait_for_code(
            http_post,
            http_delete,
            url,
            admin_email,
            admin_password,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            raise_if_cancelled=raise_if_cancelled,
            sleep_with_cancel=sleep_with_cancel,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    try:
        text = str(res.text or "")
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", log_callback=None):
    proxies = get_proxies()
    user_agent = get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": user_agent,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

browser = None
page = None
browser_proxy_bridge = None
browser_started_with_proxy = False


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=None, **kwargs):
    if state is None:
        state = tk.NORMAL if HAS_TK else "normal"
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


def _apply_browser_stealth(tab, log_callback=None):
    """Best-effort anti-automation patches after tab is ready."""
    if tab is None:
        return
    js = r"""
try {
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
} catch (e) {}
try {
  if (!window.chrome) { window.chrome = { runtime: {} }; }
} catch (e) {}
try {
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
} catch (e) {}
try {
  Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
} catch (e) {}
"""
    try:
        tab.run_js(js)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] stealth 脚本注入失败: {exc}")


def _browser_engine_name() -> str:
    """Preferred engine for hybrid token harvest. nodriver avoids CDP Runtime.enable leak."""
    try:
        eng = str((config or {}).get("browser_engine") or os.environ.get("GROK_BROWSER_ENGINE") or "nodriver")
    except Exception:
        eng = "nodriver"
    eng = eng.strip().lower()
    if eng in ("nd", "uc", "undetected", "nodriver"):
        return "nodriver"
    return "drission"


def start_browser(log_callback=None, use_proxy=True):
    global browser, page, browser_proxy_bridge, browser_started_with_proxy
    last_exc = None
    proxy_enabled = bool(use_proxy and get_configured_proxy())
    if sys.platform != "win32":
        # Bring up Xvfb early so headed mode can work
        _ensure_xvfb(log_callback=log_callback)

    # Reuse already-started browser (avoid double nodriver/Chromium launch)
    if browser is not None and page is not None:
        try:
            from browser.nodriver_backend import get_backend

            backend = get_backend()
            if backend is not None and backend.page is not None:
                if log_callback:
                    log_callback("[*] 复用已启动的 nodriver 浏览器")
                return browser, page
        except Exception:
            pass
        # DrissionPage reuse
        try:
            _ = getattr(page, "url", None)
            if log_callback:
                log_callback("[*] 复用已启动的浏览器")
            return browser, page
        except Exception:
            pass

    engine = _browser_engine_name()
    if engine == "nodriver":
        try:
            from browser.nodriver_backend import NodriverBackend, NodriverBrowser, set_backend

            browser_proxy, bridge = prepare_browser_proxy(use_proxy=use_proxy, log_callback=log_callback)
            headless = False
            try:
                # Prefer headed+Xvfb; only headless if forced
                if os.environ.get("GROK_REGISTER_HEADLESS", "").strip() == "1":
                    headless = True
                elif _linux_should_headless() and not _linux_display_socket_ok():
                    headless = True
            except Exception:
                headless = False
            backend = NodriverBackend(log=log_callback)
            backend.start(
                proxy=browser_proxy or "",
                headless=headless,
                extension_path=EXTENSION_PATH if os.path.exists(EXTENSION_PATH) else "",
            )
            set_backend(backend)
            browser = NodriverBrowser(backend)
            page = backend.page
            browser_proxy_bridge = bridge
            browser_started_with_proxy = bool(browser_proxy)
            if log_callback:
                log_callback(f"[Debug] 当前浏览器资料目录: {backend.user_data_dir}")
                log_callback(
                    f"[*] 浏览器引擎: nodriver | 显示模式: "
                    f"{'无头 headless' if headless else '有头 headed(Xvfb/DISPLAY)'} "
                    f"DISPLAY={os.environ.get('DISPLAY') or '(空)'}"
                )
                if get_configured_proxy():
                    log_callback(f"[*] 浏览器网络模式: {'代理' if browser_started_with_proxy else '直连'}")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[!] nodriver 启动失败，回退 DrissionPage: {exc}")
            try:
                from browser.nodriver_backend import set_backend

                set_backend(None)
            except Exception:
                pass
            browser = None
            page = None

    for attempt in range(1, 5):
        bridge = None
        # After 2 headed failures, force headless fallback so registration can continue
        force_hl = None
        if sys.platform != "win32" and attempt >= 3:
            force_hl = True
            if log_callback and attempt == 3:
                log_callback("[!] 有头模式多次失败，回退无头 headless=new 重试")
        try:
            browser_proxy, bridge = prepare_browser_proxy(use_proxy=use_proxy, log_callback=log_callback)
            browser = Chromium(
                create_browser_options(browser_proxy=browser_proxy, force_headless=force_hl)
            )
            browser_proxy_bridge = bridge
            browser_started_with_proxy = bool(browser_proxy)
            tabs = browser.get_tabs()
            page = tabs[-1] if tabs else browser.new_tab()
            _apply_browser_stealth(page, log_callback=log_callback)
            if log_callback and getattr(browser, "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {browser.user_data_path}")
            if log_callback:
                if sys.platform != "win32":
                    hl = force_hl if force_hl is not None else _linux_should_headless()
                    log_callback(
                        f"[*] 浏览器显示模式: {'无头 headless' if hl else '有头 headed(Xvfb/DISPLAY)'} "
                        f"DISPLAY={os.environ.get('DISPLAY') or '(空)'} "
                        f"Xsocket={'ok' if _linux_display_socket_ok() else 'missing'}"
                    )
                if get_configured_proxy():
                    mode = "代理" if browser_started_with_proxy else "直连"
                    log_callback(f"[*] 浏览器网络模式: {mode}")
                    meta = config.get("_last_proxy_exit") if isinstance(config, dict) else None
                    if isinstance(meta, dict) and meta.get("exit_ip"):
                        log_callback(
                            f"[*] 出口家宽提醒: {meta.get('exit_ip')} "
                            f"({meta.get('exit_org') or '?'}) "
                            f"res={meta.get('isResidential')} fraud={meta.get('fraudScore')} "
                            f"| 入口网关仅={meta.get('gateway')}"
                        )
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if bridge is not None:
                try:
                    bridge.stop()
                except Exception:
                    pass
            if log_callback:
                mode = "代理" if proxy_enabled else "直连"
                log_callback(f"[Debug] 浏览器{mode}启动失败(第{attempt}/4次): {exc}")
            try:
                if browser is not None:
                    browser.quit(del_data=True)
            except Exception:
                pass
            browser = None
            page = None
            browser_proxy_bridge = None
            browser_started_with_proxy = False
            if sys.platform != "win32" and attempt == 1:
                _ensure_xvfb(log_callback=log_callback)
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    global browser, page, browser_started_with_proxy
    try:
        from browser.nodriver_backend import get_backend, set_backend

        backend = get_backend()
        if backend is not None:
            try:
                backend.stop(del_data=True)
            except Exception:
                pass
            set_backend(None)
    except Exception:
        pass
    if browser is not None:
        try:
            browser.quit(del_data=True)
        except Exception:
            pass
    stop_browser_proxy_bridge()
    browser = None
    page = None
    browser_started_with_proxy = False


def shutdown_browser():
    """Alias for hybrid/token_harvester (grok_reg API)."""
    stop_browser()


def _get_browser():
    """Alias for hybrid/token_harvester."""
    global browser
    return browser


def _get_page():
    """Alias for hybrid/token_harvester; refresh tab if needed."""
    global page, browser
    if browser is None:
        return None
    if page is None:
        try:
            return refresh_active_page()
        except Exception:
            return None
    return page


def restart_browser(log_callback=None, use_proxy=True):
    stop_browser()
    return start_browser(log_callback=log_callback, use_proxy=use_proxy)


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    if log_callback:
        log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
    stop_browser()
    collected = gc.collect()
    if log_callback:
        log_callback(f"[*] Python GC 已回收对象数: {collected}")


def refresh_active_page():
    global browser, page
    if browser is None:
        restart_browser()
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        else:
            page = browser.new_tab()
    except Exception:
        restart_browser()
    return page


def click_email_signup_button(timeout=10, log_callback=None, cancel_callback=None):
    global page
    deadline = time.time() + timeout
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
const target = candidates[0]?.node || null;
if (!target) {
    return false;
}
target.click();
return candidates[0].text || true;
        """)

        if clicked:
            if log_callback:
                detail = f": {clicked}" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{detail}")
            sleep_with_cancel(2, cancel_callback)
            return True

        if log_callback:
            current_url = page.url if page else "none"
            log_callback(f"[Debug] 当前URL: {current_url}")

        sleep_with_cancel(1, cancel_callback)

    if log_callback:
        page_html = page.html[:500] if page else "no page"
        log_callback(f"[Debug] 页面内容片段: {page_html}")

    raise Exception("未找到「使用邮箱注册」按钮")


def page_is_cloudflare_challenge(page_obj=None):
    """Detect Cloudflare interstitial / attention page."""
    p = page_obj or page
    if p is None:
        return False
    try:
        title = str(getattr(p, "title", "") or "")
        url = str(getattr(p, "url", "") or "")
        html = ""
        try:
            html = str(p.html or "")[:4000]
        except Exception:
            html = ""
        blob = f"{title}\n{url}\n{html}".lower()
        markers = (
            "attention required",
            "just a moment",
            "cf-browser-verification",
            "cf-challenge",
            "cf-turnstile",
            "checking your browser",
            "enable javascript and cookies",
            "cloudflare",
            "blocked due to abusive traffic",
            "sorry, you have been blocked",
        )
        # real signup pages also load on cloudflare-backed domains; require strong signal
        strong = (
            "attention required" in blob
            or "just a moment" in blob
            or "cf-browser-verification" in blob
            or "checking your browser" in blob
            or "blocked due to abusive traffic" in blob
            or "sorry, you have been blocked" in blob
            or ("cloudflare" in title.lower() and "sign" not in title.lower())
        )
        return strong
    except Exception:
        return False


def wait_cloudflare_passthrough(timeout=45, log_callback=None, cancel_callback=None):
    """Wait for CF challenge page to clear (JS challenge may auto-pass)."""
    deadline = time.time() + timeout
    reported = False
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        refresh_active_page()
        if not page_is_cloudflare_challenge(page):
            if reported and log_callback:
                log_callback("[*] Cloudflare 挑战已通过")
            return True
        if log_callback and not reported:
            meta = config.get("_last_proxy_exit") if isinstance(config, dict) else None
            if isinstance(meta, dict) and meta.get("exit_ip"):
                log_callback(
                    f"[!] 检测到 Cloudflare 拦截页（出口={meta.get('exit_ip')} "
                    f"{meta.get('exit_org') or ''}，不是入口网关），等待自动放行..."
                )
            else:
                log_callback("[!] 检测到 Cloudflare 拦截页，等待自动放行...")
            if sys.platform != "win32" and _linux_should_headless():
                log_callback(
                    "[!] 当前为无头浏览器，CF 通过率偏低；建议 Xvfb + GROK_REGISTER_HEADLESS=0"
                )
            reported = True
        # try click common verify buttons if present
        try:
            page.run_js(
                """
const btn = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], a'))
  .find(n => /verify|继续|human|确认|i am human/i.test((n.innerText||n.value||'')));
if (btn) btn.click();
"""
            )
        except Exception:
            pass
        sleep_with_cancel(2, cancel_callback)
    return not page_is_cloudflare_challenge(page)


def refresh_cliproxy_and_restart_browser(log_callback=None):
    """Fetch a new Cliproxy IP and restart browser with it."""
    mode = str(config.get("proxy_mode", "") or "").strip().lower()
    if mode in ("cliproxy_white", "cliproxy", "white_api", "api"):
        try:
            apply_resolved_proxy_to_config(log_callback=log_callback, fetch_live=True)
        except Exception as exc:
            if log_callback:
                log_callback(f"[!] 更换 Cliproxy IP 失败: {exc}")
    restart_browser(log_callback=log_callback, use_proxy=True)


def open_signup_page(log_callback=None, cancel_callback=None):
    global browser, page
    raise_if_cancelled(cancel_callback)
    if browser is None:
        start_browser(log_callback=log_callback)
        if log_callback:
            log_callback("[*] 浏览器已启动")

    def _open_with_current_browser():
        global page
        try:
            try:
                page = browser.get_tab(0)
            except Exception:
                page = _get_page() or browser
            if page is None:
                page = browser.new_tab(SIGNUP_URL)
            else:
                page.get(SIGNUP_URL)
        except Exception as e:
            if log_callback:
                log_callback(f"[Debug] 打开URL异常: {e}")
            try:
                page = browser.new_tab(SIGNUP_URL)
            except Exception:
                # nodriver: get() on existing page
                page = _get_page()
                if page is not None:
                    page.get(SIGNUP_URL)
        try:
            if hasattr(page, "wait") and hasattr(page.wait, "doc_loaded"):
                page.wait.doc_loaded()
        except Exception:
            pass
        sleep_with_cancel(2, cancel_callback)

    max_proxy_rounds = 4
    last_err = None
    for round_i in range(1, max_proxy_rounds + 1):
        raise_if_cancelled(cancel_callback)
        try:
            _open_with_current_browser()
        except Exception as e:
            last_err = e
            if browser_started_with_proxy and get_configured_proxy():
                if log_callback:
                    log_callback(f"[!] 浏览器代理访问注册页失败，换 IP/重试 ({round_i}/{max_proxy_rounds}): {e}")
                refresh_cliproxy_and_restart_browser(log_callback=log_callback)
                continue
            raise

        if browser_started_with_proxy and page_has_proxy_error(page):
            if log_callback:
                log_callback("[!] 浏览器页面显示代理错误，更换代理重试")
            refresh_cliproxy_and_restart_browser(log_callback=log_callback)
            continue

        if log_callback:
            log_callback(f"[*] 当前URL: {page.url}")

        # Cloudflare challenge: wait then rotate proxy if still blocked
        if page_is_cloudflare_challenge(page):
            ok = wait_cloudflare_passthrough(
                timeout=50, log_callback=log_callback, cancel_callback=cancel_callback
            )
            if not ok:
                if log_callback:
                    log_callback(
                        f"[!] Cloudflare 仍拦截（第 {round_i}/{max_proxy_rounds} 次），更换代理 IP 重试"
                    )
                refresh_cliproxy_and_restart_browser(log_callback=log_callback)
                continue

        try:
            click_email_signup_button(
                timeout=15,
                log_callback=log_callback,
                cancel_callback=cancel_callback,
            )
            return
        except Exception as e:
            last_err = e
            # If still CF or button missing, rotate and retry
            if page_is_cloudflare_challenge(page) or "未找到" in str(e):
                if log_callback:
                    log_callback(
                        f"[!] 注册页未就绪: {e}；更换代理重试 ({round_i}/{max_proxy_rounds})"
                    )
                refresh_cliproxy_and_restart_browser(log_callback=log_callback)
                continue
            raise

    # last resort: try direct once if proxy kept failing
    if get_configured_proxy():
        if log_callback:
            log_callback("[!] 代理多次失败，最后尝试直连打开注册页")
        restart_browser(log_callback=log_callback, use_proxy=False)
        _open_with_current_browser()
        wait_cloudflare_passthrough(
            timeout=40, log_callback=log_callback, cancel_callback=cancel_callback
        )
        click_email_signup_button(
            timeout=15, log_callback=log_callback, cancel_callback=cancel_callback
        )
        return

    if last_err:
        raise last_err
    raise Exception("打开注册页失败")


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            """
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                reclicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
if (!candidates.length) return false;
candidates[0].node.click();
return candidates[0].text || true;
                """)
                last_reclick_time = now
                if reclicked and log_callback:
                    detail = f": {reclicked}" if isinstance(reclicked, str) else ""
                    log_callback(f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口{detail}")
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", page.url if page else "") if isinstance(filled, dict) else (page.url if page else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            if log_callback:
                detail = f" ({clicked})" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
            return email, dev_token
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", page.url if page else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        page.run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            sleep_with_cancel(1.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def _patch_turnstile_iframe(iframe) -> bool:
    """Inject screenX/screenY patch + auto-click inside a Turnstile iframe.

    Works without the turnstilePatch extension by patching MouseEvent
    properties directly in the iframe's execution context via CDP.
    """
    if iframe is None:
        return False
    try:
        iframe.run_js(
            """
(function(){
  if (window.__ts_patched) return;
  window.__ts_patched = true;
  function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
  var sx = getRandomInt(800, 1200);
  var sy = getRandomInt(400, 700);
  try { Object.defineProperty(MouseEvent.prototype, 'screenX', { get: function(){return sx;}, configurable: true }); } catch(e){}
  try { Object.defineProperty(MouseEvent.prototype, 'screenY', { get: function(){return sy;}, configurable: true }); } catch(e){}
})();
            """
        )
    except Exception:
        return False
    return True


def _click_turnstile_checkbox(iframe, page_obj=None) -> bool:
    """Find and click the Turnstile checkbox inside the iframe shadow DOM."""
    if iframe is None:
        return False
    clicked = False
    # Path 1: shadow body -> input
    try:
        body_sr = iframe.ele("tag:body").shadow_root
        btn = body_sr.ele("tag:input")
        if btn:
            btn.click()
            clicked = True
    except Exception:
        pass
    # Path 2: any clickable element in shadow
    if not clicked:
        try:
            body_sr = iframe.ele("tag:body").shadow_root
            for cand in body_sr.eles("tag:input") if hasattr(body_sr, "eles") else []:
                try:
                    cand.click()
                    clicked = True
                    break
                except Exception:
                    pass
        except Exception:
            pass
    # Path 3: real mouse coordinates via page Actions
    if not clicked and page_obj is not None:
        try:
            # get iframe bounding rect to click center
            rect = iframe.rect.location  # (x, y)
            size = iframe.rect.size  # (w, h)
            cx = rect[0] + size[0] / 2
            cy = rect[1] + size[1] / 2
            page_obj.actions.move_to(cx, cy).click()
            clicked = True
        except Exception:
            pass
    return clicked


def getTurnstileToken(log_callback=None, cancel_callback=None):
    global page
    if page is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    # Helper for logging
    def _lg(msg):
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    # Try to reset existing widget
    try:
        page.run_js(
            "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
        )
    except Exception:
        pass

    # Patch top-level page too (some Turnstile widgets in MAIN world)
    try:
        page.run_js(
            """
(function(){
  if (window.__ts_patched) return;
  window.__ts_patched = true;
  function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
  var sx = getRandomInt(800, 1200);
  var sy = getRandomInt(400, 700);
  try { Object.defineProperty(MouseEvent.prototype, 'screenX', { get: function(){return sx;}, configurable: true }); } catch(e){}
  try { Object.defineProperty(MouseEvent.prototype, 'screenY', { get: function(){return sy;}, configurable: true }); } catch(e){}
})();
            """
        )
    except Exception:
        pass

    iframe_patched = False
    checkbox_clicked = False
    last_status = ""

    for attempt in range(0, 45):
        raise_if_cancelled(cancel_callback)
        try:
            # 1. Check for token via JS
            token = page.run_js(
                """
try {
  var tok = '';
  // injected widget token (hybrid mode)
  try { tok = String(window.__hybrid_turnstile || ''); } catch(e){}
  if (!tok) {
    const byInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (byInput) tok = String(byInput.value || '').trim();
  }
  if (!tok && window.turnstile && typeof turnstile.getResponse === 'function') {
    tok = String(turnstile.getResponse() || '').trim();
  }
  // scan all hidden inputs
  if (!tok) {
    document.querySelectorAll('input[type="hidden"]').forEach(function(inp){
      var v = String(inp.value || '').trim();
      if (v.length >= 80 && v.length < 10000) tok = v;
    });
  }
  return tok;
} catch(e) { return ''; }
                """
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                _lg(f"[*] Turnstile 已通过，token长度={len(token)} (attempt={attempt})")
                return token

            # 2. Find the Turnstile iframe and interact with it
            challenge_input = page.ele("@name=cf-turnstile-response")
            if challenge_input:
                wrapper = challenge_input.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None

                # Also try by selector
                if iframe is None:
                    try:
                        iframe = page.ele('css:iframe[src*="challenges.cloudflare.com"]')
                    except Exception:
                        iframe = None

                if iframe and not iframe_patched:
                    if _patch_turnstile_iframe(iframe):
                        iframe_patched = True
                        _lg("[Debug] Patched Turnstile iframe screenX/screenY")

                if iframe and not checkbox_clicked:
                    if _click_turnstile_checkbox(iframe, page_obj=page):
                        checkbox_clicked = True
                        _lg("[Debug] Clicked Turnstile checkbox")
                    else:
                        # retry: reset and patch again
                        try:
                            iframe = wrapper.shadow_root.ele("tag:iframe")
                            if iframe:
                                _patch_turnstile_iframe(iframe)
                                _click_turnstile_checkbox(iframe, page_obj=page)
                        except Exception:
                            pass

                # 3. If iframe interaction didn't yield token, patch the iframe iframe context again
                if iframe and attempt > 0 and attempt % 10 == 5:
                    # Re-inject patch every 10 iterations if token not yet obtained
                    _patch_turnstile_iframe(iframe)
                    if not checkbox_clicked or attempt % 20 == 5:
                        _click_turnstile_checkbox(iframe, page_obj=page)

            else:
                # No challenge input found - maybe injected widget (hybrid)
                # Try clicking any turnstile container
                page.run_js(
                    """
const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
                    """
                )
        except Exception:
            pass
        sleep_with_cancel(1.5, cancel_callback)

    raise Exception("Turnstile 获取 token 失败")


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not form_filled_once:
            filled = page.run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});

// 必须等待 Cloudflare 校验通过后再提交
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

if (submitBtn) {
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )

            if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                form_filled_once = True
                if log_callback:
                    token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                    log_callback(f"[*] 资料已填写，等待 Cloudflare 人机验证通过... 当前token长度={token_len}")
                if token_len == "0":
                    pause_seconds = random.uniform(1, 3)
                    if log_callback:
                        log_callback(f"[*] Cloudflare token 为空，暂停 {pause_seconds:.1f}s 后继续检测")
                    sleep_with_cancel(pause_seconds, cancel_callback)
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                # 卡住后自动二次复用 Turnstile 组件
                if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                    if log_callback:
                        log_callback("[*] Cloudflare 验证卡住，开始二次复用 Turnstile...")
                    try:
                        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                        if token:
                            synced = page.run_js(
                                """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                """,
                                token,
                            )
                            if log_callback:
                                log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.8, cancel_callback)
                continue

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
            """
        )

        if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
            if log_callback:
                token_len = submit_state.split(":", 1)[1] if ":" in submit_state else "0"
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                if log_callback:
                    log_callback("[*] 提交前仍卡住，自动再次复用 Turnstile...")
                try:
                    token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    if token:
                        synced = page.run_js(
                            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                            """,
                            token,
                        )
                        if log_callback:
                            log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.8, cancel_callback)
            continue

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def wait_for_sso_cookie(timeout=120, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            now = time.time()
            if now - last_submit_retry >= 2.5:
                retried = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
                    """
                )
                last_submit_retry = now
                if log_callback and (retried == "final-page-clicked-submit" or (isinstance(retried, str) and retried.startswith("final-page-no-submit"))):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 10:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 卡住，自动二次复用 Turnstile...")
                        try:
                            token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                            if token:
                                synced = page.run_js(
                                    """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                    """,
                                    token,
                                )
                                if log_callback:
                                    log_callback(f"[*] 最终页 Turnstile 二次复用完成，回填长度={synced}")
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 二次复用失败: {cf_exc}")
                        last_cf_retry_at = now

            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    if log_callback:
                        log_callback("[*] 已获取到 sso cookie")
                    return value
        except PageDisconnectedError:
            refresh_active_page()
        except AccountRetryNeeded:
            raise
        except Exception:
            pass

        sleep_with_cancel(1, cancel_callback)

    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(
            config_frame,
            self.email_provider_var,
            ["duckmail", "yyds", "cloudflare", "mailnest", "cloudmail"],
            width=12,
        )
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "号池本地入池:")
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        add_field(self.grok2api_local_auto_check, 5, 1, sticky=tk.W)

        add_label(5, 2, "号池名称:")
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = tk_option_menu(
            config_frame, self.grok2api_pool_name_var, ["ssoBasic", "ssoSuper"], width=12
        )
        add_field(self.grok2api_pool_name_combo, 5, 3, sticky=tk.W)

        add_label(6, 0, "本地 token.json:")
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = tk_entry(config_frame, textvariable=self.grok2api_local_file_var, width=72)
        add_field(self.grok2api_local_file_entry, 6, 1, columnspan=3)

        add_label(7, 0, "号池远端入池:")
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        add_field(self.grok2api_remote_auto_check, 7, 1, sticky=tk.W)

        add_label(8, 0, "号池远端 Base:")
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_base_var, width=72)
        add_field(self.grok2api_remote_base_entry, 8, 1, columnspan=3)

        add_label(9, 0, "号池远端 app_key:")
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_key_var, width=72)
        add_field(self.grok2api_remote_key_entry, 9, 1, columnspan=3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(f"[*] 当前邮箱服务商: {self.email_provider_var.get()} | 注册数量: {self.count_var.get()}")

    def log(self, message):
        timestamp = now_beijing("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_text.insert(tk.END, f"{line}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), f"accounts_{now}.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        try:
            start_browser(log_callback=self.log)
            self.log("[*] 浏览器已启动")
            i = 0
            retry_count_for_slot = 0
            max_slot_retry = 3
            while i < count:
                if self.should_stop():
                    break
                self.log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
                try:
                    email = ""
                    dev_token = ""
                    code = ""
                    mail_ok = False
                    max_mail_retry = 3
                    for mail_try in range(1, max_mail_retry + 1):
                        self.log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                        open_signup_page(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log("[*] 2. 创建邮箱并提交")
                        email, dev_token = fill_email_and_submit(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log(f"[*] 邮箱: {email}")
                        self.log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                        try:
                            with open(
                                os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                                "a",
                                encoding="utf-8",
                            ) as f:
                                f.write(f"{email}\t{dev_token}\n")
                        except Exception:
                            pass
                        self.log("[*] 3. 拉取验证码")
                        try:
                            code = fill_code_and_submit(
                                email,
                                dev_token,
                                log_callback=self.log,
                                cancel_callback=self.should_stop,
                            )
                            mail_ok = True
                            break
                        except Exception as mail_exc:
                            msg = str(mail_exc)
                            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                                self.log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                                restart_browser(log_callback=self.log)
                                sleep_with_cancel(1, self.should_stop)
                                continue
                            raise

                    if not mail_ok:
                        raise Exception("验证码阶段失败，已达到最大重试次数")
                    self.log(f"[*] 验证码: {code}")
                    self.log("[*] 4. 填写资料")
                    profile = fill_profile_and_submit(
                        log_callback=self.log, cancel_callback=self.should_stop
                    )
                    self.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                    self.log("[*] 5. 等待 sso cookie")
                    sso = wait_for_sso_cookie(
                        log_callback=self.log, cancel_callback=self.should_stop
                    )
                    self.results.append({"email": email, "sso": sso, "profile": profile})
                    try:
                        line = f"{email}----{profile.get('password','')}----{sso}\n"
                        with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception as file_exc:
                        self.log(f"[Debug] 保存账号文件失败: {file_exc}")
                    # NSFW / g2a / CPA：默认后台，不阻塞下一号（功能仍执行）
                    schedule_post_registration(
                        email,
                        str(profile.get("password") or ""),
                        sso,
                        page=page,
                        log_callback=self.log,
                    )
                    self.success_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    self.log(f"[+] 注册成功: {email}")
                    if (
                        self.success_count > 0
                        and self.success_count % MEMORY_CLEANUP_INTERVAL == 0
                        and i < count
                    ):
                        cleanup_runtime_memory(
                            log_callback=self.log,
                            reason=f"已成功 {self.success_count} 个账号，执行定期清理",
                        )
                except RegistrationCancelled:
                    self.log("[!] 注册被用户停止")
                    break
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        self.log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                    else:
                        self.fail_count += 1
                        self.log(
                            f"[-] 当前账号已达到最大重试次数，跳过: {exc}"
                        )
                        retry_count_for_slot = 0
                        i += 1
                except Exception as exc:
                    self.fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    self.log(f"[-] 注册失败: {exc}")
                finally:
                    self.update_stats()
                    if self.should_stop():
                        break
                    if browser is None:
                        start_browser(log_callback=self.log)
                    else:
                        restart_browser(log_callback=self.log)
                    sleep_with_cancel(1, self.should_stop)
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            # 等后台 g2a/CPA/NSFW 尽量跑完再关浏览器进程环境
            wait_post_success_queue(timeout=300, log_callback=self.log)
            stop_browser()
            self._set_running_ui(False)
            self.log("[*] 任务结束")


class CliStopController:
    def __init__(self):
        self.stop_requested = False

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        self.stop_requested = True


def cli_log(message):
    timestamp = now_beijing("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_registration_job(count, log_callback=None, controller=None):
    """Non-interactive registration loop for CLI and Web.

    Returns dict: success, fail, accounts_file, stopped.
    """
    log = log_callback or cli_log
    if controller is None:
        controller = CliStopController()

    reg_mode = str(config.get("register_mode") or "browser").strip().lower()
    if reg_mode in ("hybrid", "protocol_hybrid", "mixed"):
        log(f"[*] 注册模式: hybrid（协议 + 短浏览器）")
        try:
            from hybrid_register import run_hybrid_registration_job

            return run_hybrid_registration_job(
                count, log_callback=log, controller=controller
            )
        except Exception as hybrid_exc:
            log(f"[!] 混合模式启动失败，回退全浏览器: {hybrid_exc}")

    success_count = 0
    fail_count = 0
    retry_count_for_slot = 0
    max_slot_retry = 3
    accounts_output_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"accounts_{now_beijing('%Y%m%d_%H%M%S')}.txt",
    )
    log(f"[*] 任务启动，目标数量: {count}")
    log(f"[*] 注册模式: browser（全浏览器）")
    log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    mode = str(config.get("proxy_mode", "direct") or "direct")
    try:
        resolved_proxy = apply_resolved_proxy_to_config(log_callback=log, fetch_live=True)
    except Exception as proxy_exc:
        log(f"[!] 获取/解析代理失败: {proxy_exc}")
        raise
    if resolved_proxy:
        # mask password in log
        safe = resolved_proxy
        try:
            parsed = urllib.parse.urlparse(resolved_proxy)
            if parsed.password:
                safe = resolved_proxy.replace(":" + parsed.password + "@", ":****@")
        except Exception:
            pass
        log(f"[*] 代理模式: {mode} | {safe}")
        if mode in ("whitelist", "group", "proxy_group"):
            log(
                f"[*] 代理组: 国家={config.get('proxy_country','')} "
                f"分隔符={config.get('proxy_delimiter','-')!r} "
                f"时长={config.get('proxy_duration','120')}分钟"
            )
        if mode in ("cliproxy_white", "cliproxy", "white_api", "api"):
            log(
                f"[*] Cliproxy 白名单: region={config.get('proxy_country','US')} "
                f"time={config.get('proxy_duration','10')}m"
            )
    else:
        log(f"[*] 代理模式: {mode or 'direct'}（直连）")
    try:
        start_browser(log_callback=log)
        log("[*] 浏览器已启动")
        i = 0
        while i < count:
            if controller.should_stop():
                break
            log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
            try:
                email = ""
                dev_token = ""
                code = ""
                mail_ok = False
                max_mail_retry = 3
                for mail_try in range(1, max_mail_retry + 1):
                    log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                    open_signup_page(
                        log_callback=log, cancel_callback=controller.should_stop
                    )
                    log("[*] 2. 创建邮箱并提交")
                    email, dev_token = fill_email_and_submit(
                        log_callback=log, cancel_callback=controller.should_stop
                    )
                    log(f"[*] 邮箱: {email}")
                    log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                    try:
                        with open(
                            os.path.join(
                                os.path.dirname(os.path.abspath(__file__)),
                                "mail_credentials.txt",
                            ),
                            "a",
                            encoding="utf-8",
                        ) as f:
                            f.write(f"{email}\t{dev_token}\n")
                    except Exception:
                        pass
                    log("[*] 3. 拉取验证码")
                    try:
                        code = fill_code_and_submit(
                            email,
                            dev_token,
                            log_callback=log,
                            cancel_callback=controller.should_stop,
                        )
                        mail_ok = True
                        break
                    except Exception as mail_exc:
                        msg = str(mail_exc)
                        if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                            log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                            restart_browser(log_callback=log)
                            sleep_with_cancel(1, controller.should_stop)
                            continue
                        raise

                if not mail_ok:
                    raise Exception("验证码阶段失败，已达到最大重试次数")
                log(f"[*] 验证码: {code}")
                log("[*] 4. 填写资料")
                profile = fill_profile_and_submit(
                    log_callback=log, cancel_callback=controller.should_stop
                )
                log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                log("[*] 5. 等待 sso cookie")
                sso = wait_for_sso_cookie(
                    log_callback=log, cancel_callback=controller.should_stop
                )
                try:
                    line = f"{email}----{profile.get('password','')}----{sso}\n"
                    with open(accounts_output_file, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as file_exc:
                    log(f"[Debug] 保存账号文件失败: {file_exc}")
                # NSFW / g2a / CPA：默认后台补写，功能保留、不挡下一号
                global page
                schedule_post_registration(
                    email,
                    str(profile.get("password") or ""),
                    sso,
                    page=page,
                    log_callback=log,
                )
                success_count += 1
                retry_count_for_slot = 0
                i += 1
                log(f"[+] 注册成功: {email}")
                log(f"[*] 当前统计: 成功 {success_count} | 失败 {fail_count}")
                if success_count > 0 and success_count % MEMORY_CLEANUP_INTERVAL == 0 and i < count:
                    cleanup_runtime_memory(
                        log_callback=log,
                        reason=f"已成功 {success_count} 个账号，执行定期清理",
                    )
            except RegistrationCancelled:
                log("[!] 注册被停止")
                break
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    log(
                        f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                    )
                else:
                    fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except Exception as exc:
                fail_count += 1
                retry_count_for_slot = 0
                i += 1
                log(f"[-] 注册失败: {exc}")
            finally:
                if controller.should_stop():
                    break
                if browser is None:
                    start_browser(log_callback=log)
                else:
                    restart_browser(log_callback=log)
                sleep_with_cancel(1, controller.should_stop)
    except KeyboardInterrupt:
        controller.stop()
        log("[!] 收到 Ctrl+C，正在停止并清理")
    except Exception as exc:
        log(f"[!] 任务异常: {exc}")
    finally:
        # 浏览器关掉前先尽量完成后台入池/CPA/NSFW（不依赖 page）
        wait_post_success_queue(timeout=300, log_callback=log)
        cleanup_runtime_memory(log_callback=log, reason="任务结束")
        log(f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}")
    return {
        "success": success_count,
        "fail": fail_count,
        "accounts_file": accounts_output_file,
        "stopped": bool(controller.should_stop()),
    }


def run_registration_cli(count):
    return run_registration_job(count, log_callback=cli_log, controller=CliStopController())


def main_cli():
    load_config()
    count = int(config.get("register_count", 1) or 1)
    cli_log("[*] CLI 已加载配置")
    cli_log(f"[*] 当前邮箱服务商: {config.get('email_provider', 'duckmail')} | 注册数量: {count}")
    cli_log("[*] 输入 start 后开始；按 Ctrl+C 可强制停止")
    try:
        command = input("> ").strip().lower()
    except KeyboardInterrupt:
        cli_log("[!] 已取消")
        return
    if command != "start":
        cli_log("[!] 未输入 start，已退出")
        return
    run_registration_cli(count)


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("start", "cli", "--cli"):
        main_cli()
        return
    if not HAS_TK:
        print("[!] 当前环境无 Tkinter，请使用 CLI: python grok_register_ttk.py cli")
        sys.exit(1)
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
