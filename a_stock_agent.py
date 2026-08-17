#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股舆情操作指引 Agent（数据引擎）
读取 config.json，抓取微博舆情 + 全球宏观 + 日本传导链 + A股行情，写入 JSON 快照与可选 PostgreSQL。
全功能 9 章节报告由 build_report_20260816.py + WebSearch 实时拼装生成（简版模式已取消）。

用法:
    python a_stock_agent.py            # 采集数据 → 快照 → 入库（全功能报告另由 build_report 生成）
    python a_stock_agent.py --no-fetch # 仅用缓存数据生成快照
    python a_stock_agent.py --backtest # 回测模式（独立，生成回测报告）

依赖: 仅需Python标准库（urllib/json/re），无需pip安装
"""
import json
import urllib.request
import urllib.parse
import re
import sys
import socket
import html
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


def fetch_url(url, headers=None, timeout=10, quiet=False):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        if not quiet:
            print(f"  [fetch error] {url[:80]} -> {e}")
        return None


# ----------------------------------------------------------------------------
# 外网连通性探测 + 搜索引擎（Google 优先，Bing 兜庫）
# ----------------------------------------------------------------------------
_NETWORK_OK = None  # 网络探测结果缓存，避免重复建连


def check_network(host="news.google.com", port=443, timeout=4):
    """快速探测外网是否连通（连接 Google News 443）。结果缓存。

    返回 True 表示外网可达，采集路径进入「深度」模式（搜集更多关键信息）；
    返回 False 则退守「浅度」模式，仅做必要查询。
    """
    global _NETWORK_OK
    if _NETWORK_OK is not None:
        return _NETWORK_OK
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)  # 仅作用于本 socket，避免污染全局默认超时
        s.connect((host, port))
        s.close()
        _NETWORK_OK = True
    except Exception:
        _NETWORK_OK = False
    return _NETWORK_OK


def collect_depth():
    """返回采集深度：网络通 -> 'deep'（搜集更多关键信息），不通 -> 'shallow'。"""
    return "deep" if check_network() else "shallow"


# Google News RSS 优先；必应 News RSS 兜底（对「最新」类词易返回空频道）
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
BING_NEWS_RSS = "https://www.bing.com/news/search?q={q}&format=rss"


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
    """外网新闻搜索：优先 Google News RSS，失败/空结果回退 Bing News RSS。

    - query 建议为干净短语；Google 通道对「最新」类词无碍，Bing 通道可能返回空频道。
    - 返回 [{"text":..., "time":""}, ...]，统一结构供下游消费。
    - Google 尝试静默（quiet=True）：它在受限网络常失败，且有 Bing 兜底，
      避免每次都刷 [fetch error] 噪音；Bing 失败仍正常打印以便排查。
    """
    q = urllib.parse.quote(query)
    text = fetch_url(GOOGLE_NEWS_RSS.format(q=q), quiet=True)
    if text:
        res = _parse_rss(text, max_items)
        if res:
            return res
    # Google 不可达或空 -> 回退必应
    text = fetch_url(BING_NEWS_RSS.format(q=q))
    if text:
        return _parse_rss(text, max_items)
    return []


def fetch_weibo(user_id, name, cookie=""):
    """抓取微博用户最新内容（m.weibo.cn API），需在config.json配置weibo_cookie"""
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
        for card in cards:
            mblog = card.get("mblog", {})
            if not mblog:
                continue
            raw_text = mblog.get("text", "")
            clean = re.sub(r"<[^>]+>", "", raw_text).strip()
            created = mblog.get("created_at", "")
            if clean and len(clean) > 10:
                posts.append({"text": clean[:500], "time": created})
        print(f"  获取 {len(posts)} 条微博")
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


def fetch_index_quotes():
    """抓取主要指数行情（新浪API）"""
    print("[行情] 抓取指数数据...")
    codes = "sh000001,sz399001,sz399006,sh000688,sh000300"
    url = f"https://hq.sinajs.cn/list={codes}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    text = fetch_url(url, headers)
    if not text:
        return {}
    result = {}
    name_map = {
        "sh000001": "上证指数", "sz399001": "深证成指",
        "sz399006": "创业板指", "sh000688": "科创50", "sh000300": "沪深300",
    }
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


def fetch_us_market():
    """隔夜美股主要指数与科技/存储龙头（新浪美股实时行情）。

    返回 [(name, pct, price, signal), ...]，pct 为涨跌幅(float)。
    网络/解析失败时返回空列表（由报告层渲染为「实时数据缺失」占位，绝不写死假数）。
    """
    print("[美股] 抓取隔夜美股行情（新浪美股）...")
    # 代码 -> 中文名（稳定的代码映射，非行情数据）
    symbols = {
        "gb_dji": "道琼斯", "gb_ixic": "纳斯达克", "gb_inx": "标普500",
        "gb_sox": "费城半导体", "gb_nvda": "英伟达", "gb_tsla": "特斯拉",
        "gb_mu": "美光科技", "gb_stx": "希捷科技", "gb_wdc": "西部数据",
        "gb_sndk": "闪迪", "gb_amat": "应用材料", "gb_avgo": "博通",
        "gb_lite": "Lumentum", "gb_glw": "康宁",
    }
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    text = fetch_url(url, headers={"Referer": "https://finance.sina.com.cn"})
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
    print(f"  获取 {len(results)} 个美股标的")
    return results


def fetch_etf_flows():
    """ETF 实时资金净流（东方财富 push2，单位：亿元）。

    返回 [(name, code, direction, cls, signal), ...]。direction=净申购/净赎回，
    cls 为徽章色（b-red 净流入 / b-green 净赎回）。失败/限流返回空列表。
    """
    print("[ETF] 抓取 ETF 资金净流（东方财富）...")
    etfs = [
        ("沪深300ETF", "510300", "1"), ("芯片ETF", "159995", "0"),
        ("半导体设备ETF国泰", "159516", "0"), ("科创50ETF", "588280", "1"),
    ]
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
    print(f"  获取 {len(results)} 只 ETF 资金流")
    return results


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
    """简单规则分析：提取舆情关键词 + 行情涨跌判断"""
    print("[分析] 提取信号...")
    all_text = ""
    for user, posts in weibo_data.items():
        for p in posts:
            all_text += p["text"] + " "

    matched_sectors = extract_keywords(all_text, SECTOR_KEYWORDS)

    market_state = "neutral"
    up_count = sum(1 for q in quotes.values() if q["chg_pct"] > 0)
    if up_count >= 4:
        market_state = "bullish"
    elif up_count <= 1:
        market_state = "bearish"

    return {
        "matched_sectors": matched_sectors,
        "market_state": market_state,
        "up_count": up_count,
        "total_indices": len(quotes),
    }


def main():
    print("=" * 50)
    print("A股舆情操作指引 Agent")
    print("=" * 50)

    config = load_config()
    print(f"配置加载完成: {len(config.get('weibo_sources', []))} 个微博源, {len(config.get('watchlist_stocks', []))} 只自选股")
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

    no_fetch = "--no-fetch" in sys.argv

    weibo_cookie = config.get("weibo_cookie", "")
    weibo_data = {}
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
    us_market, etf = [], []
    if not no_fetch:
        quotes = fetch_index_quotes()
        us_market = fetch_us_market()
        etf = fetch_etf_flows()

    analysis = analyze_sentiment(weibo_data, quotes)

    # 简版模式已取消（2026-08-17）：除回测外一律跑全功能 9 章节报告。
    # a_stock_agent 作为数据引擎：采集 → 快照 → 入库；
    # 全功能报告由 build_report_20260816.py + WebSearch 实时拼装生成。
    today = datetime.now().strftime("%Y%m%d")
    snap_dir = OUTPUT_DIR / "data"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"fetched_{today}.json"
    snapshot = {
        "date": today,
        "market_state": analysis["market_state"],
        "matched_sectors": analysis["matched_sectors"],
        "weibo_data": weibo_data,
        "quotes": quotes,
        "us_market": us_market,
        "etf": etf,
    }
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"\n[数据引擎] 采集完成，快照: {snap_path}")
    print(f"  市场状态: {analysis['market_state']} | 指数 {len(quotes)} 个 | 美股 {len(us_market)} 个 | ETF {len(etf)} 只 | 板块信号 {len(analysis['matched_sectors'])} 个 | 舆情条目 {len(weibo_data)} 类")
    print("  全功能 9 章节报告通过 build_report_20260816.py + 实时快照拼装生成（简版模式已取消）。")

    if DB_AVAILABLE and config.get("database"):
        try:
            dc = config["database"]
            db = StockAgentDB(host=dc.get("host","localhost"), port=dc.get("port",5432),
                              user=dc.get("user","postgres"), password=dc.get("password",""),
                              dbname=dc.get("dbname","a_stock_agent"))
            td = datetime.now().date()
            if quotes:
                db.save_index_quotes(td, quotes)
            if weibo_data:
                db.save_sentiment_batch(td, weibo_data)
            print("[DB] 行情+舆情入库完成（全功能报告 HTML 由 build_report 生成时另存）")
        except Exception as e:
            print(f"[DB] 入库失败: {e}")


if __name__ == "__main__":
    main()
