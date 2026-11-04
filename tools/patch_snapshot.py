#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补采：把 collect 因预算耗尽而跳过的衍生段补进**当天快照**（2026-11-04 新增）。

背景：`collect` 的 `_budget_ok()` 有 15 个检查点、总预算 `BUDGET_SEC=180`。
宽度走妙想兜底、微博源慢时，后段（宏观数据/事件因子/技术分析/国家队/日本传导链）
会被 `[预算] 跳过` —— 表现为第九节自选股子卡缺技术指标、第八节共振缺块，
**但报告不报错**，只是渲染「数据缺失」，容易被当成数据源故障。

用法：
    python tools/patch_snapshot.py --date 2026-11-04                 # 补默认五项
    python tools/patch_snapshot.py --date 2026-11-04 --parts ta,japan # 只补指定项
    python tools/patch_snapshot.py --date 2026-11-04 --show           # 只看现状不写

实现约束（重要）：
  · **只写快照、不入库** —— `db.save_sentiment` 是 INSERT 无 UPSERT，
    补采重复入库会造重复舆情行；快照是渲染的唯一来源，够用。
  · 不碰 `meta`（`data_date` / `basis` / `as_of` 由 collect 锁定，改了就是口径事故）。
  · 伪源命名必须与 collect 完全一致（`[技术] <sym>`、`[国家队] ...`、`[日本] ...`），
    否则渲染层 `name.startswith("[")` 分流和入库分类都会失配。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ra.paths import ROOT  # noqa: E402
from ra import stock_report_agent as agent  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
ALL_PARTS = ("macro", "event", "ta", "national", "japan")


def pick_snapshot(date_yyyymmdd: str) -> Path:
    """取该采集日**最新**的快照（有 pre_market 时优先，报告正是读它）。"""
    files = sorted(SNAP_DIR.glob(f"fetched_{date_yyyymmdd}_*.json"))
    if not files:
        raise SystemExit(f"[错误] 没有 {date_yyyymmdd} 的快照，请先跑 collect")
    pre = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8")).get("meta", {})
        except Exception:
            continue
        if meta.get("basis") == "pre_market":
            pre.append(f)
    return (pre or files)[-1]


def main():
    ap = argparse.ArgumentParser(description="补采被预算跳过的衍生段到当天快照")
    ap.add_argument("--date", required=True, help="采集日 YYYY-MM-DD（=快照文件名日期）")
    ap.add_argument("--parts", default=",".join(ALL_PARTS),
                    help=f"要补的段，逗号分隔：{','.join(ALL_PARTS)}")
    ap.add_argument("--show", action="store_true", help="只打印现状，不写文件")
    args = ap.parse_args()

    parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    bad = [p for p in parts if p not in ALL_PARTS]
    if bad:
        raise SystemExit(f"[错误] 未知段：{bad}，可选 {ALL_PARTS}")

    path = pick_snapshot(args.date.replace("-", ""))
    snap = json.loads(path.read_text(encoding="utf-8"))
    meta = snap.get("meta", {})
    wb = snap.setdefault("weibo_data", {})
    cfg = agent.load_config()

    print(f"[快照] {path.name}")
    print(f"       data_date={meta.get('data_date')} basis={meta.get('basis')} as_of={meta.get('as_of')}")
    print(f"[前]   源数 {len(wb)}｜伪源 {sum(1 for k in wb if k.startswith('['))}")

    added = 0
    if args.show:
        for k in sorted(wb):
            print(f"       {'【伪源】' if k.startswith('[') else ''}{k}: {len(wb[k])} 条")
        return 0

    if "macro" in parts:
        d = agent.fetch_macro_data(cfg.get("macro_indicators", {})) or {}
        wb.update(d)
        added += len(d)
        print(f"  宏观数据  → {len(d)} 个源")

    if "event" in parts:
        d = agent.fetch_event_factors(cfg.get("event_factors", {})) or {}
        wb.update(d)
        added += len(d)
        print(f"  事件因子  → {len(d)} 个源")

    if "ta" in parts:
        ta = agent.fetch_technical_analysis(cfg.get("technical_analysis", {})) or {}
        for sym, k in ta.items():
            desc = (f"收盘{k['close']} MA5={k['ma5']} MA10={k['ma10']} MA20={k['ma20']} "
                    f"趋势:{k['trend']} 近5日高{k['high5']} 低{k['low5']}")
            wb[f"[技术] {sym}"] = [{"text": desc, "time": ""}]
        added += len(ta)
        print(f"  技术分析  → {len(ta)} 个标的")

    if "national" in parts:
        d = agent.fetch_national_team(cfg.get("national_team", {})) or {}
        wb.update(d)
        added += len(d)
        print(f"  国家队    → {len(d)} 个源")

    if "japan" in parts:
        d = agent.fetch_japan_carry(cfg.get("japan_carry", {})) or {}
        wb.update(d)
        added += len(d)
        print(f"  日本传导链 → {len(d)} 个源")

    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[后]   源数 {len(wb)}（新增 {added}）｜伪源 {sum(1 for k in wb if k.startswith('['))}")
    print("[提示] 只补快照、未入库（save_sentiment 无 UPSERT，重复入库会造重复行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
