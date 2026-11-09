# GitHub Actions 强制重启版

Actions 定时走一次停止 → 开始，PAT 直调，不用浏览器、Turnstile、sing-box。

## 文件

| 文件 | 作用 |
|------|------|
| `.github/workflows/blackhost-keepalive.yml` | 定时触发 |
| `github-action/power_cycle.py` | 停止 → 轮询停止 → 开始 → 轮询运行 |
| `github-action/renew_browser.py` | 备用：Turnstile 续期版（手动用，不在定时里） |

## Secrets

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

## 手动触发

Actions 页点 `blackhost-keepalive` → Run workflow 即可。

## 说明

- 时间算法见项目根的 `../time.txt`。
- 停止/开始不会延长到期时间；真正延期要靠 Turnstile 那步。
- 不要把真实 PAT、Cookie、sing-box 凭证提交到仓库。
