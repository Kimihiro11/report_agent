#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把已生成的 9章节 HTML 报告解析后入库到 PostgreSQL (a_stock_agent)。
覆盖表: daily_reports / sentiment_data / stock_analysis / resonance_signals / technical_indicators
（index_quotes 报告内无干净的指数收盘行情表，留待实时抓取补充）
幂等：同一 report_date 先 DELETE 再 INSERT，可重复运行。
"""
import re
import json
import importlib.util
from pathlib import Path
import psycopg2

ROOT = Path(r"C:/Users/cjass/WorkBuddy/2026-08-13-14-52-10")
config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
dbcfg = config["database"]

# ---- 初始化库表 ----
spec = importlib.util.spec_from_file_location("dbmod", str(ROOT / "db.py"))
dbmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dbmod)
db = dbmod.StockAgentDB(**dbcfg)
db.init_database()
print("[DB] 库表初始化完成")


def conn():
    return psycopg2.connect(host=dbcfg["host"], port=dbcfg["port"], user=dbcfg["user"],
                            password=dbcfg["password"], dbname=dbcfg["dbname"],
                            client_encoding="UTF8")


def strip(html):
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"&[a-z]+;|&#\d+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


reports = sorted(ROOT.glob("A股操作指引-9章节-*.html"))
total = {"daily_reports": 0, "sentiment_data": 0, "stock_analysis": 0,
         "resonance_signals": 0, "technical_indicators": 0}

# 章节 -> (source_name, source_type, tier)
SENT_MAP = {
    "1": ("隔夜美股与全球人物", "global", 2),
    "2": ("CPI/PPI与宏观", "macro", 0),
    "3": ("宏观传导链", "macro", 0),
    "4": ("地缘政治与原油", "event", 0),
    "5": ("ETF资金流向", "macro", 0),
    "6": ("唐史主任司马迁", "weibo", 1),
}

for rpt in reports:
    html = rpt.read_text(encoding="utf-8")
    m = re.search(r"(\d{4})(\d{2})(\d{2})", rpt.stem)
    if not m:
        print(f"[SKIP] 无法解析日期: {rpt.name}")
        continue
    rd = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        # 按 ch 切分章节
        parts = re.split(r'<div class="card section-anchor" id="ch(\d)">', html)
        sec = {}
        for i in range(1, len(parts), 2):
            sec[parts[i]] = parts[i + 1]

        # 核心结论 + header meta 作为 summary
        core = ""
        cm = re.search(r'<h2>核心结论</h2>(.*?)</div>\s*(?:<div class="toc"|<div class="card section-anchor" id="ch1">)', html, re.S)
        if cm:
            core = strip(cm.group(1))
        header_meta = ""
        hm = re.search(r'<div class="header">(.*?)</div>\s*(?:<div class="weekend-note"|<div class="toc")', html, re.S)
        if hm:
            header_meta = strip(hm.group(1))
        summary = (header_meta + " | " + core)[:2000]

        # market_state 判定
        stext = (core + " " + sec.get("9", "") + " " + sec.get("1", ""))
        if "回补" in stext or "偏多" in stext:
            ms = "bullish"
        elif "降至" in stext or "减仓" in stext:
            ms = "bearish"
        else:
            ms = "neutral"

        c = conn()
        cur = c.cursor()
        # 幂等删除
        cur.execute("DELETE FROM daily_reports WHERE report_date=%s", (rd,))
        cur.execute("DELETE FROM sentiment_data WHERE record_date=%s", (rd,))
        cur.execute("DELETE FROM stock_analysis WHERE analysis_date=%s", (rd,))
        cur.execute("DELETE FROM resonance_signals WHERE signal_date=%s", (rd,))
        cur.execute("DELETE FROM technical_indicators WHERE analysis_date=%s", (rd,))

        # daily_reports 全文
        cur.execute(
            "INSERT INTO daily_reports(report_date, market_state, summary, html_content) VALUES(%s,%s,%s,%s)",
            (rd, ms, summary, html))
        total["daily_reports"] += 1

        # sentiment_data (ch1-ch6)
        for ch, (sn, st, tier) in SENT_MAP.items():
            if ch in sec:
                content = strip(sec[ch])[:4000]
                if content:
                    cur.execute(
                        "INSERT INTO sentiment_data(record_date, source_name, source_type, tier, content, post_time) VALUES(%s,%s,%s,%s,%s,%s)",
                        (rd, sn, st, int(tier), content, ""))
                    total["sentiment_data"] += 1

        # resonance_signals (ch7)
        if "7" in sec:
            for mt in re.finditer(r'<div class="pt-title"[^>]*>(.*?)</div>\s*<div class="pt-body">(.*?)</div>', sec["7"], re.S):
                title = strip(mt.group(1))
                body = strip(mt.group(2))
                lvl_m = re.search(r"(\d+)重共振", title)
                lvl = int(lvl_m.group(1)) if lvl_m else None
                conf = "高" if (lvl and lvl >= 5) or ("风险" in title) else ("中" if lvl else "低")
                cur.execute(
                    "INSERT INTO resonance_signals(signal_date, signal_name, resonance_level, sources, confidence) VALUES(%s,%s,%s,%s,%s)",
                    (rd, title[:200], lvl, body[:1500], conf))
                total["resonance_signals"] += 1

        # stock_analysis + technical_indicators (ch8)
        if "8" in sec:
            cards = re.findall(r'<div class="stock-card">(.*?)</div>\s*(?=<div class="stock-card">|<!--)', sec["8"], re.S)
            for card in cards:
                nm = re.search(r'class="name">([^<]+)<', card)
                if not nm:
                    continue
                raw = nm.group(1).strip()
                code_m = re.search(r"(\d{6})", raw)
                code = code_m.group(1) if code_m else ""
                sname = re.split(r"[\s—\-]", raw)[0].strip()

                badge = re.search(r'class="badge[^"]*">([^<]+)<', card)
                price = chg = None
                if badge:
                    bp = re.search(r"收([\d.]+)", badge.group(1))
                    price = float(bp.group(1)) if bp else None
                    cp = re.search(r"([+-]?\d+\.?\d*)%", badge.group(1))
                    chg = float(cp.group(1)) if cp else None

                logic = re.search(r'class="stock-logic">(.*?)</div>', card, re.S)
                logic_t = strip(logic.group(1)) if logic else ""
                risk = re.search(r'风险：</strong>(.*?)</div>', card, re.S)
                risk_t = strip(risk.group(1)) if risk else ""
                op = re.search(r'class="op-row">(.*?)</div>', card, re.S)
                action = ""
                if op:
                    ops = re.findall(r'class="op[^"]*">([^<]+)<', op.group(1))
                    if ops:
                        action = "/".join(ops)[:20]
                reasoning = (logic_t + " 风险：" + risk_t)[:2000]
                cur.execute(
                    "INSERT INTO stock_analysis(analysis_date, stock_code, stock_name, price, change_pct, action, reasoning) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (rd, code, sname, price, chg, action, reasoning))
                total["stock_analysis"] += 1

                # technical_indicators
                meta = re.search(r'class="stock-meta">(.*?)</div>', card, re.S)
                ma5 = ma10 = ma20 = ma60 = None
                trend = "震荡"
                if meta:
                    mt = meta.group(1)
                    d5 = re.search(r"MA5\s*([\d.]+)", mt)
                    ma5 = float(d5.group(1)) if d5 else None
                    d10 = re.search(r"MA10\s*([\d.]+)", mt)
                    ma10 = float(d10.group(1)) if d10 else None
                    d20 = re.search(r"MA20\s*([\d.]+)", mt)
                    ma20 = float(d20.group(1)) if d20 else None
                    d60 = re.search(r"MA60\s*([\d.]+)", mt)
                    ma60 = float(d60.group(1)) if d60 else None
                    comb = logic_t + mt
                    if "多头排列" in comb:
                        trend = "多头排列(偏多)"
                    elif "空头排列" in comb:
                        trend = "空头排列(偏空)"
                if code:
                    cur.execute(
                        "INSERT INTO technical_indicators(analysis_date, symbol, close_price, ma5, ma10, ma20, ma60, trend, high5, low5) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (rd, code, price, ma5, ma10, ma20, ma60, trend, None, None))
                    total["technical_indicators"] += 1

        c.commit()
        cur.close()
        c.close()
        print(f"[OK] {rd} 市场:{ms} | 舆情{total['sentiment_data']} 自选{total['stock_analysis']} 共振{total['resonance_signals']} 技术{total['technical_indicators']}")
    except Exception as e:
        print(f"[ERR] {rd}: {e}")

print("\n=== 入库完成汇总 ===")
for k, v in total.items():
    print(f"  {k}: +{v}")
print("=== index_quotes 跳过（报告内无干净的指数收盘行情表，待实时抓取补充） ===")
