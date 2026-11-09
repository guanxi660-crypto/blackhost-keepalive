#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 版：真浏览器续期入口（BlackHost 免费服）。

两阶段结构：
  1) PAT 只读检查（requests）：算剩余时间。只有剩余 < 2 小时（或已挂起）才进入浏览器阶段，
     因为服务端规定「剩不到 2 小时才能续期」（bundle 文案：`Przedłużenie będzie dostępne,
     gdy do wygaśnięcia sesji pozostaną mniej niż 2 godziny.`）。
  2) 真浏览器续期（Playwright）：注入 Better-Auth 会话，打开 /game/4dbfac39，
     点 `Przedłuż o 8h` 打开弹窗（标题 `Przedłuż darmowy serwer`），等 Turnstile 回调，
     点提交 `Przedłuż serwer o 8h`，监听 POST /api/v1/servers/game/free/renew 的响应。

为什么不能只用 API：
  - PAT 调 renew -> 403 common.apiKeyActionForbidden
  - 空 turnstileToken -> 400 turnstile_failed
  必须在真浏览器里现场生成 Turnstile token，所以这里用 Playwright 真渲染页面。
  注意：GitHub Actions 出口是机房 IP，Turnstile 很可能直接上交互式挑战，
  届时本脚本会明确报错留截图，不要把它当“一定能自动过”的承诺。见同目录 README.md。

环境变量（GitHub Secrets 里配，不要写进文件）：
  BH_PAT            bh_pat_ 开头串；从 D:/project/.private/accounts.md 的 BlackHost 段复制
  BH_COOKIE_HEADER  浏览器 Network 里某个 dash 请求的完整 Cookie 头（HAR 里是 cf_clearance /
                    __Secure-better-auth.state / __Secure-better-auth.session_token / NEXT_LOCALE）
  BH_PROXY          可选住宅代理 http://user:pass@host:port（让浏览器出口换成住宅 IP）
  BH_TG_TOKEN / BH_TG_CHAT  可选 Telegram 通知
