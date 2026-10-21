#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补数体检：列出最近交易日各表的覆盖情况，标出缺口。

用法
  python tools/gap_check.py                # 最近 7 个交易日
  python tools/gap_check.py --from 2026-10-14
"""
import argparse
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ra.infra.db import StockAgentDB
from ra.infra import view

TABLES = [
    ("index_quotes", "quote_date", "指数行情", 5),
    ("etf_flows", "flow_date", "ETF资金流", 4),
    ("market_width", "width_date", "市场宽度", 1),
    ("daily_klines", "trade_date", "自选股日K", 10),
]


def main():
    ap = argparse.ArgumentParser(description="补数体检")
    ap.add_argument("--from", dest="dt_from", help="起始日期 YYYY-MM-DD（缺省：最近 7 个交易日）")
    ap.add_argument("--password", default="1q2w3e4r")
    args = ap.parse_args()

    today = datetime.date.today()
    if args.dt_from:
        start = datetime.date.fromisoformat(args.dt_from)
    else:
        d, days = today, []
        for _ in range(12):
            if view.is_trading_day(d):
                days.append(d)
            d -= datetime.timedelta(days=1)
        days = sorted(days)[:7]
        start = days[0]

    db = StockAgentDB(password=args.password)
    have = {}
    with db._cursor() as cur:
        for tbl, col, label, _ in TABLES:
            cur.execute(f'SELECT {col}::text, count(*) FROM {tbl} WHERE {col} >= %s '
                        f'GROUP BY 1 ORDER BY 1', (start.isoformat(),))
            have[tbl] = {r[0]: r[1] for r in cur.fetchall()}

    d = start
    while d <= today:
        ds = d.isoformat()
        if view.is_trading_day(d):
            marks = []
            for tbl, col, label, expect in TABLES:
                n = have[tbl].get(ds, 0)
                marks.append(f"{label}={'OK' if n >= expect else f'缺({n}/{expect})'}")
            flag = "" if all("OK" in m for m in marks) else "   ← 有缺口"
            print(f"{ds}  " + "  ".join(marks) + flag)
        else:
            print(f"{ds}  （非交易日）")
        d += datetime.timedelta(days=1)

    missing = []
    d = start
    while d <= today:
        if view.is_trading_day(d):
            for tbl, col, label, expect in TABLES:
                if have[tbl].get(d.isoformat(), 0) < expect:
                    missing.append((d.isoformat(), label))
        d += datetime.timedelta(days=1)
    print()
    if missing:
        print(f"[缺口合计 {len(missing)} 项]")
        for ds, label in missing:
            print(f"  {ds}  {label}")
    else:
        print("[无缺口]")


if __name__ == "__main__":
    sys.exit(main())