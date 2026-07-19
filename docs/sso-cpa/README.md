# SSO / CPA 适配总览

本目录说明 **grok-regkit** 注册成功后如何对接两条下游线。

```text
注册 (browser | hybrid)
        │
        ▼
   SSO cookie
        │
   ┌────┴────┐
   ▼         ▼
 号池 g2a   CPA mint (OIDC)
 Web 模型   xai-*.json → CLIProxyAPI → grok-4.5
```

| 文档 | 内容 |
|------|------|
| [01-credentials.md](./01-credentials.md) | SSO ≠ OIDC；session / wrapper |
| [02-mode-matrix.md](./02-mode-matrix.md) | browser / hybrid 适配矩阵 |
| [03-config.md](./03-config.md) | 相关配置项 |
| [04-outputs.md](./04-outputs.md) | 产物路径与调用注意 |

## 30 秒结论

| 凭证 | 用途 | 谁产出 |
|------|------|--------|
| **SSO** | 网页会话 / 号池 | 注册主流程（两种模式） |
| **OIDC** | free 4.5 | 注册后 **二次 mint**（协议优先，浏览器回退） |

两种 `register_mode` 只决定 **怎么拿到 SSO**；CPA 是同一套后处理。
