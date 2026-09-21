#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 SVG 图表组件（纯字符串拼装，无第三方依赖，报告保持单文件自包含）。

把「折线图 / 条形图」抽成与业务无关的通用渲染器：章节只准备数据
（dates + lines / items），不再各自手写一套 SVG。

约定
  - 红涨绿跌（中文报告惯例）：UP_COLOR / DOWN_COLOR
  - 浅色主题：网格 #eef0f4、轴标签 #9aa3b2、图例 #4a5568、标题 #3C3489
  - 折线图图例一律放**底部横排** —— 右侧竖排会挤压中文名导致截断
  - 缺口（None）不画线：整段断开为多段 polyline，不跨缺口连线
"""
from __future__ import annotations

import html
import math

UP_COLOR = "#d63031"       # 涨 / 利多
DOWN_COLOR = "#00a865"     # 跌 / 利空
NEUTRAL_COLOR = "#9aa3b2"  # 中性
PALETTE = ["#1967d2", "#e67e22", "#8e44ad", "#16a085", "#c0392b", "#2c7fb8",
           "#7f8c8d", "#d35400"]

GRID = "#eef0f4"
AXIS_TEXT = "#9aa3b2"
LEGEND_TEXT = "#4a5568"
TITLE_COLOR = "#3C3489"


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def color_for(value, idx: int = 0) -> str:
    """按涨跌取色（红涨绿跌），0/None 取中性。"""
    if value is None:
        return NEUTRAL_COLOR
    if value > 0:
        return UP_COLOR
    if value < 0:
        return DOWN_COLOR
    return NEUTRAL_COLOR


def fmt(value, unit: str = "", sign: bool = False) -> str:
    """数值格式化。

    - unit='%' 走百分比；sign 只对百分比生效（绝对价格/计数不该带正号）
    - 非百分比且为整数时按整数显示（如舆情「3 条」而非「3.00 条」）
    """
    if value is None:
        return "—"
    if unit == "%":
        return f"{value:+.2f}%" if sign else f"{value:.2f}%"
    s = f"{value:.2f}"
    if abs(value - round(value)) < 1e-9:
        s = f"{value:.0f}"
    return s + unit


def cumulative(values):
    """价格序列 → 相对首值（首个非空值）的累计涨跌幅(%)；缺口保持 None。"""
    base = next((v for v in values if v not in (None, 0)), None)
    if not base:
        return [None] * len(values)
    return [None if v is None else (v / base - 1) * 100 for v in values]


def _segments(values):
    """把含缺口的序列切成若干连续片段：[[(i, v), ...], ...]"""
    segs, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if cur:
                segs.append(cur)
                cur = []
        else:
            cur.append((i, v))
    if cur:
        segs.append(cur)
    return segs


def line_chart(spec: dict) -> str:
    """通用多序列折线图。

    spec = {
      dates: [...],                      # X 轴标签（等距）
      lines: [{name, values, color?, dash?}],   # values 与 dates 等长，允许 None
      title?: str,                       # 图上方小标题
      note?: str,                        # 图下方脚注
      y_unit?: "%",                      # Y 轴数值后缀
      y_fmt?: "pct|num",                 # 默认按 y_unit 推断
      legend_cols?: 3,
      width?: 660, plot_h?: 200,
      zero_line?: True,                  # 0 轴虚线（跨零时有效）
      pad_l?: 52,
    }
    """
    dates = [str(d) for d in (spec.get("dates") or [])]
    lines = [l for l in (spec.get("lines") or []) if l and l.get("values")]
    if len(dates) < 2 or not lines:
        return ""
    n = len(dates)
    for l in lines:
        if len(l["values"]) != n:
            return ""  # 长度不齐不出图，避免误导

    y_unit = spec.get("y_unit", "")   # 缺省不带单位（绝对价格/指数点位），百分比请显式传 "%"
    vals = [v for l in lines for v in l["values"] if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or (abs(hi) or 1)
    lo -= span * 0.10
    hi += span * 0.10

    width = int(spec.get("width", 660))
    plot_h = int(spec.get("plot_h", 200))
    pad_l = int(spec.get("pad_l", 52))
    pad_r, pad_t = 16, 16
    legend_cols = max(1, int(spec.get("legend_cols", 3)))
    legend_rows = math.ceil(len(lines) / legend_cols)
    pad_b = 24 + legend_rows * 17
    height = pad_t + plot_h + pad_b
    plot_w = width - pad_l - pad_r

    def _x(i):
        return pad_l + plot_w * (i / (n - 1))

    def _y(v):
        return pad_t + plot_h * (hi - v) / (hi - lo)

    grids = ""
    for k in range(5):
        gy = pad_t + plot_h * (k / 4)
        grids += (f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width-pad_r}" y2="{gy:.1f}" '
                  f'stroke="{GRID}" stroke-width="1"/>'
                  f'<text x="{pad_l-6}" y="{gy+3:.1f}" text-anchor="end" font-size="9" '
                  f'fill="{AXIS_TEXT}">{fmt(hi - (hi-lo)*k/4, y_unit)}</text>')

    body, legend = "", ""
    for k, l in enumerate(lines):
        color = l.get("color") or PALETTE[k % len(PALETTE)]
        last_v = None
        dash = ' stroke-dasharray="5,3"' if l.get("dash") else ""
        segs = _segments(l["values"])
        for seg in segs:
            pts = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in seg)
            if len(seg) > 1:
                body += (f'<polyline points="{pts}" fill="none" stroke="{color}" '
                         f'stroke-width="1.9" stroke-linejoin="round" '
                         f'stroke-linecap="round"{dash}/>')
        # 末个非空值处打点
        last = next((s for s in reversed(segs) if s), None)
        if last:
            i, last_v = last[-1]
            body += (f'<circle cx="{_x(i):.1f}" cy="{_y(last_v):.1f}" r="2.8" fill="{color}">'
                     f'<title>{esc(l.get("name"))} '
                     f'{fmt(last_v, y_unit, sign=(y_unit == "%"))}</title></circle>')

        col, row = k % legend_cols, k // legend_cols
        col_w = (width - 16) / legend_cols
        lx = 8 + col * col_w
        ly = pad_t + plot_h + 26 + row * 17
        legend += (f'<line x1="{lx:.0f}" y1="{ly}" x2="{lx+16:.0f}" y2="{ly}" '
                   f'stroke="{color}" stroke-width="2.4"{dash}/>'
                   f'<text x="{lx+21:.0f}" y="{ly+3.5}" font-size="10" fill="{LEGEND_TEXT}">'
                   f'{esc(l.get("name"))} {fmt(last_v, y_unit, sign=(y_unit == "%"))}</text>')

    zero = ""
    if spec.get("zero_line", True) and lo < 0 < hi:
        zero = (f'<line x1="{pad_l}" y1="{_y(0):.1f}" x2="{width-pad_r}" y2="{_y(0):.1f}" '
                f'stroke="#c3cad6" stroke-width="1" stroke-dasharray="4,3"/>')

    labels, step = "", max(1, n // 6)
    for i in range(0, n, step):
        labels += (f'<text x="{_x(i):.1f}" y="{pad_t+plot_h+14}" text-anchor="middle" '
                   f'font-size="9" fill="{AXIS_TEXT}">{esc(dates[i][5:])}</text>')

    head = ""
    if spec.get("title"):
        head = (f'<div style="margin:14px 0 4px;font-size:13px;font-weight:700;'
                f'color:{TITLE_COLOR};">{esc(spec["title"])}</div>')
    foot = ""
    if spec.get("note"):
        foot = (f'<p class="muted" style="font-size:11px;margin:4px 0 0;">'
                f'{esc(spec["note"])}</p>')

    return (head
            + f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;display:block">'
            + grids + zero + body + legend + labels + '</svg>'
            + foot)


def bar_chart(spec: dict) -> str:
    """通用横向条形图（适合分类计数：情绪分布 / 资金构成 / 分项得分）。

    spec = {
      items: [{name, value, color?, note?}],   # 自上而下渲染
      title?: str, note?: str,
      unit?: "",                                # 值后缀
      width?: 660, row_h?: 24,
      vmax?: float,                             # 统一量程，缺省取最大值
      label_w?: 96,                             # 左侧名称宽度
    }
    """
    items = [it for it in (spec.get("items") or []) if it and it.get("value") is not None]
    if not items:
        return ""
    width = int(spec.get("width", 660))
    row_h = int(spec.get("row_h", 24))
    label_w = int(spec.get("label_w", 96))
    unit = spec.get("unit", "")
    vmax = spec.get("vmax") or max((abs(it["value"]) for it in items), default=0) or 1
    val_w = 62
    bar_max = width - label_w - val_w - 16
    height = len(items) * row_h + 8

    rows = ""
    for k, it in enumerate(items):
        v = it["value"]
        color = it.get("color") or color_for(v, k)
        w = max(1.0, bar_max * min(abs(v) / vmax, 1.0))
        y = 4 + k * row_h
        rows += (f'<text x="{label_w-8}" y="{y+row_h/2+3:.1f}" text-anchor="end" font-size="10.5" '
                 f'fill="{LEGEND_TEXT}">{esc(it.get("name"))}</text>'
                 f'<rect x="{label_w}" y="{y+5:.1f}" width="{bar_max}" height="{row_h-12}" '
                 f'rx="3" fill="#f4f6f9"/>'
                 f'<rect x="{label_w}" y="{y+5:.1f}" width="{w:.1f}" height="{row_h-12}" '
                 f'rx="3" fill="{color}"><title>{esc(it.get("name"))} '
                 f'{fmt(v, unit, sign=(unit == "%"))}</title></rect>'
                 f'<text x="{label_w+bar_max+8}" y="{y+row_h/2+3:.1f}" font-size="10.5" '
                 f'fill="{color}" font-weight="600">{fmt(v, unit, sign=(unit == "%"))}</text>')
        if it.get("note"):
            rows += (f'<text x="{label_w+bar_max+8}" y="{y+row_h/2+14:.1f}" font-size="9" '
                     f'fill="{AXIS_TEXT}">{esc(it["note"])}</text>')

    head = ""
    if spec.get("title"):
        head = (f'<div style="margin:14px 0 4px;font-size:13px;font-weight:700;'
                f'color:{TITLE_COLOR};">{esc(spec["title"])}</div>')
    foot = ""
    if spec.get("note"):
        foot = (f'<p class="muted" style="font-size:11px;margin:4px 0 0;">'
                f'{esc(spec["note"])}</p>')
    return head + f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;display:block">' \
                  + rows + '</svg>' + foot


if __name__ == "__main__":  # 自检：两图均可渲染
    demo = line_chart({"dates": [f"2026-09-{d:02d}" for d in range(1, 21)],
                       "lines": [{"name": "WTI", "values": [90 + i * 0.3 for i in range(20)]},
                                 {"name": "Brent", "values": [93 + i * 0.25 for i in range(20)]}],
                       "title": "demo 折线", "y_unit": "%"})
    demo2 = bar_chart({"items": [{"name": "利多", "value": 5},
                                 {"name": "利空", "value": 3},
                                 {"name": "中性", "value": 2}],
                       "title": "demo 条形", "unit": " 条"})
    print("line_chart:", len(demo), "chars |", "svg" if "<svg" in demo else "MISSING")
    print("bar_chart :", len(demo2), "chars |", "svg" if "<svg" in demo2 else "MISSING")
