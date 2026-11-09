#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BlackHost 免费服自动续期：真浏览器点按钮（GitHub Actions 每 6 小时跑一次）。

点过续期就重置：无论剩余多少，倒计时直接回到满 8h（不叠加）。
窗口 = 到期前 2h。PAT 先查剩余，剩 <2h10m（含挂起）才拉浏览器：
  - 跳点比窗点早到几秒~几分钟 → 原地等按钮亮（最多 12 分钟）再点
  - 跳点晚到 → 按钮已亮，直接点
必须真浏览器：PAT 调 renew 403、空 turnstileToken 400，token 只能在页面里现场生成。

环境变量（GitHub Secrets）：BH_PAT / BH_COOKIE_HEADER / BH_TG_TOKEN / BH_TG_CHAT（可选）
产物：artifacts/ 截图
"""
import argparse
import atexit
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs, unquote

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


def parse_node_link(link):
    """分享链接 -> sing-box outbound 对象。支持 vless/vmess/trojan/hysteria2/tuic/anytls/socks5。"""
    link = link.strip()
    scheme = link.split("://", 1)[0].lower()
    default_port = {"vless": 443, "trojan": 443, "hysteria2": 443, "hy2": 443,
                    "tuic": 443, "anytls": 443, "socks5": 1080, "socks": 1080}.get(scheme, 443)

    def tls_block(sec, sni, fp, q, allow_utls=True):
        t = {"enabled": sec in ("tls", "reality"), "server_name": sni,
             "insecure": q.get("insecure", q.get("allowInsecure", "")) in ("1", "true")}
        # hysteria2/tuic 是 QUIC 系协议，sing-box 不支持 uTLS，塞了直接报 unsupported usage for uTLS
        if allow_utls:
            t["utls"] = {"enabled": True, "fingerprint": fp or "chrome"}
        if sec == "reality":
            t["reality"] = {"enabled": True, "public_key": q.get("pbk", ""), "short_id": q.get("sid", "")}
        return t

    def transport_block(t, q):
        t = t or "tcp"
        if t == "tcp":
            return None
        if t == "grpc":
            return {"type": "grpc", "service_name": unquote(q.get("path", ""))}
        tr = {"type": t, "path": unquote(q.get("path", "/") or "/")}
        if q.get("host"):
            tr["headers"] = {"Host": q["host"]}
        return tr

    if scheme == "vmess":
        b64 = link.split("://", 1)[1].strip()
        b64 += "=" * (-len(b64) % 4)
        info = json.loads(base64.b64decode(b64).decode("utf-8", "ignore"))
        out = {"type": "vmess", "server": info.get("add", ""), "server_port": int(info.get("port") or 443),
               "uuid": info.get("id", ""), "alter_id": int(info.get("aid") or 0), "security": info.get("scy", "auto")}
        tr = transport_block(info.get("net", "tcp"), {"path": info.get("path", "/"), "host": info.get("host", "")})
        if tr:
            out["transport"] = tr
        if info.get("tls"):
            out["tls"] = tls_block("tls", info.get("sni") or info.get("host") or info.get("add", ""), info.get("fp"), {})
        return out if out["server"] else None

    u = urlsplit(link)
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    server = u.hostname or ""
    try:
        port = u.port or default_port
    except ValueError:
        port = default_port
    user = unquote(u.username or "")
    pwd = unquote(u.password or "")
    sec = q.get("security", "none")
    sni = q.get("sni") or (server if sec in ("tls", "reality") else "")
    fp = q.get("fp", "chrome")

    if scheme == "vless":
        out = {"type": "vless", "server": server, "server_port": port, "uuid": user, "packet_encoding": "xudp"}
        if q.get("flow"):
            out["flow"] = q["flow"]
        tr = transport_block(q.get("type"), q)
        if tr:
            out["transport"] = tr
        if sec in ("tls", "reality"):
            out["tls"] = tls_block(sec, sni, fp, q)
        return out

    if scheme == "trojan":
        out = {"type": "trojan", "server": server, "server_port": port, "password": user}
        tr = transport_block(q.get("type"), q)
        if tr:
            out["transport"] = tr
        out["tls"] = tls_block("tls", sni or server, fp, q)
        return out

    if scheme in ("hysteria2", "hy2"):
        out = {"type": "hysteria2", "server": server, "server_port": port, "tls": tls_block("tls", q.get("sni") or server, fp, q, allow_utls=False)}
        if user:
            out["password"] = user
        if q.get("obfs"):
            out["obfs"] = {"type": "salamander", "password": q["obfs"]}
        return out

    if scheme == "tuic":
        out = {"type": "tuic", "server": server, "server_port": port, "uuid": user, "password": pwd,
               "congestion_control": q.get("congestion_control", "bbr"), "udp_over_stream": True}
        out["tls"] = tls_block("tls", q.get("sni") or server, fp, q, allow_utls=False)
        if q.get("alpn"):
            out["tls"]["alpn"] = [q["alpn"]]
        return out

    if scheme == "anytls":
        out = {"type": "anytls", "server": server, "server_port": port, "password": user}
        out["tls"] = tls_block("tls", q.get("sni") or server, fp, q)
        return out

    if scheme in ("socks5", "socks"):
        out = {"type": "socks", "server": server, "server_port": port, "version": "5"}
        if user:
            out["username"] = user
        if pwd:
            out["password"] = pwd
        return out

    return None


def _spawn_and_probe(outbound):
    """按给定 outbound 拉起 sing-box 并探测出口，成功返回 proxy 配置，失败返回 None。"""
    cfg = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": 1080}],
        "outbounds": [outbound],
    }
    with open("sing-box.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    logf = open("artifacts/sing-box.log", "wb")
    proc = subprocess.Popen(["./sing-box", "run", "-c", "sing-box.json"],
                            stdout=logf, stderr=subprocess.STDOUT)
    atexit.register(proc.terminate)
    proxy_url = "socks5://127.0.0.1:1080"
    for _ in range(3):
        if proc.poll() is not None:
            proc.terminate()
            return None
        try:
            r = requests.get("https://api.ipify.org",
                             proxies={"http": proxy_url, "https": proxy_url}, timeout=10)
            if r.status_code == 200 and r.text.strip():
                log(f"代理出口就绪，出口 IP {r.text.strip()}")
                return {"server": proxy_url}
        except Exception:
            pass
        time.sleep(3)
    # 失败要杀掉进程，否则重试时 1080 端口被占，第二个 sing-box 起不来
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    return None


def _force_insecure(outbound):
    """递归把 outbound 里的 tls.insecure 全部置 true（节点证书只有 CN 无 SAN 时 Go 会拒）。"""
    if isinstance(outbound, dict):
        if "server_name" in outbound and "enabled" in outbound:
            outbound["insecure"] = True
        for v in outbound.values():
            _force_insecure(v)
    elif isinstance(outbound, list):
        for v in outbound:
            _force_insecure(v)


def start_proxy():
    """配置了 BH_NODE_LINK 就拉起本地 sing-box（socks5 127.0.0.1:1080），返回 Playwright proxy 配置；没配返回 None。

    先按链接原样连；失败则自动 insecure 重试一次（免费节点证书经常只有 CN 没有 SAN）。
    """
    link = os.environ.get("BH_NODE_LINK", "").strip()
    if not link:
        log("未配置代理节点，直连模式")
        return None
    if requests is None:
        raise RuntimeError("缺少 requests，无法探测代理出口")
    out = parse_node_link(link)
    if not out or not out.get("server"):
        raise RuntimeError("BH_NODE_LINK 无法解析成 sing-box outbound（支持 vless/vmess/trojan/hysteria2/tuic/anytls/socks5）")

    result = _spawn_and_probe(out)
    if result:
        return result

    forced = json.loads(json.dumps(out))
    _force_insecure(forced)
    log("节点直连失败，改用 insecure 模式重试")
    result = _spawn_and_probe(forced)
    if result:
        return result

    raise RuntimeError("sing-box 代理探测失败（原样 + insecure 都不通），看 artifacts/sing-box.log")


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

    renew_result = {}

    with sync_playwright() as p:
        proxy_cfg = start_proxy()
        launch_opts = {"headless": True}
        if proxy_cfg:
            launch_opts["proxy"] = proxy_cfg
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
            # 6h 跳点会比窗口开点早几秒~几分钟（续期动作耗时 ε），原地等它亮，不跳过
            log("按钮还没亮（差一点到 2h 窗口），原地等它变可用，最多 12 分钟")
            deadline = time.time() + 720
            ok_wait = False
            while time.time() < deadline:
                page.wait_for_timeout(15000)
                if btn.first.is_enabled():
                    ok_wait = True
                    log("按钮已亮，进窗口了，继续")
                    break
            if not ok_wait:
                page.screenshot(path="artifacts/error_btn_disabled.png")
                raise RuntimeError("等了 12 分钟续期按钮仍 disabled —— 窗口没到或被服务端限制")
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
    # 放宽到 2h10m：6h 跳点会比窗口开点早几秒~几分钟，进浏览器原地等按钮亮，跳点就能全部续上
    need = (status == "suspended") or (remaining is not None and remaining < 2 * 3600 + 600) or a.force
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
