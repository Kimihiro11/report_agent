#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股舆情操作指引 Agent（数据引擎）
读取 config.json，抓取微博舆情 + 全球宏观 + 日本传导链 + A股行情，写入 JSON 快照与可选 PostgreSQL。
全功能 9 章节报告由 build_report.py + WebSearch 实时拼装生成（简版模式已取消）。

用法:
    python a_stock_agent.py            # 采集数据 → 快照 → 入库（全功能报告另由 build_report 生成）
    python a_stock_agent.py --no-fetch # 仅用缓存数据生成快照
    python a_stock_agent.py --backtest # 回测模式（独立，生成回测报告）

依赖: 仅需Python标准库（urllib/json/re），无需pip安装
"""
import json
import os
import urllib.request
import urllib.parse
import re
import sys
import socket
import subprocess
import html
import gzip
import csv
import io
import time as _time_mod
import functools
from datetime import datetime
from pathlib import Path

try:
    from db import StockAgentDB
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_url(url, headers=None, timeout=10, quiet=False, encoding="utf-8"):
    """抓取 URL 文本。encoding 默认 utf-8；新浪行情（hq.sinajs.cn）等 GBK 源传 encoding="gbk"。

    增强请求头：Accept / Accept-Language / Accept-Encoding / Connection，
    支持 gzip 自动解压，提升兼容性与反爬通过率。
    """
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "*/*")
    req.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    req.add_header("Accept-Encoding", "gzip, deflate")
    req.add_header("Connection", "keep-alive")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # 自动解压 gzip
            if resp.headers.get("Content-Encoding") == "gzip":
                try:
                    data = gzip.decompress(data)
                except Exception:
                    pass
            return data.decode(encoding, errors="replace")
    except Exception as e:
        if not quiet:
            print(f"  [fetch error] {url[:80]} -> {e}")
        return None


# ----------------------------------------------------------------------------
# 通用兜底：多源切换 + 重试
# ----------------------------------------------------------------------------

def _fetch_with_fallback(sources, label="数据"):
    """按优先级尝试多个抓取源，返回第一个非空结果；全部失败返回 None。

    sources: [(名称, callable), ...]，callable 返回 list/dict（空=falsy 视为失败）。
    任一源抛异常不影响后续源；最终打印命中来源，便于排查哪一层生效。
    """
    for name, fn in sources:
        try:
            data = fn()
            if data:
                print(f"  [兜底] {label} 命中来源: {name}（{len(data)} 条）")
                return data
            print(f"  [兜底] {name} 返回空，尝试下一源")
        except Exception as e:
            print(f"  [兜底] {name} 异常: {e}，尝试下一源")
    print(f"  [兜底] {label} 所有源均失败")
    return None


# ----------------------------------------------------------------------------
# 东方财富「妙想」金融数据技能（结构化实时数据兜底源，需用户授权 EM_API_KEY）
#   路径随用户主目录变化，运行时探测；未授权/未安装时返回 None，由上游继续兜底。
# ----------------------------------------------------------------------------

def _mx_skill_script():
    """探测妙想技能脚本路径（~/.workbuddy/skills/mx-finance-data/scripts/get_data.py）。"""
    cand = Path.home() / ".workbuddy" / "skills" / "mx-finance-data" / "scripts" / "get_data.py"
    return cand if cand.exists() else None


def _run_mx_skill(query, indicators, timeout=120):
    """调用东方财富妙想技能取结构化实时数据，返回 Markdown 文本或 None。

    - 未安装/未授权 -> 返回 None（不抛异常，交由上层兜底）。
    - 成功 -> 读取技能输出的 .md 文件并返回其文本。
    """
    script = _mx_skill_script()
    if not script:
        return None
    py = sys.executable
    cmd = [py, str(script), "--query", query, "--indicators", indicators]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=str(script.parent.parent))
    except Exception as e:
        print(f"  [妙想] 调用失败: {e}")
        return None
    out = proc.stdout or ""
    if "need_auth" in out or "尚未完成授权" in out:
        print("  [妙想] 尚未授权 EM_API_KEY，跳过（请在东方财富妙想授权后重跑）。")
        return None
    m = re.search(r"Markdown:\s*(\S+)", out)
    if not m:
        return None
    md_path = m.group(1)
    try:
        return Path(md_path).read_text(encoding="utf-8")
    except Exception:
        return None


def _parse_mx_quote(md, name):
    """从妙想 md 中定位 name 所在行，提取 (涨跌幅%, 最新价)。任一缺失返回 (None, None)。"""
    for line in md.splitlines():
        if name in line:
            pm = re.search(r"(-?\d+\.?\d*)%", line)
            pct = float(pm.group(1)) if pm else None
            price = None
            for n in re.findall(r"-?\d+\.\d+", line):
                if pm and n == pm.group(1):
                    continue
                price = n
                break
            return pct, price
    return None, None


# ----------------------------------------------------------------------------
# 腾讯自选股（westock）连接器：ETF 资金净流兜底源（streamableHttp MCP）
#   鉴权由 WorkBuddy 运行时管理；脚本内调用需在 config.json 的 westock.auth_token
#   填入有效 token，或预置环境变量 WESTOCK_AUTH_TOKEN；未配置/不可达时返回 None，
#   由上层继续使用东方财富 push2 主源（绝不抛异常阻断主流程）。
#   返回字段（A股）：mainNetFlow（主力净流入，单位：元）→ 换算亿元。
# ----------------------------------------------------------------------------

def _westock_mcp_url():
    """读取 westock MCP 地址（config 优先，缺省回退官方地址）。"""
    try:
        cfg = load_config()
        w = cfg.get("westock") or {}
        if w.get("mcp_url"):
            return w["mcp_url"]
    except Exception:
        pass
    return "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"


def _westock_auth_token():
    """读取 westock 访问令牌：环境变量优先，其次 config.westock.auth_token。"""
    t = os.environ.get("WESTOCK_AUTH_TOKEN")
    if t:
        return t
    try:
        cfg = load_config()
        w = cfg.get("westock") or {}
        return w.get("auth_token") or ""
    except Exception:
        return ""


def _westock_strip_sse(raw):
    """从 MCP 响应体（纯 JSON 或 SSE text/event-stream）中提取 JSON-RPC 消息列表。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("{"):
        try:
            return [json.loads(raw)]
        except Exception:
            return []
    msgs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            msgs.append(json.loads(payload))
        except Exception:
            continue
    return msgs


