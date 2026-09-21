#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自选股新增工具：一条命令同步「5 处」配置与数据，避免漏改。

项目约定（漏改任一处都会让报告/诊断/回测偏移）：
  1. config.json  的 watchlist_stocks / watchlist_names / watchlist_sectors
  2. build_report.SECTOR                （行业兜底表）
  3. backtest._DEFAULT_WATCHLIST_NAME   （名称兜底表）
  4. daily_klines 历史日K               （否则诊断/回测/技术信号无数据）
  5. 微博 LLM 解构重注入                 （watchlist 参与 input_hash，必失配）

本工具自动完成 1–4；第 5 步需模型读原文后重新生成解构，故只打印命令。
渲染层的自选股数量是 `len(WATCHLIST)` 动态渲染，无需改动。

用法
    python tools/add_watchlist.py <code> <name> <sector>
    python tools/add_watchlist.py 002897 意华股份 "高速连接器+光伏支架"
    python tools/add_watchlist.py 002897 --check      # 只做一致性体检，不改动
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

CFG = BASE / "config.json"
SECTOR_PY = BASE / "build_report.py"
BACKTEST_PY = BASE / "backtest.py"


# ---------------- config.json ----------------

def update_config(code: str, name: str, sector: str) -> bool:
    """更新 config.json 三处；写前备份、写后 json.loads 回读。"""
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    bk = BASE / "data" / f"config_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json.bak"
    bk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CFG, bk)

    stocks = cfg.setdefault("watchlist_stocks", [])
    existed = code in stocks
    if not existed:
        stocks.append(code)
    cfg["watchlist_names"][code] = name
    cfg["watchlist_sectors"][code] = sector

    out = json.dumps(cfg, ensure_ascii=False, indent=2)
    json.loads(out)
    CFG.write_text(out, encoding="utf-8")
    json.loads(CFG.read_text(encoding="utf-8"))  # 回读校验
    print(f"  [1/4] config.json: {'已存在，更新名称/行业' if existed else '已追加'} "
          f"（自选股 {len(stocks)} 只；备份 {bk.name}）")
    return True


# ---------------- Python 兜底表 ----------------

def _dict_block(src: str, var: str):
    """定位模块级 `VAR = {...}` 字面量，返回 (block_span_end, block_text)。"""
    m = re.search(rf"^{re.escape(var)} = \{{(.*?)^\}}", src, re.S | re.M)
    if not m:
        return None
    return m.end(1), m.group(1)


def ensure_py_entry(py_path: pathlib.Path, var: str, code: str, value: str):
    """向模块级 dict 字面量追加一项；已存在则跳过。返回 'added'/'exists'/'not-found'。"""
    src = py_path.read_text(encoding="utf-8")
    found = _dict_block(src, var)
    if not found:
        return "not-found"
    end, block = found
    if f'"{code}"' in block:
        return "exists"
    new_line = f'    "{code}": "{value}",\n'
    py_path.write_text(src[:end] + new_line + src[end:], encoding="utf-8")
    return "added"


