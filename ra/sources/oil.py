#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原油价格与舆情跟踪（数据层）。

产出 `data/oil/oil_<DATE8>.json`，供 build_report.oil_section() 渲染。

价格（双口径）
  - WTI 原油：新浪外盘期货 `hf_CL`（纽约原油）
  - 布伦特原油：新浪外盘期货 `hf_OIL`
  - 实时：`https://hq.sinajs.cn/list=hf_CL,hf_OIL`
  - 历史：`GlobalFuturesService.getGlobalFuturesDailyKLine`（全历史，1996/2016 起）
  指标：最新价、当日、近 5/20/60 交易日动量、MA5/10/20、距 52 周高/低。

舆情
  取自 news_intel.py 的主题（优先 `oil`，缺失回退 `geopolitics`）——
  英文标题/正文做**关键词定向情绪打分**（利多/利空/中性），并透出标题列表。
  ⚠️ 关键词打分为确定性规则口径，非研报观点；`summary_zh` 由 Agent 注入。

用法
  python cli.py oil --date 2026-09-21            # 抓取+落盘
  python cli.py oil --date 2026-09-21 --show     # 抓取并打印摘要
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import urllib.request
from datetime import datetime

from ra.paths import ROOT as BASE_DIR  # 包化后统一根路径
OUT_DIR = BASE_DIR / "data" / "daily" / "oil"
NEWS_DIR = BASE_DIR / "data" / "daily" / "news_intel"

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

# 双口径：新浪外盘期货代码
SPOT_TARGETS = [
    {"code": "CL", "sina": "hf_CL", "name": "WTI原油（纽约）", "short": "WTI"},
    {"code": "OIL", "sina": "hf_OIL", "name": "布伦特原油", "short": "Brent"},
]

# ---- 关键词情绪词典（英文正文；按匹配顺序计数，先命中先归类）----
_BEARISH_KW = [
    "oversupply", "over-supply", "surplus", "glut", "demand concern", "demand worry",
    "weak demand", "demand destruction", "slowdown", "slump", "selloff", "sell-off",
    "bearish", "plunge", "tumble", "slid", "slides", "fall", "falls", "fell", "drop",
    "drops", "dropped", "decline", "declines", "lower", "weaken", "weakens", "losses",
    "output increase", "production increase", "output rise", "raise output", "opec+ increase",
    "record output", "inventories rise", "inventory build", "stockpiles rise", "build in crude",
    "ceasefire", "easing sanctions", "resume exports", "peace deal",
]
_BULLISH_KW = [
    "supply cut", "production cut", "output cut", "opec cut", "opec+ cut", "extend cuts",
    "sanction", "embargo", "attack", "strike", "drone", "tension", "escalation",
    "disruption", "outage", "force majeure", "hurricane", "halt", "blockade", "seize",
    "undersupply", "deficit", "tight supply", "drawdown", "inventories fall",
    "inventory draw", "stockpiles fall", "draw in crude", "demand growth", "strong demand",
    "bullish", "surge", "surges", "soar", "soars", "rally", "rallies", "jump", "jumps",
    "climb", "climbs", "rose", "rise", "rises", "gain", "gains", "higher",
]


def _get(url: str, encoding: str = "utf-8", timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, errors="replace")


# ---------------- 价格 ----------------

def fetch_spot(sina_code: str):
    """新浪外盘期货实时快照。

    hf_ 字段位：0现价 1买价 2卖价 3昨结算 4最高 5最低 6时间 7昨收 8开盘
              9持仓 10买量 11卖量 12日期 13名称 14成交量
    """
    text = _get(f"https://hq.sinajs.cn/list={sina_code}", encoding="gbk")
    m = re.search(r'var hq_str_%s="([^"]*)"' % re.escape(sina_code), text)
    if not m or not m.group(1):
        return None
    p = m.group(1).split(",")

    def f(i):
        try:
            return float(p[i])
        except Exception:
            return None

    price, prev = f(0), f(7)
    if not price:
        return None
    return {
        "price": price,
        "high": f(4),
        "low": f(5),
        "open": f(8),
        "prev_close": prev,
        "chg": (price / prev - 1) * 100 if prev else None,
        "time": p[6] if len(p) > 6 else "",
        "trade_date": p[12] if len(p) > 12 else "",
        "name": p[13] if len(p) > 13 else "",
    }


def fetch_kline(symbol: str):
    """新浪外盘期货日K（全历史），返回升序 [{date,open,high,low,close}]。"""
    url = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20_{symbol}=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}")
    text = _get(url)
    m = re.search(r"=\s*\((\[.*\])\)", text, re.S) or re.search(r"=\s*(\[.*\])", text, re.S)
    if not m:
        return []
    try:
        rows = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append({"date": r["date"], "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"])})
        except Exception:
            continue
    out.sort(key=lambda x: x["date"])
    return out