产物：artifacts/ 下 before.png / modal.png / after.png + run.log
"""
import argparse
import json
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
DASH_BASE = "https://dash.blackhost.pl"
SERVID_UNIQUE = "4dbfac39"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0"
# bundle 实证的文案（2etc7oet20cr8.js）
RENEW_BTN_TEXT = "Przedłuż o 8h"
SUBMIT_BTN_TEXT = "Przedłuż serwer o 8h"
MODAL_TITLE = "Przedłuż darmowy serwer"


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


def pat_check():
    pat = os.environ.get("BH_PAT", "").strip()
    if not pat:
        raise RuntimeError("缺少 BH_PAT（GitHub Secrets）")
    if requests is None:
        raise RuntimeError("缺少 requests")
    r = requests.get(f"{API_BASE}/servers/game/{SERVID_UNIQUE}/details",
                     headers={"Authorization": f"Bearer {pat}", "User-Agent": UA, "Accept": "application/json"},
                     timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"PAT 检查失败 {r.status_code}: {r.text[:200]}")
    srv = r.json()["server"]
    expires = parse_iso(srv.get("active_until") or srv.get("expires_at"))
    remaining = (expires - datetime.now(timezone.utc)).total_seconds() if expires else None
    status = srv.get("status") or srv.get("serverState")
    log(f"PAT 检查：status={status}｜到期={srv.get('active_until')}｜剩余 {fmt_remaining(remaining)}")
    return status, remaining, srv


def parse_cookie_header(raw):
    jar = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            jar[k.strip()] = v.strip()
    return jar


def do_browser_renew():
    cookie_raw = os.environ.get("BH_COOKIE_HEADER", "").strip()
    if not cookie_raw:
        raise RuntimeError("缺少 BH_COOKIE_HEADER（GitHub Secrets 放完整 Cookie 头）")
    jar = parse_cookie_header(cookie_raw)
    need = {"__Secure-better-auth.session_token", "__Secure-better-auth.state"}
    if not need.issubset(jar):
        log(f"⚠️ Cookie 头里缺少会话项，实际有：{list(jar.keys())}（含 cf_clearance 绑定出口 IP，Actions 跨机可能失效）")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("缺少 playwright：pip install playwright && playwright install firefox")

    os.makedirs("artifacts", exist_ok=True)
    proxy_raw = os.environ.get("BH_PROXY", "").strip()
    launch_opts = {"headless": True}
    if proxy_raw:
        # 支持 sing-box 本地 mixed 入口 http://127.0.0.1:18080（无认证）和带认证代理
        # 形如 http://user:pass@host:port；Playwright 需要分开写 server/username/password
        from urllib.parse import urlparse
        u = urlparse(proxy_raw)
        proxy_cfg = {"server": f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"}
        if u.username and u.password:
            proxy_cfg = {"server": proxy_cfg["server"], "username": u.username, "password": u.password}
        launch_opts["proxy"] = proxy_cfg
        log(f"使用代理出口：{proxy_cfg['server']}（sing-box 本地入口或 BH_PROXY）")

    renew_result = {}

    with sync_playwright() as p:
        browser = p.firefox.launch(**launch_opts)
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="pl-PL",
        )
        # 注入会话（HAR 里的 __Secure-* 必须 secure=True）
        cookies = []
        for name, value in jar.items():
            cookies.append({
                "name": name,
                "value": value,
                "domain": "dash.blackhost.pl",
                "path": "/",
                "secure": name.startswith("__Secure-"),
            })
        ctx.add_cookies(cookies)
        page = ctx.new_page()

        def on_response(resp):
            if "/api/v1/servers/game/free/renew" in resp.url:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                renew_result["status"] = resp.status
                renew_result["body"] = body
                log(f"续期响应：HTTP {resp.status} success={body.get('success')}")

        page.on("response", on_response)

        log("打开面板页")
        page.goto(f"{DASH_BASE}/game/{SERVID_UNIQUE}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        page.screenshot(path="artifacts/before.png", full_page=False)

        # 找续期按钮：bundle 文案 `Przedłuż o 8h`（eZ 组件）
        btn = page.get_by_role("button", name=RENEW_BTN_TEXT)
        if btn.count() == 0:
            # 兜底文本搜索（波兰语/英文/中文都留一个）
            btn = page.locator("text=Przedłuż o 8h")
        if btn.count() == 0:
            page.screenshot(path="artifacts/error_no_renew_btn.png")
            raise RuntimeError("没找到续期按钮（会话可能已过期被打回登录页，或页面文案变了）。截图见 artifacts/error_no_renew_btn.png")
        if not btn.first.is_enabled():
            page.screenshot(path="artifacts/error_btn_disabled.png")
            raise RuntimeError("续期按钮 disabled —— 服务端只在剩不到 2 小时才让续期，现在还没到窗口")
        log("点击续期按钮")
        btn.first.click()
        page.wait_for_timeout(3000)
        try:
            page.get_by_text(MODAL_TITLE).wait_for(timeout=10000)
        except Exception:
            pass
        page.screenshot(path="artifacts/modal.png")

        # Turnstile：显式渲染 iframe（challenges.cloudflare.com），appearance=always
        # 等页面回调把 token 存进 React state；外部无法直接读 state，改用“等 iframe 消失/勾选成功 + 固定等待”
        log("等 Turnstile 完成（机房 IP 可能上交互式挑战，过不了会在这里超时）")
        try:
            page.wait_for_selector('iframe[src*="challenges.cloudflare.com"]', timeout=15000)
            # 尝试点复选框（Turnstile 勾选框在子 frame 的 body 里）
            for frame in page.frames:
                if "challenges.cloudflare.com" in (frame.url or ""):
                    try:
                        frame.locator("input[type='checkbox']").first.click(timeout=5000)
                        log("已点击 Turnstile 复选框")
                    except Exception:
                        pass
                    break
            page.wait_for_timeout(12000)
        except Exception as e:
            log(f"Turnstile 元素等待异常：{e!r}")

        page.screenshot(path="artifacts/after_turnstile.png")

        # 提交：bundle 文案 `Przedłuż serwer o 8h`
        submit = page.get_by_role("button", name=SUBMIT_BTN_TEXT)
        if submit.count() == 0:
            submit = page.locator(f"text={SUBMIT_BTN_TEXT}")
        log("点击提交续期")
        submit.first.click(timeout=10000)
        page.wait_for_timeout(6000)
        page.screenshot(path="artifacts/after.png")

        try:
            body = renew_result.get("body") or {}
            if renew_result.get("status") == 200 and body.get("success"):
                log(f"🎉 续期成功，新到期 {body.get('activeUntil')}")
                return True
            log(f"❌ 续期未成功：{renew_result}")
            return False
        finally:
            browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无视 2 小时窗口也要跑浏览器（仅调试）")
    a = ap.parse_args()

    status, remaining, _ = pat_check()
    need = (status == "suspended") or (remaining is not None and remaining < 2 * 3600) or a.force
    if not need:
        log("⏭ 还没到 2 小时续期窗口，跳过浏览器")
        return 0
    ok = do_browser_renew()
    # 续期后复查
    info = pat_check()
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"❌ 失败：{e}")
        # 工作流里失败也要把截图带走，退出码 1 让 TG 收到失败通知
        sys.exit(1)
