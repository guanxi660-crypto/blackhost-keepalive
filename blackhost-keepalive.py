#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blackhost.pl 免费服保活脚本（HAR 实证版）

HAR 来源：C:\\Users\\Jack\\Desktop\\dash.blackhost.pl_Archive [26-11-09 12-29-51].har
逆向结论（已实证，2026-11-09）：
  - 读接口（details/stats/servers）：PAT（bh_pat_...）走 Bearer 完全够用，
    dash.blackhost.pl 与 api.blackhost.pl 两个 host 都行，免 cookie、免 Turnstile。
  - 续期接口 POST /api/v1/servers/game/free/renew：
      * PAT 调它 -> 403 common.apiKeyActionForbidden "This action is not available via API key. Use the Dashboard."
      * 会话 cookie 调它但 turnstileToken 为空 -> 400 turnstile_failed "Wymagana jest weryfikacja antybotowa (Turnstile)."
    结论：续期必须用浏览器会话 + 真实 Turnstile token，无法纯 API 无人值守续期。
    本脚本定位：全自动监控 + 阈值告警 + 半自动续期（你过一次 Turnstile，脚本去提交）。

用法：
  python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --check
  python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --monitor
  python3 D:/project/blackhost-keepalive/blackhost-keepalive.py --renew --har "C:\\Users\\Jack\\Desktop\\dash.blackhost.pl_Archive [26-11-09 12-29-51].har" --turnstile "粘贴浏览器里 Network 面板看到的 POST /free/renew 的 turnstileToken"

PAT 解析顺序：环境变量 BH_PAT > 同目录 .env 的 BH_PAT > D:/project/.private/accounts.md 中 BlackHost 段的 bh_pat_...
日志永不回显 PAT / cookie / turnstile 的完整值，只打状态。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
    BJ = ZoneInfo("Asia/Shanghai")
except Exception:
    # Windows 无系统 tzdata 且未装 tzdata 包时兜底：北京固定 UTC+8（无夏令时）
    BJ = timezone(timedelta(hours=8))

try:
    import requests
except ImportError:
    requests = None

# ─────────── CONFIG ───────────
API_BASE = "https://api.blackhost.pl/v1"          # 监控读接口用这个（PAT 鉴权已验证）
DASH_BASE = "https://dash.blackhost.pl"           # 续期提交用这个（必须浏览器会话）
SERVID_UNIQUE = "4dbfac39"                        # 服务器 short id（HAR 里 /game/4dbfac39）
SERVER_UUID = "4dbfac39-b699-41f5-8d79-ca548a859cb1"
REGISTER_EMAIL = "camera4fun@gmail.com"
TURNSTILE_SITEKEY = "0x4AAAAAABdrMnrgYP19vAzT"    # HAR bundle 实证，仅供浏览器端过验证时对照
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"
CHECK_EVERY = 10800        # 监控轮询间隔（秒）3 小时；须与 systemd --every 一致
ALERT_HOURS = 7            # 剩余不足这个小时数就 TG 告警；须大于轮询间隔(3h)，且小于续期加时(约11h20m)-间隔，否则每轮续期后立刻告警
FAIL_RETRY = 600
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_PATH = "D:/project/.private/accounts.md"

# TG（可选，环境变量注入）
TG_TOKEN = os.environ.get("BH_TG_TOKEN", "").strip()
TG_CHAT = os.environ.get("BH_TG_CHAT", "").strip()


def log(m):
    print(datetime.now(BJ).strftime("%m-%d %H:%M:%S"), m, flush=True)


def fmt_bytes(n):
    """字节数 -> 人类可读（用于内存/磁盘/流量）。"""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n < 1024:
        return f"{n:.0f} B"
    n /= 1024
    if n < 1024:
        return f"{n:.0f} KB"
    n /= 1024
    if n < 1024:
        return f"{n:.2f} MB"
    return f"{n / 1024:.2f} GB"


