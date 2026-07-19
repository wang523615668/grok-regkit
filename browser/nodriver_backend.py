"""Sync nodriver backend for hybrid Grok registration.

DrissionPage is CDP-heavy (Runtime.enable leak) and CF Turnstile now rejects it.
nodriver (uc) avoids that path; this module exposes a *sync* API so hybrid_register /
token_harvester keep working without rewriting the whole protocol stack.
"""
from __future__ import annotations

import asyncio
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class _LoopThread:
    """Background asyncio loop for nodriver (async-only)."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="nodriver-loop", daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def run(self, coro, timeout: Optional[float] = 120):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        try:
            self._thread.join(timeout=3)
        except Exception:
            pass


def _deep_to_py(obj):
    """Convert nodriver deep_serialized_value / RemoteObject into plain Python."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # Object-as-list-of-pairs: [['k', v], ['k2', v2]] (nodriver deep serialize)
    if isinstance(obj, (list, tuple)) and obj:
        first = obj[0]
        if (
            isinstance(first, (list, tuple))
            and len(first) == 2
            and isinstance(first[0], str)
            and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in obj)
        ):
            out = {}
            for k, v in obj:
                out[str(k)] = _deep_to_py(v)
            return out
        # array of typed nodes
        if isinstance(first, dict) and "type" in first and "value" in first:
            return [_deep_to_py(x) for x in obj]
        return [_deep_to_py(x) for x in obj]

    if isinstance(obj, dict):
        # typed node
        if "type" in obj and ("value" in obj or obj.get("type") in ("undefined", "null")):
            t = obj.get("type")
            if t in ("undefined", "null"):
                return None
            if t in ("string", "number", "boolean"):
                return obj.get("value")
            if t == "array":
                return [_deep_to_py(x) for x in (obj.get("value") or [])]
            if t == "object":
                raw = obj.get("value") or []
                return _deep_to_py(raw) if isinstance(raw, list) else {}
            return obj.get("value")
        return {k: _deep_to_py(v) for k, v in obj.items()}

    try:
        dsv = getattr(obj, "deep_serialized_value", None)
        if dsv is not None:
            val = getattr(dsv, "value", None)
            typ = getattr(dsv, "type_", None) or getattr(dsv, "type", None)
            if typ == "array":
                return [_deep_to_py(x) for x in (val or [])]
            if typ == "object":
                return _deep_to_py(val)
            if typ in ("string", "number", "boolean"):
                return val
            return _deep_to_py(val)
        if getattr(obj, "value", None) is not None:
            return obj.value
    except Exception:
        pass
    return obj


class NodriverPage:
    """Minimal DrissionPage-like facade used by hybrid token harvest."""

    def __init__(self, backend: "NodriverBackend", tab: Any):
        self._backend = backend
        self._tab = tab

    @property
    def url(self) -> str:
        try:
            return str(self._backend.run(self._get_url()) or "")
        except Exception:
            return ""

    async def _get_url(self):
        try:
            return await self._tab.evaluate("location.href", return_by_value=True)
        except Exception:
            return getattr(self._tab, "url", "") or ""

    def run_js(self, script: str, *args) -> Any:
        return self._backend.run(self._eval(script, args))

    async def _eval(self, script: str, args: tuple) -> Any:
        tab = self._tab
        import json

        def _normalize(result):
            return _deep_to_py(result)

        if args:
            payload = json.dumps(list(args), ensure_ascii=False)
            # IMPORTANT: do NOT assign `var arguments = ...` then nest a function —
            # the inner function gets its own empty Arguments object and shadows
            # the array, so arguments[0] becomes undefined and field fills break.
            # Pass the payload as a parameter literally named `arguments` so the
            # existing page scripts keep working.
            wrapped = (
                "(function(){\n"
                f"  var __nd_args = {payload};\n"
                f"  return (function(arguments){{\n{script}\n  }})(__nd_args);\n"
                "})()"
            )
            try:
                res = await tab.evaluate(wrapped, return_by_value=True)
                return _normalize(res)
            except Exception:
                await tab.evaluate(
                    f"window.__nd_args = {payload}; true;", return_by_value=True
                )
                alt = (
                    "(function(){\n"
                    "  var __nd_args = window.__nd_args || [];\n"
                    f"  return (function(arguments){{\n{script}\n  }})(__nd_args);\n"
                    "})()"
                )
                res = await tab.evaluate(alt, return_by_value=True)
                return _normalize(res)

        src = script.strip()
        # Prefer IIFE for multi-statement / return bodies
        if "return " in src or "function" in src[:40] or ";" in src:
            if not (src.startswith("(") or src.startswith("!")):
                src = f"(function(){{ {script} }})()"
        try:
            res = await tab.evaluate(src, return_by_value=True)
            # If return_by_value didn't materialize complex objects, retry without it
            if res is None or (hasattr(res, "deep_serialized_value") and getattr(res, "value", None) is None):
                res2 = await tab.evaluate(src, return_by_value=False)
                return _normalize(res2)
            return _normalize(res)
        except Exception:
            try:
                res = await tab.evaluate(
                    f"(function(){{ {script} }})()", return_by_value=False
                )
                return _normalize(res)
            except Exception as exc:
                raise RuntimeError(f"run_js failed: {exc}") from exc

    def get(self, url: str, timeout: int = 60) -> None:
        self._backend.run(self._navigate(url), timeout=timeout + 10)

    async def _navigate(self, url: str):
        await self._tab.get(url)
        try:
            await self._tab.wait(1.0)
        except Exception:
            pass

    def cookies(self, all_domains: bool = True, all_info: bool = True) -> List[dict]:
        return self._backend.export_cookies()

    @property
    def html(self) -> str:
        try:
            return str(self.run_js("return document.documentElement.outerHTML") or "")
        except Exception:
            return ""

    @property
    def title(self) -> str:
        try:
            return str(self.run_js("return document.title") or "")
        except Exception:
            return ""

    def ele(self, selector: str, timeout: float = 2):
        """Very small subset: @name=x or css:/tag: selectors used by turnstile helpers."""
        return _NdElement(self, selector, timeout)

    @property
    def actions(self):
        return _NdActions(self)

    @property
    def rect(self):
        return type("R", (), {"location": (0, 0), "size": (0, 0)})()


class _NdElement:
    def __init__(self, page: NodriverPage, selector: str, timeout: float = 2):
        self._page = page
        self._selector = selector
        self._timeout = timeout
        self._node = None

    def _resolve(self):
        sel = self._selector
        js = ""
        if sel.startswith("@name="):
            name = sel[6:]
            js = f"return document.querySelector('[name=\"{name}\"]')"
        elif sel.startswith("css:"):
            css = sel[4:]
            js = f"return document.querySelector({css!r})"
        elif sel.startswith("tag:"):
            tag = sel[4:]
            js = f"return document.querySelector({tag!r})"
        else:
            js = f"return document.querySelector({sel!r})"
        # We cannot return real DOM nodes via evaluate easily; use existence checks.
        # For turnstile path we mostly need parent/shadow_root/click — implement via JS.
        self._node = True
        return self

    def parent(self):
        return _NdElement(self._page, f"parent:{self._selector}", self._timeout)

    @property
    def shadow_root(self):
        return _NdShadow(self._page, self._selector)

    def ele(self, selector: str, timeout: float = 2):
        return _NdElement(self._page, f"{self._selector}>>{selector}", timeout)

    def eles(self, selector: str):
        return []

    def click(self):
        # Best-effort: for @name=cf-turnstile-response parent iframe click is handled elsewhere
        try:
            if self._selector.startswith("@name="):
                name = self._selector[6:]
                self._page.run_js(
                    f"""
const el = document.querySelector('[name="{name}"]');
if (el) el.click();
true;
"""
                )
        except Exception:
            pass
        return self

    @property
    def value(self):
        try:
            if self._selector.startswith("@name="):
                name = self._selector[6:]
                return self._page.run_js(
                    f'return (document.querySelector(\'[name="{name}"]\')||{{}}).value||""'
                )
        except Exception:
            return ""
        return ""

    @property
    def rect(self):
        # Approximate iframe rect for turnstile (pierce open shadow DOM)
        try:
            data = self._page.run_js(
                """
(function(){
  function deepFind(root, pred, out){
    try {
      const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
      for (const n of nodes) {
        try { if (pred(n)) out.push(n); } catch(e){}
        try { if (n.shadowRoot) deepFind(n.shadowRoot, pred, out); } catch(e){}
      }
    } catch(e){}
  }
  const found = [];
  deepFind(document, function(n){
    if (!n || !n.tagName || n.tagName.toLowerCase() !== 'iframe') return false;
    const src = String(n.src||n.getAttribute('src')||'');
    return src.includes('challenges.cloudflare.com') || src.includes('turnstile');
  }, found);
  let best = found[0] || null;
  // also check light-DOM fallbacks
  if (!best) best = document.querySelector('#hybrid-turnstile-host iframe')
    || document.querySelector('iframe[src*="challenges.cloudflare.com"]')
    || document.querySelector('iframe[src*="turnstile"]');
  if (!best) return null;
  const r = best.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {x:r.x,y:r.y,w:r.width,h:r.height};
})();
"""
            )
            if isinstance(data, dict):
                return type(
                    "R",
                    (),
                    {
                        "location": (float(data.get("x") or 0), float(data.get("y") or 0)),
                        "size": (float(data.get("w") or 0), float(data.get("h") or 0)),
                    },
                )()
        except Exception:
            pass
        return type("R", (), {"location": (40, 300), "size": (300, 65)})()


