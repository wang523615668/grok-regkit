# 01 · 凭证与下游

## 两种凭证

| 类型 | 形态 | 用途 |
|------|------|------|
| **SSO** | Cookie `sso` / `sso-rw` | 号池 Web 反代（4.20 / 4.3 等） |
| **OIDC** | `access_token` + `refresh_token`（`xai-*.json`） | CLIProxyAPI → grok-4.5 |

### SSO 子类型

| 类型 | 特征 | 号池 | 协议 CPA mint |
|------|------|------|----------------|
| **session SSO** | 较短 JWT，非 set-cookie 包装 | 适合 | 适合 |
| **wrapper SSO** | payload 含 `config.success_url`，往往很长 | 不稳 | 需先 materialize |

Hybrid 协议注册常先拿到 wrapper，应转为 session 再落盘 / 入池 / mint。

## 下游

| 下游 | 吃什么 | 模型 |
|------|--------|------|
| 号池（grok2api 等） | SSO | Web 线模型 |
| CLIProxyAPI (CPA) | `xai-*.json` | grok-4.5 |

## 不要混用

- 用 SSO 当 CPA 的 Bearer → 错误
- 用 CPA Key 打号池 → 错误
- 注册成功 ≠ 4.5 可用（还要 mint + CPA 加载）
- free OIDC 导出成功 ≠ 一定能 chat（上游权限不保证）

公网暴露 CLIProxy / 多 key 配额网关：见 [docs/reverse-proxy.md](../reverse-proxy.md) 与 [deploy/](../../deploy/)。
