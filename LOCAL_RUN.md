# 本机运行

```bash
cd grok-regkit
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.json config.json
```

编辑 `config.json`：

1. 邮箱：`email_provider` + Cloudflare/DuckMail 等密钥  
2. 代理：`proxy_mode` / `proxy`  
3. 模式：`register_mode` = `browser` 或 `hybrid`  
4. CPA：默认 `cpa_export_enabled=true`，产物在 `cpa_auths/`

运行：

```bash
# Web
uvicorn web.server:app --host 127.0.0.1 --port 8092

# CLI（仍会启动 Chromium）
python grok_register_ttk.py --cli
```

可选环境变量（号池联动）：

```text
GROK2API_INTERNAL_URL=http://127.0.0.1:8010
GROK2API_PUBLIC_URL=http://127.0.0.1:8010
GROK_REGISTER_ACCESS_PASSWORD=   # Web 访问密码，空则不鉴权
```

存量账号补 OIDC：

```bash
python scripts/backfill_cpa_xai_from_accounts.py
```

凭证说明：[docs/sso-cpa/](./docs/sso-cpa/)。
