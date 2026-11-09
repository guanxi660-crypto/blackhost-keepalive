# GitHub Action 版：真浏览器续期

能做，但先把账算清楚：**能不能无人值守跑通，不在代码，在 Turnstile 关不关机房 IP 的事。**

## 结构

| 文件 | 作用 |
|------|------|
| `.github/workflows/blackhost-keepalive.yml` | 每 20 分钟触发；手动可 `workflow_dispatch` 强制跑 |
| `renew_browser.py` | 两阶段：PAT 先算剩余（<2h 才进浏览器），浏览器点 `Przedłuż o 8h` → 过 Turnstile → 点 `Przedłuż serwer o 8h` |
| `requirements.txt` | `requests` + `playwright`（浏览器用 Firefox，对上 HAR 的 UA） |

## 为什么是这个形状

- 服务端只让剩不到 2 小时续期（bundle 文案锚定），所以 Action 先用 PAT 便宜地检查，不到窗口就退出，省掉一次浏览器
- 续期接口 `POST /api/v1/servers/game/free/renew` 禁止 PAT（403 `common.apiKeyActionForbidden`），空 token 直接 400，必须在真浏览器里现场生成 token
- 会话 cookie 只有 Better-Auth 的 `__Secure-better-auth.session_token`（HAR 里 `session.expiresAt 2026-11-15`），到点就死；`cf_clearance` 绑定出口 IP，从你浏览器导出的那个在 Actions 上大概率失效，脚本目前靠浏览器现场重新拿 clearance

## Secrets 配置（仓库 Settings → Secrets and variables → Actions）

| Secret | 内容 | 从哪拿 |
|--------|------|--------|
| `BH_PAT` | `bh_pat_...` | `D:/project/.private/accounts.md` BlackHost 段（聊天里不贴全串） |
| `BH_COOKIE_HEADER` | 完整 Cookie 头 | 浏览器登录后 Network 里任意 dash 请求的 `Cookie:` 值（HAR 的请求头也是它） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | TG 通知（可选） | 与 VPS 那份 `.env` 同款 |
| `BH_PROXY` | 住宅代理（强烈建议，sing-box 未启用时的兜底） | `http://user:pass@host:port`，机房直连成功率很低 |
| `SINGBOX_CONFIG_JSON` | **sing-box 客户端 JSON（推荐）** | 按 `sing-box.client.example.json` 填你那台 VLESS/SS 的参数，整段 JSON 贴进 Secret。工作流会下载 sing-box 1.14.4 起在 `127.0.0.1:18080` 暴露 mixed 入口，再让浏览器走 `http://127.0.0.1:18080`，浏览器只认本地入口，真正出口 IP 是你 sing-box 的服务器 |

## 我能保证的 vs 不能保证的

- 能保证：PAT 检查、2 小时窗口判断、页面自动化流程（按钮文字、弹窗标题、提交文字全部用 bundle 实证的波兰语原文定位，不瞎编 selector）、失败截图留 `artifacts/`
- 不能保证：Turnstile 在 GitHub 机房 IP 上给你放行。GitHub 出口全是数据中心，`appearance=always` 的勾选框大概率升级成交互式挑战。第一次跑如果截图是挑战页而非勾选框：先给 `BH_PROXY` 填住宅代理再跑；还是不行就退回“本机浏览器点一下 + `--renew` 提交”那条，别在 Actions 上死磕

## 上线前自己先确认一遍

1. 手动跑一次 `workflow_dispatch`（`force=true`），看 `artifacts/` 的 `before.png`：如果跳到登录页，说明会话已死，去重导 `BH_COOKIE_HEADER`
2. 看 `modal.png`：点不到 `Przedłuż o 8h`＝要么没到 2 小时窗口（按钮 disabled），要么页面文案改版了
3. 再看 `after_turnstile.png`：区分“勾选框已过”和“交互式图片挑战”——后者救不了，就得换代理或退回人工

> 做出这一整套只是给了你一个高概率自动化通道；按 HAR + 反馈推算，无人值守成功率要等你配上住宅代理、实跑几天看日志才算数。
