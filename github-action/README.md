# 窗口对齐强制重启版

当前到期 `12:28:45Z`、剩余 3h15m，窗口 `10:28:45Z` 开启（剩不到 2h）。cron `28 */6 * * *`（UTC :28 每 6 小时）：窗口内续期 +8h，下一个窗口 +6h。

## 文件

| 文件 | 作用 |
|------|------|
| `.github/workflows/blackhost-keepalive.yml` | `28 */6 * * *` 定时触发 |
| `github-action/power_cycle.py` | 停止 → 轮询停止 → 开始 → 轮询运行，窗口对齐 |
| `github-action/renew_browser.py` | 备用：Turnstile 续期版（手动用，不在定时里） |

## Secrets

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

## 手动触发

Actions 页点 `blackhost-keepalive` → Run workflow 即可。

## 说明

- 停止/开始不会延长到期时间（已实测，`active_until` 不变）；到期临近时仍需手动走 Turnstile 续期。
- 免费服窗口内操作，剩下不到 2 小时时重启后建议尽快补续期。
