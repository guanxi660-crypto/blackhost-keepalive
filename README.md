# BlackHost 免费服保活

> **部署现状见 [DEPLOY.md](./DEPLOY.md)**（2026-11-09：systemd 常驻、轮询 3 小时、告警阈值 7 小时、TG 详细通知）。
> 以下内容为原始 HAR 逆向分析，其中轮询间隔/告警阈值等参数已过时，以 DEPLOY.md 为准。

HAR 实证结论（`C:\Users\Jack\Desktop\dash.blackhost.pl_Archive [26-11-09 12-29-51].har`，72 条请求）。

## 一句话

读接口能全自动，续期接口被 Turnstile 拦死：

| 能力 | 接口 | 鉴权 | 实证结果 |
|------|------|------|----------|
| 查服务器详情 | `GET /v1/servers/game/4dbfac39/details`（dash 与 api 两 host 都行） | PAT `Authorization: Bearer bh_pat_...` | 200，返回 `active_until` / `expires_at` / `delete_after` |
| 查实时状态 | `GET /v1/servers/game/4dbfac39/stats` | PAT | 200，返回 running/offline、uptime、玩家数 |
| 列表 | `GET /v1/servers` | PAT | 200，返回 pterodactyl_servers |
| 续期 | `POST /api/v1/servers/game/free/renew`，body `{"turnstileToken": "..."}` | **必须浏览器会话 + 真实 Turnstile token** | PAT 调它 403 `common.apiKeyActionForbidden`（"Use the Dashboard"）；空 token 400 `turnstile_failed` |

所以本目录的脚本是「全自动监控 + 到期告警 + 半自动续期」：平时脚本每 3 小时轮询一次到期时间，剩余不足 7 小时或已挂起时拉 TG 告警并提示你去浏览器过一次验证、把 token 传给脚本提交。不要指望纯 API 无人值守续期，服务端明确拒绝。

## 当前账号状态快照（2026-11-09 12:29 北京时间当轮）

- 服务器 `Mój Serwer Minecraft`，short id `4dbfac39`，节点 PL04-Free，IP `pl04-free.blackhost.pl:25596`
- HAR 里续期前已挂起（`suspended`，`active_until 2026-11-09T01:08:32Z`），续期后恢复 `ready`，新到期 `2026-11-09T12:28:45Z`、删除 `2026-11-16T04:28:45Z`
- HAR 里这次续期把到期从 `01:08:32Z` 推到 `12:28:45Z`，加时约 **11 小时 20 分**。续期前剩余仍充足时是否还给固定加时，请下一次真实续期时回读确认，README 不写死。
- 会话（Better-Auth）`expiresAt 2026-11-15`，监控用 PAT 不受它影响；只有半自动续期提交时才要浏览器 Cookie。

## 用法

监控读侧演示（推荐先跑一次）：

```bash
python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --check
```

常驻监控（每 3 小时，剩不到 7 小时或挂起自动 TG 告警；以 `.service` 里的 `--every 10800` 为准）：

```bash
BH_TG_TOKEN=你的bot_token BH_TG_CHAT=你的chat_id python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --monitor
# 改轮询间隔：--every 300
```

半自动续期提交（告警后手动步骤）：

1. 浏览器打开 `https://dash.blackhost.pl/game/4dbfac39`，登录态应该还在，点续期按钮，过完 Turnstile（sitekey `0x4AAAAAABdrMnrgYP19vAzT`）。
2. 开 DevTools → Network，找到那次 `POST /api/v1/servers/game/free/renew`，复制请求体里的 `turnstileToken`。
3. 把 token 与 HAR 的会话 Cookie 一起喂给脚本：

```bash
python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --renew \
  --har "C:\Users\Jack\Desktop\dash.blackhost.pl_Archive [26-11-09 12-29-51].har" \
  --turnstile "你刚才复制的那串 token"
```

脚本会走 dash 会话提交续期，然后立刻用 PAT 复查新到期时间并在日志/TG 里打印。Cookie 和 token 只在进程内用，不落日志。

会话 Cookie 过期后（11-15 后），HAR 里的 Cookie 就不能用了：重新在浏览器登录一次、重抓一份 HAR 或直接复制 Network 里任意 dash 请求的 Cookie 头，`--har` 换新文件即可；监控读侧始终走 PAT，不受影响。

## PAT 从哪来

脚本按这个顺序找：环境变量 `BH_PAT` → 同目录 `.env` → `D:/project/.private/accounts.md` 的 BlackHost 段。仓库外的服务器上部署时，建议用 `.env`（抠 `.env.example`），不要把 PAT 写进脚本正文或日志。

## 部署（VPS）

1. 把 `D:/project/blackhost-keepalive/` 整个拷到服务器，`python3 -m pip install requests`。
2. 放入 `.env`（`BH_PAT=...`，可选 `BH_TG_TOKEN` / `BH_TG_CHAT`）。
3. 用同目录的 `blackhost-keepalive.service` 挂 systemd（`Restart=always`），先 `journalctl -u blackhost-keepalive -f` 看轮询日志。
4. systemd 无法解决 Turnstile：告警后仍需人工在有公网的浏览器过一次验证。住宅 IP 浏览器过验证成功率高得多。

## 想要全自动续期还剩两条路（都不保证）

- 浏览器挂机：用 Camoufox/Playwright 打开 `/game/4dbfac39`，替你点续期按钮、等 Turnstile 资源加载完再读浏览器生成的 token 后提交。能否一次过取决于 Turnstile 会不会升级成交互式挑战，过不了就升级成图片题。先在自己本机试，行再挪 VPS。
