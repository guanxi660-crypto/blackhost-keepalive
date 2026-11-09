# BlackHost 免费服保活

## 本地监控

1. 安装依赖：

```bash
python3 -m pip install requests
```

2. 在项目目录新建 `.env`：

```bash
BH_PAT=bh_pat_xxx
BH_TG_TOKEN=你的 Bot Token
BH_TG_CHAT=你的 Chat ID
```

3. 直接运行：

```bash
PYTHONUTF8=1 python3 blackhost-keepalive.py --monitor
```

默认轮询 3 小时，剩余不足 7 小时或已挂起时发送 Telegram 告警。需要常驻可用 cron 定时拉起 `--check`，或自行编写 systemd 单元。

## 手动续期

告警后在浏览器里操作一次：

1. 打开 `https://dash.blackhost.pl/game/4dbfac39`
2. 点击续期按钮，过完 Cloudflare Turnstile
3. 打开浏览器开发者工具 → Network，找到 `POST /api/v1/servers/game/free/renew`，复制请求体里的 `turnstileToken`
4. 在服务器执行：

```bash
python3 blackhost-keepalive.py --renew \
  --har "/path/to/dash.blackhost.pl_Archive.har" \
  --turnstile "你复制的 turnstileToken"
```

续期后脚本会立即用 PAT 复查新的到期时间。

Cookie 和 token 只用于这一次提交，不写入日志。

## GitHub Actions 真浏览器续期

Actions 版位于 `github-action/`，工作流位于 `.github/workflows/blackhost-keepalive.yml`，每 20 分钟检查到期时间，剩不到 2 小时或已挂起时自动拉起浏览器续期。

仓库 `Settings → Secrets and variables → Actions` 需要配置：

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT（`bh_pat_...`） |
| `BH_COOKIE_HEADER` | dash 站点完整 Cookie 头 |
| `SINGBOX_CONFIG_JSON` | sing-box 客户端配置 JSON（见 `github-action/sing-box.client.example.json`） |
| `BH_PROXY` | 普通 HTTP 代理（可选，没启用 sing-box 时用） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

详细配置见 [github-action/README.md](./github-action/README.md)。

## 常用命令

```bash
# 单轮检查
python3 blackhost-keepalive.py --check

# 常驻监控
python3 blackhost-keepalive.py --monitor
```

## 注意事项

- 续期只能在剩余不到 2 小时或已挂起时操作。
- Turnstile 有效期约 5 分钟，token 必须现取现用，不可复用。
- Better-Auth 会话过期后需要重新登录并更新 `BH_COOKIE_HEADER`。
- 不要把真实 PAT、Cookie、sing-box 凭证提交到仓库。