def _indicators(kl):
    if not kl or len(kl) < 21:
        return None
    cl = [k["close"] for k in kl]

    def pct(days):
        return (cl[-1] / cl[-1 - days] - 1) * 100 if len(cl) > days and cl[-1 - days] else None

    win52 = kl[-250:]
    hi52 = max(k["high"] for k in win52)
    lo52 = min(k["low"] for k in win52)
    return {
        "last": round(cl[-1], 3),
        "ma5": round(sum(cl[-5:]) / 5, 3),
        "ma10": round(sum(cl[-10:]) / 10, 3),
        "ma20": round(sum(cl[-20:]) / 20, 3),
        "m5": pct(5), "m20": pct(20), "m60": pct(60),
        "high52": round(hi52, 3), "low52": round(lo52, 3),
        "from_high": (cl[-1] / hi52 - 1) * 100 if hi52 else None,
        "from_low": (cl[-1] / lo52 - 1) * 100 if lo52 else None,
        "bars": len(kl),
        "data_date": kl[-1]["date"],
    }


def _align_series(series_map: dict, dates: list):
    """多标的按日期对齐，缺失前向填充（避免各交易所休市造成断线）。"""
    last, rows = {}, []
    for d in dates:
        for code, m in series_map.items():
            if d in m:
                last[code] = m[d]
        rows.append({c: last.get(c) for c in series_map})
    return rows


def _build_series(klines_map: dict, days: int = 60):
    """X 轴取 WTI 最近 days 个交易日，其余对齐前向填充。"""
    primary = klines_map.get("CL") or next(iter(klines_map.values()), [])
    if len(primary) < 5:
        return None
    dates = [k["date"] for k in primary[-days:]]
    cmap = {c: {k["date"]: k["close"] for k in kl} for c, kl in klines_map.items()}
    rows = _align_series(cmap, dates)
    lines = []
    for code, _ in klines_map.items():
        name = next((t["name"] for t in SPOT_TARGETS if t["code"] == code), code)
        vals = [r.get(code) for r in rows]
        if any(v is not None for v in vals):
            lines.append({"name": name, "code": code, "values": vals})
    return {"dates": dates, "lines": lines}


# ---------------- 舆情 ----------------

