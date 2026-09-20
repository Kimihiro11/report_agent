#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中美动量对照数据（momentum）。

标的
----
美国侧：**安硕 MSCI 美国动量因素 ETF（MTUM，iShares MSCI USA Momentum Factor）**
        跟踪 MSCI USA Momentum SR Variant Index，持仓科技占比约 57%、半导体约 26%、存储约 24%，
        实际是美国科技动量的代理指标。
中国侧：科技动量代表 —— 科创50、创业板指、芯片ETF(159995)、半导体设备ETF(159516)。

指标
----
最新值、当日涨跌、近 5/20/60 交易日动量（区间涨跌幅）、距 52 周高点回撤。

用法
----
    python momentum.py --date 2026-09-18           # 抓取并写 data/momentum/momentum_<DATE8>.json
    python momentum.py --date 2026-09-18 --show    # 抓取后打印汇总

产物由 build_report 的「中美动量对照」章节消费；抓取失败时报告渲染占位，绝不写死假数。
"""
import argparse
import json
import pathlib
import re
import urllib.request
from datetime import datetime

BASE = pathlib.Path(__file__).parent
OUT_DIR = BASE / "data" / "momentum"

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

# 美国侧：新浪实时代码 + 东财历史 secid（BATS 上市 → 107 市场）
US_TARGETS = [
    {"code": "MTUM", "name": "安硕 MSCI 美国动量因素 ETF", "sina": "gb_mtum", "em": "107.MTUM",
     "note": "美国动量因子 · 科技占比约 57%"},
]

# 中国侧：新浪 K 线 symbol
# ⚠️ 用指数口径，不用 ETF —— ETF 存在份额拆分/大比例分红，新浪返回未复权价，
#    长周期动量会严重失真（实测芯片ETF 159995 于 2026-07-07 拆分 -50.1%，60 日动量算成 -63%）。
CN_TARGETS = [
    {"code": "000688", "name": "科创50", "kline": "sh000688", "tag": "硬科技"},
    {"code": "000685", "name": "科创芯片", "kline": "sh000685", "tag": "半导体"},
    {"code": "399995", "name": "国证芯片", "kline": "sz399995", "tag": "芯片产业链"},
    {"code": "399006", "name": "创业板指", "kline": "sz399006", "tag": "成长风格"},
    {"code": "000300", "name": "沪深300", "kline": "sh000300", "tag": "宽基对照"},
]


def _get(url: str, encoding: str = "utf-8", timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, errors="replace")


def _pct(cur: float, prev: float):
    if not prev:
        return None
    return round((cur / prev - 1) * 100, 2)


def _indicators(rows):
    """rows: [(date, close, high)] 升序 → 动量指标。"""
    if len(rows) < 2:
        return None
    closes = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    last = closes[-1]

    def mom(n):
        return _pct(last, closes[-1 - n]) if len(closes) > n else None

    high52 = max(highs[-250:]) if highs else None
    return {
        "last": round(last, 3),
        "chg": _pct(last, closes[-2]),
        "m5": mom(5),
        "m20": mom(20),
        "m60": mom(60),
        "high52": round(high52, 3) if high52 else None,
        "from_high": _pct(last, high52) if high52 else None,
        "last_date": rows[-1][0],
    }


def fetch_cn_kline(symbol: str, datalen: int = 260):
    """A 股/ETF 日K（新浪），返回 [(date, close, high)] 升序。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}")
    raw = json.loads(_get(url))
    if not raw:
        return []
    return [(k["day"], float(k["close"]), float(k["high"])) for k in raw]


def fetch_us_kline(symbol: str, secid: str = ""):
    """美股/ETF 日K，返回 [(date, close, high)] 升序。

    新浪美股 `US_MinKService.getDailyK` 为主源（含 2015 年以来全历史，稳定），
    东财 push2his 兜底（对美股 ETF 偶发限流：「Remote end closed connection」）。
    """
    try:
        url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
               f"var%20_M=/US_MinKService.getDailyK?symbol={symbol}&___qn=3")
        text = _get(url)
        m = re.search(r"var _M=\((\[.*?\])\)", text, re.S)
        if m:
            raw = json.loads(m.group(1))
            rows = [(r["d"], float(r["c"]), float(r["h"])) for r in raw if r.get("c")]
            if rows:
                return rows
    except Exception as e:
        print(f"  [提示] 新浪美股K线 {symbol} 失败: {e}")

    if not secid:
        return []
    end = datetime.now().strftime("%Y-%m-%d")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&fields1=f1&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1&beg=2025-09-01&end={end}")
    data = (json.loads(_get(url)) or {}).get("data") or {}
    return [(p[0], float(p[2]), float(p[3]))
            for p in (line.split(",") for line in (data.get("klines") or []))]


def fetch_us_spot(sina_code: str):
    """美股实时快照（新浪）：现价/涨跌/52周高低。"""
    text = _get(f"https://hq.sinajs.cn/list={sina_code}", encoding="gbk")
    m = re.search(r'var hq_str_%s="([^"]*)"' % re.escape(sina_code), text)
    if not m:
        return {}
    p = m.group(1).split(",")
    if len(p) < 10:
        return {}
    def f(i):
        try:
            return float(p[i])
        except (ValueError, IndexError):
            return None
    return {"name": p[0], "price": f(1), "pct": f(2), "high52": f(8), "low52": f(9)}


