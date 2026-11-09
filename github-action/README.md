# GitHub Actions 真浏览器续期

先用 PAT 查到期时间，剩不到 2 小时或已挂起才拉起浏览器续期。

## 文件

| 文件 | 作用 |
|------|------|
| `.github/workflows/blackhost-keepalive.yml` | 定时触发（每 20 分钟），也可手动运行 |
| `github-action/renew_browser.py` | PAT 检查 + 浏览器续期 |
| `github-action/requirements.txt` | Python 依赖 |
| `github-action/sing-box.client.example.json` | sing-box 配置模板 |

## Secrets 配置

仓库 `Settings → Secrets and variables → Actions` 配置：

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT（`bh_pat_...`） |
| `BH_COOKIE_HEADER` | dash 站点完整 Cookie 头 |
| `SINGBOX_CONFIG_JSON` | sing-box 客户端配置 JSON，按模板填写后整段贴入 |
| `BH_PROXY` | 普通 HTTP 代理（未启用 sing-box 时用） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

sing-box 启用后，浏览器走本地 `http://127.0.0.1:18080` 出口；不启用时走 `BH_PROXY`；都不填则直连。

## 运行

1. 在仓库 Actions 页手动运行一次工作流（可勾 `force=true` 强制跑浏览器）
2. 到期前看运行日志确认代理出口 IP 和 PAT 检查结果
3. 失败时下载 `artifacts/` 里的截图排查：`before.png`、`modal.png`、`after_turnstile.png`、`sing-box.log`

## 注意事项

- 续期只能在剩余不到 2 小时或已挂起时操作。
- Turnstile 有效期约 5 分钟，token 必须现取现用。
- 会话 Cookie 过期后需要重新登录并更新 `BH_COOKIE_HEADER`。
- 不要把真实 PAT、Cookie、sing-box 凭证提交到仓库。