def _pub_date(published: str):
    """解析 RSS 发布时间（RFC822 变体），返回 date 或 None。"""
    if not published:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(published).date()
    except Exception:
        m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", published)
        if not m:
            return None
        months = {m2: i for i, m2 in enumerate(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
        try:
            import datetime as _dt
            return _dt.date(int(m.group(3)), months[m.group(2)], int(m.group(1)))
        except Exception:
            return None


def _tone(text: str) -> str:
    low = (text or "").lower()
    for kw in _BEARISH_KW:
        if kw in low:
            return "bearish"
    for kw in _BULLISH_KW:
        if kw in low:
            return "bullish"
    return "neutral"


def sentiment_from_news(date_str: str, max_age_days: int = 7):
    """从 news_intel 主题（优先 oil，回退 geopolitics）做关键词情绪统计。

    ⚠️ 搜索型 RSS 会混入**陈旧条目**（实测同一批里夹着 2025-12 的旧闻）——
    按发布时间过滤到 max_age_days 内；若过滤后不足 max(3, 全部1/3)，退回按时间取最新若干条。
    """
    d8 = date_str.replace("-", "")
    path = NEWS_DIR / f"news_intel_{d8}.json"
    if not path.exists():
        cands = sorted(NEWS_DIR.glob("news_intel_*.json"))
        if not cands:
            return None
        path = cands[-1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    topics = payload.get("topics") or {}
    key = "oil" if topics.get("oil") else ("geopolitics" if topics.get("geopolitics") else None)
    if not key:
        return None
    topic = topics[key] or {}
    raw = topic.get("raw") or []
    if not raw:
        return None

    # 时效过滤：以 news_intel 的日期为基准（缺失则用报告日）
    import datetime as _dt
    base = None
    try:
        base = _dt.date.fromisoformat(str(payload.get("date") or date_str))
    except Exception:
        base = _dt.date.fromisoformat(date_str)

    items, dropped = [], 0
    for it in raw:
        pd = _pub_date(it.get("published", ""))
        if pd is not None and (base - pd).days > max_age_days:
            dropped += 1
            continue
        items.append((pd, it))
    if len(items) < max(3, len(raw) // 3):
        items = sorted([( _pub_date(it.get("published", "")), it) for it in raw],
                       key=lambda x: x[0] or _dt.date.min, reverse=True)[:5]
        dropped = len(raw) - len(items)

    heads, tally = [], {"bullish": 0, "bearish": 0, "neutral": 0}
    for pd, it in sorted(items, key=lambda x: x[0] or _dt.date.min, reverse=True):
        title = it.get("title_en") or it.get("title") or ""
        body = it.get("content_en") or it.get("desc") or ""
        tone = _tone(title or body)
        tally[tone] += 1
        heads.append({"title": title, "link": it.get("link", ""),
                      "published": it.get("published", ""),
                      "published_date": pd.isoformat() if pd else "",
                      "tone": tone, "snippet": (body or "")[:220]})
    score = tally["bullish"] - tally["bearish"]
    label = "偏多" if score > 0 else ("偏空" if score < 0 else "中性")
    return {
        "source": f"news_intel:{key}",
        "source_date": payload.get("date", ""),
        "bullish": tally["bullish"], "bearish": tally["bearish"], "neutral": tally["neutral"],
        "total": len(heads), "score": score, "label": label,
        "dropped_stale": dropped, "max_age_days": max_age_days,
        "summary_zh": (topic.get("summary_zh") or "").strip(),
        "summary_structured": topic.get("summary_structured") or {},
        "headlines": heads[:10],
    }


# ---------------- 汇总 ----------------

def collect(date_str: str | None = None):
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    out = {"date": date_str, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "quotes": [], "errors": [], "series": None, "sentiment": None}

    klines_map = {}
    for t in SPOT_TARGETS:
        rec = {"code": t["code"], "name": t["name"], "short": t["short"]}
        try:
            kl = fetch_kline(t["code"])
            if kl:
                klines_map[t["code"]] = kl
                ind = _indicators(kl) or {}
                rec.update(ind)
        except Exception as e:
            out["errors"].append(f"{t['code']} 日K: {e}")
        try:
            spot = fetch_spot(t["sina"])
            if spot:
                rec["spot"] = spot
                rec["chg"] = spot.get("chg")
                rec["day_high"] = spot.get("high")
                rec["day_low"] = spot.get("low")
                if rec.get("last") is None and spot.get("price"):
                    rec["last"] = round(spot["price"], 3)
        except Exception as e:
            out["errors"].append(f"{t['code']} 实时: {e}")
        if rec.get("last") is not None:
            out["quotes"].append(rec)
        else:
            out["errors"].append(f"{t['code']}: 无数据")

    try:
        out["series"] = _build_series(klines_map, days=60)
    except Exception as e:
        out["errors"].append(f"序列构建: {e}")

    try:
        out["sentiment"] = sentiment_from_news(date_str)
    except Exception as e:
        out["errors"].append(f"舆情: {e}")

    return out


def path_of(date_str: str) -> pathlib.Path:
    return OUT_DIR / f"oil_{date_str.replace('-', '')}.json"


def save(data: dict):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = path_of(data["date"])
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(date_str: str):
    p = path_of(date_str)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="原油价格与舆情跟踪")
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD（缺省今天）")
    ap.add_argument("--show", action="store_true", help="打印摘要")
    args = ap.parse_args()

    data = collect(args.date)
    p = save(data)
    print(f"[原油] 已写入 {p}")
    if data["errors"]:
        print("  抓取异常:", "；".join(data["errors"]))

    if args.show:
        print()
        for q in data["quotes"]:
            print(f"  {q['name']:<14} {q.get('last')} ({q.get('chg'):+.2f}%) "
                  f"MA5={q.get('ma5')} m5={q.get('m5'):+.2f}% m20={q.get('m20'):+.2f}% "
                  f"m60={q.get('m60'):+.2f}% 距52周高={q.get('from_high'):+.1f}%" if q.get("m5") is not None
                  else f"  {q['name']}: {q.get('last')}")
        s = data.get("sentiment")
        if s:
            print(f"  舆情[{s['source']}] 利多{s['bullish']}/利空{s['bearish']}/中性{s['neutral']} "
                  f"→ {s['label']}({s['score']:+d})")
            for h in s["headlines"][:5]:
                print(f"     [{h['tone']}] {h['title'][:90]}")
        ser = data.get("series")
        if ser:
            print(f"  序列: {len(ser['dates'])} 个交易日 {ser['dates'][0]} → {ser['dates'][-1]}")
            for l in ser["lines"]:
                v = [x for x in l["values"] if x is not None]
                if v:
                    print(f"     {l['name']}: {v[0]} → {v[-1]}")


if __name__ == "__main__":
    main()
