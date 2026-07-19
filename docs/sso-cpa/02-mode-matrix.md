# 02 · 注册模式适配矩阵

`register_mode`: `browser` | `hybrid`

## 总表

| 能力 | browser | hybrid |
|------|---------|--------|
| 主路径 | 全程 Chromium UI | 短浏览器采 castle/turnstile + 协议 RPC/Server Action |
| 产出 session SSO | 通常直接 | 常需 materialize |
| SSO → 号池 | 适配 | 适配（session 后） |
| SSO → CPA 协议 mint | 适配 | 适配（session + 尽量带完整 cookie jar） |
| CPA 浏览器 mint 回退 | 适配 | 适配（有 password） |
| 速度 / 资源 | 慢、吃内存 | 更快，仍依赖浏览器过 CF |

## 后处理（共用）

```text
SSO 落盘
  → 可选 NSFW
  → 可选号池入池
  → CPA mint（prefer protocol → browser fallback）
```

## 推荐

| 场景 | 模式 | CPA |
|------|------|-----|
| 求稳 | browser | prefer protocol，允许浏览器回退 |
| 求快 | hybrid | 同上；确认 session SSO |
| 只协议 mint | 任选 | `cpa_protocol_only=true`（必须 session SSO） |
| 高峰只出号 | 任选 | 可关 `cpa_export_enabled`，事后 backfill |
