# BlackHost 免费服保活

## 自动续期

按钮 `Przedłuż o 8h` 可用时点它，点过就重置：无论剩余多少，倒计时直接回到满 8 小时（不是叠加）。

续期窗口 = 到期前 2h。6 小时一跳，跳点偶尔比窗点早几秒~几分钟，脚本进浏览器后
原地等按钮亮（最多 12 分钟）就点；晚到更是直接点。每跳都续上 → 无挂起、无限 +8h：

```yaml
- cron: "0 */6 * * *"
```

手动跑一次（强制点按钮）：

```bash
BH_PAT=bh_pat_xxx BH_COOKIE_HEADER="Cookie: ..." PYTHONUTF8=1 python3 renew.py --force
```

通过标准：日志出现 `🎉 续期成功`，`status=ready`。

## Secrets

仓库 `Settings → Secrets and variables → Actions` 配置：

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT（`bh_pat_...`） |
| `BH_COOKIE_HEADER` | dash 站点完整 Cookie 头（含 `__Secure-better-auth.session_token`） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

时间算法见 [time.txt](./time.txt)。

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
