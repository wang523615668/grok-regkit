# 点状接入 grokRegister-cpa 能力（本机）

不替换主线 hybrid；只补了公开仓库里最有用的三块。

## 1) 远程 CPA Management 入库

chat 门禁通过后（与 hotload 同策略），除拷贝到 `cpa_hotload_dir` 外，还会：

```text
POST {cpa_remote_url}/v0/management/auth-files?name=xai-<email>.json
Authorization: Bearer {cpa_management_key | $CPA_MGMT_KEY}
```

本机已写：

```json
"cpa_remote_url": "http://127.0.0.1:8317"
```

密钥优先读环境变量 `CPA_MGMT_KEY`（`/vol1/1000/openzl/cpa/.secrets.env`），不必把 key 写进 `config.json`。

可选：

```json
"cpa_remote_upload_on_chat_fail": false,
"cpa_remote_timeout_sec": 30
```

`register_one_then_kill.sh` 会自动 `source` `.secrets.env`。

## 2) 邮箱：MailNest / CloudMail

`email_provider` 现支持：

- `duckmail` / `cloudflare` / `yyds`（原有）
- `mailnest`（需 `mailnest_api_key`，可选 `mailnest_project_code`）
- `cloudmail`（需 `cloudmail_url` + 管理员邮箱密码 + `defaultDomains`）

hybrid / browser 共用 `get_email_and_token` / `get_oai_code`。

## 3) 连通性检查

```bash
cd /vol1/1000/openzl/grok-regkit
set -a; . /vol1/1000/openzl/cpa/.secrets.env; set +a
./.venv/bin/python scripts/check_connectivity.py
```

Web：`GET /api/connectivity`（需 access key，若配置了密码）。

## 验证（已测）

- connectivity：代理 / CF 邮箱 / CPA 本地+远程 Management 全 OK
- `upload_cpa_auth_remote`：对本机 `:8317` 上传已有 `xai-*.json` 成功
- 主文件 `py_compile` 通过