class _NdShadow:
    def __init__(self, page: NodriverPage, owner_selector: str):
        self._page = page
        self._owner = owner_selector

    def ele(self, selector: str, timeout: float = 2):
        return _NdElement(self._page, f"shadow:{self._owner}:{selector}", timeout)


class _NdActions:
    def __init__(self, page: NodriverPage):
        self._page = page
        self._x = 0.0
        self._y = 0.0

    def move_to(self, x, y=None):
        if y is None and isinstance(x, (tuple, list)):
            self._x, self._y = float(x[0]), float(x[1])
        else:
            self._x, self._y = float(x), float(y or 0)
        self._page._backend.mouse_move(self._x, self._y)
        return self

    def click(self):
        self._page._backend.mouse_click(self._x, self._y)
        return self


class NodriverBrowser:
    def __init__(self, backend: "NodriverBackend"):
        self._backend = backend
        self.user_data_path = backend.user_data_dir

    def cookies(self) -> List[dict]:
        return self._backend.export_cookies()

    def get_tabs(self):
        page = self._backend.page
        return [page] if page is not None else []

    def new_tab(self, url: str = "about:blank"):
        return self._backend.new_tab(url)

    def get_tab(self, idx: int = 0):
        return self._backend.page

    def quit(self, del_data: bool = True):
        # stop_browser() may already have stopped the backend; be idempotent
        try:
            if get_backend() is self._backend:
                self._backend.stop(del_data=del_data)
                set_backend(None)
            elif self._backend is not None and self._backend._loop_thread is not None:
                self._backend.stop(del_data=del_data)
        except Exception:
            pass