def fmt_bj(dt):
    """datetime -> 北京时间字符串（UTC+8）。"""
    if not dt:
        return "—"
    return dt.astimezone(BJ).strftime("%m-%d %H:%M:%S")


class Run:
    """流程关键行收集器：逐行打印到 journal；轮末按 ctx 组装结构化详细 TG 消息。

    journal 侧保持逐行可读；TG 侧用 ctx 里的完整字段拼一张“面板”，
    正常轮/告警轮/挂起/接口异常各自有不同结论段与操作指引。
    """

    ICONS = {"ok": "🟢", "warn": "🟠", "suspended": "🔴", "error": "⚫"}
    TITLES = {
        "ok": "BlackHost 监控 · 正常",
        "warn": "BlackHost 监控 · 需要续期",
        "suspended": "BlackHost 监控 · 已挂起",
        "error": "BlackHost 监控 · 接口异常",
    }
    SEP = "─" * 14

    def __init__(self):
        self.lines = []
        self.ctx = None

    def line(self, t):
        log(t)
        self.lines.append(t)

    def build(self):
        import html
        esc = lambda x: html.escape(str(x), quote=False)
        c = self.ctx
        if not c:
            return "\n".join(self.lines)
        kind = c.get("kind", "ok")
        L = [f"{self.ICONS.get(kind, '')} <b>{self.TITLES.get(kind, 'BlackHost 监控')}</b>", self.SEP]

        if c.get("name"):
            L.append(f"🖥 <b>{esc(c['name'])}</b>  <code>{esc(c.get('unique_id', ''))}</code>")
            meta = [x for x in [
                c.get("system"),
                f"节点 {c['node']}" if c.get("node") else "",
                "免费服" if c.get("is_free") else "",
            ] if x]
            if meta:
                L.append(f"🏷 {esc(' · '.join(meta))}")
        if c.get("ip"):
            L.append(f"🌐 <code>{esc(c['ip'])}</code>")
        if c.get("ram_mb"):
            L.append(f"📦 配额：CPU {int((c.get('cpu') or 0) / 100)} 核 · "
                     f"内存 {c.get('ram_mb')} MB · 磁盘 {c.get('disk_gb')} GB")
        L.append(self.SEP)

        L.append(f"📅 检查时间：{fmt_bj(c.get('now'))} (UTC+8)")
        L.append(f"➡️ 服务状态：<b>{esc(c.get('status', '?'))}</b>")
        if c.get("email"):
            L.append(f"👤 账号：{esc(c['email'])}")

        st = c.get("stats")
        if st:
            pl = st.get("players") or {}
            line = f"🎮 玩家：{pl.get('online', 0)}/{pl.get('max', 0)}"
            if pl.get("version"):
                line += f" · {esc(pl['version'])}"
            L.append(line)
            bits = []
            if st.get("cpu") is not None:
                bits.append(f"CPU {float(st['cpu']):.1f}%")
            if st.get("mem") is not None and c.get("ram_mb"):
                bits.append(f"内存 {fmt_bytes(st['mem'])}/{c['ram_mb'] / 1024:.2f} GB")
            if st.get("disk") is not None and c.get("disk_gb"):
                bits.append(f"磁盘 {fmt_bytes(st['disk'])}/{c['disk_gb']:.2f} GB")
            if bits:
                L.append("📊 " + " · ".join(bits))
            if st.get("uptime"):
                L.append(f"⏱ 已运行 {fmt_remaining(st['uptime'])}")
            if st.get("netin") is not None or st.get("netout") is not None:
                L.append(f"🔁 流量：↓{fmt_bytes(st.get('netin'))} ↑{fmt_bytes(st.get('netout'))}")
        L.append(self.SEP)

        if c.get("active_until"):
            L.append(f"⏳ 到期：<b>{fmt_bj(c['active_until'])}</b> (UTC+8)")
            L.append(f"    剩余：<b>{fmt_remaining(c.get('remaining'))}</b>")
        if c.get("delete_after"):
            dd = (c["delete_after"] - datetime.now(timezone.utc)).total_seconds()
            L.append(f"🗑 删除：{fmt_bj(c['delete_after'])} (UTC+8) · 还剩 {fmt_remaining(dd)}")
        L.append(self.SEP)

        concl = {
            "ok": "✅ 结论：无需续期，继续监控",
            "warn": f"⚠️ 结论：剩余不足 {ALERT_HOURS} 小时，请尽快续期",
            "suspended": "⛔ 结论：服务器已被挂起，需立即续期恢复",
            "error": "❌ 结论：本轮读取失败",
        }
        L.append(concl.get(kind, ""))
        if kind in ("warn", "suspended"):
            L += [
                "",
                "<b>📢 续期步骤</b>",
                f"1. 打开下方链接，过一次 Turnstile 验证",
                "2. F12 → Network 找 <code>POST .../free/renew</code>，复制 "
                "<code>turnstileToken</code> 与 Cookie",
                "3. 在 VPS 执行：<code>python3 blackhost-keepalive.py --renew "
                "--cookie-header '...' --turnstile '...'</code>",
            ]
        if kind == "error" and c.get("note"):
            L.append(f"原因：{esc(c['note'])}")
        if c.get("every"):
            L.append(f"🔁 下轮检查：{fmt_bj(c['now'] + timedelta(seconds=c['every']))} (UTC+8)")
        L.append(f"🔗 {DASH_BASE}/game/{SERVID_UNIQUE}")
        return "\n".join(L)

    def send(self):
        if not (TG_TOKEN and TG_CHAT and requests):
            return
        text = self.build()
        if not text:
            return
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code != 200:
                log(f"TG 发送失败 {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"TG 发送失败: {e!r}")



def now_bj():
    return datetime.now(BJ)


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_remaining(total_seconds):
    s = max(int(total_seconds or 0), 0)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}天{h}小时{m}分"
    if h:
        return f"{h}小时{m}分"
    return f"{m}分{sec:02d}秒"


