# BlackHost 保活服务 · 部署与运维记录

> 部署日期：2026-11-09
> 主机：`TW.bytevirtV6`（`2a12:bec0:162:64e::`，Debian 12 bookworm，systemd）
> 目标服务器：`Mój Serwer Minecraft`，short id `4dbfac39`，节点 `PL04-Free`，`pl04-free.blackhost.pl:25596`

---

## 1. 一句话结论

本服务做的是 **监控 + 提前告警**，不是无人值守自动续期。
续期接口 `POST /api/v1/servers/game/free/renew` 被 Cloudflare Turnstile 强制人机验证，**任何纯 API 方案都无法完成续期**，必须人工过一次验证。

---

## 2. 部署形态

| 项 | 值 |
|---|---|
| 服务名 | `blackhost-keepalive.service` |
| 运行用户 | `root`（代码在 `/root` 下，`www-data` 无法读取） |
| 工作目录 | `/root/blackhost-keepalive` |
| 轮询间隔 | **3 小时**（`--every 10800`） |
| 告警阈值 | **7 小时**（`ALERT_HOURS = 7`） |
| 开机自启 | 已 `enable` |
| 崩溃策略 | `Restart=always`，`RestartSec=60`，且 `StartLimitBurst=5` 防刷屏 |
| 编码 | `Environment=PYTHONUTF8=1`（必需，见 4.1） |
| 通知 | Telegram Bot → chat `8600129634`，`parse_mode=HTML` |

### systemd 单元（实际生效版本，与本目录 `.service` 文件一致）

```ini
[Unit]
Description=BlackHost free server keepalive monitor
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=/root/blackhost-keepalive
ExecStart=/usr/bin/python3 -u /root/blackhost-keepalive/blackhost-keepalive.py --monitor --every 10800
Restart=always
RestartSec=60
Environment=PYTHONUTF8=1
EnvironmentFile=/root/blackhost-keepalive/.env
User=root

[Install]
WantedBy=multi-user.target
```

---

## 3. 参数为什么是 3 小时 / 7 小时

不是拍脑袋，是被三个硬约束夹出来的：

1. **阈值必须 > 间隔**
   若两者相等（如都是 3h），检查点可能正好落在"剩余 = 阈值"那一刻（`<` 不成立、不告警），下一轮就已经到期，**告警会在服务器挂起之后才发出**。下限约 4.5h。

2. **阈值必须 < 续期加时 − 间隔**
   实测续期只加约 **11 小时 20 分**。若阈值 ≥ 9h，则每轮续期后第一次检查就立刻告警，等于每 11 小时被叫一次，失去意义。上限约 8h。

3. **需要覆盖"人多久看一次手机"**
   间隔 3h 时，最坏情况首次告警出现在剩余 `阈值 − 3h`。

| 阈值 | 首次告警时剩余（最好/最坏） | 续期后首轮 | 评价 |
|---|---|---|---|
| 4.5h | 4.5h / 1.5h | 正常 | 太紧 |
| 6h | 6h / 3h | 正常 | 可用 |
| **7h** | **7h / 4h** | **正常（余 1h20m）** | **当前采用** |
| 8h | 8h / 5h | 仅余 20 分钟 | 易翻车 |
| 9h+ | — | 每轮都告警 | 告警疲劳 |

### 一个周期的时间线（以 2026-11-09 为例，到期 20:28:45 +08）

| 检查时刻 | 剩余 | 结果 |
|---|---|---|
| 13:08 | 7h20m | 正常 |
| 16:08 | 4h20m | **首次告警**（< 7h） |
| 19:08 | 1h20m | 再次告警 |
| 22:08 | 已过期 | 挂起告警 |

---

## 4. 部署中修复的三个问题

### 4.1 `UnicodeEncodeError: 'latin-1' codec`

脚本源于 Windows，输出大量中文与 emoji；本机 `LANG=en_US`，Python stdout 落为 `latin-1`，一启动即崩。

修复：systemd 单元加 `Environment=PYTHONUTF8=1`（固化，不依赖系统 locale）。

### 4.2 原始 `.service` 模板不可用

原模板写 `WorkingDirectory=/opt/blackhost-keepalive` + `User=www-data`，但代码实际在 `/root`（`drwx------`），`www-data` 会 `Permission denied`。

修复：路径改为 `/root/blackhost-keepalive`，用户改为 `root`。

### 4.3 通知里"下轮检查"时间错误

`ctx["every"]` 误用脚本常量 `CHECK_EVERY`（曾为 900），而真实间隔由 systemd 的 `--every` 传入。导致告警消息显示"下轮检查 +15 分钟"，实际是 +3 小时。

修复：`cycle_check(s, run, every=CHECK_EVERY)` 接收真实间隔，三个调用点均传 `a.every`，常量默认值同步为 `10800`。

---

## 5. Turnstile 实证（关键实验）

2026-11-09 16:4x，用 HAR 中的会话 Cookie 与 token 直接打接口：

