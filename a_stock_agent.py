#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股舆情操作指引 Agent
读取 config.json，抓取指定微博用户内容 + A股行情，生成HTML操作指引报告。

用法:
    python a_stock_agent.py            # 抓取并生成报告
    python a_stock_agent.py --no-fetch # 仅用缓存数据生成报告

依赖: 仅需Python标准库（urllib/json/re），无需pip安装
"""
import json
import urllib.request
import urllib.parse
import re
import os
import sys
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


def fetch_url(url, headers=None, timeout=10):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [fetch error] {url[:80]} -> {e}")
        return None


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
    """通过必应新闻搜索抓取全球人物最新动态（X发言/新闻转载）

    注意：必应新闻 RSS 对查询中的「最新/最新发言/最新动态」等词会返回空频道，
    故查询仅保留「{name} 中国 股市」这类干净短语，确保能拿到新闻条目。
    """
    print(f"[全球] 搜索 {name} 最新动态...")
    query = urllib.parse.quote(f"{name} 中国 股市")
    url = f"https://www.bing.com/news/search?q={query}&format=rss"
    text = fetch_url(url)
    if not text:
        return []
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    results = []
    for item in items[:5]:
        title_m = re.search(r"<title>(.*?)</title>", item, re.S)
        desc_m = re.search(r"<description>(.*?)</description>", item, re.S)
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip() if title_m else ""
        desc = re.sub(r"<!\[CDATA\[|\]\]>", "", desc_m.group(1)).strip() if desc_m else ""
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        content = f"{title}。{desc}" if desc else title
        if content and len(content) > 10:
            results.append({"text": content[:300], "time": ""})
    print(f"  获取 {len(results)} 条")
    return results


def fetch_macro_data(macro_config):
    """通过新闻搜索获取最新宏观经济数据动态（中美GDP/CPI/非农/利率等）"""
    print("[宏观] 搜索最新宏观数据...")
    results = {}
    search_pairs = [
        ("中国", "CPI"), ("中国", "GDP"), ("中国", "PMI"),
        ("美国", "CPI"), ("美国", "非农就业"), ("美国", "美联储利率"),
    ]
    for country, indicator in search_pairs:
        query = urllib.parse.quote(f"{country} {indicator} {datetime.now().strftime('%Y年%m月')}")
        url = f"https://www.bing.com/news/search?q={query}&format=rss"
        text = fetch_url(url, timeout=8)
        if not text:
            continue
        items = re.findall(r"<item>(.*?)</item>", text, re.S)
        if items:
            title_m = re.search(r"<title>(.*?)</title>", items[0], re.S)
            if title_m:
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip()
                results[f"[宏观] {country}{indicator}"] = [{"text": title[:300], "time": ""}]
    print(f"  获取 {len(results)} 条宏观数据")
    return results


def fetch_event_factors(factors_config):
    """通过新闻搜索获取全球事件因子动态（地缘战争/原油/自然灾害）"""
    print("[事件] 搜索全球事件因子...")
    results = {}
    search_pairs = [
        ("地缘", "中东局势"), ("地缘", "俄乌冲突"),
        ("原油", "WTI原油价格"), ("原油", "OPEC决议"),
        ("灾害", "台风"), ("灾害", "地震"),
    ]
    for category, keyword in search_pairs:
        query = urllib.parse.quote(f"{keyword} {datetime.now().strftime('%Y年%m月')}")
        url = f"https://www.bing.com/news/search?q={query}&format=rss"
        text = fetch_url(url, timeout=8)
        if not text:
            continue
        items = re.findall(r"<item>(.*?)</item>", text, re.S)
        if items:
            title_m = re.search(r"<title>(.*?)</title>", items[0], re.S)
            if title_m:
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip()
                results[f"[事件] {category}{keyword}"] = [{"text": title[:300], "time": ""}]
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
    """通过新闻搜索获取国家队资金流向动态（汇金/证金/国新/诚通/社保）"""
    if not nt_config.get("enabled"):
        return {}
    print("[国家队] 搜索国家队资金动态...")
    results = {}
    for keyword in ["汇金 ETF 增持", "国家队 ETF 净流入", "社保基金 加仓"]:
        query = urllib.parse.quote(f"{keyword} {datetime.now().strftime('%Y年%m月')}")
        url = f"https://www.bing.com/news/search?q={query}&format=rss"
        text = fetch_url(url, timeout=8)
        if not text:
            continue
        items = re.findall(r"<item>(.*?)</item>", text, re.S)
        if items:
            title_m = re.search(r"<title>(.*?)</title>", items[0], re.S)
            if title_m:
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip()
                results[f"[国家队] {keyword}"] = [{"text": title[:300], "time": ""}]
    print(f"  获取 {len(results)} 条国家队动态")
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
            open_p, prev_close = float(vals[1]), float(vals[2])
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


def generate_html(config, weibo_data, quotes, analysis):
    """生成HTML报告"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    state_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}[analysis["market_state"]]

    idx_rows = ""
    for name, q in quotes.items():
        cls = "up" if q["chg_pct"] > 0 else "down"
        sign = "+" if q["chg_pct"] > 0 else ""
        idx_rows += f'<tr><td>{name}</td><td>{q["price"]}</td><td class="{cls}">{sign}{q["chg_pct"]}%</td></tr>\n'

    weibo_sections = ""
    for user, posts in weibo_data.items():
        if not posts:
            weibo_sections += f'<div class="wb-user"><h3>{user}</h3><p class="muted">未获取到内容</p></div>'
            continue
        items = ""
        for p in posts[:5]:
            items += f'<div class="wb-item"><span class="wb-time">{p["time"]}</span><p>{p["text"]}</p></div>'
        weibo_sections += f'<div class="wb-user"><h3>{user}</h3>{items}</div>'

    sector_tags = "".join(f'<span class="tag">{s}</span>' for s in analysis["matched_sectors"])

    stocks = config.get("watchlist_stocks", [])
    stock_row = "".join(f"<td>{s}</td>" for s in stocks) if stocks else '<td colspan="4" class="muted">未配置自选股</td>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股舆情操作指引 · {today}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1d2129;line-height:1.7}}
