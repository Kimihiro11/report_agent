#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补录指定历史交易日的收盘数据（四张表）。

背景：日报管线是**盘前出报**，DB 里当日行情常年为空；只跑早报的日子
（如 2026-10-20）收盘数据从未入库，下期早报锚定时就会缺。

覆盖范围
  index_quotes   ← 新浪日K（腾讯只给实时，无法取历史）
  daily_klines   ← backtest._fetch_kline_live（新浪主源 + 东财兜底 + 腾讯补尾端）
  market_width   ← 妙想 MCP 指定日期（**只给全市场口径**，板块列留空）
  etf_flows      ← 妙想 MCP（**须用已有历史行反查校验「万/亿」单位**）

⚠️ 宽度与 ETF 需 MCP 通道，`stock_report_agent._mx_query` **问不出这两个指标**
   （CTFI 误命中债券、BDTI 误命中 INE 期货、宽��/ETF 同理），
   故这两项由调用方通过 MCP 取数后写入 `data/derived/_backfill_<date>.json`，
   本脚本只负责消费。详见 skill `close-data-backfill`。

校验（入库前必做，任何一项不过即拒绝该标的/该日）
  ① 日K：OHLC 自洽（low<=min(o,c)，high>=max(o,c)）
  ② 日K：|涨跌幅| <= 11%（A 股涨跌停上限，20cm 创业板为 20%）
  ③ ETF：与上一交易日的同标的方向/量级做反查比对

用法
  python tools/backfill_day.py --date 2026-10-20
  python tools/backfill_day.py --date 2026-10-20 --only index_quotes,daily_klines
"""
import argparse
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ra.infra import net  # noqa: E402
from ra.infra.db import StockAgentDB  # noqa: E402

IDX = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300",
}
# 涨跌校验阈值（按板块涨跌幅限制）
# ⚠️ 2026-10-30 修：原为固定 11%（主板 ±10% + 余量），**把科创板/创业板的合法大跌误杀了**
#   实测：688668 鼎通 2026-10-29 跌 -18.71%（科创板 ±20%，合法）被拒 → 日K 序列缺一天。
#   板块限制：主板 ±10% / 科创板(688)·创业板(30) ±20% / 北交所(8·4) ±30%。
#   阈值 = 板块限制 + 1pct 余量。
def _max_chg_for(code):
    c = str(code)
    if c.startswith(("688", "689", "300", "301", "302")):
        return 21.0      # 科创板 / 创业板 ±20%
    if c.startswith(("8", "4", "92")):
        return 31.0      # 北交所 ±30%
    return 11.0          # 主板 ±10%


MAX_CHG = 11.0


def fetch_index_rows(target: str):
    """新浪日K 取target 日收盘 + 自算涨跌幅。返回 {name: {...}} 或 None。"""
    out = {}
    for sym, name in IDX.items():
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=8")
        try:
            txt = net.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12, url=url
            ).read().decode("utf-8", "ignore")
            rows = [r for r in json.loads(txt) if r["day"] <= target]
            if len(rows) < 2 or rows[-1]["day"] != target:
                return None
            c = float(rows[-1]["close"])
            p = float(rows[-2]["close"])
            out[name] = {"price": round(c, 2),
                         "chg_pct": round((c / p - 1) * 100, 2),
                         "volume": ""}
        except Exception:
            return None
    return out


def backfill_index(db, target):
    rows = fetch_index_rows(target)
    if not rows:
        print("  [指数] 新浪日K 取数失败，跳过")
        return False
    db.save_index_quotes(target, rows)
    for n, v in rows.items():
        print(f"    {n} {v['price']} ({v['chg_pct']:+.2f}%)")
    return True


def backfill_klines(db, target, codes):
    from ra.analysis import backtest as bt
    ok, bad = 0, []
    for code in codes:
        try:
            kl = bt._fetch_kline_live(code)
        except Exception as e:
            bad.append(f"{code} 抓取异常 {e}")
            continue
        row = next((k for k in kl if k.get("date") == target), None)
        if not row:
            bad.append(f"{code} 无 {target} 数据")
            continue
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]
        if not (l <= min(o, c) and h >= max(o, c)):
            bad.append(f"{code} OHLC 不自洽")
            continue
        with db._cursor() as cur:
            cur.execute("SELECT close FROM daily_klines WHERE stock_code=%s "
                        "AND trade_date < %s ORDER BY trade_date DESC LIMIT 1",
                        (code, target))
            r = cur.fetchone()
        if r and r[0]:
            chg = (c / float(r[0]) - 1) * 100
            limit = _max_chg_for(code)
            if abs(chg) > limit:
                bad.append(f"{code} 涨跌 {chg:+.2f}% 越界（阈值 {limit}%）")
                continue
            prev_note = f"{chg:+.2f}%"
        else:
            prev_note = "无prev"
        db.save_klines(code, [{"date": target, "open": o, "high": h, "low": l, "close": c}])
        ok += 1
        print(f"    {code} close={c} ({prev_note})")
    if bad:
        print("    拒绝：")
        for b in bad:
            print(f"      {b}")
    print(f"    日K 入库 {ok}/{len(codes)}")
    return ok > 0


def backfill_from_file(db, target, path):
    """消费 MCP 取数落盘的 width / etf 数据。"""
    p = pathlib.Path(path)
    if not p.exists():
        print(f"  [width/etf] 未提供 {path}，跳过（须经 MCP mx_macro_data 取数）")
        return False
    payload = json.loads(p.read_text(encoding="utf-8"))
    w = payload.get("market_width")
    if w:
        db.save_market_width(target, w["up"], w["down"], w.get("flat", 0))
        print(f"  [宽度] up={w['up']} down={w['down']} flat={w.get('flat', 0)}")
    etf = payload.get("etf_flows")
    if etf:
        # save_etf_flows 是 5 元组，amount 由 signal 内「净流入 X 亿元」正则解析
        db.save_etf_flows(target, [(e["name"], e["code"], e["direction"],
                                   e["cls"], e["signal"]) for e in etf],
                          data_date=target)
    return True


def main():
    ap = argparse.ArgumentParser(description="补录历史交易日收盘数据")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--password", default="1q2w3e4r")
    ap.add_argument("--only", help="逗号分隔：index_quotes,daily_klines,width_etf")
    ap.add_argument("--file", help="width/etf 的 MCP 取数文件路径")
    args = ap.parse_args()
    only = set((args.only or "index_quotes,daily_klines,width_etf").split(","))

    cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent
                      / "config.json").read_text(encoding="utf-8"))
    codes = [c["code"] if isinstance(c, dict) else c
             for c in cfg.get("watchlist_stocks", [])]
    db = StockAgentDB(password=args.password)
    print(f"[补数] 目标日 {args.date}｜自选股 {len(codes)} 只")

    if "index_quotes" in only:
        print("  [指数]")
        backfill_index(db, args.date)
    if "daily_klines" in only:
        print("  [日K]")
        backfill_klines(db, args.date, codes)
    if "width_etf" in only:
        print("  [宽度/ETF]")
        f = args.file or f"data/derived/_backfill_{args.date.replace('-', '')}.json"
        backfill_from_file(db, args.date, f)


if __name__ == "__main__":
    main()