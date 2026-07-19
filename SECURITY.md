# Security

## Do not commit

- `config.json` / `.env` with real keys
- `accounts_*.txt`, cookies, Chrome profiles
- `cpa_auths/xai-*.json` (OIDC tokens)
- Private proxies, mail API keys, pool admin keys

## Report issues

Open a GitHub Issue **without** pasting tokens, cookies, or account passwords.  
Redact emails if needed (`a***@example.com`).

## Runtime tips

- Bind Web UI to `127.0.0.1` unless behind auth
- Prefer env `GROK_REGISTER_ACCESS_PASSWORD` for remote access
- Treat exported SSO / OIDC as secrets
