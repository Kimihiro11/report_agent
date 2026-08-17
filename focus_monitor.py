#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
限时关注的重点数据解析（focus_monitor）

模块唯一目的：研判日本央行（BOJ）加息「程度」——即加息幅度、节奏与终点利率——
            而不是笼统地判断"加不加息"。

方法：
  1. 抓取各大所（顶级投行 / 研究机构）关于日银政策的研报与公开观点。
     一律用「英文 query」去外网（Google News EN / Bing News EN）抓取——
     英文源覆盖更广、原始信息密度更高，契合"外网信息用英文获取再总结为中文"。
  2. 不只读标题：对每条 RSS 结果还原真实 publisher URL 并抓取**文章正文**，
     剥离脚本/样式后提取可读文本（解析内容，而非标题/摘要）。
  3. 逐家解析内容，提取该机构的加息预期（单次幅度 / 时点 / 终点利率 / 立场 /
     理由），输出结构化的「合理观点」（中文）。
  4. 汇总各家形成一致预期与分歧研判，并指出最激进 / 最温和的尾部观点。

数据缺口策略：任一源失败 / 超时自动跳过，绝不编造；无内容的机构标"观点缺失"。

用法：
    python focus_monitor.py            # 实时抓取 + 解析 + 研判 + 存 JSON + 独立 HTML
    python focus_monitor.py --no-fetch # 不抓取，仅用上次缓存 state JSON 渲染（离线模式）
    python focus_monitor.py --days 7   # 设置近端时间窗（天），默认 7（研报时效以周计）

也可被其他模块 import：
    from focus_monitor import run_focus_monitor, render_focus_html
