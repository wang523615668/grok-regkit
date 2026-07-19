# 反代与 CPA 网关（可选部署）

注册机只负责出号与 mint。**把 4.5 / 号池挂到公网**是下游的事。本仓库 `deploy/` 提供一套**可自建**的参考链路，不是运行注册的必选项。

凭证说明见 [sso-cpa/01-credentials.md](./sso-cpa/01-credentials.md)。

## 架构

```text
Client (OpenAI SDK / curl)
    │  Authorization: Bearer cpa_xxx
    ▼
Nginx (TLS)  location /cpa/
    ▼
cpa-gateway :8318
    │  校验 keys.json 配额
    │  换成 CLIProxy 的 upstream API key
    ▼
CLIProxyAPI :8317
    │  热加载 xai-*.json (OIDC)
    ▼
Grok 4.5

并行（与 CPA 无关）：
  注册 → SSO → 号池 / Web 反代模型
```

| 凭证 | 打哪里 | 不要 |
|------|--------|------|
| SSO cookie | 号池 | 不要当 CPA Bearer |
| `xai-*.json` | 只给 CLIProxy 热加载 | 不要当 HTTP API key 发给终端用户 |
| `cpa_xxx` | 只打公开 `/cpa/` | 不要拿去打号池 |
| CLIProxy upstream key | 只给 gateway / 本机调试 | 不要直接暴露给所有客户端 |

## 你需要准备

1. 本机或 VPS：Python 3.9+（gateway 仅标准库）
2. 已运行的 **CLIProxyAPI**（示例端口 `8317`），且已加载至少一份 `xai-*.json`
3. （推荐）域名 + TLS + Nginx
4. 注册机产出的 `cpa_auths/xai-*.json`（或 hotload 目录）

## 端到端步骤

### 1. 注册并 mint

见仓库根 [README](../README.md) / [LOCAL_RUN.md](../LOCAL_RUN.md)。  
确认 `cpa_auths/xai-*.json` 存在，并复制到 CLIProxy 的 auth 目录（或配置 `cpa_hotload_dir`）。

### 2. 确认 CLIProxy 本机可用

```bash
curl -sS http://127.0.0.1:8317/v1/models \
  -H "Authorization: Bearer YOUR_UPSTREAM_API_KEY"
```

### 3. 配置并启动 cpa-gateway

```bash
cd grok-regkit
# 见 deploy/env.example
export CPA_GATEWAY_ROOT=/opt/cliproxyapi
export CPA_UPSTREAM=http://127.0.0.1:8317
export CPA_PUBLIC_BASE=https://api.example.com/cpa/v1

# 首次可把 upstream key 写入 $CPA_GATEWAY_ROOT/keys.json:
# { "keys": {}, "upstream_api_key": "YOUR_UPSTREAM_API_KEY" }
# 或在同目录 API_CREDENTIALS.txt 中写: API Key: YOUR_UPSTREAM_API_KEY

python deploy/cpa_gateway.py serve
# 监听 0.0.0.0:8318
```

systemd 模板：`deploy/cpa-gateway.service`。

### 4. 签发客户端 key

```bash
python deploy/cpa_gateway.py add --name alice --quota 1000
# 打印 cpa_... 与 curl 示例（base 来自 CPA_PUBLIC_BASE）
python deploy/cpa_gateway.py list
```

`quota=0` 表示不限次数。`disable` / `enable` / `set-quota` 见脚本 `--help`。

### 5. Nginx

将 `deploy/nginx-cpa.snippet.conf` 并入你的 HTTPS `server`，把 `proxy_pass` 指到 gateway（宿主机 Nginx 用 `127.0.0.1:8318`；容器内 Nginx 访问宿主机时常见 `172.17.0.1:8318`）。

注册 Web 面板可选：`deploy/nginx-register.snippet.conf` → `:8092`。

### 6. 客户端调用

```bash
export BASE=https://api.example.com/cpa/v1
export KEY=cpa_YOUR_KEY

curl -sS "$BASE/models" -H "Authorization: Bearer $KEY"

curl -sS "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.5","messages":[{"role":"user","content":"hi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(base_url="https://api.example.com/cpa/v1", api_key="cpa_YOUR_KEY")
print(client.chat.completions.create(
    model="grok-4.5",
    messages=[{"role": "user", "content": "hi"}],
))
```

## Gateway 行为说明

- **多 key + 请求配额**：存在 `keys.json`（路径由 `CPA_GATEWAY_KEYS` / `CPA_GATEWAY_ROOT` 决定）。
- **换 key**：客户端 `cpa_xxx` → 上游 CLIProxy `upstream_api_key`。
- **Chat 稳定性**：对 `/chat/completions` 默认改为非流式再回包；若客户端要 `stream:true`，gateway 可能把整包 completion **伪造成 SSE**（避免长连接在 CDN/Nginx 上被掐断）。需要真上游流式时请改代码或直连本机 CLIProxy。
- **健康检查**：`GET /health` → `{"ok":true,"service":"cpa-gateway"}`（无需 Bearer）。

## 与 SSO 号池

号池（如 grok2api）吃 **SSO**，模型是 Web 线；**不要**把 `cpa_xxx` 或 OIDC 塞进号池。  
CPA 链路只服务 **OIDC → 4.5**。详见 [sso-cpa](./sso-cpa/)。

## 排错

| 现象 | 可能原因 |
|------|----------|
| 401 invalid api key | 客户端 key 不在 `keys.json` |
| 403 disabled | key 被 `disable` |
| 429 quota exceeded | 配额用尽，`set-quota` 或新 key |
| 503 upstream_api_key not configured | 未配置 CLIProxy 真 key |
| 502 upstream failed | CLIProxy 未起 / 端口错 / 无可用 `xai-*.json` |
| models 空或 chat 失败 | mint 未成功、账号无 4.5 权限、热加载目录不对 |

## 安全

- 不要把 `keys.json`、upstream key、`xai-*.json` 提交进 git。
- 公网务必 TLS；限制管理机访问 `8317`（仅本机或 gateway）。
- 本参考实现**无**管理 UI 鉴权；`add`/`list` 依赖你对服务器的 shell 权限。
