# -*- coding: utf-8 -*-
"""启动前连通性检查：代理 / 邮箱 API / CPA（本地 hotload + 远程 Management）。"""
from __future__ import annotations

import os
import socket
from typing import Callable, List, Tuple
from urllib.parse import urlparse

CheckResult = Tuple[str, bool, str]  # name, ok, detail


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def check_proxy(proxy_url: str, http_get: Callable) -> CheckResult:
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return "代理", True, "未配置（直连）"
    try:
        u = urlparse(proxy_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        if not _tcp_open(host, port):
            return "代理", False, f"无法连接 {host}:{port}"
        try:
            http_get(
                "https://www.cloudflare.com/cdn-cgi/trace",
                timeout=8,
                proxies={"http": proxy_url, "https": proxy_url},
            )
        except Exception as exc:
            return "代理", False, f"TCP 通，出站探测失败: {exc}"
        return "代理", True, f"{host}:{port} 可用"
    except Exception as exc:
        return "代理", False, str(exc)


def check_email_api(provider: str, config: dict, http_get: Callable, http_post: Callable) -> CheckResult:
    provider = (provider or "").strip().lower()
    try:
        if provider == "cloudflare":
            base = str(config.get("cloudflare_api_base", "") or "").rstrip("/")
            if not base:
                return "邮箱API", False, "未配置 cloudflare_api_base"
            path = str(config.get("cloudflare_path_domains", "/api/domains") or "/api/domains")
            if not path.startswith("/"):
                path = "/" + path
            url = f"{base}{path}"
            resp = http_get(url, timeout=10)
            if resp.status_code >= 400:
                accounts_path = str(
                    config.get("cloudflare_path_accounts", "/api/new_address") or "/api/new_address"
                ).rstrip("/").lower()
                direct_create = accounts_path.endswith("/new_address") and not accounts_path.endswith(
                    "/admin/new_address"
                )
                if direct_create and resp.status_code in (401, 403):
                    return (
                        "邮箱API",
                        True,
                        f"Cloudflare 直建模式可继续（domains HTTP {resp.status_code}）",
                    )
                return "邮箱API", False, f"Cloudflare HTTP {resp.status_code}"
            return "邮箱API", True, f"Cloudflare 可达 HTTP {resp.status_code}"

        if provider == "duckmail":
            base = str(config.get("duckmail_api_base", "") or "https://api.duckmail.sbs").rstrip("/")
            resp = http_get(f"{base}/domains", headers={"Accept": "application/json"}, timeout=12)
            if resp.status_code >= 400:
                return "邮箱API", False, f"DuckMail/Mail.tm HTTP {resp.status_code}"
            return "邮箱API", True, f"DuckMail/Mail.tm 可达 HTTP {resp.status_code}"

        if provider == "yyds":
            key = str(config.get("yyds_api_key", "") or "")
            jwt = str(config.get("yyds_jwt", "") or "")
            if not key and not jwt:
                return "邮箱API", False, "YYDS 需配置 API Key 或 JWT"
            headers = {}
            if jwt:
                headers["Authorization"] = f"Bearer {jwt}"
            elif key:
                headers["X-API-Key"] = key
            resp = http_get("https://maliapi.215.im/v1/domains", headers=headers, timeout=12)
            return "邮箱API", resp.status_code < 400, f"YYDS HTTP {resp.status_code}"

        if provider == "mailnest":
            key = str(config.get("mailnest_api_key", "") or "").strip()
            if not key:
                return "邮箱API", False, "MailNest 需配置 mailnest_api_key"
            resp = http_get(
                "https://mailnest.top/",
                headers={"Authorization": f"Bearer {key}"},
                timeout=12,
            )
            return "邮箱API", resp.status_code < 400, f"MailNest 站点 HTTP {resp.status_code}"

        if provider == "cloudmail":
            url = str(config.get("cloudmail_url", "") or "").rstrip("/")
            if not url:
                return "邮箱API", False, "未配置 cloudmail_url"
            resp = http_get(url, timeout=10)
            return "邮箱API", resp.status_code < 400, f"CloudMail HTTP {resp.status_code}"

        return "邮箱API", True, f"提供商 {provider or '(empty)'} 跳过深度探测"
    except Exception as exc:
        return "邮箱API", False, str(exc)


def _resolve_mgmt_key(config: dict) -> str:
    key = str(config.get("cpa_management_key", "") or "").strip()
    if key:
        return key
    return (
        os.environ.get("CPA_MGMT_KEY")
        or os.environ.get("CPA_MANAGEMENT_KEY")
        or os.environ.get("CLIPROXYAPI_MANAGEMENT_KEY")
        or ""
    ).strip()


def check_cpa(config: dict, http_get: Callable) -> CheckResult:
    export_on = bool(config.get("cpa_export_enabled", True))
    if not export_on:
        return "CPA", True, "cpa_export_enabled=false（跳过）"

    auth_dir = str(config.get("cpa_auth_dir", "") or "").strip()
    hot_dir = str(config.get("cpa_hotload_dir", "") or "").strip()
    remote = str(config.get("cpa_remote_url", "") or "").strip()
    key = _resolve_mgmt_key(config)
    parts: list[str] = []

    if auth_dir:
        path = auth_dir
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if os.path.isdir(path):
            parts.append("本地 cpa_auths OK")
        else:
            # will be created on mint
            parts.append(f"本地 cpa_auths 将创建: {path}")

    if hot_dir:
        if os.path.isdir(hot_dir):
            parts.append("hotload 目录 OK")
        else:
            return "CPA", False, f"hotload 目录不存在: {hot_dir}"

    if remote:
        if not key:
            return "CPA", False, "已配 cpa_remote_url 但缺少 cpa_management_key / CPA_MGMT_KEY"
        try:
            u = urlparse(remote)
            host = u.hostname or "127.0.0.1"
            port = u.port or (443 if u.scheme == "https" else 80)
            if not _tcp_open(host, port):
                return "CPA", False, f"远程不可达 {host}:{port}"
            base = remote.rstrip("/")
            resp = http_get(
                f"{base}/v0/management/auth-files",
                headers={"Authorization": f"Bearer {key}"},
                timeout=8,
                proxies={},
            )
            if resp.status_code in (401, 403):
                return "CPA", False, f"管理密钥无效 HTTP {resp.status_code}"
            if resp.status_code >= 500:
                return "CPA", False, f"CPA 服务异常 HTTP {resp.status_code}"
            parts.append(f"远程 Management OK HTTP {resp.status_code}")
        except Exception as exc:
            return "CPA", False, f"远程探测失败: {exc}"

    if not parts:
        return "CPA", True, "仅 mint 到默认 cpa_auths"
    return "CPA", True, "；".join(parts)


def run_connectivity_checks(config: dict, http_get: Callable, http_post: Callable) -> List[CheckResult]:
    results: List[CheckResult] = []
    results.append(check_proxy(str(config.get("proxy", "") or ""), http_get))
    results.append(
        check_email_api(
            str(config.get("email_provider", "") or ""),
            config,
            http_get,
            http_post,
        )
    )
    results.append(check_cpa(config, http_get))
    return results


def format_check_results(results: List[CheckResult]) -> str:
    lines = []
    for name, ok, detail in results:
        mark = "OK" if ok else "FAIL"
        lines.append(f"[{mark}] {name}: {detail}")
    return "\n".join(lines)