class NodriverBackend:
    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.log = log or (lambda _m: None)
        self._loop_thread: Optional[_LoopThread] = None
        self._browser = None
        self.page: Optional[NodriverPage] = None
        self.user_data_dir = ""
        self._proxy = ""
        self._castle_tokens: List[str] = []
        self._create_email_seen = False
        self._create_email_status = 0
        self._net_hooked = False

    def _lg(self, msg: str) -> None:
        try:
            self.log(msg)
        except Exception:
            pass

    def run(self, coro, timeout: Optional[float] = 120):
        if self._loop_thread is None:
            raise RuntimeError("nodriver not started")
        return self._loop_thread.run(coro, timeout=timeout)

    def start(
        self,
        *,
        proxy: str = "",
        user_data_dir: str = "",
        headless: bool = False,
        extension_path: str = "",
    ) -> "NodriverBackend":
        import nodriver as uc

        self._proxy = (proxy or "").strip()
        if not user_data_dir:
            user_data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ".chrome-data",
                f"nd-{os.getpid()}-{int(time.time())}-{random.randint(1000,9999):x}",
            )
        os.makedirs(user_data_dir, exist_ok=True)
        self.user_data_dir = user_data_dir

        args = [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
            "--disable-blink-features=AutomationControlled",
            "--lang=en-US",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--hide-crash-restore-bubble",
        ]
        if self._proxy:
            args.append(f"--proxy-server={self._proxy}")
        if extension_path and os.path.isdir(extension_path) and not headless:
            # Chromium needs absolute path; load both disable-except and load
            ext = os.path.abspath(extension_path)
            args.append(f"--disable-extensions-except={ext}")
            args.append(f"--load-extension={ext}")

        self._loop_thread = _LoopThread()

        async def _boot():
            browser = await uc.start(
                headless=bool(headless),
                browser_executable_path="/usr/bin/chromium",
                sandbox=False,
                browser_args=args,
                user_data_dir=user_data_dir,
                lang="en-US",
            )
            tab = await browser.get("about:blank")
            # light stealth
            try:
                await tab.evaluate(
                    """
(() => {
  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e){}
  try { if (!window.chrome) window.chrome = {runtime:{}}; } catch(e){}
  try { Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); } catch(e){}
  try { Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]}); } catch(e){}
})();
true;
"""
                )
            except Exception:
                pass
            return browser, tab

        browser, tab = self.run(_boot(), timeout=60)
        self._browser = browser
        self.page = NodriverPage(self, tab)
        try:
            self.install_network_capture()
        except Exception as exc:
            self._lg(f"[nd] network capture install fail: {exc}")
        self._lg(f"[nd] started user_data={user_data_dir} proxy={bool(self._proxy)}")
        return self

    def _maybe_capture_castle(self, url: str, body: str) -> None:
        u = str(url or "")
        s = str(body or "")
        if "CreateEmailValidationCode" in u:
            self._create_email_seen = True
        if not s:
            return
        tok = ""
        if "castleRequestToken" in s:
            try:
                import json
                import re

                j = json.loads(s)
                if isinstance(j, list) and j and isinstance(j[0], dict):
                    tok = str(j[0].get("castleRequestToken") or "")
                elif isinstance(j, dict):
                    tok = str(j.get("castleRequestToken") or "")
                if len(tok) < 200:
                    m = re.search(r'castleRequestToken["\']?\s*:\s*["\']([^"\']{200,})', s)
                    if m:
                        tok = m.group(1)
            except Exception:
                import re

                m = re.search(r'castleRequestToken["\']?\s*:\s*["\']([^"\']{200,})', s)
                if m:
                    tok = m.group(1)
        if not tok:
            import re

            m2 = re.search(r"IBYIll\|[A-Za-z0-9+/=|_-]{200,}", s)
            if m2:
                tok = m2.group(0)
        if tok and len(tok) >= 200:
            if tok not in self._castle_tokens:
                self._castle_tokens.append(tok)
                self._lg(f"[nd] castle capture len={len(tok)} url={u[:80]}")

    def install_network_capture(self) -> bool:
        """CDP Network.requestWillBeSent → capture CreateEmail castle body."""
        if self.page is None or self.page._tab is None:
            return False

        async def _install():
            from nodriver import cdp

            tab = self.page._tab
            # Persist JS hook across navigations
            try:
                await tab.send(
                    cdp.page.add_script_to_evaluate_on_new_document(
                        source=r"""
(function(){
  if (window.__hybrid_net_hooked_boot) return;
  window.__hybrid_net_hooked_boot = true;
  window.__hybrid_castles = window.__hybrid_castles || [];
  window.__hybrid_castle = window.__hybrid_castle || '';
  window.__hybrid_net = window.__hybrid_net || [];
  function storeTok(tok){
    tok = String(tok||'');
    if (tok.length < 200) return;
    window.__hybrid_castle = tok;
    window.__hybrid_castles.push(tok);
  }
  function captureBody(body, url){
    try{
      if(!body) return;
      let s='';
      if (typeof body==='string') s=body;
      else if (body instanceof ArrayBuffer) s=new TextDecoder().decode(body);
      else if (body instanceof Uint8Array) s=new TextDecoder().decode(body);
      else if (typeof body==='object'){ try{s=JSON.stringify(body);}catch(e){return;} }
      else return;
      const u=String(url||'');
      window.__hybrid_net.push({url:u.slice(0,160), len:s.length});
      if (u.includes('CreateEmailValidationCode')) window.__hybrid_create_email_seen=true;
      if (s.includes('castleRequestToken')){
        try{
          const j=JSON.parse(s);
          const tok=(j&&j[0]&&j[0].castleRequestToken)||(j&&j.castleRequestToken);
          if(tok) storeTok(tok);
        }catch(e){
          const m=s.match(/castleRequestToken["']?\s*:\s*["']([^"']{200,})/);
          if(m) storeTok(m[1]);
        }
      }
      const m2=s.match(/IBYIll\|[A-Za-z0-9+/=|_-]{200,}/);
      if(m2) storeTok(m2[0]);
    }catch(e){}
  }
  const ofetch=window.fetch;
  window.fetch=async function(input, init){
    let url='';
    try{
      url=(typeof input==='string')?input:(input&&input.url)||'';
      if(init&&init.body) captureBody(init.body,url);
    }catch(e){}
    return ofetch.apply(this, arguments);
  };
  const oopen=XMLHttpRequest.prototype.open;
  const osend=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return oopen.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(body){captureBody(body,this.__u);return osend.apply(this,arguments);};
})();
"""
                    )
                )
            except Exception as exc:
                self._lg(f"[nd] add_script_on_new_document: {exc}")

            try:
                # maxPostDataSize so castle (~14KB) is included in RequestWillBeSent
                await tab.send(cdp.network.enable(max_post_data_size=262144))
            except Exception:
                try:
                    await tab.send(cdp.network.enable())
                except Exception as exc:
                    self._lg(f"[nd] network.enable: {exc}")

            def on_req(event):
                try:
                    req = getattr(event, "request", None)
                    if req is None:
                        return
                    url = getattr(req, "url", "") or ""
                    post = getattr(req, "post_data", None) or ""
                    has_post = getattr(req, "has_post_data", None)
                    if post:
                        self._maybe_capture_castle(url, post)
                    elif has_post and ("CreateEmail" in url or "accounts.x.ai" in url):
                        # fetch post_data async via getRequestPostData
                        req_id = getattr(event, "request_id", None)
                        if req_id is not None:
                            try:
                                fut = asyncio.run_coroutine_threadsafe(
                                    tab.send(cdp.network.get_request_post_data(request_id=req_id)),
                                    self._loop_thread.loop,
                                )
                                # non-blocking: schedule callback
                                def _done(f):
                                    try:
                                        data = f.result(timeout=5)
                                        body = getattr(data, "post_data", None) or (data if isinstance(data, str) else "")
                                        self._maybe_capture_castle(url, body or "")
                                    except Exception:
                                        pass

                                fut.add_done_callback(_done)
                            except Exception:
                                pass
                    if "CreateEmailValidationCode" in url:
                        self._create_email_seen = True
                        if post:
                            self._maybe_capture_castle(url, post)
                except Exception:
                    pass

            try:
                tab.add_handler(cdp.network.RequestWillBeSent, on_req)
            except Exception as exc:
                self._lg(f"[nd] add_handler RequestWillBeSent: {exc}")
                return False
            self._net_hooked = True
            self._lg("[nd] CDP network capture enabled")
            return True

        try:
            return bool(self.run(_install(), timeout=20))
        except Exception as exc:
            self._lg(f"[nd] install_network_capture: {exc}")
            return False

    def get_captured_castle(self) -> str:
        best = ""
        for t in self._castle_tokens:
            if len(t) > len(best):
                best = t
        # also try page JS store
        try:
            if self.page is not None:
                data = self.page.run_js(
                    """
const list = window.__hybrid_castles || [];
let best = window.__hybrid_castle || '';
for (const t of list) {
  if (String(t||'').length > String(best||'').length) best = t;
}
return String(best||'');
"""
                )
                js_tok = str(data or "")
                if len(js_tok) > len(best):
                    best = js_tok
        except Exception:
            pass
        if len(best) >= 1000 and best.startswith("IBYIll"):
            return best
        if len(best) >= 2000:
            return best
        return best if len(best) >= 1000 else ""

    def clear_captured_castle(self) -> None:
        self._castle_tokens = []
        self._create_email_seen = False
        self._create_email_status = 0

    def stop(self, del_data: bool = True) -> None:
        async def _stop():
            if self._browser is not None:
                try:
                    self._browser.stop()
                except Exception:
                    pass
            return True

        try:
            if self._loop_thread is not None:
                self.run(_stop(), timeout=15)
        except Exception:
            pass
        try:
            if self._loop_thread is not None:
                self._loop_thread.stop()
        except Exception:
            pass
        self._loop_thread = None
        self._browser = None
        self.page = None
        if del_data and self.user_data_dir and os.path.isdir(self.user_data_dir):
            try:
                import shutil

                shutil.rmtree(self.user_data_dir, ignore_errors=True)
            except Exception:
                pass

    def new_tab(self, url: str = "about:blank") -> NodriverPage:
        async def _new():
            tab = await self._browser.get(url, new_tab=True)
            return tab

        tab = self.run(_new())
        self.page = NodriverPage(self, tab)
        return self.page

    def export_cookies(self) -> List[dict]:
        async def _cookies():
            out: List[dict] = []
            try:
                # nodriver browser.cookies is a CookieJar-like helper
                jar = self._browser.cookies
                if jar is None:
                    return out
                # try get_all
                if hasattr(jar, "get_all"):
                    items = await jar.get_all()
                elif callable(jar):
                    items = await jar()
                else:
                    items = jar
                for c in items or []:
                    if isinstance(c, dict):
                        out.append(
                            {
                                "name": c.get("name") or c.get("Name"),
                                "value": c.get("value") or c.get("Value"),
                                "domain": c.get("domain") or c.get("Domain") or ".x.ai",
                                "path": c.get("path") or "/",
                            }
                        )
                    else:
                        out.append(
                            {
                                "name": getattr(c, "name", None) or getattr(c, "Name", ""),
                                "value": getattr(c, "value", None) or getattr(c, "Value", ""),
                                "domain": getattr(c, "domain", None) or ".x.ai",
                                "path": getattr(c, "path", None) or "/",
                            }
                        )
            except Exception as exc:
                self._lg(f"[nd] cookies: {exc}")
            return out

        try:
            return self.run(_cookies(), timeout=20)
        except Exception as exc:
            self._lg(f"[nd] cookies fail: {exc}")
            return []

    def export_cookies_dict(self) -> Dict[str, str]:
        jar: Dict[str, str] = {}
        for c in self.export_cookies():
            n, v = c.get("name"), c.get("value")
            if n and v is not None:
                jar[str(n)] = str(v)
        return jar

    def mouse_move(self, x: float, y: float, steps: int = 12) -> None:
        async def _move():
            try:
                await self.page._tab.mouse_move(x, y, steps=steps)
            except Exception as exc:
                self._lg(f"[nd] mouse_move: {exc}")

        self.run(_move(), timeout=10)

    def mouse_click(self, x: float, y: float) -> None:
        async def _click():
            tab = self.page._tab
            try:
                await tab.mouse_move(x, y, steps=8)
                await asyncio.sleep(0.05 + random.random() * 0.1)
                await tab.mouse_click(x, y)
            except Exception as exc:
                self._lg(f"[nd] mouse_click: {exc}")

        self.run(_click(), timeout=10)

    def type_text(self, text: str, clear: bool = True) -> None:
        """Type into currently focused element via CDP Input (React-friendly)."""
        async def _type():
            from nodriver import cdp

            tab = self.page._tab
            try:
                if clear:
                    # Ctrl+A then Backspace
                    await tab.send(
                        cdp.input_.dispatch_key_event(
                            type_="keyDown",
                            modifiers=2,  # ctrl
                            key="a",
                            code="KeyA",
                            windows_virtual_key_code=65,
                        )
                    )
                    await tab.send(
                        cdp.input_.dispatch_key_event(
                            type_="keyUp",
                            modifiers=2,
                            key="a",
                            code="KeyA",
                            windows_virtual_key_code=65,
                        )
                    )
                    await tab.send(
                        cdp.input_.dispatch_key_event(
                            type_="keyDown",
                            key="Backspace",
                            code="Backspace",
                            windows_virtual_key_code=8,
                        )
                    )
                    await tab.send(
                        cdp.input_.dispatch_key_event(
                            type_="keyUp",
                            key="Backspace",
                            code="Backspace",
                            windows_virtual_key_code=8,
                        )
                    )
                # Prefer insert_text for unicode email
                try:
                    await tab.send(cdp.input_.insert_text(text=str(text)))
                except Exception:
                    for ch in str(text):
                        await tab.send(
                            cdp.input_.dispatch_key_event(
                                type_="keyDown", text=ch, unmodified_text=ch
                            )
                        )
                        await tab.send(
                            cdp.input_.dispatch_key_event(
                                type_="keyUp", text=ch, unmodified_text=ch
                            )
                        )
                        await asyncio.sleep(0.02 + random.random() * 0.03)
            except Exception as exc:
                self._lg(f"[nd] type_text: {exc}")

        self.run(_type(), timeout=30)

    def press_enter(self) -> None:
        async def _enter():
            from nodriver import cdp

            tab = self.page._tab
            try:
                await tab.send(
                    cdp.input_.dispatch_key_event(
                        type_="keyDown",
                        key="Enter",
                        code="Enter",
                        windows_virtual_key_code=13,
                        native_virtual_key_code=13,
                    )
                )
                await tab.send(
                    cdp.input_.dispatch_key_event(
                        type_="keyUp",
                        key="Enter",
                        code="Enter",
                        windows_virtual_key_code=13,
                        native_virtual_key_code=13,
                    )
                )
            except Exception as exc:
                self._lg(f"[nd] press_enter: {exc}")

        self.run(_enter(), timeout=10)

    def _page_diag(self) -> dict:
        page = self.page
        if page is None:
            return {}
        try:
            return page.run_js(
                """
return {
  url: location.href,
  title: document.title,
  inputs: Array.from(document.querySelectorAll('input')).map(n=>({
    type:n.type,name:n.name,ph:n.placeholder,vis:!!(n.offsetWidth||n.offsetHeight)
  })).slice(0,16),
  btns: Array.from(document.querySelectorAll('button,a,[role=button]')).map(n=>(n.innerText||n.getAttribute('aria-label')||'').trim()).filter(Boolean).slice(0,14)
};
"""
            ) or {}
        except Exception as exc:
            return {"err": str(exc)}

    def _dismiss_cookies(self) -> None:
        page = self.page
        if page is None:
            return
        try:
            page.run_js(
                """
(function(){
  const texts = ['Accept All Cookies','Allow All','Accept All','接受全部','全部接受'];
  const nodes = Array.from(document.querySelectorAll('button, [role=button], a'));
  for (const n of nodes) {
    const t = (n.innerText||'').trim();
    if (texts.some(x => t.includes(x))) { n.click(); }
  }
  // close OneTrust preference center if open
  const closeBtns = Array.from(document.querySelectorAll('button, [role=button], a, #onetrust-close-btn-container button'));
  for (const n of closeBtns) {
    const t = ((n.innerText||'')+' '+(n.getAttribute('aria-label')||'')+' '+(n.id||'')+' '+(n.className||'')).toLowerCase();
    if (t.includes('close preference') || t.includes('onetrust-close') || t === 'close' || (n.id||'').includes('onetrust-close')) {
      try { n.click(); } catch(e) {}
    }
  }
  // hide leftover overlays that intercept clicks
  ['#onetrust-consent-sdk','#onetrust-banner-sdk','#onetrust-pc-sdk','.onetrust-pc-dark-filter','#ot-sdk-btn-floating'].forEach(sel=>{
    document.querySelectorAll(sel).forEach(el=>{
      try{ el.style.display='none'; el.style.pointerEvents='none'; el.remove(); }catch(e){}
    });
  });
  return true;
})();
"""
            )
        except Exception:
            pass

    def _click_email_entry(self) -> bool:
        """Click Sign up with email / Continue with email if present."""
        page = self.page
        if page is None:
            return False
        try:
            info = page.run_js(
                """
function isVisible(node){
  if(!node) return false;
  const s=getComputedStyle(node);
  if(s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false;
  const r=node.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
function score(n){
  const t=((n.innerText||'')+' '+(n.getAttribute('aria-label')||'')+' '+(n.getAttribute('href')||'')).replace(/\\s+/g,' ').trim();
  const c=t.replace(/\\s+/g,'').toLowerCase();
  if(c.includes('使用邮箱注册')) return 100;
  if(c.includes('signupwithemail')) return 95;
  if(c.includes('continuewithemail')) return 90;
  if(c.includes('email') && (c.includes('sign')||c.includes('continue')||c.includes('use')||c.includes('with'))) return 80;
  if(c.includes('邮箱')) return 70;
  return 0;
}
const cands=Array.from(document.querySelectorAll('button,a,[role=button]')).filter(isVisible);
let best=null, bestS=0, bestT='';
for(const n of cands){
  const s=score(n);
  if(s>bestS){bestS=s; best=n; bestT=((n.innerText||'')||'').trim();}
}
if(!best||bestS<=0) return null;
const r=best.getBoundingClientRect();
return {text:bestT, x:r.x+r.width/2, y:r.y+r.height/2, score:bestS};
"""
            )
            if isinstance(info, dict) and info.get("x") is not None:
                self._lg(f"[nd] click email-entry '{info.get('text')}' score={info.get('score')}")
                self.mouse_click(float(info["x"]), float(info["y"]))
                try:
                    page.run_js(
                        """
const labels=['sign up with email','continue with email','使用邮箱','email'];
const cands=Array.from(document.querySelectorAll('button,a,[role=button]'));
const best=cands.find(n=>{
  const t=((n.innerText||'')+' '+(n.getAttribute('aria-label')||'')).toLowerCase();
  return labels.some(l=>t.includes(l)) && t.includes('email');
});
if(best) best.click();
true;
"""
                    )
                except Exception:
                    pass
                return True
        except Exception as exc:
            self._lg(f"[nd] click email-entry: {exc}")
        return False

    def _find_email_rect(self):
        page = self.page
        if page is None:
            return None
        try:
            return page.run_js(
                """
const input = Array.from(document.querySelectorAll('input')).find(n=>{
  const style = window.getComputedStyle(n);
  if (style.display==='none' || style.visibility==='hidden') return false;
  const r = n.getBoundingClientRect();
  if (r.width<=0 || r.height<=0) return false;
  const meta=[n.type,n.name,n.id,n.placeholder,n.autocomplete,n.getAttribute('aria-label')].join(' ').toLowerCase();
  return meta.includes('email') || n.type==='email';
});
if (!input) return null;
input.scrollIntoView({block:'center', inline:'center'});
const r = input.getBoundingClientRect();
return {x:r.x+r.width/2, y:r.y+r.height/2, val:String(input.value||'')};
"""
            )
        except Exception:
            return None

    def human_fill_email_and_submit(self, email: str) -> str:
        """Accept cookies if needed, focus email, type with CDP, click Sign up.

        Retries hard on SPA timing: cookie banner / Sign up with email / form paint.
        """
        page = self.page
        if page is None:
            return "no-page"

        last = "no-input"
        for attempt in range(1, 7):
            self._dismiss_cookies()
            time.sleep(0.25)

            rect = self._find_email_rect()
            if not isinstance(rect, dict):
                clicked = self._click_email_entry()
                wait = 1.0 + attempt * 0.45
                self._lg(f"[nd] email form missing try={attempt} clicked_entry={clicked} wait={wait:.1f}s")
                time.sleep(wait)
                rect = self._find_email_rect()

            if not isinstance(rect, dict):
                # soft reload once mid-retries
                if attempt == 3:
                    try:
                        cur = page.url or "https://accounts.x.ai/sign-up?redirect=grok-com"
                        self._lg(f"[nd] reload signup for email form: {cur}")
                        page.get(cur if "sign" in str(cur) else "https://accounts.x.ai/sign-up?redirect=grok-com")
                        time.sleep(2.0)
                        self._click_email_entry()
                        time.sleep(1.5)
                    except Exception as exc:
                        self._lg(f"[nd] reload signup: {exc}")
                diag = self._page_diag()
                self._lg(f"[nd] no-input diag try={attempt} {diag}")
                last = "no-input"
                continue

            try:
                self.mouse_click(float(rect["x"]), float(rect["y"]))
                time.sleep(0.15)
            except Exception:
                pass
            self.type_text(str(email), clear=True)
            time.sleep(0.35)

            val = page.run_js(
                """
const input = Array.from(document.querySelectorAll('input')).find(n=>{
  const meta=[n.type,n.name,n.id,n.placeholder].join(' ').toLowerCase();
  return meta.includes('email') || n.type==='email';
});
return input ? String(input.value||'') : '';
"""
            )
            self._lg(f"[nd] email field value try={attempt} {str(val)[:40]}")
            if str(val or "").strip() != str(email).strip():
                try:
                    page.run_js(
                        """
const email = arguments[0];
const input = Array.from(document.querySelectorAll('input')).find(n=>{
  const meta=[n.type,n.name,n.id,n.placeholder].join(' ').toLowerCase();
  return meta.includes('email') || n.type==='email';
});
if (!input) return false;
input.focus();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
const tracker = input._valueTracker; if (tracker) tracker.setValue('');
if (setter) setter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('input',{bubbles:true,data:email,inputType:'insertText'}));
input.dispatchEvent(new Event('change',{bubbles:true}));
return true;
""",
                        email,
                    )
                except Exception:
                    pass
                val2 = page.run_js(
                    """
const input = Array.from(document.querySelectorAll('input')).find(n=>{
  const meta=[n.type,n.name,n.id,n.placeholder].join(' ').toLowerCase();
  return meta.includes('email') || n.type==='email';
});
return input ? String(input.value||'') : '';
"""
                )
                if str(val2 or "").strip() != str(email).strip():
                    last = "fill-mismatch"
                    self._lg(f"[nd] email fill mismatch try={attempt} got={str(val2)[:40]}")
                    continue

            btn = page.run_js(
                """
const btns = Array.from(document.querySelectorAll('button'));
function score(n){
  const t=((n.innerText||'')+(n.getAttribute('aria-label')||'')).replace(/\\s+/g,' ').trim();
  const tl=t.toLowerCase();
  if (n.disabled) return -1;
  if (tl==='sign up' || tl==='continue' || t==='继续' || t==='注册') return 100;
  if (tl.includes('sign up') || tl.includes('continue')) return 80;
  if (n.type==='submit') return 50;
  return 0;
}
let best=null, bestS=-1;
for (const n of btns){
  const s=score(n); if (s>bestS){bestS=s; best=n;}
}
if (!best || bestS<=0) return null;
const r=best.getBoundingClientRect();
return {text:(best.innerText||'').trim(), x:r.x+r.width/2, y:r.y+r.height/2, score:bestS};
"""
            )
            if isinstance(btn, dict) and btn.get("x") is not None:
                self._lg(f"[nd] click submit '{btn.get('text')}' try={attempt}")
                self.mouse_click(float(btn["x"]), float(btn["y"]))
                try:
                    page.run_js(
                        """
const btns = Array.from(document.querySelectorAll('button'));
const best = btns.find(n=>{
  const t=((n.innerText||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
  return t==='sign up' || t==='continue' || t.includes('sign up');
});
if (best) best.click();
true;
"""
                    )
                except Exception:
                    pass
                return f"submitted:{btn.get('text')}"
            self.press_enter()
            return "enter"

        return last


    def complete_signup_in_browser(
        self,
        *,
        email: str,
        code: str,
        given_name: str,
        family_name: str,
        password: str,
        timeout: int = 180,
    ) -> dict:
        """Finish signup on current page after CreateEmail: code/name/password + native Turnstile."""
        page = self.page
        out = {"ok": False, "sso": "", "stage": "init", "turnstile_len": 0}
        if page is None:
            out["stage"] = "no-page"
            return out

        clean = str(code or "").replace("-", "").strip()
        deadline = time.time() + timeout

        def _accept_cookies():
            try:
                page.run_js(
                    """
(function(){
  const texts=['Accept All Cookies','Allow All','Accept All','接受全部','全部接受'];
  for (const n of Array.from(document.querySelectorAll('button,[role=button],a'))) {
    const t=(n.innerText||'').trim();
    if (texts.some(x=>t.includes(x))) { n.click(); return t; }
  }
  return '';
})();
"""
                )
            except Exception:
                pass

        def _find_input(kind: str):
            return page.run_js(
                """
const kind = arguments[0];
const inputs = Array.from(document.querySelectorAll('input,textarea'));
function vis(n){
  if(!n) return false;
  const s=getComputedStyle(n);
  if(s.display==='none'||s.visibility==='hidden') return false;
  const r=n.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
function meta(n){
  return [n.type,n.name,n.id,n.placeholder,n.autocomplete,n.getAttribute('data-testid'),n.getAttribute('aria-label')].join(' ').toLowerCase();
}
// exact name map first (x.ai signup)
const nameMap = {
  code: ['code','otp','verificationcode'],
  email: ['email'],
  given: ['givenname','firstname','first_name','given_name'],
  family: ['familyname','lastname','last_name','surname','family_name'],
  password: ['password','passwd','newpassword']
};
const want = nameMap[kind] || [];
let best=null;
for (const n of inputs){
  if(!vis(n)) continue;
  const nm = String(n.name||'').toLowerCase();
  const id = String(n.id||'').toLowerCase();
  if (want.some(w => nm===w || id===w || nm.includes(w) || id.includes(w))) { best=n; break; }
}
if(!best){
  for (const n of inputs){
    if(!vis(n)) continue;
    const m=meta(n);
    let ok=false;
    if(kind==='code') ok = m.includes('code') || m.includes('otp') || m.includes('验证') || n.type==='tel' || n.type==='number';
    if(kind==='email') ok = n.type==='email' || m.includes('email');
    if(kind==='given') ok = m.includes('given')||m.includes('first')||m.includes('firstname');
    if(kind==='family') ok = m.includes('family')||m.includes('last')||m.includes('surname');
    if(kind==='password') ok = n.type==='password' || m.includes('password')||m.includes('密码');
    if(ok){ best=n; break; }
  }
}
if(!best) return null;
const r=best.getBoundingClientRect();
return {x:r.x+r.width/2,y:r.y+r.height/2,type:best.type,name:best.name,ph:best.placeholder,val:String(best.value||'')};
""",
                kind,
            )

        def _set_input_js(kind: str, value: str) -> bool:
            try:
                return bool(
                    page.run_js(
                        """
const kind = arguments[0];
const value = String(arguments[1]||'');
const nameMap = {
  code: ['code','otp','verificationcode'],
  email: ['email'],
  given: ['givenname','firstname','first_name','given_name'],
  family: ['familyname','lastname','last_name','surname','family_name'],
  password: ['password','passwd','newpassword']
};
const want = nameMap[kind] || [];
function vis(n){
  if(!n) return false;
  const s=getComputedStyle(n);
  if(s.display==='none'||s.visibility==='hidden') return false;
  const r=n.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
const inputs = Array.from(document.querySelectorAll('input,textarea'));
let best=null;
for (const n of inputs){
  if(!vis(n)) continue;
  const nm=String(n.name||'').toLowerCase();
  const id=String(n.id||'').toLowerCase();
  if (want.some(w => nm===w || id===w || nm.includes(w) || id.includes(w))) { best=n; break; }
}
if(!best){
  for (const n of inputs){
    if(!vis(n)) continue;
    const m=[n.type,n.name,n.id,n.placeholder].join(' ').toLowerCase();
    if(kind==='given' && (m.includes('given')||m.includes('first'))) best=n;
    if(kind==='family' && (m.includes('family')||m.includes('last')||m.includes('surname'))) best=n;
    if(kind==='password' && (n.type==='password'||m.includes('password'))) best=n;
    if(kind==='code' && (m.includes('code')||m.includes('otp'))) best=n;
    if(kind==='email' && (n.type==='email'||m.includes('email'))) best=n;
    if(best) break;
  }
}
if(!best) return false;
best.scrollIntoView({block:'center'});
best.focus();
best.click();
const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')?.set
  || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value')?.set;
const tracker = best._valueTracker;
if (tracker) tracker.setValue('');
if (setter) setter.call(best, value); else best.value = value;
try { best.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,data:value,inputType:'insertText'})); } catch(e) {}
best.dispatchEvent(new InputEvent('input',{bubbles:true,data:value,inputType:'insertText'}));
best.dispatchEvent(new Event('change',{bubbles:true}));
best.blur();
return String(best.value||'') === value;
""",
                        kind,
                        value,
                    )
                )
            except Exception as exc:
                self._lg(f"[nd] set_input_js {kind}: {exc}")
                return False

        def _read_input_val(kind: str) -> str:
            rect = _find_input(kind)
            if isinstance(rect, dict):
                return str(rect.get("val") or "")
            return ""

        def _fill(kind: str, value: str) -> bool:
            value = str(value or "")
            if not value:
                return False
            rect = _find_input(kind)
            if not isinstance(rect, dict):
                ok = _set_input_js(kind, value)
                self._lg(f"[nd] fill {kind} no-rect js={ok}")
                return ok
            try:
                self.mouse_click(float(rect["x"]), float(rect["y"]))
                time.sleep(0.12)
            except Exception:
                pass
            self.type_text(value, clear=True)
            time.sleep(0.15)
            cur = _read_input_val(kind)
            if cur != value:
                # React controlled: force prototype setter
                ok = _set_input_js(kind, value)
                cur = _read_input_val(kind)
                self._lg(f"[nd] fill {kind} cdp_val={cur[:20]!r} js={ok} final={cur[:20]!r}")
            else:
                self._lg(f"[nd] fill {kind} ok len={len(cur)}")
            return _read_input_val(kind) == value or len(_read_input_val(kind)) >= max(1, len(value) - 1)

        def _click_btn(labels, allow_disabled: bool = False) -> str:
            info = page.run_js(
                """
const labels = arguments[0].map(x=>String(x).toLowerCase());
const allowDisabled = !!arguments[1];
const btns = Array.from(document.querySelectorAll('button,[role=button]'));
let best=null, bestS=-1, bestT='', disabled=false;
for (const n of btns){
  if(n.disabled && !allowDisabled) continue;
  const t=((n.innerText||'')+(n.getAttribute('aria-label')||'')).replace(/\\s+/g,' ').trim();
  const tl=t.toLowerCase();
  let s=0;
  for (const lab of labels){
    if(!lab) continue;
    if(tl===lab) s=Math.max(s,100 + lab.length);
    else if(tl.includes(lab)) s=Math.max(s,70 + lab.length);
  }
  // only boost complete-signup when caller asked for complete/signup labels
  const wantsComplete = labels.some(lab => lab.includes('complete') || lab === 'sign up' || lab.includes('signup') || lab.includes('create account') || lab.includes('注册'));
  if(wantsComplete && (tl.includes('complete sign up') || tl.includes('complete signup'))) s=Math.max(s,130);
  if(n.type==='submit' && s>0) s=Math.max(s, s+5);
  if(s>bestS){bestS=s; best=n; bestT=t; disabled=!!n.disabled;}
}
if(!best||bestS<=0) return null;
const r=best.getBoundingClientRect();
return {text:bestT,x:r.x+r.width/2,y:r.y+r.height/2,score:bestS,disabled:disabled};
""",
                list(labels),
                allow_disabled,
            )
            if not isinstance(info, dict):
                return ""
            self._lg(
                f"[nd] click btn '{info.get('text')}' score={info.get('score')} disabled={info.get('disabled')}"
            )
            if info.get("x") is not None:
                self.mouse_click(float(info["x"]), float(info["y"]))
            try:
                page.run_js(
                    """
const labels = arguments[0].map(x=>String(x).toLowerCase());
const allowDisabled = !!arguments[1];
const btns = Array.from(document.querySelectorAll('button,[role=button]'));
let best=null, bestS=-1;
for (const n of btns){
  if(n.disabled && !allowDisabled) continue;
  const t=((n.innerText||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
  let s=0;
  for (const lab of labels){ if(!lab) continue; if(t===lab) s=Math.max(s,100+lab.length); else if(t.includes(lab)) s=Math.max(s,70+lab.length); }
  const wantsComplete = labels.some(lab => lab.includes('complete') || lab === 'sign up' || lab.includes('signup') || lab.includes('create account') || lab.includes('注册'));
  if(wantsComplete && (t.includes('complete sign up') || t.includes('complete signup'))) s=Math.max(s,130);
  if(s>bestS){bestS=s; best=n;}
}
if(best){
  try{ best.removeAttribute('disabled'); best.disabled=false; }catch(e){}
  best.click();
}
true;
""",
                    list(labels),
                    allow_disabled,
                )
            except Exception:
                pass
            return str(info.get("text") or "")

        def _read_sso() -> str:
            jar = self.export_cookies_dict()
            return str(jar.get("sso") or jar.get("sso-rw") or "")

        def _turnstile_tok() -> str:
            try:
                data = page.run_js(
                    """
(function(){
  let tok='';
  try{tok=String(window.__hybrid_turnstile||'');}catch(e){}
  const by=document.querySelector('input[name="cf-turnstile-response"]');
  if(by && by.value) tok=String(by.value);
  try{ if(!tok && window.turnstile && turnstile.getResponse) tok=String(turnstile.getResponse()||''); }catch(e){}
  document.querySelectorAll('input[type=hidden]').forEach(inp=>{
    const v=String(inp.value||'');
    if(v.length>=80 && v.length<10000) tok=v;
  });
  const iframe=document.querySelector('iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"]');
  let rect=null;
  if(iframe){const r=iframe.getBoundingClientRect(); rect={x:r.x,y:r.y,w:r.width,h:r.height};}
  return {tok:tok||'', status:String(window.__hybrid_turnstile_status||''), rect:rect, url:location.href};
})();
"""
                )
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            return {}

        _accept_cookies()
        try:
            snap = self._page_diag()
            self._lg(f"[nd] finish-page {snap}")
        except Exception as exc:
            self._lg(f"[nd] finish-page snap: {exc}")

        # Stage: wait for confirm-code UI (SPA lag after CreateEmail)
        out["stage"] = "code"
        code_filled = False
        code_deadline = min(deadline, time.time() + 45)
        while time.time() < code_deadline and not code_filled:
            _accept_cookies()
            code_rect = _find_input("code")
            if not code_rect:
                code_rect = page.run_js(
                    """
const n = Array.from(document.querySelectorAll('input')).find(el=>{
  if(!el || el.type==='hidden' || el.type==='password' || el.type==='email') return false;
  const r=el.getBoundingClientRect(); return r.width>0 && r.height>0;
});
if(!n) return null;
const r=n.getBoundingClientRect();
return {x:r.x+r.width/2,y:r.y+r.height/2,type:n.type,name:n.name,ph:n.placeholder};
"""
                )
            if isinstance(code_rect, dict):
                self._lg(f"[nd] fill code into {code_rect}")
                try:
                    self.mouse_click(float(code_rect["x"]), float(code_rect["y"]))
                    time.sleep(0.1)
                except Exception:
                    pass
                self.type_text(clean, clear=True)
                time.sleep(0.2)
                if _read_input_val("code") != clean:
                    _set_input_js("code", clean)
                # also try multi-box OTP if value didn't stick as single field
                try:
                    page.run_js(
                        """
const code = arguments[0];
const boxes = Array.from(document.querySelectorAll('input')).filter(el=>{
  if(!el || el.type==='hidden' || el.type==='password' || el.type==='email') return false;
  const r=el.getBoundingClientRect(); return r.width>0 && r.height>0;
});
if(boxes.length >= 4 && code && code.length >= boxes.length){
  for(let i=0;i<boxes.length && i<code.length;i++){
    const input=boxes[i];
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
    const tracker=input._valueTracker; if(tracker) tracker.setValue('');
    const ch=code[i];
    if(setter) setter.call(input, ch); else input.value=ch;
    input.dispatchEvent(new InputEvent('input',{bubbles:true,data:ch,inputType:'insertText'}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
  }
  return boxes.length;
}
return 0;
""",
                        clean,
                    )
                except Exception:
                    pass
                self._lg(f"[nd] code val now={_read_input_val('code')!r}")
                # exact primary button — avoid OneTrust / header text nodes
                clicked = page.run_js(
                    """
function isVisible(n){
  if(!n) return false;
  const s=getComputedStyle(n);
  if(s.display==='none'||s.visibility==='hidden') return false;
  const r=n.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
const wantExact = ['confirm email','confirm e-mail','验证邮箱','确认邮箱'];
const btns = Array.from(document.querySelectorAll('button')).filter(isVisible);
let best=null, bestS=-1, bestT='';
for (const n of btns){
  if(n.disabled) continue;
  // prefer only button's own text, not nested huge blobs
  const t=((n.innerText||n.textContent||'')+'').replace(/\\s+/g,' ').trim();
  const tl=t.toLowerCase();
  if(!t || t.length > 40) continue;
  if(tl.includes('choice') || tl.includes('cookie') || tl.includes('preference') || tl.includes('allow all') || tl.includes('reject') || tl.includes('signing into') || tl.includes('terms') || tl.includes('privacy') || tl.includes('go back')) continue;
  let s=0;
  for (const w of wantExact){
    if(tl===w) s=300;
    else if(tl.includes(w)) s=Math.max(s,200);
  }
  if(tl==='continue' || tl==='verify') s=Math.max(s,80);
  if(s>bestS){bestS=s; best=n; bestT=t;}
}
if(!best) return '';
const r=best.getBoundingClientRect();
return {text:bestT, x:r.x+r.width/2, y:r.y+r.height/2, score:bestS};
"""
                )
                if isinstance(clicked, dict) and clicked.get("x") is not None:
                    self._lg(f"[nd] confirm-email target '{clicked.get('text')}' score={clicked.get('score')}")
                    self.mouse_click(float(clicked["x"]), float(clicked["y"]))
                    try:
                        page.run_js(
                            """
const want=['confirm email','confirm e-mail','验证邮箱','确认邮箱','continue','verify'];
const btns=Array.from(document.querySelectorAll('button'));
const best=btns.find(n=>{
  const t=((n.innerText||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
  if(!t || t.length>40) return false;
  if(t.includes('choice')||t.includes('cookie')||t.includes('signing into')) return false;
  return want.some(w=>t===w || t.includes(w));
});
if(best) best.click();
true;
"""
                        )
                    except Exception:
                        pass
                else:
                    self._lg(f"[nd] confirm-email no exact target: {clicked}")
                    _click_btn(["confirm email", "验证邮箱", "确认邮箱"])
                time.sleep(2.0)
                code_filled = True
                try:
                    self._lg(f"[nd] after-code {self._page_diag()}")
                except Exception:
                    pass
                break
            # maybe already past code stage
            if _find_input("password") or _find_input("given"):
                self._lg("[nd] code stage skipped; profile already visible")
                break
            time.sleep(0.8)
        if not code_filled and not (_find_input("password") or _find_input("given")):
            self._lg(f"[nd] no code input found for browser-finish diag={self._page_diag()}")

        # Stage: profile fields (poll; SPA steps may be sequential)
        out["stage"] = "profile"
        filled_any = False
        profile_deadline = min(deadline, time.time() + 50)
        while time.time() < profile_deadline:
            self._dismiss_cookies()
            g = _find_input("given")
            f = _find_input("family")
            p = _find_input("password")
            if g or f or p:
                self._lg(f"[nd] profile fields given={bool(g)} family={bool(f)} password={bool(p)}")
            if g:
                if _fill("given", given_name):
                    filled_any = True
            if f:
                if _fill("family", family_name):
                    filled_any = True
            if p:
                if _fill("password", password):
                    filled_any = True
            # values present? force complete even if fill check flaky
            vals = {
                "given": _read_input_val("given"),
                "family": _read_input_val("family"),
                "password": _read_input_val("password"),
            }
            if any(vals.values()):
                filled_any = True
                self._lg(
                    f"[nd] profile vals g={vals['given'][:12]!r} "
                    f"f={vals['family'][:12]!r} p_len={len(vals['password'])}"
                )
            if filled_any and vals.get("given") and vals.get("family") and vals.get("password"):
                # dump button / turnstile state before click
                try:
                    bstate = page.run_js(
                        """
const b = Array.from(document.querySelectorAll('button')).find(n=>{
  const t=((n.innerText||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
  return t.includes('complete sign') || t==='sign up' || t.includes('create account');
});
const cf = document.querySelector('input[name="cf-turnstile-response"]');
const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"],iframe[src*="turnstile"],#hybrid-turnstile-host iframe');
const net=(window.__hybrid_net||[]).slice(-8);
let rect=null;
if(iframe){const r=iframe.getBoundingClientRect(); rect={x:r.x,y:r.y,w:r.width,h:r.height,src:(iframe.src||'').slice(0,80)};}
return {
  text:b?(b.innerText||'').trim():'',
  disabled:b?!!b.disabled:null,
  aria:b?b.getAttribute('aria-disabled'):null,
  type:b?b.type:null,
  form:b?!!b.form:false,
  ts:!!iframe || !!document.querySelector('div.cf-turnstile,[data-sitekey],script[src*="turnstile"]'),
  tsVal:String((cf&&cf.value)||'').length,
  tsStatus:String(window.__hybrid_turnstile_status||''),
  rect:rect,
  net:net,
  vals:{
    g:String((document.querySelector('input[name=givenName]')||{}).value||''),
    f:String((document.querySelector('input[name=familyName]')||{}).value||''),
    p:String((document.querySelector('input[name=password],input[type=password]')||{}).value||'').length
  },
  err:Array.from(document.querySelectorAll('[role=alert],.error,p,span')).map(n=>(n.innerText||'').trim()).filter(t=>t && t.length<140 && /error|invalid|required|failed|try again|password|too short|weak|verify/i.test(t)).slice(0,8)
};
"""
                    )
                    self._lg(f"[nd] complete-btn state={bstate}")
                except Exception as exc:
                    self._lg(f"[nd] complete-btn state err: {exc}")

                # Profile page usually hosts managed/invisible Turnstile — wait/solve before submit
                st = _turnstile_tok()
                tok = str((st or {}).get("tok") or "")
                if len(tok) < 80:
                    # try human click native iframe if present
                    rect = (st or {}).get("rect") if isinstance(st, dict) else None
                    if isinstance(rect, dict) and rect.get("w"):
                        try:
                            cx = float(rect["x"]) + float(rect["w"]) * (0.15 + random.random() * 0.2)
                            cy = float(rect["y"]) + float(rect["h"]) * (0.45 + random.random() * 0.15)
                            self.mouse_click(cx, cy)
                            self._lg(f"[nd] pre-submit click turnstile ({cx:.0f},{cy:.0f})")
                        except Exception:
                            pass
                    # short native poll
                    for _ in range(8):
                        st = _turnstile_tok()
                        tok = str((st or {}).get("tok") or "")
                        if len(tok) >= 80:
                            break
                        time.sleep(0.6)
                if len(tok) < 80:
                    # fall back to inject path briefly (same page)
                    try:
                        tok2 = self.get_turnstile_token(timeout=25)
                        if tok2:
                            tok = tok2
                            page.run_js(
                                """
const token = String(arguments[0]||'');
const cf = document.querySelector('input[name="cf-turnstile-response"]');
if(!cf || !token) return 0;
const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
if(setter) setter.call(cf, token); else cf.value=token;
cf.dispatchEvent(new Event('input',{bubbles:true}));
cf.dispatchEvent(new Event('change',{bubbles:true}));
return String(cf.value||'').length;
""",
                                tok,
                            )
                            self._lg(f"[nd] injected turnstile into form len={len(tok)}")
                    except Exception as exc:
                        self._lg(f"[nd] pre-submit turnstile: {exc}")
                else:
                    out["turnstile_len"] = len(tok)
                    self._lg(f"[nd] profile-page turnstile ready len={len(tok)}")

                # focus password then submit via form API + button click
                try:
                    page.run_js(
                        """
const labels = ['complete sign up','complete signup','sign up','create account'];
const btns = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'));
let best=null;
for (const n of btns){
  const t=((n.innerText||n.value||'')+'').replace(/\\s+/g,' ').trim().toLowerCase();
  if(t.includes('choice')||t.includes('cookie')||t.includes('preference')) continue;
  if(labels.some(l=>t===l || t.includes(l))) { best=n; break; }
}
const form = (best && best.form) || document.querySelector('form');
if(best){
  try{ best.removeAttribute('disabled'); best.disabled=false; }catch(e){}
  try{ best.focus(); }catch(e){}
  best.click();
}
if(form){
  try{
    if(typeof form.requestSubmit === 'function') form.requestSubmit(best||undefined);
    else form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  }catch(e){}
}
return {clicked:!!best, form:!!form};
"""
                    )
                except Exception as exc:
                    self._lg(f"[nd] form submit js: {exc}")
                _click_btn(
                    [
                        "complete sign up",
                        "complete signup",
                        "create account",
                        "sign up",
                        "注册",
                        "创建账户",
                    ]
                )
                try:
                    self.press_enter()
                except Exception:
                    pass
                time.sleep(1.8)
                try:
                    net = page.run_js("return (window.__hybrid_net||[]).slice(-10);")
                    self._lg(f"[nd] after-complete net={net}")
                except Exception:
                    pass
            elif filled_any:
                time.sleep(0.5)
            sso = _read_sso()
            if sso and len(sso) > 40:
                out["ok"] = True
                out["sso"] = sso
                out["stage"] = "sso"
                return out
            st = _turnstile_tok()
            if isinstance(st, dict) and (st.get("rect") or len(str(st.get("tok") or "")) >= 80):
                break
            if not (g or f or p):
                time.sleep(0.8)
            else:
                time.sleep(0.5)

        # Stage: wait native turnstile + optional re-submit
        out["stage"] = "turnstile"
        clicked_ts = False
        while time.time() < deadline:
            sso = _read_sso()
            if sso and len(sso) > 40:
                out["ok"] = True
                out["sso"] = sso
                out["stage"] = "sso"
                self._lg(f"[nd] browser signup got sso len={len(sso)}")
                return out
            st = _turnstile_tok()
            tok = str((st or {}).get("tok") or "")
            if len(tok) >= 80:
                out["turnstile_len"] = len(tok)
                self._lg(f"[nd] native turnstile len={len(tok)}")
                _click_btn(["complete sign up", "complete signup", "sign up", "continue", "create account", "注册", "继续"])
                time.sleep(1.5)
            rect = (st or {}).get("rect") if isinstance(st, dict) else None
            if isinstance(rect, dict) and rect.get("w") and (not clicked_ts or int(time.time()) % 6 == 0):
                try:
                    cx = float(rect["x"]) + float(rect["w"]) * (0.15 + random.random() * 0.2)
                    cy = float(rect["y"]) + float(rect["h"]) * (0.45 + random.random() * 0.15)
                    self.mouse_click(cx, cy)
                    clicked_ts = True
                    self._lg(f"[nd] click native turnstile ({cx:.0f},{cy:.0f})")
                except Exception as exc:
                    self._lg(f"[nd] turnstile click: {exc}")
            # keep filling profile if still on profile page
            if _find_input("password") or _find_input("given"):
                _fill("given", given_name)
                _fill("family", family_name)
                _fill("password", password)
                _click_btn(
                    ["complete sign up", "complete signup", "sign up", "continue", "create account", "注册", "继续"],
                    allow_disabled=True,
                )
            time.sleep(1.0)

        out["stage"] = "timeout"
        out["sso"] = _read_sso()
        out["ok"] = bool(out["sso"] and len(out["sso"]) > 40)
        try:
            self._lg(f"[nd] finish timeout diag={self._page_diag()} ts={_turnstile_tok()}")
        except Exception:
            pass
        return out

    def get_turnstile_token(self, timeout: int = 90) -> str:
        """Find native / injected Turnstile, human-like click, poll token.

        Prefer existing widgets; only inject if none present. Re-inject on error.
        """
        deadline = time.time() + timeout
        page = self.page
        if page is None:
            return ""

        # patch MouseEvent screen coords early
        try:
            page.run_js(
                """
(function(){
  if (window.__ts_patched) return true;
  window.__ts_patched = true;
  var sx = 800 + Math.floor(Math.random()*400);
  var sy = 400 + Math.floor(Math.random()*300);
  try { Object.defineProperty(MouseEvent.prototype,'screenX',{get:function(){return sx;},configurable:true}); } catch(e){}
  try { Object.defineProperty(MouseEvent.prototype,'screenY',{get:function(){return sy;},configurable:true}); } catch(e){}
  return true;
})();
"""
            )
        except Exception:
            pass

        def _deep_find_iframe_js() -> str:
            # Shared JS: pierce open shadow DOM (Turnstile mounts iframe inside shadow)
            return r"""
function __deepIframes(){
  const out=[];
  function walk(root){
    try{
      const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
      for (const n of nodes){
        try{
          if (n.tagName && n.tagName.toLowerCase()==='iframe'){
            const src=String(n.src||n.getAttribute('src')||'');
            if (src.includes('challenges.cloudflare.com') || src.includes('turnstile')) out.push(n);
          }
        }catch(e){}
        try{ if (n.shadowRoot) walk(n.shadowRoot); }catch(e){}
      }
    }catch(e){}
  }
  walk(document);
  return out;
}
function __bestIframeRect(){
  const list = __deepIframes();
  let best = list[0] || document.querySelector('#hybrid-turnstile-host iframe')
    || document.querySelector('iframe[src*="challenges.cloudflare.com"]')
    || document.querySelector('iframe[src*="turnstile"]');
  if (!best) return null;
  const r = best.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {x:r.x,y:r.y,w:r.width,h:r.height,src:String(best.src||'').slice(0,120)};
}
"""

        def _has_widget() -> bool:
            try:
                return bool(
                    page.run_js(
                        _deep_find_iframe_js()
                        + """
(function(){
  const ifr = __deepIframes().length;
  return !!(ifr
    || document.querySelector('input[name="cf-turnstile-response"]')
    || document.querySelector('#hybrid-turnstile-host')
    || document.querySelector('div.cf-turnstile,[data-sitekey]'));
})();
"""
                    )
                )
            except Exception:
                return False

        def _inject(force: bool = False) -> None:
            try:
                page.run_js(
                    """
(function(force){
  var st = String(window.__hybrid_turnstile_status||'');
  if (!force && (st==='rendered' || st==='done' || st==='loading' || st==='waiting-api')) {
    return st;
  }
  window.__hybrid_turnstile = '';
  window.__hybrid_turnstile_status = 'init';
  var sitekey = '0x4AAAAAAAhr9JGVDZbrZOo0';
  try {
    var html = document.documentElement.innerHTML || '';
    var m = html.match(/sitekey[\"']?\\s*[:=]\\s*[\"'](0x4[^\"']+)/i);
    if (m) sitekey = m[1];
    var el = document.querySelector('[data-sitekey]');
    if (el) {
      var sk = el.getAttribute('data-sitekey') || '';
      if (sk.indexOf('0x4')===0) sitekey = sk;
    }
  } catch(e){}
  function renderWhenReady(){
    if (!window.turnstile || typeof turnstile.render !== 'function') {
      window.__hybrid_turnstile_status = 'waiting-api';
      return false;
    }
    var host = document.getElementById('hybrid-turnstile-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'hybrid-turnstile-host';
      host.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#111;padding:6px;';
      document.body.appendChild(host);
    } else { host.innerHTML=''; }
    try {
      turnstile.render(host, {
        sitekey: sitekey,
        theme: 'dark',
        size: 'flexible',
        callback: function(t){ window.__hybrid_turnstile=String(t||''); window.__hybrid_turnstile_status='done'; },
        'error-callback': function(){ window.__hybrid_turnstile_status='error'; },
        'expired-callback': function(){ window.__hybrid_turnstile_status='expired'; }
      });
      window.__hybrid_turnstile_status = 'rendered';
      return true;
    } catch(e) {
      window.__hybrid_turnstile_status = 'render-fail';
      return false;
    }
  }
  if (renderWhenReady()) return 'rendered';
  var old = document.getElementById('hybrid-cf-script');
  if (force && old) { try { old.remove(); } catch(e){} }
  if (!document.getElementById('hybrid-cf-script')) {
    var s = document.createElement('script');
    s.id = 'hybrid-cf-script';
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.async = true;
    s.onload = function(){ renderWhenReady(); };
    s.onerror = function(){ window.__hybrid_turnstile_status='script-fail'; };
    document.head.appendChild(s);
  }
  var n=0; var t=setInterval(function(){ n++; if (renderWhenReady()||n>40) clearInterval(t); }, 250);
  return 'loading';
})(arguments[0]);
"""
                    ,
                    bool(force),
                )
                self._lg(f"[nd] turnstile inject force={force}")
            except Exception as exc:
                self._lg(f"[nd] turnstile inject: {exc}")

        if not _has_widget():
            _inject(force=False)
        else:
            self._lg("[nd] turnstile native/existing widget present")

        clicked = False
        attempt = 0
        last_status = ""
        while time.time() < deadline:
            attempt += 1
            data = None
            # read token
            try:
                data = page.run_js(
                    _deep_find_iframe_js()
                    + """
(function(){
  var tok = '';
  try { tok = String(window.__hybrid_turnstile || ''); } catch(e){}
  if (!tok) {
    var byInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (byInput) tok = String(byInput.value || '').trim();
  }
  // pierce shadow for response inputs too
  if (!tok) {
    (function walk(root){
      try {
        root.querySelectorAll('input[name="cf-turnstile-response"],input[type=hidden]').forEach(function(inp){
          var v = String(inp.value||'').trim();
          if (v.length >= 80 && v.length < 10000) tok = v;
        });
        root.querySelectorAll('*').forEach(function(n){
          try { if (n.shadowRoot) walk(n.shadowRoot); } catch(e){}
        });
      } catch(e){}
    })(document);
  }
  try {
    if (!tok && window.turnstile && typeof turnstile.getResponse === 'function')
      tok = String(turnstile.getResponse() || '').trim();
  } catch(e){}
  var ifr = __deepIframes().length;
  var rect = __bestIframeRect();
  return {
    tok: tok||'',
    status: String(window.__hybrid_turnstile_status||''),
    iframes: ifr,
    rect: rect
  };
})();
"""
                )
                if isinstance(data, dict):
                    tok = str(data.get("tok") or "").strip()
                    st = str(data.get("status") or "")
                    if st and st != last_status:
                        self._lg(
                            f"[nd] turnstile status={st} iframes={data.get('iframes')} "
                            f"rect={bool(data.get('rect'))}"
                        )
                        last_status = st
                    if len(tok) >= 80:
                        self._lg(f"[nd] turnstile ok len={len(tok)} status={st}")
                        return tok
                    if st in ("error", "expired", "render-fail", "script-fail") and attempt % 8 == 0:
                        _inject(force=True)
                        clicked = False
                else:
                    tok = str(data or "").strip()
                    if len(tok) >= 80:
                        return tok
            except Exception:
                pass

            # human-like click on iframe (checkbox-ish left-center; pierce shadow)
            if (not clicked) or attempt % 5 == 0:
                try:
                    rect = None
                    if isinstance(data, dict):
                        rect = data.get("rect")
                    if not (isinstance(rect, dict) and rect.get("w")):
                        rect = page.run_js(
                            _deep_find_iframe_js()
                            + """
(function(){ return __bestIframeRect(); })();
"""
                        )
                    if isinstance(rect, dict) and rect.get("w"):
                        # checkbox sits on left side of managed widget (~30x30 inside ~300x65)
                        cx = float(rect["x"]) + float(rect["w"]) * (0.08 + random.random() * 0.12)
                        cy = float(rect["y"]) + float(rect["h"]) * (0.40 + random.random() * 0.20)
                        self._lg(
                            f"[nd] click turnstile at ({cx:.0f},{cy:.0f}) "
                            f"box={float(rect['w']):.0f}x{float(rect['h']):.0f} "
                            f"iframes={data.get('iframes') if isinstance(data, dict) else '?'}"
                        )
                        self.mouse_click(cx, cy)
                        clicked = True
                        # second micro-click slightly offset (helps stubborn widgets)
                        if attempt % 10 == 0:
                            self.mouse_click(cx + 3 + random.random() * 4, cy + random.random() * 2)
                    elif attempt % 8 == 0:
                        # fallback fixed bottom-right inject host area
                        self._lg("[nd] no iframe rect; click hybrid host fallback")
                        try:
                            hr = page.run_js(
                                """
(function(){
  var h=document.getElementById('hybrid-turnstile-host')
    || document.querySelector('.cf-turnstile,[data-sitekey]');
  if(!h) return null;
  var r=h.getBoundingClientRect();
  return {x:r.x,y:r.y,w:r.width,h:r.height};
})();
"""
                            )
                            if isinstance(hr, dict) and hr.get("w"):
                                self.mouse_click(
                                    float(hr["x"]) + max(20.0, float(hr["w"]) * 0.15),
                                    float(hr["y"]) + max(18.0, float(hr["h"]) * 0.5),
                                )
                                clicked = True
                        except Exception:
                            pass
                        if not _has_widget():
                            _inject(force=True)
                except Exception as exc:
                    self._lg(f"[nd] click turnstile: {exc}")

            time.sleep(1.0)

        self._lg("[nd] turnstile timeout")
        return ""


# process-global singleton used by grok_register_ttk adapters
_BACKEND: Optional[NodriverBackend] = None


def get_backend() -> Optional[NodriverBackend]:
    return _BACKEND


def set_backend(b: Optional[NodriverBackend]) -> None:
    global _BACKEND
    _BACKEND = b
