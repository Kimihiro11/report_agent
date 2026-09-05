# -*- coding: utf-8 -*-
"""构造 2026-09-04 收盘版快照 fetched_20260904_153000.json。

周报需要 9/4（周五）收盘口径数据，但 9/4 仅存早盘快照（084626，行情=9/3 收盘）。
本脚本：复用 084626 的 weibo_data（保持与 weibo_deconstruct_20260904.json 指纹一致），
行情类字段全部用引擎 fetch 函数实抓（周六新浪/东财/财政部返回 9/4 收盘数据），
ETF/两融走 westock override（date=20260905 已配），再 analyze_sentiment 重算状态。
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import stock_report_agent as s
OLD = BASE / "data" / "snapshots" / "fetched_20260904_084626.json"
NEW = BASE / "data" / "snapshots" / "fetched_20260904_153000.json"

old = json.loads(OLD.read_text(encoding="utf-8"))

# 1) weibo_data 原样复用（舆情/唐史/技术条目与 9/4 deconstruct 指纹锁定）
weibo_data = old["weibo_data"]

# 2) 行情字段实抓（周六返回 9/4 收盘）
print("[1/6] 指数 quotes ...")
quotes = s.fetch_index_quotes()
print("    ", {k: v["price"] for k, v in quotes.items()})

print("[2/6] 隔夜美股 us_market ...")
us_market = s.fetch_us_market()
print(f"      {len(us_market)} 条，前3:", [(x[0], x[1]) for x in us_market[:3]])

print("[3/6] ETF 资金流（westock override 9/4 收盘）...")
etf = s.fetch_etf_flows()
print("      ", [(x[0], x[1], x[2], x[4]) for x in etf])

print("[4/6] 美债收益率 ...")
us_yield = s.fetch_us_yield()
print("      ", us_yield)

print("[5/6] 两融/北向 override ...")
fund_flows = s.fetch_fund_flows()
print("      margin:", len((fund_flows or {}).get("margin") or []),
      "| north:", len((fund_flows or {}).get("north_holding") or []),
      "| data_date:", (fund_flows or {}).get("data_date"))

print("[6/6] 市场宽度 ...")
market_width = s.fetch_market_width()
print("      ", market_width)

# 3) 重算市场状态（基于 9/4 收盘 quotes）
analysis = s.analyze_sentiment(weibo_data, quotes)
print("重算 market_state:", analysis["market_state"], "| matched:", analysis["matched_sectors"])

snapshot = {
    "date": "20260904",
    "market_state": analysis["market_state"],
    "matched_sectors": analysis["matched_sectors"],
    "weibo_data": weibo_data,
    "quotes": quotes,
    "us_market": us_market,
    "etf": etf,
    "us_yield": us_yield,
    "fund_flows": fund_flows,
    "market_width": market_width,
}
NEW.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n✅ 收盘快照已写入: {NEW} ({NEW.stat().st_size // 1024} KB)")
