#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强制停止->开始流程（GitHub Actions 定时触发）。

流程：PAT 读 details/stats → POST power stop → 轮询到 stopped/offline
      → POST power start → 轮询到 running → 记 uptime。
接口：POST /v1/servers/game/{id}/power，body {"signal": "...", "action": "..."}，实测 PAT 返回 204 可用。

时间算法（2026-11-09 实测）：
  - 到期 active_until = 2026-11-09T12:28:45Z；现在 09:14Z 剩余 3h15m；
    服务端文案：剩不到 2h 才能续期，所以窗口在 10:28:45Z（还有 74 分钟）开启；
  - 窗口内续期是按“续期时刻 +8h”算的，不是按旧到期 +8h：
    10:28 续期 → 新到期 18:28，下一窗口 16:28，间隔 6 小时；
    cron 固定在 :28 每 6 小时（`28 */6 * * *`），正好落在每次窗口开点±几分钟，
    比 `0 */8` 更稳（后者在 08:00 太早按钮 disabled、16:00 已过 12:28）。
  - 停止/开始后 active_until 不变（同日验证：12:28:45Z 前后一致），重启不延期；
    真正把到期往后推的是 Turnstile 续期那步（+8h）。本脚本的强制重启只是节奏，不是续期本身。

环境变量：BH_PAT（必填）；BH_TG_TOKEN/BH_TG_CHAT（可选通知）
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    BJ = ZoneInfo("Asia/Shanghai")
except Exception:
    BJ = timezone(timedelta(hours=8))

try:
    import requests
except ImportError:
    requests = None

API_BASE = "https://api.blackhost.pl/v1"
SERVID_UNIQUE = "4dbfac39"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"
STOP_TIMEOUT = 90
START_TIMEOUT = 120


def log(m):
    print(datetime.now(BJ).strftime("%m-%d %H:%M:%S"), m, flush=True)


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_remaining(total):
    s = max(int(total or 0), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}小时{m}分" if h else f"{m}分{sec:02d}秒"


def get_session():
    if requests is None:
        raise RuntimeError("缺少 requests")
    pat = os.environ.get("BH_PAT", "").strip()
    if not pat:
        raise RuntimeError("缺少 BH_PAT")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {pat}", "User-Agent": UA, "Accept": "application/json"})
    return s


def details(s):
    r = s.get(f"{API_BASE}/servers/game/{SERVID_UNIQUE}/details", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"details {r.status_code}: {r.text[:200]}")
    srv = r.json()["server"]
    expires = parse_iso(srv.get("active_until") or srv.get("expires_at"))
    remaining = (expires - datetime.now(timezone.utc)).total_seconds() if expires else None
    return srv, remaining


def stats_status(s):
    r = s.get(f"{API_BASE}/servers/game/{SERVID_UNIQUE}/stats", timeout=20)
    if r.status_code != 200:
        return None, None
    d = r.json()
    return d.get("status"), (d.get("stats") or {}).get("uptime")


def power(s, signal):
    r = s.post(f"{API_BASE}/servers/game/{SERVID_UNIQUE}/power",
               json={"signal": signal, "action": signal}, timeout=20)
    log(f"power {signal}: HTTP {r.status_code}")
    if r.status_code not in (200, 204):
        raise RuntimeError(f"power {signal} 失败 {r.status_code}: {r.text[:200]}")


def poll_until(s, want, timeout, step=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, up = stats_status(s)
        log(f"  轮询：status={st} uptime={up}")
        if st == want:
            return True
        time.sleep(step)
    return False


def send_tg(text):
    token = os.environ.get("BH_TG_TOKEN", "").strip()
    chat = os.environ.get("BH_TG_CHAT", "").strip()
    if not token or not chat or requests is None:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        log(f"TG 发送失败：{e!r}")


def main():
    s = get_session()
    srv, rem = details(s)
    before_exp = srv.get("active_until") or srv.get("expires_at")
    log(f"📅 当前：{datetime.now(BJ).strftime('%m-%d %H:%M:%S')} 北京时间")
    log(f"➡️ 开始前：status={srv.get('status')}｜到期={before_exp}｜剩余 {fmt_remaining(rem)}")

    if (srv.get("status") or "").lower() == "suspended":
        log("⚠️ 已被挂起，先走 Turnstile 续期恢复，跳过普通重启")
        send_tg(f"🔴 已挂起，需手动 Turnstile 续期。到期 {before_exp}")
        return 1

    log("⏹ 发送 stop")
    power(s, "stop")
    if not poll_until(s, "stopped", STOP_TIMEOUT):
        st, _ = stats_status(s)
        if st not in ("offline", "stopped"):
            log(f"❌ 停服超时，最后状态 {st}，放弃 start")
            send_tg(f"❌ 停服超时，当前 {st}，未执行 start")
            return 1
    log("✅ 已停到 stopped/offline")
    time.sleep(5)

    log("▶ 发送 start")
    power(s, "start")
    ok = poll_until(s, "running", START_TIMEOUT)
    srv2, rem2 = details(s)
    after_exp = srv2.get("active_until") or srv2.get("expires_at")
    log(f"➡️ 结束：status={srv2.get('status')}｜到期={after_exp}｜剩余 {fmt_remaining(rem2)}")
    if before_exp != after_exp:
        log(f"ℹ️ 到期有变化：{before_exp} → {after_exp}")
    else:
        log("ℹ️ 到期未变（重启不延期；真延期靠 Turnstile 续期，现剩不到 2 小时请手动补）")
    if rem2 is not None and rem2 < 2 * 3600:
        send_tg(f"⏰ 剩余 {fmt_remaining(rem2)}，需手动 Turnstile 续期（到期 {after_exp}）")
    else:
        send_tg(f"✅ 8 小时强制重启完成，状态 {srv2.get('status')}，剩余 {fmt_remaining(rem2)}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"❌ 失败：{e}")
        sys.exit(1)
