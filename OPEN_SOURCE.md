# 开源维护说明

本目录是 **精选脱敏快照**，不是私有仓 `grok-register` 的 git 镜像。

## 来源

| 树 | 角色 |
|----|------|
| `../grok-register` | 私有/服务器向（可含 deploy） |
| `../grok-regkit`（本目录） | 对外开源包 |

## 从私有仓同步

在私有仓执行：

```bash
python scripts/export_to_regkit.py
python scripts/export_to_regkit.py --check-only
```

脚本会：

1. 按白名单拷贝 browser / hybrid / protocol / cpa / web / 公开 scripts / tests  
2. 替换私有域名与内网地址为 `127.0.0.1` 占位  
3. **不覆盖** 本树维护的 `README.md`、`LOCAL_RUN.md`、`config.example.json`、`docs/sso-cpa/`

## 发布前检查

- [ ] `export_to_regkit.py --check-only` 通过  
- [ ] 无 `config.json`、账号文件、真实密钥  
- [ ] `docs/sso-cpa` 与代码能力一致  
- [ ] 本地 `pip install -r requirements.txt` 后冒烟 CLI/Web  

## 明确不包含

- `deploy/` 私服脚本  
- 运维对话记录、生产域名、真实号池密钥  