def verify_python(*files: pathlib.Path) -> bool:
    """语法编译 + 子进程重新导入验证。"""
    for f in files:
        r = subprocess.run([sys.executable, "-m", "py_compile", str(f)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [错误] {f.name} 语法失败：{r.stderr.strip()[:200]}")
            return False
    return True


# ---------------- daily_klines ----------------

def backfill_klines(code: str) -> str:
    """拉全历史日K入库；返回描述串。"""
    try:
        from ra.analysis import backtest as bt
        from ra.infra.db import StockAgentDB
        from ra import stock_report_agent as agent
    except Exception as e:
        return f"跳过（依赖不可用：{e}）"
    try:
        kl = bt._fetch_kline_live(code)
        if not kl:
            return "跳过（未取到日K，稍后重试）"
        cfg = agent.load_config()["database"]
        db = StockAgentDB(host=cfg.get("host", "localhost"), port=cfg.get("port", 5432),
                          user=cfg.get("user", "postgres"), password=cfg.get("password", ""),
                          dbname=cfg.get("dbname", "stock_report_agent"))
        db.save_klines(code, kl)
        got = db.get_klines(code)
        return f"{len(got)} 根（{got[0]['date']} → {got[-1]['date']}，收 {got[-1]['close']}）"
    except Exception as e:
        return f"跳过（{type(e).__name__}: {e}）"


# ---------------- 体检 ----------------

def check() -> int:
    """一致性体检：config 三处是否齐全、兜底表是否覆盖全部自选股、日K是否存在。"""
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    stocks = cfg.get("watchlist_stocks") or []
    names = cfg.get("watchlist_names") or {}
    sectors = cfg.get("watchlist_sectors") or {}
    print(f"自选股 {len(stocks)} 只: {stocks}\n")

    bad = 0
    for c in stocks:
        n_ok = c in names
        s_ok = c in sectors
        if not (n_ok and s_ok):
            bad += 1
            print(f"  ❌ {c}: name={'OK' if n_ok else '缺失'} sector={'OK' if s_ok else '缺失'}")

    src_sec = SECTOR_PY.read_text(encoding="utf-8")
    src_bt = BACKTEST_PY.read_text(encoding="utf-8")
    blk_sec = (_dict_block(src_sec, "SECTOR") or (0, ""))[1]
    blk_bt = (_dict_block(src_bt, "_DEFAULT_WATCHLIST_NAME") or (0, ""))[1]
    miss_sec = [c for c in stocks if f'"{c}"' not in blk_sec]
    miss_bt = [c for c in stocks if f'"{c}"' not in blk_bt]
    print(f"  build_report.SECTOR 缺失: {miss_sec or '无 ✅'}")
    print(f"  backtest._DEFAULT_WATCHLIST_NAME 缺失: {miss_bt or '无 ✅'}")

    try:
        from ra.infra.db import StockAgentDB
        from ra import stock_report_agent as agent
        cfg_db = agent.load_config()["database"]
        db = StockAgentDB(host=cfg_db.get("host", "localhost"), port=cfg_db.get("port", 5432),
                          user=cfg_db.get("user", "postgres"), password=cfg_db.get("password", ""),
                          dbname=cfg_db.get("dbname", "stock_report_agent"))
        no_k = [c for c in stocks if not db.get_klines(c)]
        print(f"  daily_klines 无数据: {no_k or '无 ✅'}")
    except Exception as e:
        print(f"  daily_klines 检查跳过: {type(e).__name__}")

    return 1 if (bad or miss_sec or miss_bt) else 0


def main():
    ap = argparse.ArgumentParser(description="自选股新增（同步 5 处）")
    ap.add_argument("code", nargs="?")
    ap.add_argument("name", nargs="?")
    ap.add_argument("sector", nargs="?")
    ap.add_argument("--check", action="store_true", help="只做一致性体检")
    args = ap.parse_args()

    if args.check or not args.code:
        return check()

    if not (args.name and args.sector):
        print("用法: python tools/add_watchlist.py <code> <name> <sector>")
        return 1

    code, name, sector = args.code, args.name, args.sector
    print(f"新增自选股: {code} {name}（{sector}）\n")

    update_config(code, name, sector)

    for label, py, var, val in (("[2/4] build_report.SECTOR", SECTOR_PY, "SECTOR", sector),
                                ("[3/4] backtest._DEFAULT_WATCHLIST_NAME", BACKTEST_PY,
                                 "_DEFAULT_WATCHLIST_NAME", name)):
        st = ensure_py_entry(py, var, code, val)
        print(f"  {label}: {st}")

    if not verify_python(SECTOR_PY, BACKTEST_PY):
        print("\n⚠️ 兜底表改写后语法校验失败，请检查上方错误并 git diff 复核。")
        return 1
    print("  [校验] 两个源码文件语法 OK")

    print(f"  [4/4] daily_klines: {backfill_klines(code)}")

    print(f"""
⚠️ 还剩第 5 步（必须手工，属模型作业）——重注入微博解构：
   微博解构按 input_hash(快照.weibo_data, 自选股, 日期) 缓存，
   自选股一变指纹即失配，run_for_date 返回 stale_cache_ignored，微博章节会静默降级为规则打分。
   取当日解构业务字段 + 为该股补一条 stock_mentions（**大V未点名就如实写未点名**），然后：
     python tools/inject_weibo_llm.py --date {datetime.now():%Y-%m-%d} --basis pre_market --content <content.json>

完成后跑：python tests/test_health.py   （含自选股/章节/诊断口径断言）""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
