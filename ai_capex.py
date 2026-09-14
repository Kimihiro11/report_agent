# -*- coding: utf-8 -*-
"""海外 AI 巨头资本开支追踪 —— 章节数据与渲染。

回答三个问题：
  1) 钱花了多少、还要花多少（总量与指引）
  2) 钱花在哪（短周期算力 vs 长周期设施；前沿模型 vs 安全合规）
  3) 对市场意味着什么（确定性 / 风险 / A 股映射）

数据源：seeds/ai_capex.json（版本化基线，随财报季人工更新；金额单位亿美元）。
渲染输出为 HTML 片段，由 build_report.ai_capex_section() 内联进报告。
数据缺失时返回空串，由调用方决定是否渲染占位，绝不编造。
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
SEED_PATH = BASE_DIR / "seeds" / "ai_capex.json"


def load_baseline() -> dict:
    try:
        return json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def analyze(d: dict) -> dict:
    """汇总关键指标：最新季合计、2026 指引合计、指引上调家数。"""
    hs = d.get("hyperscalers") or []
    q_total = sum(h.get("quarterly_capex") or 0 for h in hs)
    g_total = sum(h.get("guidance_2026_mid") or 0 for h in hs)
    up_n = sum(1 for h in hs if "上调" in (h.get("direction") or ""))
    return {
        "n": len(hs),
        "q_total": q_total,
        "g_total": g_total,
        "up_n": up_n,
    }


def _dir_badge(direction: str) -> str:
    """指引变动方向 → 徽章。「上调」用红（扩张偏热），口径调整用蓝，其余灰。"""
    s = direction or ""
    if "上调" in s:
        return f'<span class="badge b-red">{s}</span>'
    if "口径" in s:
        return f'<span class="badge b-blue">{s}</span>'
    return f'<span class="badge b-gray">{s or "—"}</span>'


_CHART_COLORS = [
    ("亚马逊", "#d63031"),
    ("谷歌", "#1967d2"),
    ("微软", "#0f9d58"),
    ("Meta", "#8250df"),
]


def _quarterly_chart(qh: dict) -> str:
    """四家云厂商季度资本开支折线图（自包含 SVG，纵轴单位：亿美元）。

    数据源 seeds/ai_capex.json → quarterly_history（原始单位百万美元，此处换算）。
    """
    quarters = qh.get("quarters") or []
    series = qh.get("series") or {}
    if len(quarters) < 2 or not series:
        return ""
    vals = {k: [v / 100.0 for v in vs] for k, vs in series.items()}
    ymax = max((max(v) for v in vals.values() if v), default=1) * 1.18
    W, H = 640, 252
    pl, pr, pt, pb = 56, 96, 16, 34
    iw, ih = W - pl - pr, H - pt - pb
    n = len(quarters)

    def _x(i):
        return pl + iw * i / (n - 1)

    def _y(v):
        return pt + ih * (1 - v / ymax)

    parts = []
    for k in range(5):
        vv = ymax * k / 4
        yy = _y(vv)
        parts.append(f'<line x1="{pl}" y1="{yy:.1f}" x2="{pl + iw}" y2="{yy:.1f}" '
                     f'stroke="#eceff4" stroke-width="0.8"/>')
        parts.append(f'<text x="{pl - 8}" y="{yy:.1f}" text-anchor="end" dominant-baseline="central" '
                     f'font-size="10" fill="#888780">{vv:,.0f}</text>')
    for i, q in enumerate(quarters):
        parts.append(f'<text x="{_x(i):.1f}" y="{H - pb + 15}" text-anchor="middle" font-size="10" '
                     f'fill="#888780">{q}</text>')
    for name, color in _CHART_COLORS:
        vs = vals.get(name)
        if not vs:
            continue
        pts = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(vs))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for i, v in enumerate(vs):
            parts.append(f'<circle cx="{_x(i):.1f}" cy="{_y(v):.1f}" r="2.6" fill="{color}"/>')
        parts.append(f'<text x="{_x(len(vs) - 1) + 9:.1f}" y="{_y(vs[-1]):.1f}" '
                     f'dominant-baseline="central" font-size="11" fill="{color}">'
                     f'{name} {vs[-1]:,.0f}</text>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" style="margin:4px 0 2px">{"".join(parts)}</svg>'


def _growth_note(qh: dict) -> str:
    """基于季度序列给出环比/同比增速（数据不足则留空）。"""
    quarters = qh.get("quarters") or []
    total = qh.get("total") or []
    if len(total) < 2:
        return ""
    seg = []
    if len(total) >= 2:
        qoq = (total[-1] / total[-2] - 1) * 100
        seg.append(f"合计环比 <b>{qoq:+.1f}%</b>（{total[-2] / 100:,.0f} → {total[-1] / 100:,.0f} 亿美元）")
    if len(total) >= 5:
        yoy = (total[-1] / total[-5] - 1) * 100
        seg.append(f"同比 <b>{yoy:+.1f}%</b>（{quarters[-5]} → {quarters[-1]}）")
    if len(total) >= 6:
        first_last = (total[-1] / total[0] - 1) * 100
        seg.append(f"6 个季度累计增幅 <b>{first_last:+.1f}%</b>")
    return " ｜ ".join(seg)


def render_section() -> str:
    d = load_baseline()
    if not d:
        return ""
    hs = d.get("hyperscalers") or []
    agg = d.get("aggregate") or {}
    mix = d.get("capex_mix") or {}
    labs = d.get("ai_labs") or []
    cons = d.get("constraints") or []
    mr = d.get("market_readthrough") or {}
    a = analyze(d)
    if not hs:
        return ""

    qh = d.get("quarterly_history") or {}
    chart_html = _quarterly_chart(qh)
    growth_txt = _growth_note(qh)

    # ---------- 1) 云厂商：最新季 + 2026 指引 ----------
    rows = ""
    for h in hs:
        q = h.get("quarterly_capex")
        q_txt = f"{q:,.0f}" if isinstance(q, (int, float)) else "—"
        rows += (
            f'<tr><td><b>{h.get("name")}</b><br>'
            f'<span class="muted" style="font-size:11px">{h.get("ticker")} · {h.get("segment")}</span></td>'
            f'<td style="text-align:right">{q_txt}</td>'
            f'<td style="text-align:right">{h.get("guidance_2026")}</td>'
            f'<td>{_dir_badge(h.get("direction"))}</td>'
            f'<td class="muted" style="font-size:11.5px">{h.get("recycle")}</td></tr>')

    # ---------- 2) 指引上修路径 ----------
    path_rows = ""
    for h in hs:
        steps = h.get("guidance_path") or []
        chain = " → ".join(f"{t}：{v}" for t, v in steps) if steps else "—"
        path_rows += (f'<tr><td><b>{h.get("name")}</b></td>'
                      f'<td style="font-size:11.5px">{chain}</td></tr>')

    # ---------- 3) 投向结构 ----------
    sc = mix.get("short_cycle") or {}
    lc = mix.get("long_cycle") or {}
    fr = mix.get("frontier_vs_regulatory") or {}

    # ---------- 4) 模型公司：ARR vs 算力承诺 ----------
    lab_rows = ""
    for lb in labs:
        cm = lb.get("compute_commitments_total")
        arr = lb.get("arr")
        cover = ""
        if isinstance(cm, (int, float)) and isinstance(arr, (int, float)) and arr:
            cover = f'{cm / arr:.1f}× ARR'
        # xAI 为自建模式（无对外算力承诺），单独表述避免与「承诺」混淆
        commit_txt = f'{cm:,.0f}' if isinstance(cm, (int, float)) else "自建为主"
        arr_txt = (f'ARR {arr} 亿（{lb.get("arr_period")}）' if isinstance(arr, (int, float))
                   else 'ARR 未披露')
        sub = lb.get("commitments") or []
        sub_txt = "；".join(f"{n} {v}" for n, v, *_ in sub) if sub else "—"
        lab_rows += (
            f'<tr><td><b>{lb.get("name")}</b><br>'
            f'<span class="muted" style="font-size:11px">{_esc(arr_txt)}</span></td>'
            f'<td style="text-align:right">{commit_txt}</td>'
            f'<td>{cover or "—"}</td>'
            f'<td class="muted" style="font-size:11.5px">{_esc(lb.get("power_secured") or "—")}</td>'
            f'<td class="muted" style="font-size:11.5px">'
            f'{_esc(lb.get("commitment_note") or sub_txt)}</td></tr>')

    # ---------- 5) 约束 ----------
    cons_rows = "".join(
        f'<tr><td><b>{c.get("name")}</b></td>'
        f'<td><span class="badge {"b-red" if c.get("level") == "硬约束" else "b-blue" if c.get("level") == "高" else "b-gray"}">'
        f'{c.get("level")}</span></td>'
        f'<td style="font-size:11.5px">{_esc(c.get("evidence", ""))}</td></tr>'
        for c in cons)

    # ---------- 6) 市场推演 ----------
    def _ul(items):
        return "".join(f"<li>{_esc(x)}</li>" for x in items)

    readthrough = (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px">'
        f'<div class="in-struct"><div class="in-head"><b style="color:#d63031">确定性方向</b></div>'
        f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.7">{_ul(mr.get("certainty") or [])}</ul></div>'
        f'<div class="in-struct"><div class="in-head"><b style="color:#00a865">风险方向</b></div>'
        f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.7">{_ul(mr.get("risks") or [])}</ul></div>'
        '</div>')

    chain_chips = "".join(
        f'<span class="in-chip">{_esc(x)}</span>' for x in (mr.get("a_share_chain") or []))

    readme = (
        '<div class="muted" style="font-size:11.5px;background:#fafbff;border:1px solid #e8eaf6;'
        'border-radius:8px;padding:8px 12px;margin:8px 0 4px;line-height:1.75">'
        '<b>怎么看这一节：</b>金额单位统一为<b>亿美元</b>。关注三点——'
        '①<b>指引只上不下就是需求未减速的直接证据</b>（唯一例外是会计口径调整，非收缩）；'
        '②<b>短周期资产占比越高，订单向收入转化的时滞越短</b>；'
        '③模型公司「算力承诺 ÷ 自身收入」的倍数，是这轮投入可持续性的核心度量。'
        '</div>')

    return f'''
    <div class="card" id="sec-aicapex">
      <h2>海外 AI 巨头资本开支追踪（总量 · 结构 · 市场映射）</h2>
      <p class="muted" style="font-size:12px">数据截至 {_esc(d.get("as_of", ""))} ｜ 覆盖 {a["n"]} 家云厂商 + {len(labs)} 家模型公司 ｜ 金额单位：亿美元</p>
      {readme}

      <div class="in-struct" style="margin:6px 0 10px">
        <div class="in-head"><span class="in-k">结论</span>
          <b>四大云厂商最新季资本开支合计约 {a["q_total"]:,.0f} 亿美元，2026 指引合计约 {a["g_total"]:,.0f} 亿美元；
          {a["n"]} 家中无一主动收缩，{a["up_n"]} 家在上修。</b></div>
        <div class="in-sec">投向结构正从「长周期设施」转向「短周期算力」——新增投入优先落到能快速形成可用算力的设备上，
        这意味着订单向设备采购、进而向收入兑现的时滞在缩短，但对电力与散热的挤压也在同步加剧。</div>
      </div>

      <div class="chan-lv-head">▸ 云厂商：最新季投入与全年指引</div>
      <table><thead><tr><th>公司</th><th style="text-align:right">最新季资本开支</th><th style="text-align:right">2026 指引</th><th>指引变动</th><th>投入回收路径</th></tr></thead>
      <tbody>{rows}</tbody></table>

      <div class="chan-lv-head">▸ 季度资本开支走势（纵轴单位：亿美元）</div>
      {chart_html}
      <p class="muted" style="font-size:11.5px;margin:2px 0 0">{growth_txt}</p>
      <p class="muted" style="font-size:11px;margin:2px 0 0">
        注：微软财年与自然季不一致，此处按自然季折算；亚马逊始终为单季最大投入方。
        「投入是否见顶」看两条——指引是否出现真实下修、季度环比是否连续两季走平。
      </p>

      <div class="chan-lv-head">▸ 全年指引的上修轨迹（只上不下）</div>
      <table><thead><tr><th style="width:14%">公司</th><th>指引调整路径</th></tr></thead>
      <tbody>{path_rows}</tbody></table>
      <p class="muted" style="font-size:11.5px;margin:4px 0 0">
        穆迪口径：六家科技巨头 2026 年资本开支约 {agg.get("moody_forecast", {}).get("2026", 0):,.0f} 亿美元，
        2027 年{_esc(str(agg.get("moody_forecast", {}).get("2027", "")))}；
        {_esc(agg.get("cumulative_note", ""))}；
        含 OpenAI（Stargate）与 xAI（Colossus）在内的八大科技公司，2026 年 AI 基建支出区间约
        {_esc(str(agg.get("big_tech_2026_range", "")))} 亿美元；
        全球 AI 公司数据中心支出预计接近 {agg.get("global_ai_dc_spend_2026", 0):,.0f} 亿美元。
      </p>

      <div class="chan-lv-head">▸ 钱花在哪：短周期算力 vs 长周期设施</div>
      <table><thead><tr><th style="width:22%">类别</th><th style="width:26%">占比线索</th><th>含义</th></tr></thead><tbody>
        <tr><td><b>{_esc(sc.get("label", ""))}</b></td><td class="muted" style="font-size:11.5px">{_esc(sc.get("share_hint", ""))}</td><td>{_esc(sc.get("meaning", ""))}</td></tr>
        <tr><td><b>{_esc(lc.get("label", ""))}</b></td><td class="muted" style="font-size:11.5px">{_esc(lc.get("share_hint", ""))}</td><td>{_esc(lc.get("meaning", ""))}</td></tr>
        <tr><td><b>{_esc(fr.get("label", ""))}</b></td><td class="muted" style="font-size:11.5px">财报不单独披露</td><td>{_esc(fr.get("note", ""))}</td></tr>
      </tbody></table>

      <div class="chan-lv-head">▸ 模型公司：收入 vs 算力承诺（可持续性度量）</div>
      <table><thead><tr><th>公司</th><th style="text-align:right">算力承诺合计</th><th>承诺 ÷ ARR</th><th>已锁定电力</th><th>主要合约</th></tr></thead>
      <tbody>{lab_rows}</tbody></table>
      <p class="muted" style="font-size:11.5px;margin:4px 0 0">
        模型公司的算力承诺普遍远超自身收入，依赖持续股权融资与复杂债务结构支撑；这些多为数年期的 take-or-pay 容量预定，
        并非当期表内负债，但「预定容量与实际付费需求之间的缺口」是真正的尾部风险。
        {_esc(agg.get("cashflow_timing", ""))}；{_esc(agg.get("revenue_dependency", ""))}。
      </p>

      <div class="chan-lv-head">▸ 约束与风险</div>
      <table><thead><tr><th style="width:16%">约束</th><th style="width:12%">程度</th><th>证据</th></tr></thead>
      <tbody>{cons_rows}</tbody></table>

      <div class="chan-lv-head">▸ 市场推演</div>
      {readthrough}
      <div class="in-watch" style="margin-top:8px">{chain_chips}</div>
      <p class="muted" style="font-size:11px;margin:8px 0 0">
        观察口径：<b>看资本开支而不是听叙事</b>——只要这些公司仍在向市场融资（IPO／发债／银团），
        资本开支就不会降速；反之若指引出现真实下修，才应启动对算力链的需求下修。
      </p>
    </div>'''


def _esc(s) -> str:
    from html import escape
    return escape(str(s if s is not None else ""))
