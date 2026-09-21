#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微博 LLM 解构注入工具（收盘口径 / 自选股变更后复用）。

背景
----
`data/weibo_deep/weibo_deconstruct_<DATE8>.json` 按
`input_hash(快照.weibo_data, 自选股, 日期)` 缓存。以下两种情形指纹必然不符：

  1. 早报（pre_market 快照）已注入，晚报/周报用 close 快照；
  2. 自选股增减（watchlist 参与指纹计算）。

此时 `weibo_llm.run_for_date` 返回 `stale_cache_ignored`（设计如此，防止旧解构污染新报告），
微博章节会**静默降级为规则打分**——报告仍能出，但唐史深度解构、个股提及等消失。

本工具按目标快照重算指纹，把 agent（模型）产出的解构写入当日文件，使报告命中 `cached`。

用法
----
    # 注入（meta 由本工具补齐：date/as_of/method/generated_at/input_hash）
    python tools/inject_weibo_llm.py --date 2026-09-18 --content content.json

    # 指定快照口径（默认优先 basis=close）
    python tools/inject_weibo_llm.py --date 2026-09-18 --basis pre_market --content c.json

    # 只复核当前指纹是否命中（不写文件）
    python tools/inject_weibo_llm.py --date 2026-09-18 --check

content.json 只需业务字段：tangshi_deep / consensus / stock_mentions / key_points / risks。
"""
import argparse
import datetime
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from ra import stock_report_agent as agent          # noqa: E402
from ra.analysis import weibo_llm                            # noqa: E402

SNAP_DIR = BASE / "data" / "snapshots"


def pick_snapshot(date8: str, basis: str = "close"):
    """按运行日挑选快照；basis 优先匹配，缺失则回退到最后一份。"""
    cands = sorted(SNAP_DIR.glob(f"fetched_{date8}_*.json"))
    if not cands:
        return None
    if basis:
        for p in reversed(cands):
            try:
                meta = (json.loads(p.read_text(encoding="utf-8")).get("meta") or {})
            except (OSError, ValueError):
                continue
            if meta.get("basis") == basis:
                return p
    return cands[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description="微博 LLM 解构注入")
    ap.add_argument("--date", required=True, help="报告日期 YYYY-MM-DD")
    ap.add_argument("--content", help="解构内容 JSON 路径（业务字段）")
    ap.add_argument("--basis", default="close", help="快照口径：close/pre_market/intraday（默认 close）")
    ap.add_argument("--method", default="agent_inject (builtin)", help="写入的 method 标记")
    ap.add_argument("--check", action="store_true", help="仅复核指纹命中，不写文件")
    args = ap.parse_args()

    today = args.date
    date8 = today.replace("-", "")

    cfg = agent.load_config()
    watchlist = cfg.get("watchlist_stocks", []) or []
    watch_names = {c: (cfg.get("watchlist_names") or {}).get(c, c) for c in watchlist}
    print(f"[自选股] {len(watchlist)} 只: {watchlist}")

    snap = pick_snapshot(date8, args.basis)
    if snap is None:
        print(f"[错误] 未找到 {date8} 的快照（{SNAP_DIR}）")
        return 2
    snap_data = json.loads(snap.read_text(encoding="utf-8"))
    weibo_data = snap_data.get("weibo_data") or {}
    meta = snap_data.get("meta") or {}
    print(f"[快照] {snap.name} | basis={meta.get('basis')} data_date={meta.get('data_date')}")

    digest = weibo_llm.input_hash(weibo_data, watch_names, today)
    print(f"[指纹] input_hash = {digest}")

    if args.check:
        cur = weibo_llm.load_llm(date8, watch_names, digest)
        if cur:
            print("[复核] 当前文件指纹命中 ✅")
            print(f"        key_points={len(cur.get('key_points') or [])} "
                  f"risks={len(cur.get('risks') or [])} "
                  f"mentions={len(cur.get('stock_mentions') or [])} "
                  f"consensus={cur.get('consensus', {}).get('direction')}")
            return 0
        print("[复核] 指纹不命中 ❌ —— 需重新注入")
        return 1

    if not args.content:
        print("[错误] 缺少 --content（或改用 --check）")
        return 2
    content_path = pathlib.Path(args.content)
    if not content_path.is_absolute():
        content_path = (BASE / content_path).resolve()
    obj = json.loads(content_path.read_text(encoding="utf-8"))

    obj.update({
        "date": date8,
        "as_of": today,
        "method": args.method,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input_hash": digest,
    })
    path = weibo_llm.save_llm(date8, obj, watch_names)
    print(f"[写入] {path}")

    back = weibo_llm.load_llm(date8, watch_names, digest)
    ok = back is not None
    print(f"[复核] 指纹命中 = {ok}")
    if ok:
        print(f"[复核] key_points={len(back.get('key_points') or [])} "
              f"risks={len(back.get('risks') or [])} "
              f"mentions={len(back.get('stock_mentions') or [])} "
              f"consensus={back.get('consensus', {}).get('direction')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