def _westock_mcp_call(params, token, url, timeout=30):
    """最小 MCP streamableHttp 客户端：initialize → notifications/initialized → tools/call。

    返回 tools/call 的 result dict 或 None。任何失败都打印并返回 None（绝不抛异常）。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session_id = [None]

    def _post(body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        if session_id[0]:
            req.add_header("Mcp-Session-Id", session_id[0])
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    session_id[0] = sid
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace") if e.fp else ""
            print(f"  [westock] HTTP {e.code}: {err[:200]}")
            return None
        except Exception as e:
            print(f"  [westock] 请求异常: {e}")
            return None
        msgs = _westock_strip_sse(raw)
        return msgs[-1] if msgs else None

    init = _post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "report_agent", "version": "1.0"},
        },
    })
    if init is None or init.get("error"):
        return None
    _post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
    if resp is None or resp.get("error"):
        return None
    return resp.get("result")


def _westock_parse_fundflow(result):
    """从 data_fund_flow 的 result 解析每只 code（无市场前缀）的主力净流入（元）。"""
    if not result:
        return {}
    text = ""
    for c in (result.get("content") or []):
        if isinstance(c, dict) and c.get("type") == "text":
            text += c.get("text", "")
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        obj = None
    items = None
    if isinstance(obj, list):
        items = obj
    elif isinstance(obj, dict):
        for k in ("data", "list", "items", "result"):
            if isinstance(obj.get(k), list):
                items = obj[k]
                break
    out = {}
    if items:
        for it in items:
            if not isinstance(it, dict):
                continue
            code = str(it.get("code") or it.get("stockCode") or "").lower()
            for prefix in ("sh", "sz", "hk", "us"):
                if code.startswith(prefix):
                    code = code[len(prefix):]
                    break
            val = it.get("mainNetFlow")
            if val is None:
                val = it.get("MainNetFlow")
            if val is not None:
                try:
                    out[code] = float(val)
                except (ValueError, TypeError):
                    pass
    else:
        # 退化：正则从文本抓取「代码 + 主力净流入」
        for m in re.finditer(r"(sh|sz|hk|us)?(\d{6})[^\n]*?主力净流入[^\d\-]*?(-?[\d,]+\.?\d*)", text):
            code = m.group(2)
            try:
                out[code] = float(m.group(3).replace(",", ""))
            except ValueError:
                pass
    return out


def _westock_override_path():
    """Agent 经已连接连接器生成的本地 ETF 覆盖文件路径（无需静态 token）。"""
    return OUTPUT_DIR / "data" / "westock_etf_override.json"


def _etf_westock_override():
    """读取 agent 经 westock-mcp 连接器写入的 ETF 覆盖文件（date==今日 才有效）。

    平台托管鉴权下，脚本无法获取 westock 静态 token；正确用法是：连接
    westock-mcp 连接器后，由 agent 经 MCP 工具取数并写入本文件，脚本直接消费，
    避免 401 unauthorized。返回 [(name,code,direction,cls,signal),...] 或 None。
    """
    p = _westock_override_path()
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if obj.get("date") != datetime.now().strftime("%Y%m%d"):
        return None
    out = []
    for f in (obj.get("flows") or []):
        name = f.get("name")
        code = f.get("code")
        if not name or not code:
            continue
        out.append((name, code, f.get("direction", ""), f.get("cls", ""), f.get("signal", "")))
    return out or None


# ----------------------------------------------------------------------------
# 外网连通性探测 + 搜索引擎（Google 优先，Bing 兜底，必应国际版补充）
# ----------------------------------------------------------------------------
_NETWORK_OK = None   # 网络探测结果缓存
_NETWORK_TS = 0      # 缓存时间戳
_NETWORK_TTL = 30    # 缓存有效期（秒），超时后重新探测


def check_network(host="news.google.com", port=443, timeout=4):
    """快速探测外网是否连通（连接 Google News 443）。结果缓存 30 秒（TTL）。

    返回 True 表示外网可达，采集路径进入「深度」模式（搜集更多关键信息）；
    返回 False 则退守「浅度」模式，仅做必要查询。
    """
    global _NETWORK_OK, _NETWORK_TS
    now = _time_mod.time()
    if _NETWORK_OK is not None and (now - _NETWORK_TS) < _NETWORK_TTL:
        return _NETWORK_OK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)  # 仅作用于本 socket，避免污染全局默认超时
        s.connect((host, port))
        s.close()
        _NETWORK_OK = True
    except Exception:
        _NETWORK_OK = False
    _NETWORK_TS = now
    return _NETWORK_OK


@functools.lru_cache(maxsize=1)
def _collect_depth_cached():
    """采集深度缓存（lru_cache，进程内只算一次，由 check_network TTL 控制刷新）。"""
    return "deep" if check_network() else "shallow"


def collect_depth():
    """返回采集深度：网络通 -> 'deep'（搜集更多关键信息），不通 -> 'shallow'。"""
    # 清除 lru_cache 以便 check_network 的 TTL 能生效
    _collect_depth_cached.cache_clear()
    return _collect_depth_cached()


# Google News RSS 优先；必应 News RSS 兜底（对「最新」类词易返回空频道）
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
BING_NEWS_RSS = "https://www.bing.com/news/search?q={q}&format=rss"
BING_NEWS_RSS_INT = "https://www.bing.com/news/search?q={q}&format=rss&setlang=en-us&cc=US"


def _clean_rss_text(s):
    """反复 html.unescape（Bing 常把实体二次转义为 &amp;nbsp; 等，单次还原不彻底），
    再去标签、合并空白。"""
    prev = None
    for _ in range(3):
        prev = s
        s = html.unescape(s)
        if s == prev:
            break
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_rss(text, max_items=5, max_len=300):
    """解析 RSS <item>，统一提取 title/description 文本。

    Google News RSS 的 description 常为 HTML 转义片段（&lt;a href=...&gt;），
    Bing RSS 则会把实体二次转义（&amp;nbsp;）；统一走 _clean_rss_text 处理。
    """
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    out = []
    for it in items[:max_items]:
        tm = re.search(r"<title>(.*?)</title>", it, re.S)
        dm = re.search(r"<description>(.*?)</description>", it, re.S)
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip() if tm else ""
        desc = re.sub(r"<!\[CDATA\[|\]\]>", "", dm.group(1)).strip() if dm else ""
        title = _clean_rss_text(title)
        desc = _clean_rss_text(desc)
        content = f"{title}。{desc}" if desc else title
        if content and len(content) > 10:
            out.append({"text": content[:max_len], "time": ""})
    return out


def search_news(query, max_items=5):
    """外网新闻搜索：Google News RSS 优先，回退 必应（国内版）-> 必应国际版。

    - query 建议为干净短语；Google 通道对「最新」类词无碍，Bing 通道可能返回空频道。
    - 返回 [{"text":..., "time":""}, ...]，统一结构供下游消费。
    - Google 尝试静默（quiet=True）：它在受限网络常失败，且有 Bing 兜底，
      避免每次都刷 [fetch error] 噪音；Bing 失败仍正常打印以便排查。
    - 沙箱常屏蔽 Google，必应国际版（en-us/cc=US）可作为第三层兜底，提高新闻命中率。
    """
    q = urllib.parse.quote(query)
    sources = [
        ("Google", GOOGLE_NEWS_RSS.format(q=q), True),
        ("必应", BING_NEWS_RSS.format(q=q), False),
        ("必应国际版", BING_NEWS_RSS_INT.format(q=q), False),
    ]
    for name, url, quiet in sources:
        text = fetch_url(url, quiet=quiet)
        if text:
            res = _parse_rss(text, max_items)
            if res:
                return res
    return []


def _parse_weibo_time(created_at):
    """解析微博 created_at（如 'Mon Aug 17 19:30:00 +0800 2026'）→ datetime；失败返回 None。"""
    if not created_at:
        return None
    try:
        import email.utils
        parts = created_at.split()
        # 新浪格式：周几 月 日 时:分:秒 时区 年
        if len(parts) == 6:
            dt = email.utils.parsedate_tz(f"{parts[1]} {parts[2]} {parts[4]} {parts[3]} {parts[5]}")
            if dt:
                import time as _time
                ts = email.utils.mktime_tz(dt)
                return datetime.fromtimestamp(ts)
        return None
    except Exception:
        return None


def fetch_weibo(user_id, name, cookie="", only_today=True):
    """抓取微博用户最新内容（m.weibo.cn API），需在config.json配置weibo_cookie。

    only_today=True（默认）：仅保留当日发布的微博；当日无更新返回空列表。
    返回 [{"text":..., "time":...}, ...]，统一结构供下游消费。
    """
    if not cookie:
        print(f"[微博] 跳过 {name} (UID:{user_id}) — 未配置 weibo_cookie")
        return []
    print(f"[微博] 抓取 {name} (UID:{user_id})...")
    containerid = f"107603{user_id}"
    url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={user_id}&containerid={containerid}"
    headers = {
        "Referer": f"https://m.weibo.cn/u/{user_id}",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie,
    }
    text = fetch_url(url, headers)
    if not text:
        return []
    try:
        data = json.loads(text)
        if data.get("ok") != 1:
            print(f"  [微博] {name} 返回 ok={data.get('ok')}，cookie 可能已失效，请更新 config.json 的 weibo_cookie")
            return []
        cards = data.get("data", {}).get("cards", [])
        posts = []
        today = datetime.now().date()
        for card in cards:
            mblog = card.get("mblog", {})
            if not mblog:
                continue
            raw_text = mblog.get("text", "")
            clean = re.sub(r"<[^>]+>", "", raw_text).strip()
            created = mblog.get("created_at", "")
            if only_today:
                dt = _parse_weibo_time(created)
                if dt is None or dt.date() != today:
                    continue  # 非当日更新，跳过
            if clean and len(clean) > 10:
                posts.append({"text": clean[:500], "time": created})
        print(f"  获取 {len(posts)} 条微博（{'仅当日' if only_today else '全部'})")
        return posts[:10]
    except Exception as e:
        print(f"  [parse error] {e}")
        return []


def fetch_global_source(name, signal_type):
    """搜索全球人物/机构的外网动态（X发言、新闻转载）。

    优先 Google News RSS，失败回退 Bing News RSS。
    网络通时额外补充「最新表态」类查询，搜集更多关键信息。
    """
    print(f"[全球] 搜索 {name} 动态...")
    deep = collect_depth() == "deep"
    results = search_news(f"{name} 中国 股市", max_items=8 if deep else 3)
    if deep:
        # 网络通：Google 通道支持「最新」类词，补充关键信息
        results += search_news(f"{name} 最新表态 A股", max_items=5)
    print(f"  获取 {len(results)} 条")
    return results


def fetch_macro_data(macro_config):
    """通过新闻搜索获取最新宏观经济数据动态（中美GDP/CPI/非农/利率等）。
    优先 Google，回退 Bing；网络通时增加采集条目与补充指标（社融/M2/进出口）。
    """
    print("[宏观] 搜索最新宏观数据...")
    deep = collect_depth() == "deep"
    search_pairs = [
        ("中国", "CPI"), ("中国", "GDP"), ("中国", "PMI"),
        ("美国", "CPI"), ("美国", "非农就业"), ("美国", "美联储利率"),
    ]
    if deep:
        search_pairs += [("中国", "社融 M2"), ("美国", "GDP"), ("中国", "进出口")]
    results = {}
    for country, indicator in search_pairs:
        query = f"{country} {indicator} {datetime.now().strftime('%Y年%m月')}"
        items = search_news(query, max_items=5 if deep else 3)
        if items:
            results[f"[宏观] {country}{indicator}"] = items
    print(f"  获取 {len(results)} 条宏观数据")
    return results


def fetch_event_factors(factors_config):
    """通过新闻搜索获取全球事件因子动态（地缘战争/原油/自然灾害）。
    优先 Google，回退 Bing；网络通时扩充事件维度与条目数。
    """
    print("[事件] 搜索全球事件因子...")
    deep = collect_depth() == "deep"
    search_pairs = [
        ("地缘", "中东局势"), ("地缘", "俄乌冲突"),
        ("原油", "WTI原油价格"), ("原油", "OPEC决议"),
        ("灾害", "台风"), ("灾害", "地震"),
    ]
    if deep:
        search_pairs += [("地缘", "红海航运"), ("原油", "布伦特原油"),
                         ("灾害", "洪水"), ("贸易", "中美关税")]
    results = {}
    for category, keyword in search_pairs:
        query = f"{keyword} {datetime.now().strftime('%Y年%m月')}"
        items = search_news(query, max_items=5 if deep else 3)
        if items:
            results[f"[事件] {category}{keyword}"] = items
    print(f"  获取 {len(results)} 条事件因子")
    return results


def fetch_kline(symbol, datalen=60):
    """获取日K线数据并计算简单技术指标（均线/趋势判断）"""
    print(f"[技术] 获取 {symbol} K线...")
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&datalen={datalen}"
    text = fetch_url(url, timeout=10)
    if not text:
        return {}
    try:
        data = json.loads(text)
        if not data:
            return {}
        closes = [float(d["close"]) for d in data]

        def ma(arr, n):
            return round(sum(arr[-n:]) / n, 2) if len(arr) >= n else None

        latest = closes[-1]
        m5, m10, m20, m60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
        hi5 = max(float(d["high"]) for d in data[-5:])
        lo5 = min(float(d["low"]) for d in data[-5:])
        if m5 and m10 and m20:
            if m5 > m10 > m20:
                trend = "多头排列(偏多)"
            elif m5 < m10 < m20:
                trend = "空头排列(偏空)"
            else:
                trend = "震荡"
        else:
            trend = "数据不足"
        result = {"close": latest, "ma5": m5, "ma10": m10, "ma20": m20, "ma60": m60, "high5": hi5, "low5": lo5, "trend": trend}
        print(f"  {symbol}: {latest} {trend}")
        return result
    except Exception as e:
        print(f"  [parse error] {e}")
        return {}


def fetch_technical_analysis(ta_config):
    """对主线指数和自选股做技术走势分析"""
    if not ta_config.get("enabled"):
        return {}
    print("[技术] 开始技术分析...")
    results = {}
    targets = ta_config.get("index_targets", []) + ta_config.get("stock_targets", [])
    for symbol in targets:
        kline = fetch_kline(symbol)
        if kline:
            results[symbol] = kline
    print(f"  完成 {len(results)} 个标的技术分析")
    return results


def fetch_national_team(nt_config):
    """通过新闻搜索获取国家队资金流向动态（汇金/证金/国新/诚通/社保）。
    优先 Google，回退 Bing；网络通时扩充关键词与条目数。
    """
    if not nt_config.get("enabled"):
        return {}
    print("[国家队] 搜索国家队资金动态...")
    deep = collect_depth() == "deep"
    keywords = ["汇金 ETF 增持", "国家队 ETF 净流入", "社保基金 加仓"]
    if deep:
        keywords += ["国新投资 增持", "证金公司 维稳", "中央汇金 买入"]
    results = {}
    for keyword in keywords:
        query = f"{keyword} {datetime.now().strftime('%Y年%m月')}"
        items = search_news(query, max_items=5 if deep else 3)
        if items:
            results[f"[国家队] {keyword}"] = items
    print(f"  获取 {len(results)} 条国家队动态")
    return results


def fetch_japan_carry(jc_config=None):
    """日本传导链四要素搜索：加息预期/抛美债/FIMA/干预汇率借款（借美款干预）。

    传导链主线：原油→日本输入型通胀→央行加息(50-75bp,非25bp)→抛美债压力
    →FIMA 押美债借美元干预→日元/套息平仓→A股。
    优先 Google News RSS（支持「最新」类词），回退 Bing；网络通时深度采集。
    """
    if jc_config is not None and not jc_config.get("enabled", True):
        return {}
    print("[日本传导链] 搜索 日本加息/抛美债/FIMA/干预汇率...")
    deep = collect_depth() == "deep"
    queries = [
        ("[日本]加息预期", "日本央行 加息 50bp 75bp 2026年9月"),
        ("[日本]抛售美债", "日本 抛售 美国国债 干预日元 2026年8月"),
        ("[日本]FIMA回购", "美联储 FIMA 回购工具 日本 干预汇率"),
        ("[日本]借美款干预", "日本 借美元 干预汇率 FIMA"),
    ]
    if deep:
        queries += [
            ("[日本]套息平仓", "日元 套息交易 平仓 2026"),
            ("[日本]美债持仓", "日本 美国国债 持仓 1.1万亿"),
        ]
    results = {}
    for label, q in queries:
        items = search_news(q, max_items=5 if deep else 3)
        if items:
            results[label] = items
    print(f"  获取 {len(results)} 类日本传导链信息")
    return results


def _load_index_quotes():
    """从 config.json 读取指数行情配置；缺失时回退内置默认。"""
    try:
        cfg = load_config()
        idx = cfg.get("index_quotes")
        if idx:
            codes = ",".join(i["sina_code"] for i in idx)
            name_map = {i["sina_code"]: i["name"] for i in idx}
            return codes, name_map
    except Exception:
        pass
    codes = "sh000001,sz399001,sz399006,sh000688,sh000300"
    name_map = {
        "sh000001": "上证指数", "sz399001": "深证成指",
        "sz399006": "创业板指", "sh000688": "科创50", "sh000300": "沪深300",
    }
    return codes, name_map


def fetch_index_quotes():
    """抓取主要指数行情（新浪API）"""
    print("[行情] 抓取指数数据...")
    codes, name_map = _load_index_quotes()
    url = f"https://hq.sinajs.cn/list={codes}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    text = fetch_url(url, headers, encoding="gbk")  # 新浪行情接口返回 GBK
    if not text:
        return {}
    result = {}
    for line in text.strip().split("\n"):
        m = re.match(r'var hq_str_(\w+)="(.+)"', line)
        if not m:
            continue
        code, vals = m.group(1), m.group(2).split(",")
        if len(vals) < 10:
            continue
        name = name_map.get(code, code)
        try:
            prev_close = float(vals[2])
            price = float(vals[3])
            chg = price - prev_close
            chg_pct = (chg / prev_close * 100) if prev_close else 0
            result[name] = {
                "price": round(price, 2),
                "chg": round(chg, 2),
                "chg_pct": round(chg_pct, 2),
                "volume": vals[8],
            }
        except (ValueError, IndexError):
            continue
    print(f"  获取 {len(result)} 个指数")
    return result


def _load_us_symbols():
    """从 config.json 读取美股代码映射；config 缺失时回退内置默认。"""
    try:
        cfg = load_config()
        syms = cfg.get("us_symbols")
        if syms:
            return {s["sina_code"]: s["name"] for s in syms}
    except Exception:
        pass
    # 回退默认
    return {
        "gb_dji": "道琼斯", "gb_ixic": "纳斯达克", "gb_inx": "标普500",
        "gb_sox": "费城半导体", "gb_nvda": "英伟达", "gb_tsla": "特斯拉",
        "gb_mu": "美光科技", "gb_stx": "希捷科技", "gb_wdc": "西部数据",
        "gb_sndk": "闪迪", "gb_amat": "应用材料", "gb_avgo": "博通",
        "gb_lite": "Lumentum", "gb_glw": "康宁",
    }


def _us_sina():
    """隔夜美股（新浪美股实时行情），返回 [(name, pct, price, signal), ...]。"""
    symbols = _load_us_symbols()
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    text = fetch_url(url, headers={"Referer": "https://finance.sina.com.cn"}, encoding="gbk")  # 新浪行情接口返回 GBK
    if not text:
        return []
    results = []
    for sym, cn in symbols.items():
        m = re.search(r'var hq_str_%s="([^"]*)"' % re.escape(sym), text)
        if not m:
            continue
        parts = m.group(1).split(",")
        if len(parts) < 4:
            continue
        try:
            price = parts[1].strip()
            raw = parts[2].replace("%", "").strip().replace(",", "")
            pct = float(raw) if raw not in ("", "-") else 0.0
        except (ValueError, IndexError):
            continue
        results.append((cn or parts[0], pct, price, "—"))
    return results


def _us_mx():
    """隔夜美股（东方财富妙想兜底源），解析 md 为 [(name, pct, price, signal), ...]。"""
    symbols = _load_us_symbols()
    names = list(symbols.values())
    md = _run_mx_skill(
        "查询" + "、".join(names) + "的实时行情、涨跌幅",
        "实时行情、涨跌幅")
    if not md:
        return []
    out = []
    for cn in names:
        pct, price = _parse_mx_quote(md, cn)
        if pct is None:
            continue
        out.append((cn, pct, price or "—", "—"))
    return out


def fetch_us_market():
    """隔夜美股主要指数与科技/存储龙头。新浪优先，东方财富妙想兜底。

    返回 [(name, pct, price, signal), ...]，pct 为涨跌幅(float)。
    所有源失败返回空列表（由报告层渲染为「实时数据缺失」占位，绝不写死假数）。
    """
    print("[美股] 抓取隔夜美股行情（新浪优先，妙想兜底）...")
    res = _fetch_with_fallback([("新浪美股", _us_sina), ("东方财富妙想", _us_mx)], label="美股")
    res = res or []
    print(f"  获取 {len(res)} 个美股标的")
    return res


def fetch_us_yield():
    """美债收益率：美国财政部官方日度曲线优先，雅虎行情兜底。

    返回 {"ten_year": float, "short_end": float, "short_end_label": "2Y|3M",
    "data_date": "YYYY-MM-DD", "source": str, "updated": bool}；全部失败返回 None。
    财政部曲线直接提供 10Y/2Y；只有官方源失败时才以雅虎 ^TNX/^IRX 作为兜底。
    """
    print("[美债] 抓取美债收益率（美国财政部 10Y/2Y 优先，雅虎兜底）...")

    def _treasury():
        year = datetime.now().year
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&"
               f"field_tdr_date_value={year}&page&_format=csv")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode("utf-8-sig", "replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            for row in rows:  # 官方 CSV 按日期倒序；取首个 10Y/2Y 均有效的交易日
                try:
                    ten_year = float(row.get("10 Yr") or "")
                    two_year = float(row.get("2 Yr") or "")
                    data_date = datetime.strptime(row["Date"], "%m/%d/%Y").strftime("%Y-%m-%d")
                    return {
                        "ten_year": ten_year,
                        "short_end": two_year,
                        "short_end_label": "2Y",
                        "data_date": data_date,
                        "source": "美国财政部",
                        "updated": True,
                    }
                except (TypeError, ValueError, KeyError):
                    continue
        except Exception as e:
            print(f"  [美债] 美国财政部获取失败: {e}")
        return None

    def _yahoo(sym):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            meta = data["chart"]["result"][0]["meta"]
            return meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        except Exception as e:
            print(f"  [美债] 雅虎 {sym} 获取失败: {e}")
            return None, None

    result = _treasury()
    if result:
        print(f"  10Y={result['ten_year']} 2Y={result['short_end']}（{result['data_date']}，美国财政部）")
        return result

    ten_year, timestamp = _yahoo("^TNX")
    short_end, _ = _yahoo("^IRX")
    if ten_year is None and short_end is None:
        print("  [美债] 全部源不可达，跳过（报告层占位）")
        return None
    data_date = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d") if timestamp else ""
    print(f"  10Y={ten_year} 短端(3M)={short_end}（雅虎兜底）")
    return {
        "ten_year": ten_year,
        "short_end": short_end,
        "short_end_label": "3M",
        "data_date": data_date,
        "source": "雅虎财经",
        "updated": True,
    }


def _load_etf_flows():
    """从 config.json 读取 ETF 资金流配置；缺失时回退内置默认。"""
    try:
        cfg = load_config()
        etfs = cfg.get("etf_flows")
        if etfs:
            return [(e["name"], e["code"], e.get("mkt", "1")) for e in etfs]
    except Exception:
        pass
    return [
        ("沪深300ETF", "510300", "1"), ("芯片ETF", "159995", "0"),
        ("半导体设备ETF国泰", "159516", "0"), ("科创50ETF", "588280", "1"),
    ]


def _etf_push2():
    """ETF 实时资金净流（东方财富 push2，单位：亿元）。返回 [(name,code,direction,cls,signal),...]。"""
    etfs = _load_etf_flows()
    results = []
    for name, code, mkt in etfs:
        secid = f"{mkt}.{code}"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f62"
        text = fetch_url(url, timeout=8)
        if not text:
            continue
        try:
            j = json.loads(text)
            d = j.get("data") or {}
            net = d.get("f62")  # 主力净流入（元）
            if net is None:
                continue
            net_yi = net / 1e8
            direction = "净申购" if net_yi >= 0 else "净赎回"
            cls = "b-red" if net_yi >= 0 else "b-green"
            signal = f"近一日{'净流入' if net_yi >= 0 else '净流出'} {abs(net_yi):.2f}亿元"
            results.append((name, code, direction, cls, signal))
        except Exception:
            continue
    return results


def _etf_westock():
    """ETF 资金流（腾讯自选股连接器兜底源），调用 data_fund_flow 取主力净流入(元)→亿元。

    返回 [(name, code, direction, cls, signal), ...]，与 _etf_push2 同结构；
    未配置 token / 不可达 / 解析失败均返回 []（交由主源 东方财富 push2 兜底）。
    """
    etfs = _load_etf_flows()
    token = _westock_auth_token()
    if not token:
        print("  [westock] 未配置 auth_token（config.westock.auth_token 或 WESTOCK_AUTH_TOKEN），跳过 ETF 兜底。")
        return []
    url = _westock_mcp_url()
    mk = {"1": "sh", "0": "sz"}  # 沪=sh，深=sz（腾讯自选股代码格式）
    codes = [f"{mk.get(m, 'sh')}{code}" for _, code, m in etfs]
    result = _westock_mcp_call(
        {"name": "data_fund_flow", "arguments": {"codes": ",".join(codes)}},
        token, url,
    )
    if not result:
        return []
    flows = _westock_parse_fundflow(result)
    if not flows:
        return []
    out = []
    for name, code, mkt in etfs:
        net = flows.get(code)  # 元
        if net is None:
            continue
        net_yi = net / 1e8  # 元 → 亿元
        direction = "净申购" if net_yi >= 0 else "净赎回"
        cls = "b-red" if net_yi >= 0 else "b-green"
        signal = f"近一日{'净流入' if net_yi >= 0 else '净流出'} {abs(net_yi):.2f}亿元"
        out.append((name, code, direction, cls, signal))
    return out


def fetch_etf_flows():
    """ETF 实时资金净流。腾讯自选股连接器（已接上）优先，东方财富 push2 兜底。

    返回 [(name, code, direction, cls, signal), ...]。direction=净申购/净赎回，
    cls 为徽章色（b-red 净流入 / b-green 净赎回）。所有源失败返回空列表。

    说明：westock 鉴权由平台托管，脚本无法获取静态 token（实测 401）；故已连接
    连接器时由 agent 经 MCP 取数写入 data/westock_etf_override.json，此处优先消费，
    无覆盖文件再走 push2 → 脚本自带 HTTP 客户端的兜底链。
    """
    ov = _etf_westock_override()
    if ov:
        print(f"[ETF] 使用 腾讯自选股连接器（已接上）数据：{len(ov)} 只")
        return ov
    print("[ETF] 抓取 ETF 资金净流（东方财富 push2 优先，腾讯自选股连接器兜底）...")
    res = _fetch_with_fallback([("东方财富push2", _etf_push2), ("腾讯自选股", _etf_westock)], label="ETF资金流")
    res = res or []
    print(f"  获取 {len(res)} 只 ETF 资金流")
    return res


def extract_keywords(text, keywords):
    """从文本中提取匹配的关键词"""
    found = []
    for kw in keywords:
        if kw in text:
            found.append(kw)
    return found


SECTOR_KEYWORDS = [
    "存储", "光模块", "CPO", "半导体", "芯片", "算力", "AI", "创新药", "CXO",
    "医药", "房地产", "消费", "机器人", "新能源", "有色", "稀土", "军工",
    "面板", "封装", "PCB", "铜箔", "电力",
]


def analyze_sentiment(weibo_data, quotes):
    """规则分析：提取舆情关键词 + 基于加权涨跌幅判断市场情绪强度。

    情绪状态不再只看上涨家数，而是综合『上涨占比』和『加权平均涨跌幅』：
    - bullish: 上涨占比 >= 60% 且加权涨幅 >= +0.5%
    - bearish: 上涨占比 <= 30% 且加权跌幅 <= -0.5%
    - neutral: 其余情况
    同时输出强度 score（-1 ~ +1）供报告层做渐变渲染。
    """
    print("[分析] 提取信号...")
    all_text = ""
    for user, posts in weibo_data.items():
        for p in posts:
            all_text += p["text"] + " "

    matched_sectors = extract_keywords(all_text, SECTOR_KEYWORDS)

    total = len(quotes) or 1
    values = list(quotes.values())
    up_count = sum(1 for q in values if q.get("chg_pct", 0) > 0)
    up_ratio = up_count / total
    avg_pct = sum(q.get("chg_pct", 0) for q in values) / total if values else 0.0

    if up_ratio >= 0.6 and avg_pct >= 0.5:
        market_state = "bullish"
    elif up_ratio <= 0.3 and avg_pct <= -0.5:
        market_state = "bearish"
    else:
        market_state = "neutral"

    # 强度分：上涨占比与加权涨幅共同映射到 [-1, 1]
    score = (up_ratio - 0.5) * 2 * 0.6 + max(-1, min(1, avg_pct / 2)) * 0.4
    score = max(-1.0, min(1.0, score))

    return {
        "matched_sectors": matched_sectors,
        "market_state": market_state,
        "market_score": round(score, 3),
        "avg_chg_pct": round(avg_pct, 3),
        "up_count": up_count,
        "total_indices": len(quotes),
    }


def _persist_to_db(config, snapshot, ta_data):
    """强制将所有采集数据写入 PostgreSQL。

    返回 (ok: bool, err: str|None)。任何源失败都抛异常由调用方捕获并提醒，
    保证『所有获取的数据必须入 postgresql』这一硬规则。
    """
    if not DB_AVAILABLE:
        return False, "psycopg2 未安装（DB_AVAILABLE=False），无法连接数据库"
    dc = config.get("database")
    if not dc:
        return False, "config.json 缺少 database 配置"
    try:
        db = StockAgentDB(host=dc.get("host", "localhost"), port=dc.get("port", 5432),
                          user=dc.get("user", "postgres"), password=dc.get("password", ""),
                          dbname=dc.get("dbname", "a_stock_agent"))
        # 先快速探活，连接不通立即报错提醒
        db.test_connection(timeout=6)
        # 确保数据表存在（自建库自愈合，含新增的 raw_snapshots/us_market_quotes/etf_flows）
        db.init_database()
        td = datetime.now().date()
        # 1) 完整原始快照（兜底：确保『所有数据』都在库里）
        db.save_snapshot(td, snapshot)
        # 2) 结构化表
        if snapshot.get("quotes"):
            db.save_index_quotes(td, snapshot["quotes"])
        if snapshot.get("us_market"):
            db.save_us_market(td, snapshot["us_market"])
        if snapshot.get("etf"):
            db.save_etf_flows(td, snapshot["etf"])
        if snapshot.get("weibo_data"):
            db.save_sentiment_batch(td, snapshot["weibo_data"])
        if ta_data:
            db.save_technical(td, ta_data)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _cleanup_old_snapshots(max_age_days=2, base_dir=None):
    """清理历史抓取快照 JSON，最多保留 max_age_days 天（含当日）。"""
    import re as _re
    base = Path(base_dir) if base_dir else (OUTPUT_DIR / "data" / "snapshots")
    if not base.exists():
        return
    pat = _re.compile(r"^fetched_(\d{8})(?:_\d{6})?\.json$")
    today = datetime.now()
    kept = deleted = 0
    for f in sorted(base.glob("fetched_*.json")):
        m = pat.match(f.name)
        if not m:
            continue
        try:
            fdate = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        age = (today - fdate).days
        if age > max_age_days:
            try:
                f.unlink()
                deleted += 1
                print(f"  [清理] 删除 {f.name}（{age} 天前，超出 {max_age_days} 天）")
            except OSError as e:
                print(f"  [清理] 无法删除 {f.name}: {e}")
        else:
            kept += 1
    print(f"[清理] 历史快照：保留 {kept} 个，删除 {deleted} 个（> {max_age_days} 天）")


def main():
    print("=" * 50)
    print("A股舆情操作指引 Agent")
    print("=" * 50)

    config = load_config()
    no_fetch = "--no-fetch" in sys.argv
    print(f"配置加载完成: {len(config.get('weibo_sources', []))} 个微博源, {len(config.get('watchlist_stocks', []))} 只自选股")
    if not no_fetch:
        net = "通(深度采集)" if check_network() else "不通(浅度采集)"
        print(f"[网络] 外网状态: {net} — 外网搜索优先 Google News，回退 Bing")

    # 回测模式：对历史报告中的个股判断做回测与交叉验证
    if "--backtest" in sys.argv:
        import backtest as bt
        bt.seed_judgments()
        data = bt.run_backtest()
        if data:
            html = bt.build_backtest_html(data)
            out_dir = Path(__file__).parent / "reports" / "回测"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"回测报告-{datetime.now().strftime('%Y%m%d')}.html"
            out.write_text(html, encoding="utf-8")
            agg = data["agg"]
            print(f"\n回测报告已生成: {out} ({out.stat().st_size // 1024} KB)")
            print(f"方向命中率: {agg['dir_rate']}% | 交叉验证一致率: {agg['agree_rate']}% | 待回测: {data['pending']}")
        else:
            print("[回测] 无可用判断数据，请先生成报告")
        return

    today = datetime.now().strftime("%Y%m%d")
    snap_dir = OUTPUT_DIR / "data" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    if no_fetch:
        cached = sorted(snap_dir.glob(f"fetched_{today}_*.json"))
        if not cached:
            print(f"[数据引擎] --no-fetch：未找到 {today} 的缓存快照，不生成空快照。")
            return
        try:
            snapshot = json.loads(cached[-1].read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[数据引擎] --no-fetch：缓存读取失败 {cached[-1].name}: {e}")
            return
        print(f"[数据引擎] --no-fetch：复用已有快照 {cached[-1].name}，不联网、不写新空快照、不重复入库。")
        return

    weibo_cookie = config.get("weibo_cookie", "")
    weibo_data = {}
    ta_data = {}
    if not no_fetch:
        for src in config.get("weibo_sources", []):
            posts = fetch_weibo(src["user_id"], src["name"], weibo_cookie)
            weibo_data[src["name"]] = posts
        for src in config.get("global_sources", []):
            posts = fetch_global_source(src["name"], src.get("signal_type", ""))
            weibo_data[f"[全球] {src['name']}"] = posts
        macro_data = fetch_macro_data(config.get("macro_indicators", {}))
        weibo_data.update(macro_data)
        event_data = fetch_event_factors(config.get("event_factors", {}))
        weibo_data.update(event_data)
        ta_data = fetch_technical_analysis(config.get("technical_analysis", {}))
        for sym, kline in ta_data.items():
            desc = f"收盘{kline['close']} MA5={kline['ma5']} MA10={kline['ma10']} MA20={kline['ma20']} 趋势:{kline['trend']} 近5日高{kline['high5']} 低{kline['low5']}"
            weibo_data[f"[技术] {sym}"] = [{"text": desc, "time": ""}]
        nt_data = fetch_national_team(config.get("national_team", {}))
        weibo_data.update(nt_data)
        jp_data = fetch_japan_carry(config.get("japan_carry", {}))
        weibo_data.update(jp_data)

    quotes = {}
    us_market, etf, us_yield = [], [], None
    if not no_fetch:
        quotes = fetch_index_quotes()
        us_market = fetch_us_market()
        etf = fetch_etf_flows()
        us_yield = fetch_us_yield()

    analysis = analyze_sentiment(weibo_data, quotes)

    # 简版模式已取消（2026-08-17）：除回测外一律跑全功能 9 章节报告。
    # a_stock_agent 作为数据引擎：采集 → 快照 → 入库；
    # 全功能报告由 build_report.py + WebSearch 实时拼装生成。
    ts = datetime.now().strftime("%H%M%S")
    snap_path = snap_dir / f"fetched_{today}_{ts}.json"
    snapshot = {
        "date": today,
        "market_state": analysis["market_state"],
        "matched_sectors": analysis["matched_sectors"],
        "weibo_data": weibo_data,
        "quotes": quotes,
        "us_market": us_market,
        "etf": etf,
        "us_yield": us_yield,
    }
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"\n[数据引擎] 采集完成，快照: {snap_path}")
    print(f"  市场状态: {analysis['market_state']} | 指数 {len(quotes)} 个 | 美股 {len(us_market)} 个 | ETF {len(etf)} 只 | 板块信号 {len(analysis['matched_sectors'])} 个 | 舆情条目 {len(weibo_data)} 类")
    print("  全功能 9 章节报告通过 build_report.py + 实时快照拼装生成（简版模式已取消）。")

    # ===== 强制入库：所有采集数据必须写入 PostgreSQL；连接不通则及时提醒 =====
    db_ok, db_err = _persist_to_db(config, snapshot, ta_data)
    if not db_ok:
        print("\n" + "=" * 56)
        print("⚠️  ⚠️  PostgreSQL 入库失败 / 数据库连接不通！  ⚠️  ⚠️")
        print(f"⚠️  原因: {db_err}")
        print("⚠️  请检查: 1) PostgreSQL 服务是否启动  2) config.json 中 database 配置")
        print("⚠️  (host/port/user/password/dbname)  3) 本机能否连通该数据库")
        print("⚠️  本次原始数据快照已保存到本地 JSON，但未入库，请尽快修复后重跑。")
        print("=" * 56 + "\n")
    else:
        print("[DB] ✅ 所有采集数据已强制写入 PostgreSQL")

    # ===== 历史快照清理：最多保留 2 天 =====
    _cleanup_old_snapshots(max_age_days=2)


if __name__ == "__main__":
    main()
