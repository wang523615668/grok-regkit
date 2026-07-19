# 03 · 配置开关

主文件：`config.json`（从 `config.example.json` 复制）。

## 注册

| 字段 | 说明 |
|------|------|
| `register_mode` | `browser` \| `hybrid` |

## SSO 号池（可选）

| 字段 | 说明 |
|------|------|
| `grok2api_auto_add_local` / `remote` | 是否写入本地/远端号池 |
| `grok2api_remote_base` / `remote_app_key` | 远端地址与密钥（示例留空） |
| `grok2api_pool_name` | 如 `ssoBasic` |

## CPA / OIDC

| 字段 | 说明 |
|------|------|
| `cpa_export_enabled` | 注册后是否 mint |
| `cpa_auth_dir` | 默认 `./cpa_auths` |
| `cpa_prefer_protocol` | 优先 SSO HTTP device-flow |
| `cpa_protocol_only` | 仅协议，失败不回退浏览器 |
| `cpa_mint_gap_sec` | mint 间隔，防 429 |
| `cpa_proxy` | mint 专用代理；空则用注册代理 |
| `cpa_copy_to_hotload` / `cpa_hotload_dir` | 拷贝到 CLIProxy 热加载目录 |

## 代理

注册用 `proxy_mode` / `proxy`；mint 可用 `cpa_proxy` 分开。