CHART_DAYS = 60  # 折线图窗口（A 股交易日）


def _build_series(items, days: int = CHART_DAYS):
    """构建归一化走势序列（供报告折线图使用）。

    items: [(code, name, side, rows)]，rows=[(date, close, high)] 升序。
    以**中国侧首个有数据的标的**的最近 days 个交易日为 X 轴（报告以 A 股日历为准），
    其余标的按日期前向填充（中美交易日不同，美股节假日缺值沿用前值）；
    数值为相对起点的累计涨跌幅（%），便于量纲不同的标的（MTUM ~300 / 科创50 ~1600）同图对比。
    """
    base = next((r for r in items if r[2] == "cn" and r[3]), None) or next((r for r in items if r[3]), None)
    if not base or len(base[3]) < 2:
        return None
    axis = [d for d, _, _ in base[3][-days:]]
    if len(axis) < 2:
        return None

    lines = []
    for code, name, side, rows in items:
        if not rows:
            continue
        close_by_date = {d: c for d, c, _ in rows}
        filled, last = [], None
        for d in axis:
            if d in close_by_date:
                last = close_by_date[d]
            filled.append(last)
        first_known = next((v for v in filled if v), None)
        if not first_known:
            continue
        filled = [v if v else first_known for v in filled]  # 头部回填
        vals = [round((v / first_known - 1) * 100, 2) for v in filled]
        lines.append({"code": code, "name": name, "side": side, "values": vals})

    if not lines:
        return None
    return {"dates": axis, "lines": lines}


def collect(date_str: str | None = None):
    """抓取中美两侧动量数据。返回 dict。"""
    out = {"date": date_str or datetime.now().strftime("%Y-%m-%d"),
           "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "us": [], "cn": [], "errors": []}
    series_input = []

    for t in US_TARGETS:
        rec = {"code": t["code"], "name": t["name"], "note": t.get("note", "")}
        ind = None
        rows = []
        # 东财 push2his 对美股 ETF 偶发限流，重试 3 次
        for attempt in range(3):
            try:
                rows = fetch_us_kline(t["code"], t.get("em", ""))
                ind = _indicators(rows)
                if ind:
                    break
            except Exception as e:
                if attempt == 2:
                    out["errors"].append(f"US {t['code']} 历史K线失败: {e}")
                    print(f"  [警告] US {t['code']} 历史K线失败: {e}")
                import time as _t
                _t.sleep(0.8 * (attempt + 1))
        if ind:
            rec.update(ind)
        # 实时兜底：至少拿到现价/当日/52周高低（新浪美股）
        try:
            spot = fetch_us_spot(t["sina"])
            if spot:
                rec["spot_price"] = spot.get("price")
                rec["spot_pct"] = spot.get("pct")
                if spot.get("high52"):
                    rec["high52"] = spot["high52"]
                    if spot.get("price"):
                        rec["from_high"] = _pct(spot["price"], spot["high52"])
                if not rec.get("last"):
                    rec["last"] = spot.get("price")
                    rec["chg"] = spot.get("pct")
        except Exception as e:
            out["errors"].append(f"US {t['code']} 实时失败: {e}")
        if rec.get("last"):
            out["us"].append(rec)
        else:
            out["errors"].append(f"US {t['code']}: 无数据")
        if rows:
            series_input.append((t["code"], t["name"], "us", rows))

    for t in CN_TARGETS:
        rec = {"code": t["code"], "name": t["name"]}
        try:
            rows = fetch_cn_kline(t["kline"])
            ind = _indicators(rows)
            if ind:
                rec.update(ind)
                out["cn"].append(rec)
            else:
                out["errors"].append(f"CN {t['name']}: 无数据")
            if rows:
                series_input.append((t["code"], t["name"], "cn", rows))
        except Exception as e:
            out["errors"].append(f"CN {t['name']}: {e}")
            print(f"  [警告] CN {t['name']} 抓取失败: {e}")

    out["series"] = _build_series(series_input)
    return out


def save(data: dict, date_str: str | None = None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    d8 = (date_str or data["date"]).replace("-", "")
    path = OUT_DIR / f"momentum_{d8}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(date_str: str):
    d8 = date_str.replace("-", "")
    path = OUT_DIR / f"momentum_{d8}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="中美动量对照数据")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--show", action="store_true", help="打印汇总")
    args = ap.parse_args()

    print(f"[动量] 抓取中美动量数据（{args.date}）...")
    data = collect(args.date)
    path = save(data, args.date)
    print(f"[动量] 已写入 {path}")
    print(f"[动量] 美国 {len(data['us'])} 条 / 中国 {len(data['cn'])} 条"
          + (f" / 错误 {len(data['errors'])} 条" if data["errors"] else ""))

    if args.show:
        for label, rows in (("美国侧", data["us"]), ("中国侧", data["cn"])):
            print(f"\n--- {label} ---")
            for r in rows:
                print(f"  {r['name']:<32} 最新 {r.get('last')} | 当日 {r.get('chg')}% | "
                      f"5日 {r.get('m5')}% | 20日 {r.get('m20')}% | 60日 {r.get('m60')}% | "
                      f"距52周高 {r.get('from_high')}%")
        if data["errors"]:
            print("\n[错误]", data["errors"])


if __name__ == "__main__":
    main()
