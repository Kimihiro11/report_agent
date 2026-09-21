#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向 news_intel 缓存注入中文摘要（对应 summary_mode=agent_inject 工作流）。

news_intel.py 抓取英文源后，若未配置外部 LLM，summary_zh / summary_structured 为空，
需由当前运行的模型（Agent）读完 `raw` 原文后写入。本工具把这一步规范化：
校验 schema、补默认值、只覆盖指定主题，其余字段原样保留。

用法
  python tools/inject_news_intel.py --date 2026-09-21 --content <summaries.json>
  python tools/inject_news_intel.py --date 2026-09-21 --check          # 仅列出各主题当前状态

content 文件格式（只需含要注入的主题）:
  {"oil": {"summary_zh": "...", "summary_structured": {SUMMARY_SCHEMA 各字段}},
   "geopolitics": {"summary_zh": "..."}}

SUMMARY_SCHEMA: direction / confidence / as_of / facts[] / core_conclusion /
                transmission[] / priced_in / watch[] / summary_zh
"""
import argparse
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
NEWS_DIR = BASE / "data" / "news_intel"

_FIELDS = ["direction", "confidence", "as_of", "facts", "core_conclusion",
           "transmission", "priced_in", "watch", "summary_zh"]
_LIST_FIELDS = {"facts": 3, "transmission": 3, "watch": 3}
_VALID_DIR = {"偏多", "偏空", "中性"}


def _norm(text: str) -> str:
    """清掉孤立 ** 与多余空白（注入文本必须与渲染层约定一致）。"""
    s = (text or "").strip()
    if s.count("**") % 2:
        s = s.replace("**", "")
    return s


def normalize(structured: dict, summary_zh: str) -> dict:
    out = {}
    for k in _FIELDS:
        v = (structured or {}).get(k)
        if k in _LIST_FIELDS:
            seq = [x for x in (v or []) if str(x).strip()][:_LIST_FIELDS[k]]
            out[k] = seq
        elif k == "confidence":
            try:
                out[k] = max(0.0, min(1.0, float(v)))
            except Exception:
                out[k] = 0.0
        else:
            out[k] = str(v or "").strip()
    if out["direction"] not in _VALID_DIR:
        out["direction"] = "中性"
    if not out["summary_zh"]:
        out["summary_zh"] = _norm(summary_zh)
    if not out["core_conclusion"]:
        out["core_conclusion"] = out["summary_zh"][:80]
    return out


def main():
    ap = argparse.ArgumentParser(description="news_intel 中文摘要注入")
    ap.add_argument("--date", required=True)
    ap.add_argument("--content", help="含目标主题摘要的 JSON 文件")
    ap.add_argument("--check", action="store_true", help="仅打印当前状态")
    args = ap.parse_args()

    path = NEWS_DIR / f"news_intel_{args.date.replace('-', '')}.json"
    if not path.exists():
        print(f"[错误] 缓存不存在：{path}（先运行 python news_intel.py --date {args.date}）")
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    topics = payload.get("topics") or {}

    if args.check or not args.content:
        print(f"[状态] {path.name}")
        for k, t in topics.items():
            zh = (t.get("summary_zh") or "").strip()
            ss = t.get("summary_structured") or {}
            flag = "已注入" if zh else "待注入"
            print(f"  {k:<12} {flag:<6} raw={len(t.get('raw') or []):<3} "
                  f"direction={ss.get('direction') or '—':<4} "
                  f"zh={len(zh)}字")
        return 0

    content = json.loads(pathlib.Path(args.content).read_text(encoding="utf-8"))
    if not isinstance(content, dict) or not content:
        print("[错误] content 文件需为非空对象：{主题: {summary_zh, summary_structured}}")
        return 1

    unknown = [k for k in content if k not in topics]
    if unknown:
        print(f"[错误] 未知主题 {unknown}；可选：{list(topics.keys())}")
        return 1

    for key, item in content.items():
        t = topics[key]
        t["summary_zh"] = _norm(item.get("summary_zh", ""))
        t["summary_structured"] = normalize(item.get("summary_structured") or {},
                                            item.get("summary_zh", ""))
        t["summary_mode"] = "agent_inject"
        print(f"  [注入] {key:<12} {t['summary_structured'].get('direction')} "
              f"zh={len(t['summary_zh'])}字 facts={len(t['summary_structured'].get('facts') or [])}")

    payload["topics"] = topics
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    json.loads(out)  # 写前校验
    path.write_text(out, encoding="utf-8")
    print(f"[完成] 已写回 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
