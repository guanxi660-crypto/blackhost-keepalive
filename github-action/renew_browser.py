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
    # 只认 dash.blackhost.pl 的 Better-Auth 会话；别的站（next-auth / _lr_* / cto_bundle 等广告站）
    # 的 cookie 塞进来会表现为“页面骨架一直在加载 / 打回登录页”，报错必须点明
    if "__Secure-better-auth.session_token" not in jar:
        raise RuntimeError(
            "BH_COOKIE_HEADER 不是 dash.blackhost.pl 的会话：缺少 __Secure-better-auth.session_token。"
            f"实际拿到的 cookie 名：{sorted(jar.keys())}。"
            "请从浏览器访问 https://dash.blackhost.pl 后复制该站请求的完整 Cookie 头（应包含 "
            "cf_clearance / NEXT_LOCALE / __Secure-better-auth.state / __Secure-better-auth.session_token）"
        )

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
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        page.screenshot(path="artifacts/before.png", full_page=False)

        # 会话是否真生效：先直接问一次 get-session（同一套 cookie）
        try:
            sess = page.evaluate(
                "() => fetch('/api/auth/get-session', {credentials:'include'})"
                ".then(r => r.json()).then(d => (d && d.user) ? (d.user.email || 'user') : '')"
            )
            log(f"会话校验：{'已登录 ' + sess if sess else '未登录（cookie 无效或已过期）'}")
            if not sess:
                page.screenshot(path="artifacts/error_not_logged_in.png")
                raise RuntimeError("注入的 cookie 未被接受：get-session 返回空用户，需重新导出 BH_COOKIE_HEADER")
        except RuntimeError:
            raise
        except Exception as e:
            log(f"会话校验异常（继续尝试点按钮）：{e!r}")

        # 找续期按钮：bundle 文案 `Przedłuż o 8h`（eZ 组件）。SPA 首屏是骨架屏，必须轮询等它渲染
        btn = None
        for _ in range(20):
            cand = page.get_by_role("button", name=RENEW_BTN_TEXT)
            if cand.count() == 0:
                cand = page.locator(f"text={RENEW_BTN_TEXT}")
            if cand.count() > 0:
                btn = cand
                break
            page.wait_for_timeout(1500)
        if btn is None:
            page.screenshot(path="artifacts/error_no_renew_btn.png", full_page=True)
            raise RuntimeError("等了 30 秒没出现续期按钮（页面还在骨架屏或已回登录页）。截图见 artifacts/error_no_renew_btn.png")
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
        if submit.count() == 0:
            page.screenshot(path="artifacts/error_no_submit_btn.png")
            raise RuntimeError("弹窗里没有提交按钮 Przedłuż serwer o 8h，弹窗可能没打开")

        # token 在 React state 里，外部读不到；直接点提交，没拿到响应就等一会儿再点，最多 6 次
        for attempt in range(1, 7):
            log(f"点击提交续期（第 {attempt} 次）")
            try:
                submit.first.click(timeout=10000)
            except Exception as e:
                log(f"点击异常：{e!r}")
            page.wait_for_timeout(5000)
            if renew_result.get("status"):
                break
            # 再试一次点 Turnstile 复选框
            for frame in page.frames:
                if "challenges.cloudflare.com" in (frame.url or ""):
                    try:
                        frame.locator("input[type='checkbox']").first.click(timeout=3000)
                    except Exception:
                        pass
                    break
            page.wait_for_timeout(3000)
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


def send_tg(text):
    token = os.environ.get("BH_TG_TOKEN", "").strip()
    chat = os.environ.get("BH_TG_CHAT", "").strip()
    if not token or not chat:
        return
    try:
        import requests as _rq
        _rq.post(f"https://api.telegram.org/bot{token}/sendMessage",
                 json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        log(f"TG 发送失败：{e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无视 2 小时窗口也要跑浏览器（仅调试）")
    a = ap.parse_args()

    status, remaining, _ = pat_check()
    need = (status == "suspended") or (remaining is not None and remaining < 2 * 3600) or a.force
    if not need:
        log("⏭ 还没到 2 小时续期窗口，跳过浏览器")
        return 0
    try:
        ok = do_browser_renew()
    except Exception as e:
        log(f"❌ 浏览器续期异常：{e}")
        send_tg(f"❌ BlackHost 自动续期失败：{str(e)[:200]}")
        raise
    # 续期后复查
    info = pat_check()
    if ok:
        send_tg(f"✅ BlackHost 自动续期成功，新到期 {info[2].get('active_until')}")
    else:
        send_tg(f"❌ BlackHost 自动续期未成功，当前剩余 {fmt_remaining(info[1])}，需手动处理")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"❌ 失败：{e}")
        # 工作流里失败也要把截图带走，退出码 1 让 TG 收到失败通知
        sys.exit(1)
