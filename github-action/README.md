# GitHub Actions 按钮续期版

按钮 `Przedłuż o 8h` 可用时才点：先 PAT 算剩余，剩不到 2h 才拉起浏览器点它续期。

## 文件

| 文件 | 作用 |
|------|------|
| `.github/workflows/blackhost-keepalive.yml` | `28 */6 * * *` 定时触发 |
| `github-action/renew_browser.py` | PAT 检查 + 浏览器点续期按钮 |
| `github-action/sing-box.client.example.json` | sing-box 配置模板 |

## Secrets

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT |
| `BH_COOKIE_HEADER` | dash 站点完整 Cookie 头 |
| `SINGBOX_CONFIG_JSON` | sing-box 配置 JSON |
| `BH_PROXY` | 普通代理（未启用 sing-box 时） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

## 手动触发

Actions 页点 `blackhost-keepalive` → Run workflow。

## 说明

- 时间算法见 `../time.txt`。
- 不要把真实 PAT、Cookie、凭证提交到仓库。
