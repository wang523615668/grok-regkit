# CLIProxyAPI (CPA) — install notes (example)

This is a **generic** checklist. Use the official CLIProxyAPI / Cliproxy docs for the binary you choose. Paths below are examples only.

## Goal

- Listen OpenAI-compatible API on `127.0.0.1:8317` (or your choice).
- Hot-load OIDC files: `xai-*.json` produced by grok-regkit (`cpa_auths/`).
- Expose a single **upstream** API key (written to e.g. `API_CREDENTIALS.txt` or your config).

## Steps (outline)

1. Install CLIProxyAPI on the server (Docker or binary).
2. Point its **auth directory** at the folder that receives `xai-*.json`  
   - Either copy from register `cpa_auths/`  
   - Or set register `cpa_copy_to_hotload` + `cpa_hotload_dir` to that folder.
3. Confirm locally:

```bash
curl -sS http://127.0.0.1:8317/v1/models \
  -H "Authorization: Bearer YOUR_UPSTREAM_API_KEY"
```

4. Put **cpa-gateway** in front (optional but recommended for multi-user keys):

```bash
export CPA_GATEWAY_ROOT=/opt/cliproxyapi   # keys.json lives here
export CPA_UPSTREAM=http://127.0.0.1:8317
# Put upstream key into keys.json as "upstream_api_key", or keep API_CREDENTIALS.txt with "API Key: ..."
python deploy/cpa_gateway.py serve
```

5. Nginx: see `nginx-cpa.snippet.conf` and [docs/reverse-proxy.md](../docs/reverse-proxy.md).

## Credentials reminder

| Artifact | Role |
|----------|------|
| `xai-*.json` | Account OIDC for CPA to call Grok 4.5 |
| Upstream API key | Single key CLIProxy expects on `/v1/*` |
| `cpa_xxx` keys | Issued by **cpa-gateway** for clients; never the same as SSO |
