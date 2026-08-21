#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外网资讯解析模块（英文源抓取 + 正文解析）。

设计目标（对应项目需求）：
  1. 境外信息一律用「英文 query」去外网抓取（Google News EN / Bing News EN），
     而非中文 zh-CN 通道——英文源覆盖更广、原始信息密度更高。
  2. 不只读标题：对每条 RSS 结果进一步抓取**文章正文**（Bing 直链 publisher URL），
     剥离脚本/样式后提取可读文本（解析内容，而非标题/摘要）。
  3. 抓取结果存盘为原始结构化数据（英文），由 Agent（LLM）读取后「总结为中文结论」，
     再经 build_report.py 渲染为便于判断市场走向的中文研判。

数据缺口策略：任一源失败/超时自动跳过，绝不编造；JSON 中 summary_zh 为空时
build_report 渲染原始解析内容（英文）占位，待 Agent 补全中文总结。

用法:
  python news_intel.py                 # 实时抓取+解析，写入 data/news_intel/news_intel_YYYYMMDD.json
  python news_intel.py --date 2026-08-17
  python news_intel.py --no-fetch      # 仅重渲染已缓存的原始解析内容（离线模式，不重新抓取）
"""
import json
import re
import html
import argparse
from datetime import datetime
from pathlib import Path
import urllib.parse
import urllib.request

import a_stock_agent as agent
from llm_client import call_json
from templates.prompts import NewsIntelPrompts

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "data" / "news_intel"

# ---- 英文 RSS 通道（境外源，hl=en-US / cc=US） ----
GOOGLE_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
BING_EN = "https://www.bing.com/news/search?q={q}&format=rss&setlang=en-us&cc=US"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---- 主题定义默认回退：每个主题对应若干英文 query（外网抓取用语） ----
_DEFAULT_TOPICS = {
    "us_market": {
        "label_zh": "隔夜美股与全球风险偏好",
        "queries_en": [
            "US stock market today S&P 500 Nasdaq close",
            "Wall Street futures Fed earnings today",
            "US equity market rally selloff today",
        ],
    },
    "macro": {
        "label_zh": "中美宏观与通胀利率",
        "queries_en": [
            "US CPI inflation latest Federal Reserve rate",
            "China CPI PPI economy data latest",
            "Federal Reserve interest rate decision latest",
        ],
    },
    "geopolitics": {
        "label_zh": "地缘政治与原油",
        "queries_en": [
            "crude oil price today geopolitical supply",
            "Middle East oil supply news today",
            "global geopolitical risk markets today",
        ],
    },
    "japan": {
        "label_zh": "日本加息与套息交易",
        "queries_en": [
            "Japan yen carry trade unwind Bank of Japan rate hike",
            "Japan government bond selling Fed FIMA repo",
            "Bank of Japan policy rate hike latest",
        ],
    },
}

_FETCH_PER_TOPIC = 4   # 每个主题抓取正文的最多条数（控时长）
_RSS_PER_QUERY = 6     # 每个 query 取多少条 RSS 结果


def _load_config():
    cfg_path = BASE_DIR / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _load_topics():
    """优先从 config.json 的 news_intel_topics 读取主题，否则使用默认主题。"""
    cfg = _load_config()
    topics = cfg.get("news_intel_topics")
    if topics and isinstance(topics, dict):
        return topics
    return dict(_DEFAULT_TOPICS)


TOPICS = _load_topics()


def _clean(text):
    prev = None
    for _ in range(3):
        prev = text
        text = html.unescape(text)
        if text == prev:
            break
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _resolve_link(link):
    """把 Bing 的 apiclick 包装链接还原成真实 publisher URL；Google 重定向链接无法解析则返回空。"""
    if not link:
        return ""
    link = link.replace("&amp;", "&")  # RSS 中 & 被转义为 &amp;
    if "news.google.com/rss/articles" in link or "news.google.com/news" in link:
        return ""  # Google 重定向墙，无法稳定解析真实 publisher
    if "bing.com/news/apiclick" in link or "bing.com/news/search" in link:
        try:
            q = urllib.parse.urlparse(link).query
            real = urllib.parse.parse_qs(q).get("url", [""])[0]
            return urllib.parse.unquote(real) if real else link
        except Exception:
            return link
    return link


def _search_rss_en(query, max_items=_RSS_PER_QUERY):
    """英文 RSS 搜索：Bing EN 优先（直链 publisher，可解析正文），Google EN 兜底。

    返回 [{title_en, link(已还原真实URL), desc, source, published}]。
    """
    q = urllib.parse.quote(query)
    sources = [
        ("BingEN", BING_EN.format(q=q)),
        ("GoogleEN", GOOGLE_EN.format(q=q)),
    ]
    for name, url in sources:
        text = agent.fetch_url(url, quiet=(name == "GoogleEN"))
        if not text:
            continue
        items = re.findall(r"<item>(.*?)</item>", text, re.S)
        out = []
        for it in items[:max_items]:
            tm = re.search(r"<title>(.*?)</title>", it, re.S)
            lm = re.search(r"<link>(.*?)</link>", it, re.S)
            dm = re.search(r"<description>(.*?)</description>", it, re.S)
            pm = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            sm = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
            title = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip()) if tm else ""
            raw_link = re.sub(r"<!\[CDATA\[|\]\]>", "", lm.group(1)).strip() if lm else ""
            desc = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", dm.group(1)).strip()) if dm else ""
            published = re.sub(r"<!\[CDATA\[|\]\]>", "", pm.group(1)).strip() if pm else ""
            source = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", sm.group(1)).strip()) if sm else name
            if title and len(title) > 8:
                out.append({
                    "title_en": title,
                    "link": _resolve_link(raw_link),
                    "desc": desc,
                    "source": source,
                    "published": published,
                })
        if out:
            return out
    return []


def _fetch_article_text(url, max_chars=1600):
    """抓取文章正文并提取可读文本（解析内容，而非仅标题/摘要）。

    - 仅对 Bing 还原出的真实 publisher URL 尝试；Google 重定向链接在 _resolve_link 已置空。
    - 部分 publisher 对爬虫 403/拦截，失败时返回 None，由上游回退到 RSS 描述（领段文本）。
    - 取到正文后做反爬页面过滤（含 access denied / enable JavaScript 等视为失败）。
    """
    if not url or "news.google.com" in url:
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
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


def _collect_topic(topic_key):
    """对一个主题：英文 query 抓取 RSS → 去重 → 对前 N 条解析正文。"""
    cfg = TOPICS[topic_key]
    seen, merged = set(), []
    for q in cfg["queries_en"]:
        for it in _search_rss_en(q):
            key = it["title_en"][:60].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    merged = merged[: _RSS_PER_QUERY + 2]
    for it in merged[:_FETCH_PER_TOPIC]:
        body = _fetch_article_text(it.get("link"))
        # 优先用解析到的正文；publisher 拦截时回退到 RSS 描述（领段文本，已非单纯标题）
        it["content_en"] = body if (body and len(body) >= 300) else it.get("desc", "")
    return merged


def _summarize_topic(label, raw, date, config):
    """远程LLM(provider=external)可用时生成结构化摘要；默认由当前运行的模型(Agent)注入。"""
    prompt = NewsIntelPrompts.summarize_zh(label, raw, date)
    system = "你只做跨市场金融资讯压缩。事实与推断分离，不得补充输入外数字；只输出JSON。"
    obj, reason = call_json(prompt, config, system_prompt=system, temperature=0.1)
    if not isinstance(obj, dict):
        return "", {}, prompt, reason
    direction = obj.get("direction") if obj.get("direction") in {"偏多", "偏空", "中性"} else "中性"
    try:
        confidence = round(max(0.0, min(1.0, float(obj.get("confidence", 0.5)))), 2)
    except (TypeError, ValueError):
        confidence = 0.5
    structured = {
        "direction": direction,
        "confidence": confidence,
        "as_of": str(obj.get("as_of") or date)[:10],
        "facts": [str(x)[:160] for x in (obj.get("facts") or [])[:3]],
        "core_conclusion": str(obj.get("core_conclusion") or "")[:180],
        "transmission": [str(x)[:180] for x in (obj.get("transmission") or [])[:3]],
        "priced_in": str(obj.get("priced_in") or "无法判断")[:20],
        "watch": [str(x)[:160] for x in (obj.get("watch") or [])[:3]],
    }
    summary = str(obj.get("summary_zh") or structured["core_conclusion"]).strip()[:500]
    return summary, structured, prompt, "ok"


def run_intel(date=None, no_fetch=False):
    """抓取+解析全部主题，并在已配置LLM时自动生成结构化中文摘要。"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    date8 = date.replace("-", "")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"news_intel_{date8}.json"

    if no_fetch:
        if out_path.exists():
            print(f"[news_intel] --no-fetch：读取已缓存 {out_path.name}")
            return json.loads(out_path.read_text(encoding="utf-8"))
        print(f"[news_intel] --no-fetch：未找到 {out_path.name}，不生成空缓存。")
        return {}

    config = agent.load_config()
    topics = {}
    for key, cfg in TOPICS.items():
        print(f"[news_intel] 英文抓取+解析主题: {cfg['label_zh']} ...")
        raw = _collect_topic(key) if not no_fetch else []
        summary, structured, prompt_for_llm, llm_mode = (
            _summarize_topic(cfg["label_zh"], raw, date, config) if raw
            else ("", {}, "", "no_content")
        )
        topics[key] = {
            "label_zh": cfg["label_zh"],
            "queries_en": cfg["queries_en"],
            "raw": raw,
            "summary_zh": summary,
            "summary_structured": structured,
            "summary_prompt_for_llm": prompt_for_llm,
            "summary_mode": llm_mode,
        }
        print(f"  获取 {len(raw)} 条（正文 {sum(1 for r in raw if r.get('content_en'))}；摘要模式 {llm_mode}）")

    payload = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "raw=英文原始解析；默认(未配外部API)由当前运行的模型(Agent)注入 summary_zh/summary_structured；配置 config.llm(provider=external) 时自动调用远程模型",
        "topics": topics,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[news_intel] 已写入: {out_path}")
    return payload


def load_intel(date=None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    date8 = date.replace("-", "")
    p = OUTPUT_DIR / f"news_intel_{date8}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="外网资讯解析（英文抓取+正文解析）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-fetch", action="store_true", help="离线重渲染已缓存原始解析内容")
    args = ap.parse_args()
    run_intel(date=args.date, no_fetch=args.no_fetch)