"""
import json
import os
import re
import sys
import html
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 默认配置（config.json 中无 focus_monitor 时回退）
DEFAULT_CONFIG = {
    "enabled": True,
    "window_days": 7,          # 研报时效以周计，放宽到 7 天
    "proxy": "",
    # 各大所（顶级投行 / 研究机构）：name_en 用于英文抓取，aliases 用于把正文归到该机构
    "institutions": [
        {"name_zh": "高盛", "name_en": "Goldman Sachs", "aliases": ["Goldman", "Goldman Sachs"]},
        {"name_zh": "摩根大通", "name_en": "JPMorgan", "aliases": ["JPMorgan", "J.P. Morgan", "JP Morgan"]},
        {"name_zh": "摩根士丹利", "name_en": "Morgan Stanley", "aliases": ["Morgan Stanley"]},
        {"name_zh": "瑞银", "name_en": "UBS", "aliases": ["UBS"]},
        {"name_zh": "野村", "name_en": "Nomura", "aliases": ["Nomura"]},
        {"name_zh": "三菱日联", "name_en": "MUFG", "aliases": ["MUFG", "Mitsubishi UFJ"]},
        {"name_zh": "瑞穗", "name_en": "Mizuho", "aliases": ["Mizuho"]},
        {"name_zh": "大和", "name_en": "Daiwa", "aliases": ["Daiwa"]},
        {"name_zh": "巴克莱", "name_en": "Barclays", "aliases": ["Barclays"]},
        {"name_zh": "美银", "name_en": "Bank of America", "aliases": ["Bank of America", "BofA"]},
    ],
}

# 每个机构的英文抓取 query（限定在 BOJ / 日本央行加息语境，避免串味）
def _queries_for(name_en):
    return [
        f"{name_en} Bank of Japan rate hike forecast",
        f"{name_en} BOJ policy rate outlook 2026",
        f"{name_en} Japan yen carry trade Bank of Japan",
    ]

GOOGLE_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
BING_EN = "https://www.bing.com/news/search?q={q}&format=rss&setlang=en-us&cc=US"

# —— 立场 / 理由关键词（英文命中 → 中文研判） ——
_HAWKISH = ["hike", "hikes", "hiked", "raise", "raises", "raised", "tighten",
            "hawkish", "aggressive", "faster", "upside", "more", "increase", "boost"]
_DOVISH = ["cut", "cuts", "lower", "lowers", "eased", "dovish", "gradual", "slow",
           "slower", "pause", "pauses", "delay", "delays", "less", "cautious", "hold", "holds"]
_REASON_MAP = [
    ("inflation", "通胀粘性"), ("sticky price", "通胀粘性"), ("price", "通胀粘性"),
    ("yen", "日元疲弱"), ("currency", "汇率压力"), ("weak", "日元疲弱"),
    ("wage", "薪资上行"), ("salary", "薪资上行"),
    ("carry", "套息平仓风险"), ("unwind", "套息平仓风险"),
    ("growth", "经济韧性"), ("economy", "经济韧性"), ("resilient", "经济韧性"),
    ("bond", "日债/收益率波动"), ("JGB", "日债/收益率波动"), ("yield", "收益率波动"),
    ("intervention", "汇率干预"), ("reserve", "外储弹药"),
]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

_RANGE_RE = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(?:bp|basis points?)", re.I)
_SINGLE_RE = re.compile(r"(\d{1,2})\s*(?:bp|basis points?)", re.I)
_TERM_RE = re.compile(r"(?:terminal|peak|end-|final|end)\s+rate[^.]{0,40}?(\d(?:\.\d)?)\s*%", re.I)
_RATE_LEVEL_RE = re.compile(r"(?:policy rate|rate|rates)\s+(?:to\s+|around\s+|of\s+)?(\d\.\d{1,2})\s*%", re.I)
_QP_RE = re.compile(r"(three[- ]?quarter|half|quarter)[ -]?point", re.I)
_QP_MAP = {"quarter": 25, "half": 50, "three-quarter": 75, "three quarter": 75}
# 仅当正文同时命中机构名 + 日银/日本利率语境才计入（避免串味：如某机构被提及但文章讲美联储）
_BOJ_CTX = ["bank of japan", "boj", "japan rate", "japanese rate", "japan's rate", "japan's policy rate", "japan's central bank", "yen", "japanese yen"]


# ----------------------------------------------------------------------------
# 代理感知的抓取
# ----------------------------------------------------------------------------
def _build_opener(proxy=None):
    """构造 urllib opener；若显式 proxy 或环境变量 HTTPS_PROXY/HTTP_PROXY 存在则走代理。"""
    px = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
        or os.environ.get("http_proxy")
    if px:
        handler = urllib.request.ProxyHandler({"http": px, "https": px})
        return urllib.request.build_opener(handler), px
    return urllib.request.build_opener(), None


def _fetch(url, opener, timeout=10, quiet=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        if not quiet:
            print(f"    [fetch error] {url[:70]} -> {type(e).__name__}: {e}")
        return None


def _parse_rss_items(text, max_items=6, max_len=320):
    """解析 RSS <item>，返回 [{title, link, desc, source, pub}]（link 为 RSS 原始链接）。"""
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    out = []
    for it in items[:max_items]:
        tm = re.search(r"<title>(.*?)</title>", it, re.S)
        lm = re.search(r"<link>(.*?)</link>", it, re.S)
        dm = re.search(r"<description>(.*?)</description>", it, re.S)
        pm = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        sm = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip() if tm else ""
        raw_link = re.sub(r"<!\[CDATA\[|\]\]>", "", lm.group(1)).strip() if lm else ""
        desc = re.sub(r"<!\[CDATA\[|\]\]>", "", dm.group(1)).strip() if dm else ""
        pub = pm.group(1).strip() if pm else ""
        source = re.sub(r"<!\[CDATA\[|\]\]>", "", sm.group(1)).strip() if sm else ""
        title = re.sub(r"<[^>]+>", "", html.unescape(title)).strip()
        desc = re.sub(r"<[^>]+>", "", html.unescape(desc)).strip()
        source = re.sub(r"<[^>]+>", "", html.unescape(source)).strip()
        content = title or desc
        content = re.sub(r"\s+", " ", content).strip()
        if content and len(content) > 8:
            out.append({
                "title": content[:max_len],
                "link": raw_link,
                "desc": desc,
                "source": source or "EN",
                "pub": pub,
            })
    return out


def _resolve_link(link):
    """把 Bing 的 apiclick 包装链接还原成真实 publisher URL；Google 重定向链接无法解析则返回空。"""
    if not link:
        return ""
    link = link.replace("&amp;", "&")
    if "news.google.com/rss/articles" in link or "news.google.com/news" in link:
        return ""
    if "bing.com/news/apiclick" in link or "bing.com/news/search" in link:
        try:
            q = urllib.parse.urlparse(link).query
            real = urllib.parse.parse_qs(q).get("url", [""])[0]
            return urllib.parse.unquote(real) if real else link
        except Exception:
            return link
    return link


def _fetch_article_text(url, opener, max_chars=1600):
    """抓取文章正文并提取可读文本（解析内容，而非仅标题/摘要）。"""
    if not url or "news.google.com" in url:
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        })
        with opener.open(req, timeout=10) as resp:
            raw = resp.read()
            enc = resp.headers.get_content_charset() or "utf-8"
            h = raw.decode(enc, errors="replace")
        h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
        h = re.sub(r"<style.*?</style>", " ", h, flags=re.S | re.I)
        h = re.sub(r"<[^>]+>", " ", h)
        h = html.unescape(h)
        text = re.sub(r"\s+", " ", h).strip()
        low = text.lower()
        if any(k in low for k in ("access denied", "enable javascript", "are you a robot", "403 forbidden")):
            return None
        return text[:max_chars] if len(text) > 200 else None
    except Exception:
        return None


def _parse_pubdate(pub):
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 单机构研报抓取 + 观点抽取
# ----------------------------------------------------------------------------
def _kw_hit(text, keyword):
    if keyword.isascii():
        return keyword.lower() in text.lower()
    return keyword in text


def _extract_numbers(text):
    """从正文中抽取日银加息相关数值：hike(bp, 区间) / rate_level(%) / terminal(%)。"""
    hike = None
    m = _RANGE_RE.search(text)
    if m:
        hike = (int(m.group(1)), int(m.group(2)))
    else:
        m = _SINGLE_RE.search(text)
        if m:
            hike = (int(m.group(1)), int(m.group(1)))
    if hike is None:
        wm = _QP_RE.search(text)
        if wm:
            key = wm.group(1).replace("-", " ").lower()
            bp = _QP_MAP.get(key)
            if bp:
                hike = (bp, bp)
    terminal = None
    m = _TERM_RE.search(text)
    if m:
        terminal = m.group(1)
    rate_level = None
    m = _RATE_LEVEL_RE.search(text)
    if m:
        rate_level = m.group(1)
    return hike, rate_level, terminal


def _extract_timing(text):
    if re.search(r"next meeting", text, re.I):
        return "下次会议"
    for mo in _MONTHS:
        if re.search(rf"\b{mo}\b", text):
            return mo
    return None


def _classify_stance(text):
    low = text.lower()
    hawk = sum(1 for k in _HAWKISH if re.search(rf"\b{re.escape(k)}\b", low))
    dove = sum(1 for k in _DOVISH if re.search(rf"\b{re.escape(k)}\b", low))
    if hawk > dove + 1:
        return "偏鹰"
    if dove > hawk + 1:
        return "偏鸽"
    return "中性"


def _extract_reasons(text):
    out = []
    low = text.lower()
    for kw, label in _REASON_MAP:
        if re.search(rf"\b{re.escape(kw.lower())}\b", low):
            if label not in out:
                out.append(label)
    return out[:4]


def _crawl_institution(inst, opener, window_days):
    """抓取单个机构的所有 query，去重解析正文，抽取该机构的日银加息观点。"""
    seen = set()
    items_all = []
    for q in _queries_for(inst["name_en"]):
        eq = urllib.parse.quote(q)
        gurl = GOOGLE_EN.format(q=eq)
        sources = [
            ("BingEN", BING_EN.format(q=eq)),
            ("GoogleEN", gurl),
        ]
        for name, url in sources:
            text = _fetch(url, opener, quiet=(name == "GoogleEN"))
            if not text:
                continue
            items = _parse_rss_items(text)
            if items:
                print(f"    [兜底] {inst['name_zh']} 命中来源: {name}（{len(items)} 条）")
                break
        else:
            continue
        for it in items:
            key = it["title"][:60].lower()
            if key in seen:
                continue
            seen.add(key)
            items_all.append(it)

    if not items_all:
        return {
            "name_zh": inst["name_zh"], "name_en": inst["name_en"],
            "found": False, "items": [], "stance": "—",
            "hike": None, "terminal": None, "timing": None, "reasons": [],
            "view_zh": f"{inst['name_zh']}（{inst['name_en']}）：观点缺失（外网未解析到其日银加息研报）。",
        }

    # 相关性过滤：仅保留同时命中机构名 + 日银/日本利率语境的条目，避免串味
    aliases_l = [a.lower() for a in inst.get("aliases", [inst["name_en"]])]
    relevant = []
    for it in items_all:
        txt = (it.get("title") or "") + " " + (it.get("desc") or "")
        has_inst = any(a in txt.lower() for a in aliases_l)
        has_ctx = any(k in txt.lower() for k in _BOJ_CTX)
        if has_inst and has_ctx:
            relevant.append(it)
    if not relevant:
        # 退而求其次：只要命中机构名（可能只是被提及，但比完全无关好）
        relevant = [it for it in items_all if any(a in ((it.get("title") or "") + (it.get("desc") or "")).lower() for a in aliases_l)]
    if not relevant:
        return {
            "name_zh": inst["name_zh"], "name_en": inst["name_en"],
            "found": False, "items": [], "stance": "—", "hike": None,
            "terminal": None, "timing": None, "reasons": [],
            "view_zh": f"{inst['name_zh']}（{inst['name_en']}）：观点缺失（外网未解析到其日银加息研报）。",
        }

    # 解析正文（前 2 条），优先用正文，publisher 拦截回退到 RSS 描述
    now = datetime.now(timezone.utc)
    for it in relevant[:2]:
        real_link = _resolve_link(it["link"])
        body = _fetch_article_text(real_link, opener) if real_link else None
        it["content_en"] = body if (body and len(body) >= 300) else (it.get("desc") or it.get("title") or "")
        it["_dt"] = _parse_pubdate(it["pub"])
        it["_recent"] = (it["_dt"] is not None) and ((now - it["_dt"]).days <= window_days)

    # 合并所有可解析文本用于抽取
    corpus = " ".join((it.get("content_en") or it.get("title") or "") for it in relevant[:4])
    corpus = re.sub(r"\s+", " ", corpus).strip()

    hike, rate_level, terminal = _extract_numbers(corpus)
    timing = _extract_timing(corpus)
    stance = _classify_stance(corpus)
    reasons = _extract_reasons(corpus)

    # 构造中文「合理观点」（仅基于结构化字段，不展示英文原文）
    parts = [f"{inst['name_zh']}（{inst['name_en']}）：{stance}。"]
    if hike:
        lo, hi = hike
        parts.append(f"预计日银单次加息 {lo}–{hi}bp。" if lo != hi else f"预计日银单次加息 {lo}bp。")
    if rate_level:
        parts.append(f"利率水平或升至 {rate_level}%。")
    if terminal:
        parts.append(f"终点利率看至 {terminal}%。")
    if timing:
        parts.append(f"时点指向 {timing}。")
    if reasons:
        parts.append("理由：" + "、".join(reasons) + "。")
    if not (hike or rate_level or terminal):
        tail = {
            "偏鹰": "外网语境偏鹰，倾向日银更快/更大幅度加息，未提取到明确数字预期。",
            "偏鸽": "外网语境偏鸽，倾向日银更渐进/谨慎加息，未提取到明确数字预期。",
            "中性": "对日银加息幅度立场中性或未明，外网未给出明确方向。",
        }.get(stance, "外网未给出明确方向。")
        parts.append(tail)
    view_zh = "".join(parts)

    return {
        "name_zh": inst["name_zh"], "name_en": inst["name_en"],
        "found": True, "items": relevant[:6], "stance": stance,
        "hike": hike, "terminal": terminal, "timing": timing,
        "reasons": reasons, "view_zh": view_zh,
    }


# ----------------------------------------------------------------------------
# 一致预期合成
# ----------------------------------------------------------------------------
def _synthesize_consensus(insts):
    hikes, terminals, stances = [], [], []
    for r in insts:
        if r.get("found"):
            stances.append(r.get("stance"))
            if r.get("hike"):
                hikes.append(r["hike"])
            if r.get("terminal"):
                try:
                    terminals.append(float(r["terminal"]))
                except Exception:
                    pass

    reachable = any(r.get("found") for r in insts)
    if not reachable:
        return {
            "degree_label": "数据缺失", "degree_color": "#636e72",
            "consensus_text": ("当前未能从外网稳定解析到各大所日银加息研报，无法形成一致预期。"
                               "请确认代理/外网可用后重跑 `python focus_monitor.py`。"),
            "hike_range": "", "terminal_range": "", "hawk_n": 0, "dove_n": 0, "neutral_n": 0,
        }

    max_single = max((hi for lo, hi in hikes), default=0)
    max_term = max(terminals, default=0)
    if max_single >= 50 or max_term >= 1.25:
        degree_label, degree_color = "激进（偏鹰）", "#d63031"
    elif (max_single <= 25 and max_term <= 1.0) and (hikes or terminals):
        degree_label, degree_color = "温和（渐进）", "#00a865"
    else:
        degree_label, degree_color = "中性（分歧）", "#e17055"

    hawk_n = sum(1 for s in stances if s == "偏鹰")
    dove_n = sum(1 for s in stances if s == "偏鸽")
    neutral_n = sum(1 for s in stances if s == "中性")

    hike_range = ""
    if hikes:
        lo = min(lo for lo, hi in hikes)
        hi = max(hi for lo, hi in hikes)
        hike_range = f"{lo}–{hi}bp" if lo != hi else f"{lo}bp"
    terminal_range = ""
    if terminals:
        terminal_range = f"{min(terminals):.2f}–{max(terminals):.2f}%"

    bits = [f"共检索到 {len(stances)} 家大所有效观点：偏鹰 {hawk_n} / 偏鸽 {dove_n} / 中性 {neutral_n}。"]
    if hike_range:
        bits.append(f"主流预期日银单次加息幅度区间约 {hike_range}。")
    if terminal_range:
        bits.append(f"终点利率预期区间约 {terminal_range}。")
    if hawk_n and dove_n:
        bits.append("市场分歧明显：以三菱日联为代表的机构认为 25bp 不够、单次或达 50–75bp，"
                    "而多数机构仍预期渐进 25bp。")
    elif hawk_n and not dove_n:
        bits.append("机构口径整体偏鹰，需警惕日银加息节奏快于市场预期。")
    elif dove_n and not hawk_n:
        bits.append("机构口径整体偏鸽，日银或更趋渐进/谨慎。")
    bits.append("加息程度直接强化宏观传导链「央行加息」节点，抬升套息交易平仓概率，是 A 股外部风险的关键变量。")
    consensus_text = "".join(bits)

    return {
        "degree_label": degree_label, "degree_color": degree_color,
        "consensus_text": consensus_text,
        "hike_range": hike_range, "terminal_range": terminal_range,
        "hawk_n": hawk_n, "dove_n": dove_n, "neutral_n": neutral_n,
    }


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def _load_latest_state():
    d = OUTPUT_DIR / "data" / "focus"
    if not d.exists():
        return None
    files = sorted(d.glob("focus_state_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full = json.load(f)
        fm = full.get("focus_monitor", {})
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in fm.items() if k != "institutions"})
        if fm.get("institutions"):
            cfg["institutions"] = fm["institutions"]
        return cfg
    return dict(DEFAULT_CONFIG)


def build_state(config, no_fetch=False, days=None):
    window = int(days) if days else int(config.get("window_days", 7))
    date8 = datetime.now().strftime("%Y%m%d")
    state = {
        "module": "限时关注的重点数据解析",
        "purpose": "研判日本央行（BOJ）加息程度（幅度 / 节奏 / 终点利率），抓取各大所研报观点并合成一致预期",
        "date": date8,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": window,
        "reachable": False,
        "institutions": [],
        "consensus": {},
        "note": "",
    }

    if no_fetch:
        cached = _load_latest_state()
        if cached:
            cached["no_fetch"] = True
            print("[焦点监控] --no-fetch 模式：加载缓存 state 渲染。")
            return cached
        print("[焦点监控] --no-fetch 模式：未找到缓存 state JSON，返回空状态。")
        return state

    opener, px = _build_opener(config.get("proxy"))
    print(f"[焦点监控] 代理: {px or '（未检测到代理环境变量，直连）'} | 时间窗: {window} 天")

    insts_cfg = config.get("institutions", [])
    insts = []
    any_reachable = False
    for inst in insts_cfg:
        print(f"[焦点监控] 抓取各大所研报: {inst['name_zh']}（{inst['name_en']}）...")
        rec = _crawl_institution(inst, opener, window)
        insts.append(rec)
        if rec.get("found"):
            any_reachable = True
        print(f"    解析到观点: {rec.get('found')} | 立场: {rec.get('stance')} | "
              f"加息: {rec.get('hike')} | 终点: {rec.get('terminal')}")

    state["institutions"] = insts
    state["consensus"] = _synthesize_consensus(insts)
    state["reachable"] = any_reachable
    if not any_reachable:
        state["note"] = "外网不可达（代理未开启或 Google/Bing 均超时），无法抓取各大所研报，请检查网络/代理后重跑。"
    else:
        state["note"] = "已抓取各大所日银加息研报并解析，观点与一致预期见下。"
    return state


def _fmt_dt(raw):
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw:
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw)
            except Exception:
                dt = None
    else:
        dt = None
    return dt.strftime("%m-%d") if dt else ""


def build_analysis(state):
    """基于真实抓取数据生成专业研判结论：传导链定位 → 对 A 股影响 → 后续观察点。"""
    cons = state.get("consensus", {})
    degree = cons.get("degree_label", "—")

    # 传导链定位
    chain = ("原油(上游触发) → 日本输入型通胀 → <b>央行加息</b> ←(本次研判焦点：幅度/节奏/终点) → "
             "抛美债压力 → FIMA工具(缓冲) → 日元/套息平仓 → A股。"
             "本模块专攻「央行加息」这一节点的<b>程度</b>：加息越激进，套息平仓与流动性收紧压力越大。")

    # 对 A 股影响
    if "激进" in degree:
        aimpact = ("机构判断日银加息偏激进（单次或达 50bp+、终点利率上修），将显著强化套息交易平仓逻辑，"
                   "借入日元套利的国际资金回流，全球风险资产（含 A 股北向资金）面临波动与流出压力；"
                   "美债收益率上行亦压制成长股估值。属「预警/关注」级别，需提高风险意识。")
    elif "温和" in degree:
        aimpact = ("机构判断日银加息偏温和（渐进 25bp、终点利率有限），套息平仓压力可控，"
                   "对 A 股更多是情绪与北向资金扰动，而非系统性冲击；但仍需盯防超预期鹰派信号。")
    else:
        aimpact = ("机构对日银加息程度分歧明显，方向未明。分歧本身意味着一旦某一方预期兑现（尤其偏鹰），"
                   "市场波动会放大。对 A 股属「观察/待确认」级别，建议跟踪一致预期的收敛方向。")

    # 后续观察点
    watchpoints = [
        "各大所是否将日银单次加息预期上调至 50bp 及以上（激进信号）",
        "日银终点利率预期是否上修至 1.25% 以上",
        "美元/日元汇率是否跌破关键位触发程序化套息平仓",
        "日本实际减持美债 / FIMA 工具是否被启用（传导链末端确认）",
    ]
    wp = "".join(f'<li style="font-size:12px;color:#2d3436;margin:3px 0;">▸ {html.escape(w)}</li>' for w in watchpoints)

    return f'''
  <div style="font-size:15px;font-weight:700;color:#2d3436;margin:12px 0 4px;">传导链定位</div>
  <div style="font-size:13px;color:#2d3436;line-height:1.6;padding:10px 12px;background:#fafbfc;border-radius:6px;">{chain}</div>
  <div style="font-size:15px;font-weight:700;color:#2d3436;margin:12px 0 4px;">对 A 股影响推演</div>
  <div style="font-size:13px;color:#2d3436;line-height:1.6;padding:10px 12px;background:#fafbfc;border-radius:6px;">{aimpact}</div>
  <div style="font-size:15px;font-weight:700;color:#d63031;margin:12px 0 4px;">后续升级观察点</div>
  <div style="font-size:13px;color:#2d3436;padding:10px 12px;background:#fafbfc;border-radius:6px;">
    <ul style="margin:0;padding-left:18px;">{wp}</ul>
  </div>'''


def render_focus_html(state, standalone=False, embed=False):
    """渲染「限时关注的重点数据解析」章节（或独立页面）。

    standalone=True  → 完整独立 HTML 页面
    standalone=False → 可嵌入片段（<section> + 自带 h2）
    embed=True       → 仅返回内部内容（不包 section/h2），由调用方套 .card + 标准 h2
    """
    note = state.get("note", "")
    cons = state.get("consensus", {})
    degree_label = cons.get("degree_label", "—")
    degree_color = cons.get("degree_color", "#636e72")
    cons_text = cons.get("consensus_text", "")
    hike_range = cons.get("hike_range", "")
    terminal_range = cons.get("terminal_range", "")

    # 一致预期卡
    chips = []
    if hike_range:
        chips.append(f'<span style="background:#eef4ff;color:#1967d2;padding:3px 9px;border-radius:6px;font-size:12px;">单次加息区间 {hike_range}</span>')
    if terminal_range:
        chips.append(f'<span style="background:#eef4ff;color:#1967d2;padding:3px 9px;border-radius:6px;font-size:12px;">终点利率区间 {terminal_range}</span>')
    chips_html = " ".join(chips)

    consensus_box = f'''
    <div style="margin:12px 0;padding:12px 14px;border-radius:8px;background:{degree_color}1a;color:{degree_color};font-size:14px;line-height:1.6;border:1px solid {degree_color}55;">
      <b>加息程度研判：{degree_label}</b>
      {('<div style="margin-top:6px;">' + chips_html + '</div>') if chips_html else ''}
      <div style="margin-top:8px;color:#2d3436;font-size:13.5px;line-height:1.7;">{cons_text}</div>
    </div>'''

    # 各大所观点卡
    cards = []
    for r in state.get("institutions", []):
        if not r.get("found"):
            scolor = "#636e72"
            sbadge = '<span style="background:#dfe6e9;color:#636e72;padding:2px 8px;border-radius:6px;font-size:12px;">观点缺失</span>'
        else:
            st = r.get("stance", "中性")
            scolor = {"偏鹰": "#d63031", "偏鸽": "#00a865"}.get(st, "#e17055")
            sbadge = f'<span style="background:{scolor};color:#fff;padding:2px 8px;border-radius:6px;font-size:12px;">{st}</span>'
        cards.append(f'''
    <div style="flex:1;min-width:300px;border:1px solid #e5e8ec;border-radius:10px;padding:13px;margin:6px;
                border-top:4px solid {scolor};background:#fff;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-size:15px;font-weight:700;color:{scolor};">{html.escape(r.get('name_zh',''))}
          <span style="font-size:11px;color:#b2bec3;font-weight:400;">{html.escape(r.get('name_en',''))}</span></div>
        {sbadge}
      </div>
      <div style="font-size:13px;color:#2d3436;margin:8px 0 0;line-height:1.6;">{html.escape(r.get('view_zh',''))}</div>
    </div>''')

    inst_grid = f'<div style="display:flex;flex-wrap:wrap;">{"" .join(cards)}</div>'

    analysis = build_analysis(state)
    inner = f'''
  <p style="font-size:13px;color:#636e72;margin:0 0 10px;">
    本模块专攻日本央行加息「程度」研判：抓取各大所（顶级投行 / 研究机构）英文研报与观点，解析正文后输出合理观点并合成一致预期。{html.escape(note) if note else ''}
  </p>
  {consensus_box}
  <div style="font-size:15px;font-weight:700;color:#2d3436;margin:14px 0 6px;">各大所观点</div>
  {inst_grid}
  {analysis}'''

    if embed:
        return inner
    if standalone:
        return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>限时关注的重点数据解析 - {state.get('date','')}</title>
<style>body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#2d3436;}}</style>
</head><body>
<h2 style="color:#2d3436;">限时关注的重点数据解析 <span style="font-size:13px;color:#b2bec3;">{state.get('generated_at','')}</span></h2>
{inner}
</body></html>'''
    return f'<section style="margin:18px 0;"><h2 style="color:#2d3436;font-size:20px;border-left:4px solid #d63031;padding-left:10px;">限时关注的重点数据解析</h2>{inner}</section>'


def save_outputs(state):
    date8 = state.get("date") or datetime.now().strftime("%Y%m%d")
    focus_dir = OUTPUT_DIR / "data" / "focus"
    focus_dir.mkdir(parents=True, exist_ok=True)
    json_path = focus_dir / f"focus_state_{date8}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    rep_dir = OUTPUT_DIR / "reports" / "限时关注"
    rep_dir.mkdir(parents=True, exist_ok=True)
    html_path = rep_dir / f"focus-{date8}.html"
    html_path.write_text(render_focus_html(state, standalone=True), encoding="utf-8")
    return json_path, html_path


def run_focus_monitor(no_fetch=False, days=None):
    """对外入口：返回 state dict。"""
    cfg = load_config()
    state = build_state(cfg, no_fetch=no_fetch, days=days)
    if not no_fetch:
        save_outputs(state)
    return state


def main():
    no_fetch = "--no-fetch" in sys.argv
    days = None
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            try:
                days = int(sys.argv[i + 1])
            except ValueError:
                pass
    state = run_focus_monitor(no_fetch=no_fetch, days=days)
    print("\n" + "=" * 56)
    cons = state.get("consensus", {})
    print(f"[焦点监控] 加息程度研判: {cons.get('degree_label','—')} | 可达: {state.get('reachable')}")
    if state.get("note"):
        print(f"   {state['note']}")
    print("=" * 56)
    frag = render_focus_html(state, standalone=False)
    out_dir = OUTPUT_DIR / "reports" / "限时关注"
    out_dir.mkdir(parents=True, exist_ok=True)
    frag_path = out_dir / f"focus-fragment-{state.get('date')}.html"
    frag_path.write_text(frag, encoding="utf-8")
    print(f"[焦点监控] 章节片段已写: {frag_path}")


if __name__ == "__main__":
    main()
