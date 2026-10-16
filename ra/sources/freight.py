#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原油海运运费跟踪（VLCC 中东湾→中国宁波）。

数据源：上海航运交易所（SSE）
  - CTFI：CTFI 中东湾拉斯坦努拉—中国宁波（VLCC / 270000MT），单位 **WS 点**
         —— 即用户所称「CT1 原油海运运费」；⚠️ 官方单位是 WS（Worldscale），
            **不是美元/桶**，两者不可混用（换算公式：美元/桶 = WS/100 × 基准费率，
            基准费率由当月 WA(S) 与 TC1 决定，本项目不做该换算，避免写出估算值冒充实际）。
  - BDTI：原油运输指数 BDTI，单位指数点（覆盖全部油轮航线，作为 CTFI 的广度对照）。

取数路径：⚠️ **脚本通道 `stock_report_agent._mx_query` 问不出本指标**
（实测 CTFI 问成 23赣交01、BDTI 问成 INE 原油期货），
**必须走妙想 MCP `mx_macro_data`** 通道取数后落 `seeds/oil_freight.json`。
本模块只读 seed + 做指标计算与渲染，**不联网、不写死任何数值**。

用法
  python cli.py freight --show      # 打印摘要
"""
from __future__ import annotations

import argparse
import json
import pathlib

from ra.paths import ROOT as BASE_DIR  # 包化后统一根路径

SEED = BASE_DIR / "seeds" / "oil_freight.json"

# 关键拐点：与 2026 年实际走势对照，用于生成「阶段高点/低点」提示
# 每项=(日期, 说明)。日期必须在 seed 序列里存在，否则该拐点被跳过（绝不编造）。
STAGES = [
    ("2026-01-05", "年内低位起点"),
    ("2026-03-13", "阶段高点（3 月反弹）"),
    ("2026-04-01", "3 月高位回落"),
    ("2026-07-01", "二季度低位区"),
    ("2026-09-01", "9 月加速上行起点"),
    ("2026-10-13", "年内高点"),
]


def load() -> dict | None:
    if not SEED.exists():
        return None
    try:
        return json.loads(SEED.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pct(a, b):
    """变化百分比（b 相对 a）。"""
    try:
        return (b / a - 1.0) * 100.0
    except Exception:
        return None


def metrics(series: list, key: str) -> dict | None:
    """由序列算最新值/年内高低/涨幅/拐点。series=[{date, key}] 升序。"""
    pts = [(s["date"], float(s[key])) for s in series if s.get(key) is not None]
    if len(pts) < 2:
        return None
    d0, v0 = pts[0]
    d1, v1 = pts[-1]
    hi_d, hi_v = max(pts, key=lambda x: x[1])
    lo_d, lo_v = min(pts, key=lambda x: x[1])
    # 年内高点的位置（是否已从高点回落）
    peak_i = [d for d, _ in pts].index(hi_d)
    off_peak = _pct(hi_v, v1)
    out = {
        "first": {"date": d0, "value": v0},
        "last": {"date": d1, "value": v1},
        "high": {"date": hi_d, "value": hi_v},
        "low": {"date": lo_d, "value": lo_v},
        "ytd_pct": _pct(v0, v1),
        "off_peak_pct": off_peak,
        "off_peak_days": len(pts) - 1 - peak_i,
        "bars": len(pts),
        "peak_is_latest": hi_d == d1,
    }
    mk = {}
    for d, label in STAGES:
        v = next((val for dd, val in pts if dd == d), None)
        if v is not None:
            mk[d] = {"label": label, "value": v}
    out["stages"] = mk
    return out


def snapshot(date_str: str | None = None) -> dict | None:
    """产出运费快照：只含截至 date_str 的数据（date_str 为空取全部）。"""
    d = load()
    if not d:
        return None
    ctfi = d.get("ctfi_series") or []
    bdti = d.get("bdti_series") or []

    def _cut(rows):
        if not date_str:
            return rows
        return [r for r in rows if r["date"] <= date_str]

    ctfi_c, bdti_c = _cut(ctfi), _cut(bdti)
    out = {
        "date": date_str or "",
        "as_of": d.get("as_of", ""),
        "source": d.get("_source", ""),
        "ctfi_meta": (d.get("_field") or {}).get("ctfi", ""),
        "bdti_meta": (d.get("_field") or {}).get("bdti", ""),
        "unit_warning": "CTFI 官方单位为 WS（Worldscale）点，非美元/桶",
        "ctfi": metrics(ctfi_c, "ctfi"),
        "bdti": metrics(bdti_c, "bdti"),
        "ctfi_series": ctfi_c,
        "bdti_series": bdti_c,
        # 数据新鲜度：seed 最后一天 vs 报告日
        "fresh_days": _fresh(ctfi_c, date_str),
    }
    # BDTI 与 CTFI 是否同向（背离信号对判断运费见顶有交叉价值）
    cm, bm = out["ctfi"], out["bdti"]
    out["divergence"] = None
    if cm and bm and not cm["peak_is_latest"] and not bm["peak_is_latest"]:
        out["divergence"] = {
            "ctfi_off": cm["off_peak_pct"],
            "bdti_off": bm["off_peak_pct"],
            "same_direction": (cm["off_peak_pct"] > 0) == (bm["off_peak_pct"] > 0),
        }
    return out


def _fresh(series: list, date_str: str | None):
    if not series or not date_str:
        return None
    import datetime as _dt
    try:
        a = _dt.date.fromisoformat(series[-1]["date"])
        b = _dt.date.fromisoformat(date_str)
        return (b - a).days
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="原油海运运费（VLCC 中东→宁波）")
    ap.add_argument("--date", help="截至日期 YYYY-MM-DD（缺省全部）")
    ap.add_argument("--show", action="store_true", help="打印摘要")
    args = ap.parse_args()
    s = snapshot(args.date)
    if not s:
        print(f"[运费] 无数据（{SEED} 不存在或不可读）")
        return
    c, b = s["ctfi"], s["bdti"]
    print(f"[运费] 数据截至 {s['as_of']}｜{s['source']}")
    if c:
        print(f"  CTFI最新 {c['last']['value']} ({c['last']['date']})"
              f"｜年内高 {c['high']['value']} ({c['high']['date']})"
              f"｜距高点 {c['off_peak_pct']:+.2f}%"
              f"｜年内涨幅 {c['ytd_pct']:+.1f}%" if c["ytd_pct"] is not None else "")
    if b:
        print(f"  BDTI 最新 {b['last']['value']} ({b['last']['date']})"
              f"｜年内高 {b['high']['value']} ({b['high']['date']})"
              f"｜距高点 {b['off_peak_pct']:+.2f}%")
    if s.get("divergence"):
        d = s["divergence"]
        print(f"  双指数背离: 同向={d['same_direction']}")


if __name__ == "__main__":
    main()