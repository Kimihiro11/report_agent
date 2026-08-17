#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行个股见顶诊断，输出 HTML 片段到文件供晚报嵌入"""
import json, sys, os
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from stock_diagnosis import run_all, render_html

# 从 config.json 读取自选股
with open(BASE_DIR / "config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

watchlist = cfg.get("watchlist_stocks", [])
# 代码 -> 名称映射
name_map = {
    "688409": "富创精密", "301392": "汇成真空", "000725": "京东方A",
    "600580": "卧龙电驱", "688530": "欧莱新材", "600641": "先导基电", "688668": "鼎通科技",
}
items = [{"code": c, "name": name_map.get(c, "")} for c in watchlist]

print(f"开始诊断 {len(items)} 只自选股...")
results = run_all(items)

print(f"\n诊断完成: {len(results)}/{len(items)} 只成功")
for r in results:
    print(f"  {r['name']}({r['code']}) 评分{r['total_score']:.1f} {r['level_color']}{r['level']} {r['trend_color']}{r['trend_status']}")

html = render_html(results)

# 输出 HTML 片段
out_path = BASE_DIR / "data" / "diagnosis_fragment.html"
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nHTML片段已保存: {out_path}")

# 同时输出结构化 JSON 摘要
summary = []
for r in results:
    summary.append({
        "code": r.get("code"), "name": r.get("name"),
        "score": round(r["total_score"], 1),
        "level": r["level"], "level_color": r["level_color"],
        "trend": r["trend_status"], "trend_color": r["trend_color"],
        "dimensions": {k: round(v, 1) for k, v in r.get("dimensions", {}).items()},
    })
json_path = BASE_DIR / "data" / "diagnosis_summary.json"
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"摘要JSON已保存: {json_path}")