| 测试 | 结果 | 结论 |
|---|---|---|
| Cookie 读 `GET /v1/servers/game/4dbfac39/details` | **HTTP 200** | 会话 Cookie 仍然有效 |
| Cookie + token 提交 `POST /api/v1/servers/game/free/renew` | **HTTP 400** `turnstile_failed` | 唯一失败的是 token |

返回的是**业务层错误码**（波兰语提示"反机器人验证失败，请重新勾选"），**不是** Cloudflare 403 拦截页 —— 说明 `cf_clearance` 与 Cookie 均已通过，IP 与 Cookie 都不是障碍。

**为什么 token 无法复用：**

- 有效期约 **300 秒**
- **一次性**，服务端 verify 后立即作废
- 由浏览器端 Cloudflare 挑战现场生成，与出口 IP 无关

**推论：** "浏览器和 VPS 同一个出口 IP" 对续期毫无帮助。且用户**在浏览器点下续期按钮时，续期就已经完成**（HAR 中 12:28 的 200 记录即此），`--renew` 只是"有 token 但不想在浏览器点"时的备选通道。

---

## 6. 通知消息结构

四种状态各有独立标题与结论段：

| kind | 标题 | 附加内容 |
|---|---|---|
| `ok` | 🟢 正常 | — |
| `warn`（剩余 < 7h） | 🟠 需要续期 | 续期三步指引 |
| `suspended` | 🔴 已挂起 | 续期三步指引 |
| `error`（401/接口异常） | ⚫ 接口异常 | 失败原因 |

字段：服务器名 / short id / 系统 / 节点 / IP:端口 / 配额 / 检查时间 / 服务状态 / 账号（脱敏） / 玩家数与服务端版本 / CPU·内存·磁盘实时占用 / 运行时长 / 网络流量 / **到期时间与剩余** / **删除时间与剩余** / 结论 / 下轮检查 / 仪表盘直链。

journal 侧仍保持逐行可读输出，详细排版只作用于 TG。

---

## 7. 文件清单与校验

| 文件 | 说明 | MD5 |
|---|---|---|
| `blackhost-keepalive.py` | 主脚本（含详细通知改造） | `c9ce127185aa654cdadf0fa09132c6c4` |
| `blackhost-keepalive.service` | systemd 单元，与 `/etc/systemd/system/` 完全一致 | `675113f8e3ee89a2a208e691aa89c5cf` |
| `.env` | 凭证（`600`）：`BH_PAT` / `BH_TG_TOKEN` / `BH_TG_CHAT` | `1aa56db1afc641e0ba0b934ec8f950e8` |
| `README.md` | 原始 HAR 逆向分析（未改动） | `b9e282aca718a5552fa6bd1cd89080f0` |
| `blackhost-keepalive.py.bak-20261109` | 改造前备份 | — |
| `dash.blackhost.pl_Archive [26-11-09 12-29-51].har` | 原始抓包（4.5 MB，含有效会话 Cookie） | — |

校验命令：

```bash
cd /root/blackhost-keepalive
md5sum -c <<'EOF'
c9ce127185aa654cdadf0fa09132c6c4  blackhost-keepalive.py
675113f8e3ee89a2a208e691aa89c5cf  blackhost-keepalive.service
b9e282aca718a5552fa6bd1cd89080f0  README.md
EOF
```

> 注意：`.env` 与 `.py` 后续若修改，需同步更新本节 MD5。

---

## 8. 运维命令速查

```bash
# 状态 / 日志
systemctl status blackhost-keepalive
journalctl -u blackhost-keepalive -f
journalctl -u blackhost-keepalive --since '-1h' -o cat | grep -E '状态：|剩余|挂起'

# 手动跑一轮（不影响常驻进程）
cd /root/blackhost-keepalive && PYTHONUTF8=1 python3 blackhost-keepalive.py --check

# 改参数后重载
systemctl daemon-reload && systemctl restart blackhost-keepalive

# 半自动续期（token 需现取，约 5 分钟寿命）
python3 blackhost-keepalive.py --renew --har 'dash.blackhost.pl_Archive [26-11-09 12-29-51].har' --turnstile '<token>'
```

---

## 9. 已知限制与待办

- **无法自动续期**：Turnstile 是设计上的人机门槛，非配置问题。
- **续期窗口极短**：每次仅加约 11h20m，意味着服务器长期处于"离到期不到半天"的状态，一天需续两次。
- **漏续后果可控**：`delete_after` 为到期后约 7 天（当前 `2026-11-16 12:28:45 +08`），最坏结果是停机 + 挂起，不是数据被删。
- **告警重复**：进入 7h 窗口后每 3 小时重复告警一次（一个周期约 2 条），属于有意冗余。
- **凭证文件**：`/root/tgbot.txt` 仍为 `644`（bot token 明文可读），脚本已不再引用该文件，可择机删除。
- **README 过时**：`README.md` 仍写着 15 分钟轮询 / 3 小时阈值，与当前实际配置（3h / 7h）不符，以本文档为准。
