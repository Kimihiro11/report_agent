#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股见顶风险诊断集成模块

复用 peak_detector.py 的见顶诊断引擎（5维度评分 + 见底缓冲），
对 config.json 中的自选股做个股层面的风险诊断，输出结构化结果。
（HTML 渲染由 build_report.py 的 stock_card/render_signals 负责，本模块不渲染。）

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
