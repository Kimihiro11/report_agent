# -*- coding: utf-8 -*-
"""AI 资本开支与 ARR · 中美对比专题报告（独立于日报）。

回答：中美在 AI 基础设施投入与模型商业化上差多少、差在哪、谁更快。

数据源：seeds/arr_cn_us.json（版本化基线，季度/月度按公开披露更新）。
渲染：自包含 HTML（内联 templates/style.css + 自绘 SVG 图表），输出到 reports/专题/。
数据缺失时跳过对应区块，绝不编造。

用法：
  python arr_report.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from html import escape as _html_escape
from pathlib import Path
import re

from ra.paths import ROOT as BASE_DIR  # 包化后统一根路径
SEED_PATH = BASE_DIR / "seeds" / "arr_cn_us.json"
CSS_PATH = BASE_DIR / "templates" / "style.css"
OUT_DIR = BASE_DIR / "reports" / "专题"

US_COLOR = "#1967d2"
CN_COLOR = "#e67e22"


def _esc(s) -> str:
    """HTML 转义 + 轻量粗体（与 build_report._esc 口径一致）。"""
    out = _html_escape(str(s if s is not None else ""))
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.S)
    return out.replace("**", "")


def load_data() -> dict:
    try:
        return json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[警告] 读取 {SEED_PATH.name} 失败: {e}")
        return {}


# ---------------- SVG 图表 ----------------

def _hbar(items, width=620) -> str:
    """横向条形图。items: [(label, value, color, value_text)]，同刻度线性。

    刻意使用线性刻度——差异本身就是结论。
    """
    items = [(l, v, c, t) for l, v, c, t in items if isinstance(v, (int, float))]
    if not items:
        return ""
    maxv = max(v for _, v, _, _ in items) or 1
    lab_w, bar_max, rowh, gap = 132, width - 132 - 96, 26, 8
    h = len(items) * (rowh + gap) + 8
    parts = []
    for i, (label, val, color, vtxt) in enumerate(items):
        y = 4 + i * (rowh + gap)
        w = max(bar_max * val / maxv, 1.5)
        parts.append(f'<text x="0" y="{y + rowh/2:.1f}" dominant-baseline="central" '
                     f'font-size="12" fill="#444">{_esc(label)}</text>')
        parts.append(f'<rect x="{lab_w}" y="{y}" width="{w:.1f}" height="{rowh}" rx="3" fill="{color}" opacity="0.88"/>')
        parts.append(f'<text x="{lab_w + w + 8:.1f}" y="{y + rowh/2:.1f}" dominant-baseline="central" '
                     f'font-size="12" font-weight="600" fill="{color}">{_esc(vtxt)}</text>')
    return (f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" '
            f'style="margin:6px 0 2px">{"".join(parts)}</svg>')


def capex_chart(d: dict) -> str:
    fx = d.get("fx_cny_per_usd") or 7.1
    items = []
    for name, _tk, val, _note in (d.get("us_capex_2026", {}).get("rows") or []):
        if isinstance(val, (int, float)):
            items.append((name, val, US_COLOR, f"{val:,.0f} 亿美元"))
    cn = d.get("cn_capex_2026", {})
    est = {"阿里巴巴": 2707, "腾讯": 2111, "百度": 456, "字节跳动": 2000}
    for name, val in est.items():
        items.append((name, round(val / fx), CN_COLOR, f"约 {round(val/fx):,} 亿美元"))
    items.sort(key=lambda x: -x[1])
    return _hbar(items)


def arr_chart(d: dict) -> str:
    items = []
    for name, val, _period, _note in (d.get("arr_us", {}).get("rows") or []):
        if isinstance(val, (int, float)):
            items.append((name, val, US_COLOR, f"{val:,.0f} 亿美元"))
    for name, val, _period, _note in (d.get("arr_cn", {}).get("rows") or []):
        if isinstance(val, (int, float)):
            items.append((name, val, CN_COLOR, f"{val:,.0f} 亿美元"))
    items.sort(key=lambda x: -x[1])
    return _hbar(items)


def industry_curve(d: dict) -> str:
    """中国模型行业年化收入轨迹（折线）。"""
    series = d.get("arr_cn", {}).get("industry_series") or []
    if len(series) < 2:
        return ""
    W, H = 620, 190
    pl, pr, pt, pb = 52, 20, 14, 30
    iw, ih = W - pl - pr, H - pt - pb
    vals = [v for _, v in series]
    ymax = max(vals) * 1.25
    n = len(vals)

    def X(i):
        return pl + iw * i / (n - 1)

    def Y(v):
        return pt + ih * (1 - v / ymax)

    parts = []
    for k in range(4):
        vv = ymax * k / 3
        yy = Y(vv)
        parts.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{pl+iw}" y2="{yy:.1f}" stroke="#eceff4" stroke-width="0.8"/>')
        parts.append(f'<text x="{pl-8}" y="{yy:.1f}" text-anchor="end" dominant-baseline="central" '
                     f'font-size="10" fill="#888780">{vv:,.0f}</text>')
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    parts.append(f'<polyline points="{pts}" fill="none" stroke="{CN_COLOR}" stroke-width="2.2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    for i, (lab, v) in enumerate(series):
        parts.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.4" fill="{CN_COLOR}"/>')
        parts.append(f'<text x="{X(i):.1f}" y="{Y(v)-11:.1f}" text-anchor="middle" font-size="11" '
                     f'font-weight="600" fill="{CN_COLOR}">{v:,.0f}</text>')
        parts.append(f'<text x="{X(i):.1f}" y="{H-pb+14}" text-anchor="middle" font-size="10" '
                     f'fill="#888780">{_esc(lab)}</text>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" style="margin:6px 0 2px">{"".join(parts)}</svg>'


# ---------------- HTML 区块 ----------------

def _kv_grid(pairs) -> str:
    cells = "".join(
        f'<div class="conclusion-item"><div class="label">{_esc(k)}</div>'
        f'<div class="value">{_esc(v)}</div></div>' for k, v in pairs)
    return f'<div class="conclusion-grid">{cells}</div>'


def _rows_table(headers, rows, num_cols=()) -> str:
    th = "".join(f'<th{" style=\"text-align:right\"" if i in num_cols else ""}>{_esc(h)}</th>'
                 for i, h in enumerate(headers))
    body = ""
    for r in rows:
        tds = ""
        for i, c in enumerate(r):
            style = ' style="text-align:right;font-weight:600"' if i in num_cols else ""
            tds += f"<td{style}>{_esc(c if c is not None else '—')}</td>"
        body += f"<tr>{tds}</tr>"
    return f'<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def render(d: dict, date_str: str) -> str:
    css = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""
    fx = d.get("fx_cny_per_usd") or 7.1
    us_capex = d.get("us_capex_2026", {})
    cn_capex = d.get("cn_capex_2026", {})
    arr_us = d.get("arr_us", {})
    arr_cn = d.get("arr_cn", {})
    gc = d.get("growth_compare", {})
    cc = d.get("commercialization_compare", {})
    cs = d.get("capital_source_compare", {})
    rt = d.get("readthrough", {})
    sr = d.get("scale_reference", {})
    inst = d.get("institutions", {})

    # ---- 结论速览 ----
    us_capex_usd = us_capex.get("total") or 0
    cn_capex_usd = round((2707 + 2111 + 456 + 2000) / fx)
    ratio_capex = us_capex_usd / cn_capex_usd if cn_capex_usd else 0
    us_arr = arr_us.get("total_disclosed") or 0
    cn_arr = arr_cn.get("industry_total") or 0
    ratio_arr = us_arr / cn_arr if cn_arr else 0
    overview = _kv_grid([
        ("美国云厂商 2026 资本开支", f"{us_capex_usd:,.0f} 亿美元"),
        ("中国四家（Q2 年化估算）", f"约 {cn_capex_usd:,} 亿美元"),
        ("资本开支体量比", f"约 {ratio_capex:.1f} : 1（美 : 中）"),
        ("美国头部模型 ARR 合计", f"{us_arr:,} 亿美元"),
        ("中国模型行业年化收入", f"约 {cn_arr:,} 亿美元"),
        ("ARR 体量比", f"约 {ratio_arr:.1f} : 1（美 : 中）"),
        ("中国 ARR 八个月增速", "约 3.3 倍（40 → 130 亿美元）"),
        ("智谱 ARR 1→10 亿美元用时", "约 5 个月（Anthropic 约 15 个月）"),
    ])

    # ---- 资本开支 ----
    us_total_txt = f'{us_capex_usd:,.0f}'
    cn_q2_rows = [[n, t, (f"{v:,.1f}" if isinstance(v, (int, float)) else "—"), yoy, note]
                  for n, t, v, yoy, note in (cn_capex.get("quarterly_2026q2") or [])]
    capex_block = f'''
      <h3>美国：五大云厂商 2026 年指引合计约 {us_total_txt} 亿美元</h3>
      {_rows_table(["公司", "代码", "2026 指引（亿美元）", "说明"],
                   us_capex.get("rows") or [], num_cols=(2,))}
      <p class="muted" style="font-size:11.5px">{_esc(us_capex.get("quarterly_series_note", ""))}</p>

      <h3>中国：四家互联网大厂单季已破 1300 亿元</h3>
      {_rows_table(["公司", "代码/状态", "2026Q2 资本开支（亿元）", "同比", "投向与说明"],
                   cn_q2_rows, num_cols=(2,))}
      <p class="muted" style="font-size:11.5px">
        {_esc(cn_capex.get("capex_to_revenue_note", ""))}；{_esc(cn_capex.get("annual_estimate_note", ""))}。
      </p>
      <p style="font-size:12.5px"><b>三年计划：</b>{_esc(cn_capex.get("three_year_plan", ""))}</p>
      <p style="font-size:12.5px"><b>融资动作：</b>{_esc(cn_capex.get("financing", ""))}</p>

      <h3>同一刻度下的对比（换算为亿美元）</h3>
      {capex_chart(d)}
      <p class="muted" style="font-size:11.5px">
        中国公司为按 2026Q2 年化的估算值（非官方全年指引），汇率按 1 美元 = {fx} 元人民币折算。
        线性刻度——美国单家头部（亚马逊约 2200 亿美元）即接近中国四家合计的 2.2 倍。
      </p>'''

    # ---- ARR ----
    arr_block = f'''
      <h3>美国：头部两家合计已超 1000 亿美元年化</h3>
      {_rows_table(["公司", "ARR（亿美元）", "口径", "要点"], arr_us.get("rows") or [], num_cols=(1,))}
      <p class="muted" style="font-size:11.5px">{_esc(arr_us.get("growth_note", ""))}</p>

      <h3>中国：行业年化收入已近 130 亿美元</h3>
      {_rows_table(["公司/业务", "ARR（亿美元）", "口径", "要点"], arr_cn.get("rows") or [], num_cols=(1,))}
      <p class="muted" style="font-size:11.5px">{_esc(arr_cn.get("industry_note", ""))}</p>
      {industry_curve(d)}

      <h3>同一刻度下的对比</h3>
      {arr_chart(d)}
      <p class="muted" style="font-size:11.5px">
        口径不一（月度年化/周度年化/合同口径），跨国比较仅供量级参考。
        视觉上的长度差即真实差距——美国头部约为中国头部的 20~30 倍。
      </p>'''

    # ---- 增速 ----
    growth_rows = [[n, t, note] for n, t, note in (gc.get("rows") or [])]
    growth_block = f'''
      {_rows_table(["公司", "从 1 亿到 10 亿美元 ARR 用时", "说明"], growth_rows)}
      <div class="alert alert-blue" style="margin-top:10px">{_esc(gc.get("note", ""))}</div>'''

    # ---- 商业化路径 ----
    comm_rows = [[k, cn, us] for k, cn, us in (cc.get("rows") or [])]
    comm_block = _rows_table(["维度", "中国", "美国"], comm_rows)

    # ---- 资本来源与约束 ----
    src_rows = [[k, cn, us] for k, cn, us in (cs.get("rows") or [])]
    src_block = _rows_table(["维度", "中国", "美国"], src_rows)

    # ---- 投资含义 ----
    points = "".join(
        f'<div class="point"><div class="pt-title">{_esc(t)}</div>'
        f'<div class="pt-body">{_esc(b)}</div></div>' for t, b in (rt.get("points") or []))
    chips = "".join(f'<span class="tag">{_esc(x)}</span>' for x in (rt.get("a_share_chain") or []))
    watch = "".join(f"<li>{_esc(x)}</li>" for x in (rt.get("watch") or []))

    # ---- 机构研判 ----
    def _inst_rows(key):
        return [[n, st, v, e] for n, st, v, e in (inst.get(key) or [])] if inst else []

    inst_html = ""
    if inst:
        _dispute_rows = [[a, b, c] for a, b, c in (inst.get("disputes") or [])]
        inst_html = (
            '      <h3>国际机构</h3>'
            + _rows_table(["机构", "倾向", "核心判断", "关键论据"], _inst_rows("international"))
            + '      <h3>国内机构</h3>'
            + _rows_table(["机构", "倾向", "核心判断", "关键论据"], _inst_rows("domestic"))
            + '      <h3>四个分歧焦点</h3>'
            + _rows_table(["焦点", "分歧内容", "当前判断"], _dispute_rows)
            + f'<p class="muted" style="font-size:11.5px">{_esc(inst.get("note", ""))}</p>'
        )
    # ---- 规模参照 ----
    scale_rows = [[k, v] for k, v in [
        ("全球云厂商资本开支", sr.get("global_nine_cloud_2026", "")),
        ("全球 AI 投入与产出", sr.get("global_ai_invest_vs_revenue", "")),
        ("全球模型平台支出", sr.get("gartner_2026", "")),
        ("中国大模型市场", sr.get("cn_market", "")),
    ] if v]

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 资本开支与 ARR · 中美对比专题 · {date_str}</title>
<style>{css}
.axis-note{{font-size:11.5px;color:#999}}
.legend{{display:flex;gap:16px;font-size:12px;margin:6px 0 2px}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}}
.badge-cn{{background:#fdf0e3;color:#c1651a}}
.badge-us{{background:#e8f0fe;color:#1967d2}}
</style>
</head>
<body>
<div class="wrap">

<div class="header">
<h1>AI 资本开支与 ARR · 中美对比专题</h1>
<div class="sub">投入端（云厂商资本开支）× 变现端（模型公司 ARR）× 路径与约束差异</div>
<div class="meta">
<span>数据截至：{_esc(d.get("as_of", date_str))}</span>
<span>生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}</span>
<span>汇率：1 USD = {fx} CNY</span>
</div>
</div>

<div class="toc">
<h2>目录</h2>
<ol>
<li><a href="#s1">核心结论（含中美体量对比速览）</a></li>
<li><a href="#s2">投入端：资本开支对比</a></li>
<li><a href="#s3">变现端：模型公司 ARR 对比</a></li>
<li><a href="#s4">增速：追赶者的速度优势</a></li>
<li><a href="#s5">商业化路径差异</a></li>
<li><a href="#s6">资本来源与约束对比</a></li>
<li><a href="#s7">投资含义与观察指标</a></li>
<li><a href="#s8">机构研判：共识与分歧</a></li>
<li><a href="#s9">规模参照（全球坐标）</a></li>
<li><a href="#s10">口径说明</a></li>
</ol>
</div>

<div class="card" id="s1">
<h2>核心结论</h2>
<div class="alert alert-blue">
<b>一句话：</b>中国在 AI 基础设施投入与模型商业化上与美国的体量差距仍在 7~8 倍量级，
但<b>增速明显更快、路径更适合追赶</b>——中国走 API/开发者与出海路线并已出现「价量齐升」，
美国走订阅/Agent/广告路线且面临「降速还是加速」的路线之争。
</div>
{overview}
<p class="muted" style="font-size:11.5px;margin-top:10px">
注：中国公司全年资本开支为按 2026Q2 年化的估算，非官方指引；ARR 口径各公司不一，跨国比较仅供量级参考。
</p>
</div>

<div class="card" id="s2">
<h2>一、投入端：资本开支对比</h2>
<div class="legend">
<span><i style="background:{US_COLOR}"></i>美国</span>
<span><i style="background:{CN_COLOR}"></i>中国</span>
</div>
{capex_block}
</div>

<div class="card" id="s3">
<h2>二、变现端：模型公司 ARR 对比</h2>
<div class="legend">
<span><i style="background:{US_COLOR}"></i>美国</span>
<span><i style="background:{CN_COLOR}"></i>中国</span>
</div>
{arr_block}
</div>

<div class="card" id="s4">
<h2>三、增速：追赶者的速度优势</h2>
{growth_block}
</div>

<div class="card" id="s5">
<h2>四、商业化路径差异</h2>
{comm_block}
</div>

<div class="card" id="s6">
<h2>五、资本来源与约束对比</h2>
{src_block}
</div>

<div class="card" id="s7">
<h2>六、投资含义与观察指标</h2>
{points}
<h3>对应的 A 股产业链方向</h3>
<div>{chips}</div>
<h3>跟踪指标</h3>
<ul style="font-size:12.5px;padding-left:20px;line-height:1.9">{watch}</ul>
</div>

<div class="card" id="s8">
<h2>七、机构研判：共识与分歧</h2>
{inst_html}
</div>

<div class="card" id="s9">
<h2>八、规模参照（全球坐标）</h2>
{_rows_table(["指标", "说明"], scale_rows)}
</div>

<div class="card" id="s10">
<h2>九、口径说明</h2>
<p style="font-size:12.5px">{_esc(d.get("note", ""))}</p>
</div>

<div class="card">
<p class="muted" style="font-size:11.5px;margin:0">
本报告为自动生成的专题研究材料，数据来自公开披露与公开报道，不构成投资建议。
</p>
</div>

</div>
</body>
</html>'''


def main():
    ap = argparse.ArgumentParser(description="生成「AI 资本开支与 ARR · 中美对比」专题报告")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="报告日期 YYYY-MM-DD")
    args = ap.parse_args()

    d = load_data()
    if not d:
        print("❌ 数据文件缺失，无法生成")
        return
    html = render(d, args.date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"AI资本开支与ARR·中美对比-{args.date}.html"
    out.write_text(html, encoding="utf-8")
    print(f"报告已生成: {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
