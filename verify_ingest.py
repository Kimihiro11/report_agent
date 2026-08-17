#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import psycopg2, json
from pathlib import Path
ROOT = Path(r"C:/Users/cjass/WorkBuddy/2026-08-13-14-52-10")
cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))["database"]
c = psycopg2.connect(client_encoding="UTF8", **cfg)
cur = c.cursor()
print("=== 各表行数 ===")
for t in ["daily_reports", "sentiment_data", "stock_analysis", "resonance_signals", "technical_indicators", "index_quotes"]:
    cur.execute("SELECT count(*) FROM " + t)
    print(f"  {t}: {cur.fetchone()[0]}")
print("\n=== daily_reports ===")
cur.execute("SELECT report_date, market_state, octet_length(html_content) FROM daily_reports ORDER BY report_date")
for r in cur.fetchall():
    print(f"  {r[0]} | {r[1]} | html字节={r[2]}")
print("\n=== stock_analysis (2026-08-15 样本) ===")
cur.execute("SELECT stock_code, stock_name, action, price, change_pct FROM stock_analysis WHERE analysis_date='2026-08-15' ORDER BY stock_code")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} | 操作:{r[2]} | 收:{r[3]} 涨:{r[4]}")
print("\n=== resonance_signals (2026-08-15 样本) ===")
cur.execute("SELECT signal_name, resonance_level, confidence FROM resonance_signals WHERE signal_date='2026-08-15'")
for r in cur.fetchall():
    print(f"  [{r[2]}] L{r[1]} {r[0][:40]}")
cur.close(); c.close()
print("\n验证完成。")
