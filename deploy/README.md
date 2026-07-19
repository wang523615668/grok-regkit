# deploy/ — optional reverse-proxy helpers

This directory is **not** required to run registration.  
It is a **reference** for putting CLIProxyAPI (and optionally the register Web UI) behind Nginx with multi-key quotas.

**Start here:** [docs/reverse-proxy.md](../docs/reverse-proxy.md)

| File | Purpose |
|------|---------|
| `cpa_gateway.py` | Multi-key + request-quota gateway in front of CLIProxyAPI |
| `cpa-gateway.service` | systemd unit template |
| `nginx-cpa.snippet.conf` | Nginx `location /cpa/` |
| `nginx-register.snippet.conf` | Nginx for Web console `:8092` |
| `env.example` | Environment variables |
| `setup-cliproxyapi.md` | High-level CLIProxy install notes |

Register core paths do **not** import these files.
