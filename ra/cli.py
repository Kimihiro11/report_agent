#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 CLI 入口 —— 全项目唯一命令入口。

替代原先「根目录 19 个同名兼容壳」的做法：只保留一个 `cli.py`，
其余能力全部收为子命令。历史脚本名仍可作为**别名**使用，便于平滑迁移。

用法
    python cli.py <子命令> [参数...]
    python cli.py list                # 列出全部子命令
    python cli.py <子命令> --help      # 透传给原模块的 argparse

例
    python cli.py collect                                  # 采集 → 快照 → 入库
    python cli.py report --date 2026-09-21 --type 早报      # 生成日报
    python cli.py backtest --seed                          # 回测（仅用户要求时）
    python cli.py oil --date 2026-09-21 --show             # 原油
    python cli.py upload <html 绝对路径>                    # 上传资料库（需 token 走 stdin）
    python cli.py watchlist --check                        # 自选股一致性体检

迁移对照（旧写法 → 新写法；旧脚本名仍可作为子命令别名）
    python stock_report_agent.py   →  python cli.py collect
    python build_report.py         →  python cli.py report
    python backtest.py             →  python cli.py backtest
    python news_intel.py           →  python cli.py news
    python chan_analysis.py        →  python cli.py chan
    python chan_report.py          →  python cli.py chan-report
    python arr_report.py           →  python cli.py arr
    python focus_monitor.py        →  python cli.py focus
    python momentum.py / oil.py    →  python cli.py momentum / oil
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 子命令表：名字 → (实现文件相对路径, 说明)
# 说明写清「做什么 / 何时用」，list 直接可读。
COMMANDS: dict[str, tuple[str, str]] = {
    # ---- 主流程 ----
    "collect":     ("ra/stock_report_agent.py",     "采集行情/舆情/衍生数据 → 快照 → 入库（数据引擎）"),
    "report":      ("ra/report/build_report.py",    "生成日报：--date YYYY-MM-DD --type 早报|晚报|周报"),
    "backtest":    ("ra/analysis/backtest.py",      "回测：--seed 解析报告写种子 / --run 全量回测出报告"),
    # ---- 数据源 ----
    "news":        ("ra/sources/news_intel.py",     "外网资讯抓取+正文解析 --date YYYY-MM-DD"),
    "oil":         ("ra/sources/oil.py",            "原油 WTI/Brent 价格与舆情 --date [--show]"),
    "momentum":    ("ra/sources/momentum.py",       "中美动量对照（MTUM × 中国科技）--date [--show]"),
    # ---- 分析 ----
    "focus":       ("ra/analysis/focus_monitor.py", "限时关注：日银加息程度研判 [--no-fetch]"),
    "chan":        ("ra/analysis/chan_analysis.py", "缠论分析：<指数> [30分钟根数] [日线根数] [--from auto]"),
    "peak":        ("ra/analysis/peak_detector.py", "见顶/技术诊断引擎（单标的调试）"),
    "weibo":       ("ra/analysis/weibo_llm.py",     "微博解构缓存自检"),
    # ---- 报告 / 专题 ----
    "chan-report": ("ra/report/chan_report.py",     "缠论推演专题 --date [--run]"),
    "arr":         ("ra/report/arr_report.py",      "中美 AI 资本开支与 ARR 专题 --date"),
    # ---- 基础设施自检 ----
    "charts":      ("ra/infra/charts.py",           "通用图表组件自检（渲染 demo SVG）"),
    "db":          ("ra/infra/db.py",               "数据库连通性与建表自检"),
    # ---- 运维 ----
    "watchlist":   ("tools/add_watchlist.py",       "自选股新增/体检：<code> <name> <sector> 或 --check"),
    "upload":      ("tools/upload_report.py",       "上传报告到资料库（同名 UPDATE 覆盖；token 走 stdin）"),
    "archive-dups": ("tools/archive_duplicates.py", "资料库同名重复批量归档 [--dry-run]"),
    "inject-weibo": ("tools/inject_weibo_llm.py",   "注入微博解构（按目标快照重算 input_hash）"),
    "inject-news": ("tools/inject_news_intel.py",   "注入资讯中文摘要（agent_inject）"),
    "close-snapshot": ("tools/build_close_snapshot.py", "补建收盘口径快照"),
    "cookies":     ("tools/read_edge_cookies.py",   "从 Edge/Chrome 提取微博 cookie（须先退出浏览器）"),
    "ingest-reports": ("tools/ingest_reports.py",   "历史报告入库（归档用途）"),
    "verify-ingest": ("tools/verify_ingest.py",     "入库结果校验（归档用途）"),
}

# 历史脚本名 → 子命令（平滑迁移：旧写法仍可用）
ALIASES = {
    "stock_report_agent": "collect",
    "build_report": "report",
    "backtest": "backtest",
    "news_intel": "news",
    "oil": "oil",
    "momentum": "momentum",
    "focus_monitor": "focus",
    "chan_analysis": "chan",
    "chan_report": "chan-report",
    "arr_report": "arr",
    "peak_detector": "peak",
    "weibo_llm": "weibo",
    "charts": "charts",
    "db": "db",
    "add_watchlist": "watchlist",
    "upload_report": "upload",
    "archive_duplicates": "archive-dups",
    "inject_weibo_llm": "inject-weibo",
    "inject_news_intel": "inject-news",
    "build_close_snapshot": "close-snapshot",
    "read_edge_cookies": "cookies",
    "ingest_reports": "ingest-reports",
    "verify_ingest": "verify-ingest",
}


def _print_list() -> None:
    print("用法: python cli.py <子命令> [参数...]\n")
    width = max(len(k) for k in COMMANDS)
    groups = [
        ("主流程", ["collect", "report", "backtest"]),
        ("数据源", ["news", "oil", "momentum"]),
        ("分析", ["focus", "chan", "peak", "weibo"]),
        ("报告/专题", ["chan-report", "arr"]),
        ("自检", ["charts", "db"]),
        ("运维", ["watchlist", "upload", "archive-dups", "inject-weibo",
                  "inject-news", "close-snapshot", "cookies",
                  "ingest-reports", "verify-ingest"]),
    ]
    for title, keys in groups:
        print(f"[{title}]")
        for k in keys:
            rel, desc = COMMANDS[k]
            print(f"  {k:<{width}}  {desc}")
        print()
    print("旧脚本名仍可用作别名，例：python cli.py build_report --date 2026-09-21 --type 早报")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help", "list"):
        _print_list()
        return 0

    name = argv[0]
    rest = argv[1:]
    name = ALIASES.get(name, name)

    if name not in COMMANDS:
        print(f"[错误] 未知子命令: {argv[0]}\n", file=sys.stderr)
        _print_list()
        return 2

    rel, _desc = COMMANDS[name]
    target = ROOT / rel
    if not target.exists():
        print(f"[错误] 实现文件不存在: {rel}", file=sys.stderr)
        return 1

    # 用 runpy 而非 import + main()：部分模块只有 if __name__ == "__main__" 块、没有 main()
    # sys.argv 透传，故各模块自带的 argparse / sys.argv 判断照常工作。
    sys.argv = [rel] + rest
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    except KeyboardInterrupt:
        print("\n[中断] 用户中止", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