def mask_email(email):
    if not email or "@" not in email:
        return "(未知)"
    name, dom = email.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "***"
    else:
        masked = name[:1] + "***" + name[-1:]
    return f"{masked}@{dom}"


def load_pat(explicit=""):
    """PAT 解析：显式 > 环境变量 > 同目录 .env > accounts.md（BlackHost 段）。"""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("BH_PAT", "").strip()
    if env:
        return env
    env_file = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_file):
        try:
            for line in open(env_file, encoding="utf-8"):
                m = re.match(r"\s*BH_PAT\s*=\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
        except Exception:
            pass
    if os.path.exists(ACCOUNTS_PATH):
        try:
            text = open(ACCOUNTS_PATH, encoding="utf-8").read()
            # 只在 BlackHost 段之后找第一个 bh_pat_，避免误拿 GitHub PAT
            start = text.find("### BlackHost")
            seg = text[start:] if start >= 0 else text
            m = re.search(r"bh_pat_[A-Za-z0-9\-_]+", seg)
            if m:
                return m.group(0)
        except Exception:
            pass
    return ""


def make_session(pat):
    if requests is None:
        raise RuntimeError("缺少 requests：python3 -m pip install requests")
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.headers["Accept"] = "application/json"
    if pat:
        s.headers["Authorization"] = f"Bearer {pat}"
    return s


def api_get(s, path):
    r = s.get(API_BASE + path, timeout=20)
    return r


def cycle_check(s, run, every=CHECK_EVERY):
    """一轮读：details + stats，返回 (should_renew, remaining_sec, details_json)。

    同时把完整字段写入 run.ctx，供 Run.build() 拼详细 TG 面板。
    """
    now = now_bj()
    run.line(f"📅 当前：{now.strftime('%m-%d %H:%M:%S')} 北京时间")
    r = api_get(s, f"/servers/game/{SERVID_UNIQUE}/details")
    if r.status_code == 401:
        run.line("❌ PAT 无效/失效（401），请检查 BH_PAT")
        run.ctx = {"kind": "error", "now": now, "every": every,
                   "note": "PAT 无效或已失效（401）"}
        return False, None, None
    if r.status_code != 200:
        run.line(f"❌ details 接口 {r.status_code}：{r.text[:200]}")
        run.ctx = {"kind": "error", "now": now, "every": every,
                   "note": f"details 接口 {r.status_code}：{r.text[:200]}"}
        return False, None, None

    data = r.json()
    srv = data.get("server", {})
    status = srv.get("status") or srv.get("serverState") or "?"
    expires = parse_iso(srv.get("active_until") or srv.get("expires_at"))
    remaining = None
    if expires:
        remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    run.line(f"➡️ 状态：{status}｜到期：{srv.get('active_until') or srv.get('expires_at')}（{fmt_remaining(remaining)}后）")
    run.line(f"👤 账号：{mask_email(REGISTER_EMAIL)}")

    stats = None
    try:
        rs = api_get(s, f"/servers/game/{SERVID_UNIQUE}/stats")
        if rs.status_code == 200:
            stats = rs.json().get("stats", {})
            run.line(f"🌐 实时：{stats.get('status')}｜运行 {int(stats.get('uptime') or 0)}s｜玩家 {stats.get('players', {}).get('online')}/{stats.get('players', {}).get('max')}")
    except Exception as e:
        run.line(f"⚠️ stats 拉取失败（不影响到期计算）：{e!r}")

    should_renew = False
    kind = "ok"
    if status == "suspended":
        should_renew = True
        kind = "suspended"
        run.line("⏳ 已被挂起（suspended），需要立即续期恢复")
    elif remaining is not None and remaining < ALERT_HOURS * 3600:
        should_renew = True
        kind = "warn"
        run.line(f"⚠️ 剩余不足 {ALERT_HOURS} 小时（{fmt_remaining(remaining)}），需要尽快续期")
    else:
        run.line("✅ 暂不用续期，继续监控")

    run.ctx = {
        "kind": kind,
        "now": now,
        "name": srv.get("name"),
        "unique_id": srv.get("unique_id") or SERVID_UNIQUE,
        "system": srv.get("system"),
        "node": srv.get("node"),
        "ip": srv.get("ip"),
        "is_free": srv.get("is_free"),
        "cpu": srv.get("cpu"),
        "ram_mb": srv.get("ram_mb"),
        "disk_gb": srv.get("disk_gb"),
        "status": status,
        "stats": stats,
        "email": mask_email(REGISTER_EMAIL),
        "active_until": expires,
        "delete_after": parse_iso(srv.get("delete_after")),
        "remaining": remaining,
        "every": every,
    }
    return should_renew, remaining, data



def import_cookie_from_har(path):
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    for e in har["log"]["entries"]:
        if "dash.blackhost.pl" not in e["request"]["url"]:
            continue
        for h in e["request"]["headers"]:
            if h["name"].lower() == "cookie" and h["value"]:
                return h["value"]
    return None


def renew_with_token(cookie_header, turnstile_token):
    """半自动续期提交：会话 cookie + Turnstile token -> POST /servers/game/free/renew。

    成功时返回 (ok, activeUntil)。HAR 实证：成功后 activeUntil 延长约 11h20m（待再次实测确认）。
    """
    if not cookie_header or not turnstile_token:
        raise RuntimeError("需要同时提供 cookie（--har 或 --cookie-header）和 --turnstile")
    s = make_session("")
    s.headers["Referer"] = DASH_BASE + f"/game/{SERVID_UNIQUE}"
    s.headers["Origin"] = DASH_BASE
    s.headers["Cookie"] = cookie_header
    r = s.post(DASH_BASE + "/api/v1/servers/game/free/renew",
               json={"turnstileToken": turnstile_token}, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    return r.status_code == 200 and body.get("success") is True, body


def main():
    ap = argparse.ArgumentParser(description="blackhost.pl 免费服保活：监控 + 半自动续期")
    ap.add_argument("--check", action="store_true", help="只跑一轮读并打印到期时间")
    ap.add_argument("--monitor", action="store_true", help="常驻监控；到期/挂起只告警，不自动续期（Turnstile 拦死了，见 README）")
    ap.add_argument("--renew", action="store_true", help="提交一次续期（需 --har/--cookie-header + --turnstile）")
    ap.add_argument("--har", help="HAR 文件路径，用于取出浏览器会话 Cookie（只在续期模式需要）")
    ap.add_argument("--cookie-header", help="直接粘贴浏览器 Cookie 请求头（与 --har 二选一）")
    ap.add_argument("--turnstile", help="本次续期的 Turnstile token（从浏览器 Network 里 POST free/renew 的请求体复制）")
    ap.add_argument("--pat", help="直接指定 PAT（默认读环境变量/.env/accounts.md）")
    ap.add_argument("--every", type=int, default=CHECK_EVERY, help="监控间隔秒，默认 10800（3 小时）")
    a = ap.parse_args()

    pat = load_pat(a.pat or "")
    if not pat:
        sys.exit("没找到 PAT：请设置环境变量 BH_PAT，或在 D:/project/blackhost-keepalive/.env 写 BH_PAT=...，或确认 D:/project/.private/accounts.md 里有 BlackHost 段")
    log(f"PAT 已加载：bh_pat_{pat[7:11]}****（脱敏）")

    s = make_session(pat)

    if a.check:
        run = Run()
        should, rem, _ = cycle_check(s, run, a.every)
        run.line(f"➡️ 结论：{'需要续期' if should else '无需续期'}｜剩余 {fmt_remaining(rem) if rem is not None else '未知'}")
        run.send()
        return 0 if rem is None or rem >= ALERT_HOURS * 3600 else 2

    if a.renew:
        cookie = a.cookie_header
        if not cookie and a.har:
            cookie = import_cookie_from_har(a.har)
            if cookie:
                log(f"已从 HAR 提取 Cookie 头（长度 {len(cookie)}，不回显）")
        if not cookie:
            sys.exit("续期需要会话 Cookie：传 --har <HAR 路径> 或 --cookie-header '...'")
        if not a.turnstile:
            sys.exit("续期需要 Turnstile token：先在浏览器里打开 https://dash.blackhost.pl/game/4dbfac39 过验证，复制 POST free/renew 的 turnstileToken 再传 --turnstile")
        run = Run()
        run.line(f"🌐 续期中：POST {DASH_BASE}/api/v1/servers/game/free/renew（Turnstile 已由你提供）")
        ok, body = renew_with_token(cookie, a.turnstile)
        if ok:
            run.line(f"🎉 完成：续期成功，新到期 {body.get('activeUntil')}")
        else:
            run.line(f"❌ 续期被拒：{json.dumps(body, ensure_ascii=False)[:300]}")
        # 续期后立刻复查 PAT 读侧的新到期时间
        cycle_check(s, run, a.every)
        run.send()
        return 0 if ok else 1

    if a.monitor:
        log(f"常驻监控：每 {a.every} 秒一轮，告警阈值 {ALERT_HOURS} 小时。Ctrl+C 退出。")
        while True:
            run = Run()
            try:
                should, rem, _ = cycle_check(s, run, a.every)
                if should:
                    run.line("📢 提示：在浏览器打开 https://dash.blackhost.pl/game/4dbfac39，用当前会话过一次验证，然后跑 --renew 提交续期")
            except Exception as e:
                log(f"循环异常: {e!r}")
            run.send()
            try:
                time.sleep(a.every)
            except KeyboardInterrupt:
                log("收到中断，退出")
                return 0
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
