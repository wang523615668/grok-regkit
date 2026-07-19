# grok-regkit local notes (523615668)

Path: `/vol1/1000/openzl/grok-regkit`

## Verified 2026-07-16

- hybrid 单号成功：约 1–2 分钟
- hybrid 批量 5/5：约 6 分钟（`run_hybrid_n.py 5`）
- 产物：`accounts_hybrid_*.txt` + `cpa_auths/xai-*.json`
- 已配置 `cpa_copy_to_hotload=true` → `/vol1/1000/openzl/cpa/auths`
- mint 后 probe models 常 200，**不等于 chat 可用**；很多新号立刻 `permission-denied`
- 直连 chat 必须：7890 代理 + auth 文件 headers
- CPA：`max-retry-credentials: 8`（原 0 全池轮询）

## Run

```bash
cd /vol1/1000/openzl/grok-regkit
export DISPLAY=:99
# 无桌面需 Xvfb :99
# 注意：在 Hermes terminal 前台跑 chromium 可能被 -9 杀掉；用 systemd-run --user
systemd-run --user --collect --unit=grok-regkit-hybrid1 \
  --setenv=DISPLAY=:99 \
  --working-directory=/vol1/1000/openzl/grok-regkit \
  .venv/bin/python -u run_hybrid_one.py

# 或 Web 控制台
.venv/bin/uvicorn web.server:app --host 127.0.0.1 --port 8092 --workers 1
```

## Config

- `config.json` 复用了本机 CF 临时邮箱 `cf-temp-mail.523615668.xyz` + 代理 `127.0.0.1:7890`
- `register_mode=hybrid`，`cpa_prefer_protocol=true`
- `/api/domains` 会 401，但 `/api/new_address` POST 可用（同旧 grok_reg）

## Chat gate (2026-07-16)

- `cpa_probe_chat=true`
- `cpa_chat_required_for_hotload=true`
- chat 用 `/v1/chat/completions`（403/401/429 直接失败）
- **chat 200 才 hotload** 到 `/vol1/1000/openzl/cpa/auths`
- 失败进 `/vol1/1000/openzl/cpa/auths_quarantine` + `chat_failed.txt`
- 本地 `cpa_auths/` 仍会保留 mint 产物（便于复测），但**不进 CPA 轮询**

Config keys: `cpa_probe_chat`, `cpa_chat_required_for_hotload`, `cpa_quarantine_dir`, `cpa_chat_required`（true 时 chat 失败 raise）。

## Pitfalls

1. Hermes 前台 shell 跑 chromium 易被信号杀掉 → 用 `systemd-run --user`
2. 直连 grok 必须走 7890；裸请求会 Network unreachable
3. 探测 chat 必须带 auth 文件里的 headers，否则可能 `426 Upgrade Required`
4. 本机 4G 内存，不要并行多浏览器
5. `/models` 200 + `has_grok_45` **不等于** chat 可用；门禁以 `/chat/completions` 为准
6. 403 号会进 quarantine，**不要**再拷回 hotload 除非复测 chat 200
