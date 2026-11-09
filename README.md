# BlackHost 免费服保活

## 窗口对齐续期

按钮 `Przedłuż o 8h` 可用时才点：先用 PAT 算剩余，剩不到 2h 才拉起真浏览器去点它续期。

Actions 定时：

```yaml
- cron: "28 */6 * * *"
```

手动跑一次（强制点按钮）：

```bash
BH_PAT=bh_pat_xxx BH_COOKIE_HEADER="Cookie: ..." PYTHONUTF8=1 python3 github-action/renew_browser.py --force
```

通过标准：日志出现 `🎉 续期成功`，`status=ready`。

## GitHub Actions 用法

工作流位于 `.github/workflows/blackhost-keepalive.yml`。

仓库 `Settings → Secrets and variables → Actions` 配置：

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT（`bh_pat_...`） |
| `BH_COOKIE_HEADER` | dash 站点完整 Cookie 头 |
| `SINGBOX_CONFIG_JSON` | sing-box 配置（见 `github-action/sing-box.client.example.json`） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

详细配置见 [github-action/README.md](./github-action/README.md)，时间算法见 [time.txt](./time.txt)。

## 手动续期

1. 打开 `https://dash.blackhost.pl/game/<SERVER_ID>`
2. 点击续期按钮，过完 Turnstile
3. 浏览器复制 `POST /api/v1/servers/game/free/renew` 的 `turnstileToken`
4. 在服务器执行 `blackhost-keepalive.py --renew --har <HAR> --turnstile <TOKEN>`

## 常用命令

```bash
python3 blackhost-keepalive.py --check
python3 blackhost-keepalive.py --monitor
```

## 注意事项

- 不要把真实 PAT、Cookie、凭证提交到仓库。
