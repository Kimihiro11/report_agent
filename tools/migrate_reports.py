#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填 daily_reports 的报告类型 / 口径日 / 可读摘要，并标记重复版本。

为什么需要：
    站点要按「早报 / 晚报 / 周报」筛选，但历史上 `report_type` 不存在 ——
    类型被塞进 `market_state`（库内出现 盘前102 / 周报13 / 盘后7 / bullish2 混排），
    且每次重跑都 INSERT 一行（128 行只对应 58 个日期）。

做法（幂等，可重复跑）：
    1. ALTER 补列（复用 db.ensure_extras）
    2. 从 title / market_state 推断 report_type
    3. 从 HTML 的 .cc-body 提取 core_summary
    4. 同 (report_date, report_type) 只保留 created_at 最新一条，其余标 is_superseded
       —— **只标记不删除**（删数据不可逆，且回测 seed 可能引用）

用法：
    python tools/migrate_reports.py --dry-run
    python tools/migrate_reports.py
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _infer_type(title: str, market_state: str) -> str:
    """推断报告类型。title 优先（含「盘前版/收盘版/周报」），market_state 兜底。"""
    t = title or ""
    m = (market_state or "").strip()
    if "周报" in t or m == "周报":
        return "周报"
    if "收盘版" in t or m in ("盘后", "收盘"):
        return "晚报"
    if m == "盘中" or "盘中版" in t:
        return "盘中"
    return "早报"


def _extract_summary(html: str, limit: int = 180) -> str:
    """从 .cc-body 抽核心结论首段（纯文本）。取不到返回空串，不编造。"""
    if not html:
        return ""
    m = re.search(r'<div class="cc-body"[^>]*>(.*?)</div>', html, re.S)
    if not m:
        return ""
    txt = re.sub(r"<[^>]+>", "", m.group(1))
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def _extract_data_date(html: str):
    """从 HTML 里找行情锚定日（「以上一交易日收盘为基准」附近的日期），失败返回 None。"""
    if not html:
        return None
    m = re.search(r"基准[:：]?\s*(20\d{2}-\d{2}-\d{2})", html)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description="回填 daily_reports 的类型/摘要/去重标记")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    cfg_p = ROOT / "config.json"
    pwd = ""
    if cfg_p.exists():
        try:
            pwd = (json.loads(cfg_p.read_text(encoding="utf-8")).get("database") or {}).get("password", "")
        except Exception:  # noqa: BLE001
            pwd = ""

    from ra.infra.db import StockAgentDB
    db = StockAgentDB(password=pwd)
    if not args.dry_run:
        db.ensure_extras()

    rows = []
    with db._cursor() as cur:
        cur.execute("""SELECT id, report_date, market_state, html_content, created_at
                       FROM daily_reports ORDER BY report_date, created_at""")
        rows = cur.fetchall()

    print(f"待处理 {len(rows)} 行")

    # 1) 推断类型 + 摘要
    stat, plan = {}, []
    for rid, d, mstate, html, ts in rows:
        title = ""
        mt = re.search(r"<title>(.*?)</title>", html or "", re.S)
        if mt:
            title = mt.group(1).strip()
        rtype = _infer_type(title, mstate)
        summary = _extract_summary(html)
        ddate = _extract_data_date(html)
        stat[rtype] = stat.get(rtype, 0) + 1
        plan.append({"id": rid, "date": d, "type": rtype, "mstate": mstate,
                     "title": title, "summary": summary, "data_date": ddate, "ts": ts})

    print("类型分布:", stat)
    missing = sum(1 for x in plan if not x["summary"])
    print(f"摘要提取失败: {missing} 行（老报告无 .cc-body 时正常）")

    # 2) 同 (date, type) 只留 created_at 最新
    keep, drop = {}, []
    for x in plan:
        key = (x["date"], x["type"])
        if key not in keep or x["ts"] > keep[key]["ts"]:
            if key in keep:
                drop.append(keep[key])
            keep[key] = x
        else:
            drop.append(x)
    print(f"唯一 (日期,类型) 组合 {len(keep)} 个，重复待标记 {len(drop)} 行")

    if args.dry_run:
        print("\n[dry-run] 前 5 个保留项:")
        for k in sorted(keep)[:5]:
            x = keep[k]
            print(f"  {x['date']} {x['type']:<3} | {x['mstate']:<8} | {(x['summary'] or '(无摘要)')[:56]}")
        print(f"[dry-run] 未写入数据库")
        return

    with db._cursor() as cur:
        for x in keep.values():
            cur.execute("""UPDATE daily_reports
                           SET report_type=%s, core_summary=%s, data_date=COALESCE(%s, data_date),
                               is_superseded=FALSE
                           WHERE id=%s""",
                        (x["type"], x["summary"] or None, x["data_date"], x["id"]))
        for x in drop:
            cur.execute("UPDATE daily_reports SET report_type=%s, is_superseded=TRUE WHERE id=%s",
                        (x["type"], x["id"]))
    print(f"✅ 已更新 {len(keep)} 行主版本 + 标记 {len(drop)} 行重复")

    with db._cursor() as cur:
        cur.execute("""SELECT report_type, COUNT(*) FILTER (WHERE NOT is_superseded) AS live,
                              COUNT(*) AS total
                       FROM daily_reports GROUP BY report_type ORDER BY live DESC""")
        print("\n类型 | 有效 | 全部")
        for r in cur.fetchall():
            print(f"  {r[0] or '(空)':<4} | {r[1]:>4} | {r[2]:>4}")


if __name__ == "__main__":
    main()