.wrap{{max-width:900px;margin:0 auto;padding:20px}}
.card{{background:#fff;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
h1{{font-size:20px;color:#1a2b4a;margin-bottom:4px}}
.sub{{font-size:12px;color:#888;margin-bottom:16px}}
h2{{font-size:16px;color:#1a2b4a;margin-bottom:12px;padding-left:8px;border-left:3px solid #2c4a7c}}
.up{{color:#d63031;font-weight:600}}
.down{{color:#00a865;font-weight:600}}
.muted{{color:#999}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
th{{background:#f0f2f5;padding:8px;text-align:left;font-weight:600;color:#555}}
td{{padding:8px;border-bottom:1px solid #eef0f3}}
.tag{{display:inline-block;background:#e8f0fe;color:#1967d2;padding:2px 10px;border-radius:12px;font-size:12px;margin:2px}}
.wb-user{{margin-bottom:16px}}
.wb-user h3{{font-size:14px;color:#3c3489;margin-bottom:6px}}
.wb-item{{padding:6px 0;border-bottom:1px dashed #eee}}
.wb-time{{font-size:11px;color:#aaa}}
.wb-item p{{font-size:13px;color:#444;margin-top:2px}}
.state-badge{{display:inline-block;padding:4px 14px;border-radius:20px;font-size:13px;font-weight:600}}
.state-bullish{{background:#fde8e8;color:#d63031}}
.state-bearish{{background:#e8f8f0;color:#00a865}}
.state-neutral{{background:#fff3e0;color:#e67e22}}
.disclaimer{{font-size:11px;color:#999;line-height:1.6;padding:12px;background:#fafafa;border-radius:8px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>A股舆情操作指引报告</h1>
    <div class="sub">生成时间: {now}</div>
    <p>市场状态: <span class="state-badge state-{analysis["market_state"]}">{state_label}</span>
    ({analysis["up_count"]}/{analysis["total_indices"]} 指数上涨)</p>
    <p style="margin-top:8px;font-size:13px;">舆情提及板块: {sector_tags if sector_tags else '<span class="muted">无</span>'}</p>
  </div>

  <div class="card">
    <h2>主要指数</h2>
    <table>
      <thead><tr><th>指数</th><th>收盘</th><th>涨跌幅</th></tr></thead>
      <tbody>{idx_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>自选股</h2>
    <table><tbody><tr>{stock_row}</tr></tbody></table>
    <p class="muted" style="font-size:12px;margin-top:6px;">自选股操作指引需结合大V舆情与行情交叉分析，建议在WorkBuddy中运行完整分析流程。</p>
  </div>

  <div class="card">
    <h2>微博舆情（原始内容）</h2>
    {weibo_sections if weibo_sections else '<p class="muted">未获取到微博内容</p>'}
  </div>

  <div class="card">
    <h2>说明</h2>
    <p style="font-size:13px;color:#666;">
      本报告由 a_stock_agent.py 自动生成。微博数据通过 m.weibo.cn API 抓取，行情数据通过新浪财经API获取。
      完整的交叉验证分析和操作指引（含大V观点提炼、共振信号识别、个股操作建议）请在WorkBuddy中运行自动化任务获取。
    </p>
  </div>

  <div class="disclaimer">
    免责声明：以上内容基于公开数据自动生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
    任何投资决策应结合个人风险承受能力独立判断，必要时咨询持牌专业机构。
  </div>
</div>
</body>
</html>"""
    return html


def main():
    print("=" * 50)
    print("A股舆情操作指引 Agent")
    print("=" * 50)

    config = load_config()
    print(f"配置加载完成: {len(config.get('weibo_sources', []))} 个微博源, {len(config.get('watchlist_stocks', []))} 只自选股")

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

    quotes = {}
    if not no_fetch:
        quotes = fetch_index_quotes()

    analysis = analyze_sentiment(weibo_data, quotes)

    html = generate_html(config, weibo_data, quotes, analysis)

    today = datetime.now().strftime("%Y%m%d")
    draft_dir = OUTPUT_DIR / "reports" / "简版"
    draft_dir.mkdir(parents=True, exist_ok=True)
    output_path = draft_dir / f"A股舆情操作指引-{today}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n报告已生成: {output_path}")
    print(f"文件大小: {output_path.stat().st_size // 1024} KB")

    if DB_AVAILABLE and config.get("database"):
        try:
            dc = config["database"]
            db = StockAgentDB(host=dc.get("host","localhost"), port=dc.get("port",5432),
                              user=dc.get("user","postgres"), password=dc.get("password",""),
                              dbname=dc.get("dbname","a_stock_agent"))
            td = datetime.now().date()
            db.save_report(td, analysis["market_state"], "A股舆情操作指引", html)
            if quotes:
                db.save_index_quotes(td, quotes)
            if weibo_data:
                db.save_sentiment_batch(td, weibo_data)
            print("[DB] 数据入库完成")
        except Exception as e:
            print(f"[DB] 入库失败: {e}")


if __name__ == "__main__":
    main()
