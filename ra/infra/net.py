#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理感知的 HTTP 层（全局唯一入口）。

背景（2026-10-16 诊断）：
本机 TUN 虽已接管默认路由（Meta Tunnel，metric 0），但 **系统 DNS 未被接管**，
`www.google.com` 被解析到 `69.171.235.22`（Facebook 网段）→ TCP 超时。
显式走本地 HTTP 代理时，域名解析发生在代理侧（远端 DNS），一切正常。

因此：**TUN 开关不影响本项目能否联网，唯一可靠路径是显式代理。**
本模块统一探测 + 注入，业务代码只调`net.urlopen` / `net.build_opener`。

优先级：
1. 环境变量 HTTPS_PROXY / HTTP_PROXY（显式注入，最高优先）
2. 本地常见代理端口探测（17890 / 7890 / 10809 / 1080 / 8888 / 7897）
3. 都没有 → 直连（国内源仍可用）

国内域名（.cn / .com.cn 及新浪/东财/腾讯/微博/妙想等）一律 **绕过代理**，
避免无谓绕路与被代理拖慢；境外域名走代理。
"""
import os
import socket
import urllib.request

# 本地代理候选端口（按实测命中率排序；17890 为当前客户端实际监听端口）
_PROXY_CANDIDATES = ("17890", "7890", "10809", "1080", "8888", "7897", "1081")

# 国内/直连即通的域名后缀与主机名片段
_DIRECT_SUFFIXES = (
    ".cn", ".com.cn", ".gov.cn", ".org.cn", ".net.cn",
)
_DIRECT_HOSTS = (
    "sina.com.cn", "sinajs.cn", "eastmoney.com", "emdata.eastmoney.com",
    "push2.eastmoney.com", "qt.gtimg.cn", "web.ifzq.gtimg.cn",
    "weibo.com", "weibo.cn", "m.weibo.cn", "weibocdn.com",
    "10jqka.com.cn", "hexun.com", "cninfo.com.cn", "sse.com.cn",
    "szse.cn", "stats.gov.cn", "pbc.gov.cn", "stats-sb.gov.cn",
)

_state = {"probed": False, "proxy": None}

UA_B = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ⚠️ WorkBuddy 宿主注入的内部服务代理（hooks / RPC 通道），**不是**网络代理，
# 绝对不能拿来转发业务请求。只在环境变量回落时作为过滤名单。
_BLOCKED_PROXIES = {
    v for k, v in os.environ.items()
    if k.upper().endswith("_PROXY_URL") and "CODEBUDDY" in k.upper() and v
}


def _norm(px):
    """把裸端口/裸地址补成 http://host:port 形式。"""
    if not px:
        return None
    px = px.strip()
    if "://" in px:
        return px
    return "http://127.0.0.1:%s" % px


def _port_alive(host, port, timeout=0.35):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def detect_proxy(force=False):
    """探测可用代理，返回 'http://127.0.0.1:port' 或 None。结果缓存。

    ⚠️ 必须 **优先本地端口探测**，不能直接信任环境变量：
    WorkBuddy 宿主会注入 HTTP_PROXY/HTTPS_PROXY=127.0.0.1:<内部服务端口>，
    那是内部 hooks 通道，直接复用会把业务流量打到非代理端口上。
    """
    if _state["probed"] and not force:
        return _state["proxy"]

    # 1) 本地常见代理端口（真代理客户端）
    for port in _PROXY_CANDIDATES:
        if _port_alive("127.0.0.1", port):
            _state["probed"] = True
            _state["proxy"] = "http://127.0.0.1:%s" % port
            return _state["proxy"]

    # 2) 环境变量回落，但必须排除 WorkBuddy 内部服务代理端口
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = _norm(os.environ.get(var))
        if not val:
            continue
        if val in _BLOCKED_PROXIES:
            continue
        host_port = val.rsplit("/", 1)[-1]
        try:
            hp, pt = host_port.split(":")
        except ValueError:
            continue
        if _port_alive(hp, pt):
            _state["probed"] = True
            _state["proxy"] = val
            return val

    _state["probed"] = True
    _state["proxy"] = None
    return None


def is_direct(url_or_host):
    """判断目标是否应绕过代理（国内源直连更快更稳）。"""
    host = url_or_host
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split(":", 1)[0].lower()
    if not host:
        return True
    for h in _DIRECT_HOSTS:
        if host == h or host.endswith("." + h):
            return True
    return any(host.endswith(s) for s in _DIRECT_SUFFIXES)


def _proxy_bypass(host):
    """urllib ProxyHandler 钩子：返回 True 表示不走代理。"""
    return is_direct(host)


_OPENER_DIRECT = None
_OPENER_PROXY = {}


def _direct_opener():
    global _OPENER_DIRECT
    if _OPENER_DIRECT is None:
        _OPENER_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _OPENER_DIRECT


def build_opener(url=None):
    """按目标 URL 返回合适的 opener：国内直连 / 境外走代理。

    未探测到代理时统一返回直连 opener（与改造前行为完全一致，不引入回归）。
    """
    if url is not None and is_direct(url):
        return _direct_opener()

    px = detect_proxy()
    if not px:
        return _direct_opener()

    if px not in _OPENER_PROXY:
        _OPENER_PROXY[px] = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px})
        )
    return _OPENER_PROXY[px]


def urlopen(req, timeout=10, url=None):
    """代理感知的 urlopen 包装：url 参数用于决定直连/代理，缺省从 req.full_url 取。"""
    target = url or getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
    return build_opener(target).open(req, timeout=timeout)


def status():
    """返回可读状态，供 cli / collect 启动时打印。"""
    px = detect_proxy()
    if px:
        return "[net] 代理已启用: %s（国内源直连绕过）" % px
    return "[net] 未探测到本地代理，全部直连（境外源大概率超时）"


if __name__ == "__main__":
    print(status())
    for u in ("https://www.google.com", "https://news.google.com/rss?hl=zh-CN",
              "https://home.treasury.gov/", "https://finance.sina.com.cn",
              "https://push2.eastmoney.com/", "https://m.weibo.cn/"):
        op = build_opener(u)
        route = "直连" if is_direct(u) else "代理"
        try:
            r = op.open(urllib.request.Request(u, headers={"User-Agent": UA_B}), timeout=12)
            print("  %-4s %-38s %s %s bytes" % (route, u[:38], r.status, len(r.read())))
        except Exception as e:
            print("  %-4s %-38s ERR %s" % (route, u[:38], type(e).__name__))