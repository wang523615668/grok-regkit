#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight CPA multi-key + request quota gateway (stdlib only).

Sits in front of CLIProxyAPI (default 127.0.0.1:8317), validates Bearer keys
from keys.json, enforces per-key request quotas, then forwards with the real
CPA upstream API key.

Usage:
  python cpa_gateway.py serve
  python cpa_gateway.py add [--name N] [--quota 1000]
  python cpa_gateway.py list
  python cpa_gateway.py set-quota KEY|--name N QUOTA   # 0 = unlimited
  python cpa_gateway.py disable KEY|--name N
  python cpa_gateway.py enable KEY|--name N
"""
from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import secrets
import string
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# --- paths / defaults (override with env) ---
ROOT = Path(os.environ.get("CPA_GATEWAY_ROOT") or "/opt/cliproxyapi")
KEYS_PATH = Path(os.environ.get("CPA_GATEWAY_KEYS") or str(ROOT / "keys.json"))
CREDS_PATH = Path(os.environ.get("CPA_GATEWAY_CREDS") or str(ROOT / "API_CREDENTIALS.txt"))
# 0.0.0.0 so docker nginx can reach via 172.17.0.1
LISTEN = (os.environ.get("CPA_GATEWAY_HOST") or "0.0.0.0").strip()
PORT = int(os.environ.get("CPA_GATEWAY_PORT") or "8318")
UPSTREAM = (os.environ.get("CPA_UPSTREAM") or "http://127.0.0.1:8317").strip().rstrip("/")
DEFAULT_QUOTA = int(os.environ.get("CPA_DEFAULT_QUOTA") or "1000")
# Shown in `add` help output; not used for upstream Host unless CPA_UPSTREAM_HOST set.
PUBLIC_BASE = (os.environ.get("CPA_PUBLIC_BASE") or "https://api.example.com/cpa/v1").strip().rstrip("/")
UPSTREAM_HOST = (os.environ.get("CPA_UPSTREAM_HOST") or "").strip()

_lock = threading.RLock()


def _now() -> float:
    return time.time()


def _gen_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "cpa_" + "".join(secrets.choice(alphabet) for _ in range(36))


def _load_keys() -> Dict[str, Any]:
    if not KEYS_PATH.exists():
        return {"keys": {}, "upstream_api_key": ""}
    raw = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"keys": {}, "upstream_api_key": ""}
    raw.setdefault("keys", {})
    raw.setdefault("upstream_api_key", "")
    return raw


def _save_keys(data: Dict[str, Any]) -> None:
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(KEYS_PATH)
    try:
        KEYS_PATH.chmod(0o600)
    except OSError:
        pass


def _read_upstream_key_from_creds() -> str:
    if not CREDS_PATH.exists():
        return ""
    text = CREDS_PATH.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"API Key:\s*(\S+)", text)
    return (m.group(1) if m else "").strip()


def _ensure_upstream_key(data: Dict[str, Any]) -> str:
    key = str(data.get("upstream_api_key") or "").strip()
    if key:
        return key
    key = _read_upstream_key_from_creds()
    if key:
        data["upstream_api_key"] = key
        _save_keys(data)
    return key


def _find_key(data: Dict[str, Any], token: str = "", name: str = "") -> Tuple[str, Optional[Dict[str, Any]]]:
    keys = data.get("keys") or {}
    if token and token in keys:
        return token, keys[token]
    if name:
        for k, v in keys.items():
            if str(v.get("name") or "") == name:
                return k, v
    return "", None


def cmd_add(args: argparse.Namespace) -> int:
    with _lock:
        data = _load_keys()
        _ensure_upstream_key(data)
        token = _gen_key()
        quota = int(args.quota)
        if quota < 0:
            raise SystemExit("quota must be >= 0 (0 = unlimited)")
        data["keys"][token] = {
            "name": (args.name or "").strip() or f"key-{int(_now())}",
            "enabled": True,
            "quota_requests": quota,
            "used_requests": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save_keys(data)
    print(token)
    print(f"name={data['keys'][token]['name']} quota_requests={quota} (0=unlimited)")
    print(f"base={PUBLIC_BASE}")
    print(f'curl -H "Authorization: Bearer {token}" {PUBLIC_BASE}/models')
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    data = _load_keys()
    keys = data.get("keys") or {}
    if not keys:
        print("(empty)")
        return 0
    for token, meta in keys.items():
        q = int(meta.get("quota_requests") or 0)
        u = int(meta.get("used_requests") or 0)
        left = "∞" if q == 0 else str(max(0, q - u))
        en = "on" if meta.get("enabled", True) else "OFF"
        print(
            f"{token[:12]}…  name={meta.get('name','')}  {en}  "
            f"used={u}/{q or '∞'}  left={left}"
        )
    return 0


def cmd_set_quota(args: argparse.Namespace) -> int:
    with _lock:
        data = _load_keys()
        token, meta = _find_key(data, token=args.key or "", name=args.name or "")
        if not meta:
            raise SystemExit("key not found")
        q = int(args.quota)
        if q < 0:
            raise SystemExit("quota must be >= 0")
        meta["quota_requests"] = q
        _save_keys(data)
    print("ok", token[:16] + "…", "quota_requests=", q)
    return 0


def cmd_enable_disable(args: argparse.Namespace, enabled: bool) -> int:
    with _lock:
        data = _load_keys()
        token, meta = _find_key(data, token=args.key or "", name=args.name or "")
        if not meta:
            raise SystemExit("key not found")
        meta["enabled"] = enabled
        _save_keys(data)
    print("ok", token[:16] + "…", "enabled=" + str(enabled))
    return 0


def _check_and_reserve(bearer: str) -> Tuple[Optional[int], Optional[bytes], Optional[str]]:
    """Return (status, body, token) if reject; else (None, None, token)."""
    if not bearer:
        return 401, b'{"error":"missing Authorization Bearer"}', None
    with _lock:
        data = _load_keys()
        meta = (data.get("keys") or {}).get(bearer)
        if not meta:
            return 401, b'{"error":"invalid api key"}', None
        if not meta.get("enabled", True):
            return 403, b'{"error":"api key disabled"}', None
        q = int(meta.get("quota_requests") or 0)
        u = int(meta.get("used_requests") or 0)
        if q > 0 and u >= q:
            return 429, b'{"error":"quota exceeded","quota_requests":%d,"used_requests":%d}' % (
                q,
                u,
            ), None
        return None, None, bearer


def _commit_use(bearer: str) -> None:
    with _lock:
        data = _load_keys()
        meta = (data.get("keys") or {}).get(bearer)
        if not meta:
            return
        meta["used_requests"] = int(meta.get("used_requests") or 0) + 1
        meta["last_used_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_keys(data)


def _upstream_key() -> str:
    with _lock:
        data = _load_keys()
        return _ensure_upstream_key(data)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _auth_bearer(self) -> str:
        h = self.headers.get("Authorization") or ""
        if h.lower().startswith("bearer "):
            return h[7:].strip()
        return (self.headers.get("x-api-key") or "").strip()

    def _proxy(self) -> None:
        if self.path in ("/health", "/healthz"):
            self._send(200, b'{"ok":true,"service":"cpa-gateway"}')
            return

        bearer = self._auth_bearer()
        st, body, token = _check_and_reserve(bearer)
        if st is not None:
            self._send(st, body or b"{}")
            return

        up_key = _upstream_key()
        if not up_key:
            self._send(503, b'{"error":"upstream_api_key not configured"}')
            return

        path = UPSTREAM + self.path
        raw = self._read_body()
        sys.stderr.write(f"REQUEST path={path} body_len={len(raw) if raw else 0}\n")

        # Stability guard for OpenCode/ai-sdk: long SSE streams through
        # Cloudflare/nginx/Bun frequently reset. Force chat requests to
        # non-streaming, remove cache session reuse, and cap output budget.
        wants_stream = False
        is_stream = False
        try:
            pth = (self.path or "").split("?", 1)[0]
            if raw:
                js = json.loads(raw)
                if not isinstance(js, dict):
                    js = {}
            else:
                js = {}
            if pth.endswith("/chat/completions") or pth.endswith("/completions"):
                wants_stream = bool(js.get("stream"))
                is_stream = False
                js.pop("promptCacheKey", None)
                js.pop("stream_options", None)
                js["stream"] = False
                try:
                    if int(js.get("max_tokens") or 0) > 8192:
                        js["max_tokens"] = 8192
                except (TypeError, ValueError):
                    js["max_tokens"] = 8192
                raw = json.dumps(js, ensure_ascii=False).encode("utf-8")
            else:
                is_stream = bool(js.get("stream")) if js else False
                raw = json.dumps(js, ensure_ascii=False).encode("utf-8")
        except Exception:
            is_stream = False

        req_headers = {
            "Authorization": f"Bearer {up_key}",
            "Content-Type": self.headers.get("Content-Type") or "application/json",
            "Accept": self.headers.get("Accept") or ("text/event-stream" if is_stream else "application/json"),
            "User-Agent": self.headers.get("User-Agent") or "cpa-gateway/1.0",
        }
        # Optional Host override when upstream expects a public hostname (rare for local CPA).
        if UPSTREAM_HOST:
            req_headers["Host"] = UPSTREAM_HOST

        if wants_stream:
            self._proxy_block_as_stream(path, raw, req_headers, token)
        elif is_stream:
            self._proxy_stream(path, raw, req_headers, token)
        else:
            self._proxy_block(path, raw, req_headers, token)

    def _send_sse(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _completion_to_sse(self, body: bytes) -> bytes:
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return b"data: " + body.replace(b"\n", b" ") + b"\n\ndata: [DONE]\n\n"

        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            return ("data: " + json.dumps(data, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode("utf-8")

        chunks = []
        base = {
            "id": data.get("id"),
            "object": "chat.completion.chunk",
            "created": data.get("created"),
            "model": data.get("model"),
        }
        for choice in data.get("choices") or []:
            msg = choice.get("message") or {}
            idx = choice.get("index", 0)
            fr = choice.get("finish_reason")
            # 1. role chunk
            role_chunk = dict(base)
            role_chunk["choices"] = [{
                "index": idx,
                "delta": {"role": msg.get("role") or "assistant"},
                "finish_reason": None,
            }]
            chunks.append("data: " + json.dumps(role_chunk, ensure_ascii=False) + "\n\n")
            # 2. content chunk (if present)
            content = msg.get("content")
            if content:
                content_chunk = dict(base)
                content_chunk["choices"] = [{
                    "index": idx,
                    "delta": {"content": content},
                    "finish_reason": None,
                }]
                chunks.append("data: " + json.dumps(content_chunk, ensure_ascii=False) + "\n\n")
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                streamed_tool_calls = []
                for tool_index, tool_call in enumerate(tool_calls):
                    if not isinstance(tool_call, dict):
                        continue
                    streamed_tool_call = {
                        "index": tool_index,
                        "id": tool_call.get("id"),
                        "type": tool_call.get("type") or "function",
                    }
                    function = tool_call.get("function")
                    if isinstance(function, dict):
                        streamed_tool_call["function"] = {
                            "name": function.get("name"),
                            "arguments": function.get("arguments") or "",
                        }
                    streamed_tool_calls.append(streamed_tool_call)
                if streamed_tool_calls:
                    tool_chunk = dict(base)
                    tool_chunk["choices"] = [{
                        "index": idx,
                        "delta": {"tool_calls": streamed_tool_calls},
                        "finish_reason": None,
                    }]
                    chunks.append("data: " + json.dumps(tool_chunk, ensure_ascii=False) + "\n\n")
            # 3. finish chunk
            finish_chunk = dict(base)
            finish_chunk["choices"] = [{
                "index": idx,
                "delta": {},
                "finish_reason": fr,
            }]
            chunks.append("data: " + json.dumps(finish_chunk, ensure_ascii=False) + "\n\n")
        if data.get("usage") is not None:
            usage_chunk = dict(base)
            usage_chunk["choices"] = []
            usage_chunk["usage"] = data.get("usage")
            chunks.append("data: " + json.dumps(usage_chunk, ensure_ascii=False) + "\n\n")
        chunks.append("data: [DONE]\n\n")
        return "".join(chunks).encode("utf-8")

    def _proxy_block_as_stream(
        self, path: str, raw: bytes, req_headers: Dict[str, str], token: Optional[str]
    ) -> None:
        req_headers = dict(req_headers)
        req_headers["Accept"] = "application/json"
        for attempt in range(3):
            req = urllib.request.Request(path, data=raw if raw else None, method=self.command, headers=req_headers)
            try:
                with urllib.request.urlopen(req, timeout=3600) as resp:
                    resp_body = resp.read()
                    code = resp.status
            except urllib.error.HTTPError as e:
                resp_body = e.read()
                code = e.code
            except Exception as e:
                tb = traceback.format_exc()
                sys.stderr.write(f"UPSTREAM ERROR path={path} err={e} trace={tb}\n")
                self._send_sse(502, b'data: {"error":"upstream failed"}\n\ndata: [DONE]\n\n')
                return
            if code == 502 and attempt < 2:
                sys.stderr.write(f"RETRY 502 attempt={attempt + 1}/3 path={path}\n")
                time.sleep(0.1)
                continue
            break
        if 200 <= code < 300 and token:
            _commit_use(token)
        try:
            self._send_sse(code, self._completion_to_sse(resp_body) if 200 <= code < 300 else resp_body)
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def _strip_reasoning(self, body: bytes) -> bytes:
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return body
        if not isinstance(data, dict):
            return body
        choices = data.get("choices")
        if isinstance(choices, list):
            for c in choices:
                if isinstance(c, dict):
                    c.pop("native_finish_reason", None)
                    msg = c.get("message")
                    if isinstance(msg, dict):
                        msg.pop("reasoning_content", None)
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def _proxy_block(
        self, path: str, raw: bytes, req_headers: Dict[str, str], token: Optional[str]
    ) -> None:
        for attempt in range(3):
            req = urllib.request.Request(path, data=raw if raw else None, method=self.command, headers=req_headers)
            try:
                with urllib.request.urlopen(req, timeout=3600) as resp:
                    resp_body = resp.read()
                    ctype = resp.headers.get("Content-Type") or "application/json"
                    code = resp.status
            except urllib.error.HTTPError as e:
                resp_body = e.read()
                ctype = e.headers.get("Content-Type") if e.headers else "application/json"
                code = e.code
            except Exception as e:
                tb = traceback.format_exc()
                sys.stderr.write(f"UPSTREAM ERROR path={path} err={e} trace={tb}\n")
                self._send(502, json.dumps({"error": "upstream failed", "detail": str(e), "trace": tb[-300:]}).encode())
                return
            if code == 502 and attempt < 2:
                sys.stderr.write(f"RETRY 502 attempt={attempt + 1}/3 path={path}\n")
                time.sleep(0.1)
                continue
            break
        if 200 <= code < 300 and token:
            _commit_use(token)
        if 200 <= code < 300:
            resp_body = self._strip_reasoning(resp_body)
        try:
            self._send(code, resp_body, content_type=ctype or "application/json")
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def _proxy_stream(
        self, path: str, raw: bytes, req_headers: Dict[str, str], token: Optional[str]
    ) -> None:
        parsed = urllib.parse.urlparse(path)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3600)
        try:
            conn.request(self.command, parsed.path + ("?" + parsed.query if parsed.query else ""),
                         body=raw if raw else None, headers=req_headers)
            resp = conn.getresponse()
            code = resp.status
            ctype = resp.getheader("Content-Type", "text/event-stream")
            if 200 <= code < 300 and token:
                _commit_use(token)
            self._send_stream_headers(code, ctype)
            buf = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        except Exception as e:
            try:
                self._send(502, json.dumps({"error": "stream failed", "detail": str(e)}).encode())
            except Exception:
                pass
        finally:
            try:
                resp.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def _send_stream_headers(self, code: int, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


def cmd_serve(_: argparse.Namespace) -> int:
    with _lock:
        data = _load_keys()
        uk = _ensure_upstream_key(data)
        if not data.get("keys"):
            # bootstrap one key so public endpoint is usable after first deploy
            token = _gen_key()
            data["keys"][token] = {
                "name": "default",
                "enabled": True,
                "quota_requests": 0,
                "used_requests": 0,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _save_keys(data)
            print("bootstrapped default key (unlimited):", token, flush=True)
        elif not uk:
            print("WARN: no upstream_api_key; set in keys.json or API_CREDENTIALS.txt", flush=True)

    httpd = ThreadingHTTPServer((LISTEN, PORT), Handler)
    print(f"cpa-gateway listen {LISTEN}:{PORT} -> {UPSTREAM} keys={KEYS_PATH}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stop", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="CPA multi-key quota gateway")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="run gateway")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("add", help="create a client key")
    sp.add_argument("--name", default="")
    sp.add_argument("--quota", type=int, default=DEFAULT_QUOTA, help="request quota, 0=unlimited")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("list", help="list keys")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("set-quota", help="set quota for a key")
    sp.add_argument("--key", default="")
    sp.add_argument("--name", default="")
    sp.add_argument("quota", type=int)
    sp.set_defaults(func=cmd_set_quota)

    sp = sub.add_parser("disable")
    sp.add_argument("--key", default="")
    sp.add_argument("--name", default="")
    sp.set_defaults(func=lambda a: cmd_enable_disable(a, False))

    sp = sub.add_parser("enable")
    sp.add_argument("--key", default="")
    sp.add_argument("--name", default="")
    sp.set_defaults(func=lambda a: cmd_enable_disable(a, True))

    args = p.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
