# 04 · 产物与调用

## 文件

| 路径 | 内容 |
|------|------|
| `accounts_*.txt` / `accounts_hybrid_*.txt` | `邮箱----密码----sso` |
| `cpa_auths/xai-<email>.json` | OIDC，供 CLIProxyAPI |

## 调用（别混 Key）

| 线 | Base 示例 | Key | Model |
|----|-----------|-----|-------|
| SSO 号池 | `https://<host>/v1` | 号池 api_key | `grok-4.20-fast` 等 |
| CPA 4.5 | `https://<host>/cpa/v1` 或本地 CPA `/v1` | CPA api-keys | `grok-4.5` |

## 故障速查

| 现象 | 优先查 |
|------|--------|
| 有号无 4.5 | 是否 mint；json 是否进 CPA 热加载目录 |
| protocol mint 失败 | SSO 是否 wrapper；cookie jar |
| mint 429 | 加大 `cpa_mint_gap_sec` |
| hybrid 无 sso | castle / turnstile / next-action |
| probe 无 grok-4.5 | free 权限不保证 |
