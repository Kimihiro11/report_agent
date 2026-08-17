#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股见顶风险诊断集成模块

复用 peak_detector.py 的见顶诊断引擎（5维度评分 + 见底缓冲），
对 config.json 中的自选股做个股层面的风险诊断，输出结构化结果，
并提供 render_html() 把诊断渲染为可嵌入报告的 HTML 片段。

依赖: numpy / pandas / requests（与 peak_detector.py 相同）
"""
import sys
import os
from datetime import datetime

import pandas as pd

# 确保能 import 同目录下的 peak_detector
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from peak_detector import get_kline, get_realtime_quote, calc_indicators, diagnose_peak


# 各维度满分（与 peak_detector.print_diagnosis 保持一致）
DIM_MAX = {
    '超买程度': 35,
    '成交活跃度': 15,
    '量价背离': 15,
    '趋势衰竭': 20,
    '形态破位': 40,
    '见底信号': 35,
}

# 风险等级 -> 颜色（危险用红，安全用绿，符合通用风险语义）
LEVEL_COLOR = {
    '极度危险': '#d63031',
    '高危': '#e67e22',
    '红色预警': '#d63031',
    '黄色预警': '#caa300',
    '蓝色预警': '#3498db',
    '安全': '#00a865',
}

# 5 个风险维度的显示顺序（见底信号单列）
RISK_DIMS = ['超买程度', '成交活跃度', '量价背离', '趋势衰竭', '形态破位']

SIG_TYPE_COLOR = {
    '超买': '#d63031',
    '成交': '#e67e22',
    '背离': '#c0392b',
    '衰竭': '#8e44ad',
    '破位': '#d63031',
    '见底': '#00a865',
}


def _inject_today(df, quote):
    """把实时行情追加/替换到 K 线最后一行（与 peak_detector.main 逻辑一致）"""
    today = datetime.now().strftime('%Y-%m-%d')
    new_row = {
        'date': today,
        'open': quote.get('open', 0),
        'close': quote.get('price', 0),
        'high': quote.get('high', 0),
        'low': quote.get('low', 0),
        'volume': quote.get('volume', 0),
        'pct_chg': quote.get('change_pct', 0),
        'amplitude': quote.get('amplitude', 0),
    }
    df_today = pd.DataFrame([new_row])
    df_today['date'] = pd.to_datetime(df_today['date'])
    last_date = df.iloc[-1]['date'].strftime('%Y-%m-%d') if len(df) > 0 else ''
    if last_date == today:
        df.iloc[-1] = df_today.iloc[0]
    else:
        df = pd.concat([df, df_today], ignore_index=True)
    return df


def analyze_stock(code, name='', target_date=None):
    """对单只股票做见顶诊断，返回结构化结果 dict；失败返回 None。

    target_date: 指定回测日期(YYYY-MM-DD)，为 None 时运行实时模式。
    """
    try:
        df = get_kline(code, days=120)
        if df is None or len(df) < 30:
            print(f"  [诊断] {code} {name}: K线数据不足")
            return None
        df['date'] = pd.to_datetime(df['date'])

        if target_date:
            target_dt = pd.to_datetime(target_date)
            df = df[df['date'] <= target_dt].copy()
            if len(df) == 0:
                return None

        indicators = calc_indicators(df)
        if indicators is None:
            return None

        quote = None
        if not target_date:
            quote = get_realtime_quote(code)
            if quote:
                df = _inject_today(df, quote)
                indicators = calc_indicators(df)

        result = diagnose_peak(indicators, quote)
        if result is None:
            return None

        if not name and quote and quote.get('name'):
            name = quote.get('name')
        result['code'] = code
        result['name'] = name
        return result
    except Exception as e:
        print(f"  [诊断失败] {code} {name}: {e}")
        return None


def run_all(watchlist):
    """对自选股列表做批量诊断。

    watchlist: list of str (纯代码) 或 list of dict (含 code/name)。
    返回 list of result dict。
    """
    results = []
    for item in watchlist:
        if isinstance(item, dict):
            code = item.get('code', '')
            name = item.get('name', '')
        else:
            code = item
            name = ''
        print(f"  [诊断] {code} {name}...")
        r = analyze_stock(code, name)
        if r:
            results.append(r)
            print(f"    评分 {r['total_score']:.1f} | {r['level_color']} {r['level']} | {r['trend_color']} {r['trend_status']}")
        else:
            print(f"    ⚠ 诊断失败，跳过")
    return results


# ============================================================
# HTML 渲染
# ============================================================

def _bar(width_pct, color, label, value_text, max_text):
    """单个维度进度条"""
    w = max(min(width_pct, 100), 0)
    return f'''
      <div style="margin:6px 0;">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:#555;">
          <span>{label}</span><span>{value_text} / {max_text}</span>
        </div>
        <div style="background:#eef0f3;border-radius:6px;height:10px;overflow:hidden;">
          <div style="width:{w:.1f}%;height:100%;background:{color};border-radius:6px;"></div>
        </div>
      </div>'''


def _signal_chip(name, desc, score, sig_type):
    color = SIG_TYPE_COLOR.get(sig_type, '#666')
    sign = '+' if score > 0 else ''
    if sig_type == '见底':
        text = f'{name}：{desc}（{sign}{score:.1f}）'
    else:
        text = f'{name}：{desc}（{sign}{score:.1f}）'
    return f'<span style="display:inline-block;background:{color}1a;color:{color};border:1px solid {color}55;'
    f'padding:2px 8px;border-radius:10px;font-size:11px;margin:2px;">{text}</span>'


def render_stock_card(r):
    """渲染单只股票诊断卡片"""
    code = r.get('code', '')
    name = r.get('name', '')
    score = r['total_score']
    level = r['level']
    level_color = LEVEL_COLOR.get(level, '#666')
    trend = r['trend_status']

    dims = r['dimensions']
    bars = ''
    for d in RISK_DIMS:
        s = dims.get(d, 0)
        mx = DIM_MAX[d]
        # 风险维度：分数越高越危险（红），用橙红渐变
        color = '#d63031' if s >= mx * 0.5 else ('#e67e22' if s >= mx * 0.25 else '#f0a030')
        bars += _bar(s / mx * 100, color, d, f'{s:.1f}', f'{mx}')

    # 见底信号：负分=安全缓冲，绿色
    bottom = dims.get('见底信号', 0)
    bottom_abs = abs(bottom)
    bars += _bar(bottom_abs / DIM_MAX['见底信号'] * 100, '#00a865',
                 '见底信号(安全缓冲)', f'{bottom:.1f}', f"-{DIM_MAX['见底信号']}")

    # 信号（按绝对值排序取前 8）
    sigs = sorted(r.get('signals', []), key=lambda x: abs(x[2]), reverse=True)[:8]
    chips = ''.join(_signal_chip(n, d, s, t) for n, d, s, t in sigs)
    if not chips:
        chips = '<span class="muted" style="font-size:11px;color:#999;">无显著信号</span>'

    # 实时行情摘要
    q = r.get('quote') or {}
    quote_line = ''
    if q.get('price'):
        chg = q.get('change_pct', 0)
        ccls = '#d63031' if chg > 0 else ('#00a865' if chg < 0 else '#555')
        csgn = '+' if chg > 0 else ''
        turnover = q.get('turnover_rate', 0)
        amount_yi = (q.get('amount', 0) or 0) / 1e8
        quote_line = (f'现价 <b>{q["price"]:.2f}</b> '
                      f'<span style="color:{ccls};">{csgn}{chg:.2f}%</span>'
                      f'　换手率 {turnover:.2f}%　成交额 {amount_yi:.2f}亿')

    title = f'{name} <span style="color:#999;font-size:12px;">{code}</span>' if name else f'<span style="color:#999;font-size:12px;">{code}</span>'

    return f'''
  <div style="border:1px solid #eef0f3;border-radius:10px;padding:14px;margin-bottom:12px;background:#fafbfc;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
      <div style="font-size:15px;font-weight:600;color:#1a2b4a;">{title}</div>
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="text-align:center;">
          <div style="font-size:22px;font-weight:700;color:{level_color};line-height:1;">{score:.0f}</div>
          <div style="font-size:10px;color:#999;">综合评分/100</div>
        </div>
        <span style="background:{level_color}1a;color:{level_color};border:1px solid {level_color}55;
                     padding:4px 10px;border-radius:14px;font-size:13px;font-weight:600;">{r['level_color']} {level}</span>
        <span style="background:#eef2f7;color:#4a5568;padding:4px 10px;border-radius:14px;font-size:12px;">{r['trend_color']} {trend}</span>
      </div>
    </div>
    {f'<div style="font-size:12px;color:#666;margin:6px 0 2px;">{quote_line}</div>' if quote_line else ''}
    <div style="margin-top:8px;">{bars}</div>
    <div style="margin-top:8px;">{chips}</div>
  </div>'''


def render_html(results):
    """把整批诊断渲染为可嵌入报告的 HTML 片段。无结果时返回提示。"""
    if not results:
        return '<p class="muted" style="font-size:12px;color:#999;">个股见顶诊断暂不可用（行情接口未返回数据）。</p>'

    cards = ''.join(render_stock_card(r) for r in results)
    # 统计
    danger = sum(1 for r in results if r['level'] in ('极度危险', '高危'))
    warn = sum(1 for r in results if '预警' in r['level'])
    safe = len(results) - danger - warn
    summary = (f'共诊断 <b>{len(results)}</b> 只个股：'
               f'<span style="color:#d63031;">危险/高危 {danger}</span>　'
               f'<span style="color:#caa300;">预警 {warn}</span>　'
               f'<span style="color:#00a865;">安全 {safe}</span>')
    legend = ('评分越高=见顶/调整风险越大；"见底信号"为安全缓冲（绿条越长越安全）。'
              '维度：超买/成交/背离/衰竭/破位。')

    return f'''
  <div style="font-size:13px;color:#333;margin-bottom:10px;">{summary}</div>
  {cards}
  <p class="muted" style="font-size:11px;color:#999;margin-top:4px;">{legend}</p>'''
