#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票诊断系统 v2.0

评分体系（100分制）：
  - 超买程度   25分  (RSI/KDJ/WR/CCI/MFI/PSY + 短期涨幅)
  - 成交活跃度 20分  (换手率 + 成交额 + 量比)
  - 量价背离   15分  (价新高量未新高 + OBV背离 + VR过热)
  - 趋势衰竭   20分  (MACD顶背离 + 红柱缩短 + ADX拐头 + BIAS乖离)
  - 形态破位   20分  (跌破5日线 + 布林中轨 + MACD/KDJ死叉 + 放量下跌)

风险等级：
  70-100分  🔴 极度危险 - 见顶确认，立即离场
  50-69分   🟠 高危 - 大概率见顶，减仓观望
  44-49分   🔴 红色预警 - 重度预警，接近高危
  37-43分   🟡 黄色预警 - 中度预警，提高警惕
  30-36分   🔵 蓝色预警 - 轻度预警，密切关注
  15-29分   🟢 安全 - 趋势健康，持有为主
  0-14分    🟢 安全 - 上涨初期，可继续持有

特殊判断：加速冲顶阶段
  触发条件：5日涨幅≥30% + 超买≥15分 + 破位<5分
  特征：涨得最猛的时候，也是最危险的时候
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import sys


# ============================================================
# 第一部分：数据获取
# ============================================================

def get_kline(code, days=120):
    """从腾讯财经获取日K线数据（前复权）"""
    if code.startswith('6') or code.startswith('9'):
        symbol = f'sh{code}'
    elif code.startswith('0') or code.startswith('3'):
        symbol = f'sz{code}'
    elif code.startswith('8') or code.startswith('4'):
        symbol = f'bj{code}'
    else:
        symbol = f'sh{code}'
    
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    end_date = datetime.now().strftime('%Y-%m-%d')
    params = {
        'param': f'{symbol},day,2025-01-01,{end_date},{days},qfq',
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get('data'):
            key = list(data['data'].keys())[0]
            klines = data['data'][key].get('qfqday', data['data'][key].get('day', []))
            
            if not klines:
                return None
            
            # 判断板块，确定成交量单位转换系数
            # 腾讯财经K线接口，不同板块成交量单位不一样：
            # - 主板/创业板（60/00/30开头）：单位是手，需要×100转成股
            # - 科创板（68开头）：单位是股，不需要转换
            # 验证：
            #   卧龙电驱(600580主板)：90万手=9000万股，成交额约30亿 ✓
            #   京东方A(000725主板)：2120万手=21.2亿股，成交额约128亿 ✓
            #   汇成真空(301392创业板)：6.7万手=670万股，成交额约11亿 ✓
            #   富创精密(688409科创板)：1046万股，成交额约20亿 ✓（原始值就是股数）
            if code.startswith('68'):
                vol_multiplier = 1  # 科创板，单位已经是股
            else:
                vol_multiplier = 100  # 主板/创业板，手 -> 股
            
            rows = []
            for k in klines:
                if len(k) >= 6:
                    rows.append({
                        'date': k[0],
                        'open': float(k[1]),
                        'close': float(k[2]),
                        'high': float(k[3]),
                        'low': float(k[4]),
                        'volume': float(k[5]) * vol_multiplier,  # 统一转换成股
                    })
            
            df = pd.DataFrame(rows)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df['pct_chg'] = df['close'].pct_change() * 100
            df['amplitude'] = (df['high'] - df['low']) / df['close'].shift(1) * 100
            
            return df
    except Exception as e:
        print(f"获取K线失败: {e}")
    return None


def get_trade_time_ratio():
    """计算当前已交易时间占全天的比例（时间比例）"""
    from datetime import datetime, time
    
    now = datetime.now().time()
    
    # A股交易时间：上午9:30-11:30，下午13:00-15:00，全天240分钟
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)
    
    total_minutes = 240
    
    if now < morning_start:
        return 0.0  # 还没开盘
    elif morning_start <= now < morning_end:
        # 上午盘中
        minutes = (now.hour - 9) * 60 + (now.minute - 30)
        return max(minutes / total_minutes, 0.01)  # 最小1%，避免除零
    elif morning_end <= now < afternoon_start:
        # 午休
        return 120 / total_minutes  # 0.5
    elif afternoon_start <= now < afternoon_end:
        # 下午盘中
        minutes = 120 + (now.hour - 13) * 60 + now.minute
        return minutes / total_minutes
    else:
        return 1.0  # 已收盘


def get_volume_ratio():
    """计算当前已成交量占全天预估的比例（基于A股典型成交量分布）
    
    A股成交量分布特点：
    - 开盘半小时（9:30-10:00）：成交量最大，约占全天25%
    - 上午剩余（10:00-11:30）：逐渐减少，约占全天35%
    - 上午合计：约占全天60%
    - 下午开盘（13:00-14:00）：约占全天15%
    - 尾盘半小时（14:30-15:00）：又有一波，约占全天15%
    - 下午合计：约占全天40%
    
    用分段线性函数近似，比简单的时间比例更准确。
    """
    from datetime import datetime, time
    
    now = datetime.now().time()
    
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)
    
    if now < morning_start:
        return 0.01  # 还没开盘，最小比例避免除零
    
    elif morning_start <= now < morning_end:
        # 上午盘中
        minutes_since_open = (now.hour - 9) * 60 + (now.minute - 30)
        
        # 9:30-10:00（30分钟）：占全天25%
        if minutes_since_open <= 30:
            return max(0.25 * (minutes_since_open / 30), 0.01)
        
        # 10:00-11:00（60分钟）：占全天25%（累计50%）
        elif minutes_since_open <= 90:
            return 0.25 + 0.25 * ((minutes_since_open - 30) / 60)
        
        # 11:00-11:30（30分钟）：占全天10%（累计60%）
        else:
            return 0.50 + 0.10 * ((minutes_since_open - 90) / 30)
    
    elif morning_end <= now < afternoon_start:
        # 午休，上午结束，累计约60%
        return 0.60
    
    elif afternoon_start <= now < afternoon_end:
        # 下午盘中
        minutes_since_afternoon = (now.hour - 13) * 60 + now.minute
        
        # 13:00-14:00（60分钟）：占全天15%（累计75%）
        if minutes_since_afternoon <= 60:
            return 0.60 + 0.15 * (minutes_since_afternoon / 60)
        
        # 14:00-14:30（30分钟）：占全天10%（累计85%）
        elif minutes_since_afternoon <= 90:
            return 0.75 + 0.10 * ((minutes_since_afternoon - 60) / 30)
        
        # 14:30-15:00（30分钟）：占全天15%（累计100%）
        else:
            return 0.85 + 0.15 * ((minutes_since_afternoon - 90) / 30)
    
    else:
        return 1.0  # 已收盘


def adjust_quote_for_intraday(quote):
    """把盘中实时行情数据按成交量分布比例换算成全天预估（更准确的动态对比）
    
    不是简单的时间比例×2，而是基于A股典型的成交量分布曲线：
    - 上午占60%，下午占40%
    - 开盘和尾盘成交量最大
    """
    if not quote:
        return quote
    
    ratio = get_volume_ratio()
    time_ratio = get_trade_time_ratio()
    
    # 已收盘或比例接近1，不需要换算
    if ratio >= 0.95:
        quote['adjusted'] = False
        return quote
    
    # 按成交量分布比例换算成全天预估
    adjusted = quote.copy()
    adjusted['original_turnover_rate'] = quote['turnover_rate']
    adjusted['original_volume'] = quote['volume']
    adjusted['original_amount'] = quote['amount']
    
    # 换手率、成交量、成交额按成交量比例换算
    adjusted['turnover_rate'] = quote['turnover_rate'] / ratio
    adjusted['volume'] = quote['volume'] / ratio
    adjusted['amount'] = quote['amount'] / ratio
    adjusted['adjusted'] = True
    adjusted['volume_ratio'] = ratio
    adjusted['time_ratio'] = time_ratio
    
    return adjusted


def get_realtime_quote(code, adjust_for_intraday=True):
    """从腾讯财经获取实时行情数据（换手率、成交额、市值等）
    adjust_for_intraday: 是否把盘中数据按时间比例换算成全天预估（动态对比）
    """
    if code.startswith('6') or code.startswith('9'):
        symbol = f'sh{code}'
    elif code.startswith('0') or code.startswith('3'):
        symbol = f'sz{code}'
    elif code.startswith('8') or code.startswith('4'):
        symbol = f'bj{code}'
    else:
        symbol = f'sh{code}'
    
    url = f'https://qt.gtimg.cn/q={symbol}'
    
    try:
        resp = requests.get(url, timeout=10)
        data = resp.text
        
        if '=' in data and '~' in data:
            # 提取引号内的内容
            start = data.find('"') + 1
            end = data.rfind('"')
            content = data[start:end]
            parts = content.split('~')
            
            if len(parts) >= 50:
                # 关键字段索引（腾讯行情格式）
                # 1:名称 2:代码 3:当前价 4:昨收 5:今开
                # 6:成交量(手) 31:涨跌额 32:涨跌幅% 33:最高 34:最低
                # 36:成交量(手) 37:成交额(万元) 38:换手率% 39:市盈率
                # 43:振幅% 44:流通市值(亿) 45:总市值(亿) 46:市净率
                
                def safe_float(idx, default=0):
                    try:
                        return float(parts[idx]) if idx < len(parts) and parts[idx] else default
                    except (ValueError, TypeError, IndexError):
                        return default
                
                price = safe_float(3)
                amount = safe_float(37) * 10000  # 万元 -> 元
                # 用成交额/股价推算成交量，避免不同交易所单位不统一的问题
                volume = amount / price if price > 0 else 0
                
                quote = {
                    'code': parts[2] if len(parts) > 2 else '',
                    'name': parts[1] if len(parts) > 1 else '',
                    'price': price,
                    'high': safe_float(33),
                    'low': safe_float(34),
                    'open': safe_float(5),
                    'volume': volume,  # 用成交额/股价推算，单位：股
                    'amount': amount,  # 单位：元
                    'pre_close': safe_float(4),
                    'change': safe_float(31),
                    'change_pct': safe_float(32),
                    'amplitude': safe_float(43),
                    'turnover_rate': safe_float(38),  # 换手率%
                    'pe_ratio': safe_float(39),
                    'pb_ratio': safe_float(46),
                    'total_mv': safe_float(45) * 100000000,  # 亿 -> 元
                    'circ_mv': safe_float(44) * 100000000,   # 亿 -> 元
                }
                
                # 盘中数据动态换算成全天预估
                if adjust_for_intraday:
                    quote = adjust_quote_for_intraday(quote)
                
                return quote
    except Exception as e:
        print(f"获取实时行情失败: {e}")
    return None


def get_sector_kline(sector_code, days=120):
    """
    获取板块指数K线数据
    sector_code: 板块指数代码，如 'sh931865'（中证半导体）
    """
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {
        'param': f'{sector_code},day,,{datetime.now().strftime("%Y-%m-%d")},{days},qfq',
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if data.get('code') != 0:
            return None
        
        # 解析数据
        data = data['data']
        # 找到第一个key
        key = list(data.keys())[0]
        kline_data = data[key].get('day') or data[key].get('qfqday')
        
        if not kline_data:
            return None
        
        df = pd.DataFrame(kline_data, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
        df['date'] = pd.to_datetime(df['date'])
        df['open'] = df['open'].astype(float)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)
        
        # 计算涨跌幅
        df['pct_chg'] = df['close'].pct_change() * 100
        
        return df
    except Exception as e:
        print(f"获取板块K线失败: {e}")
        return None


# ============================================================
# 第二部分：技术指标计算
# ============================================================

def calc_indicators(df):
    """计算所有技术指标"""
    if df is None or len(df) < 30:
        return None
    
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    open_ = df['open']
    
    r = pd.DataFrame(index=df.index)
    r['date'] = df['date']
    r['close'] = close
    r['high'] = high
    r['low'] = low
    r['open'] = open_
    r['volume'] = volume
    r['pct_chg'] = df['pct_chg']
    
    # --- MA 均线 ---
    for n in [5, 10, 20, 30, 60]:
        r[f'MA{n}'] = close.rolling(n).mean()
    
    # --- MACD ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    r['DIF'] = ema12 - ema26
    r['DEA'] = r['DIF'].ewm(span=9, adjust=False).mean()
    r['MACD'] = (r['DIF'] - r['DEA']) * 2
    
    # --- KDJ ---
    low_min = low.rolling(9).min()
    high_max = high.rolling(9).max()
    rsv = (close - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)
    k = np.zeros(len(df))
    d = np.zeros(len(df))
    k[0] = 50
    d[0] = 50
    for i in range(1, len(df)):
        k[i] = 2/3 * k[i-1] + 1/3 * rsv.iloc[i]
        d[i] = 2/3 * d[i-1] + 1/3 * k[i]
    r['K'] = k
    r['D'] = d
    r['J'] = 3 * k - 2 * d
    
    # --- RSI (Wilder's平滑方法，与东方财富/通达信一致) ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    for n in [6, 12, 24]:
        # 第一个值用简单平均
        avg_gain = gain.rolling(n).mean()
        avg_loss = loss.rolling(n).mean()
        # 之后用Wilder's平滑：avg = (prev_avg * (n-1) + current) / n
        for i in range(n, len(close)):
            avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (n-1) + gain.iloc[i]) / n
            avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (n-1) + loss.iloc[i]) / n
        rs = avg_gain / avg_loss
        r[f'RSI{n}'] = 100 - (100 / (1 + rs))
    
    # --- WR ---
    for n in [6, 14]:
        hh = high.rolling(n).max()
        ll = low.rolling(n).min()
        r[f'WR{n}'] = (hh - close) / (hh - ll) * 100
    
    # --- CCI ---
    tp = (high + low + close) / 3
    for n in [14]:
        ma_tp = tp.rolling(n).mean()
        md = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        r[f'CCI{n}'] = (tp - ma_tp) / (0.015 * md)
    
    # --- BOLL ---
    n = 20
    mid = close.rolling(n).mean()
    std = close.rolling(n).std()
    r['BOLL_MID'] = mid
    r['BOLL_UP'] = mid + 2 * std
    r['BOLL_LOW'] = mid - 2 * std
    r['BOLL_WIDTH'] = (r['BOLL_UP'] - r['BOLL_LOW']) / mid * 100
    r['BOLL_PCT'] = (close - r['BOLL_LOW']) / (r['BOLL_UP'] - r['BOLL_LOW']) * 100
    # 用最高价和最低价分别计算布林位置，用于超买和超卖修正
    r['BOLL_PCT_HIGH'] = (high - r['BOLL_LOW']) / (r['BOLL_UP'] - r['BOLL_LOW']) * 100
    r['BOLL_PCT_LOW'] = (low - r['BOLL_LOW']) / (r['BOLL_UP'] - r['BOLL_LOW']) * 100
    
    # --- DMI ---
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    for i in range(1, len(df)):
        if plus_dm.iloc[i] > minus_dm.iloc[i]:
            minus_dm.iloc[i] = 0
        elif minus_dm.iloc[i] > plus_dm.iloc[i]:
            plus_dm.iloc[i] = 0
        else:
            plus_dm.iloc[i] = 0
            minus_dm.iloc[i] = 0
    
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    tr_n = tr.rolling(14).sum()
    r['PDI'] = 100 * plus_dm.rolling(14).sum() / tr_n
    r['MDI'] = 100 * minus_dm.rolling(14).sum() / tr_n
    dx = 100 * (r['PDI'] - r['MDI']).abs() / (r['PDI'] + r['MDI'])
    r['ADX'] = dx.rolling(14).mean()
    
    # --- SAR ---
    sar = np.zeros(len(df))
    ep = np.zeros(len(df))
    af = np.zeros(len(df))
    is_long = True
    sar[0] = low.iloc[0]
    ep[0] = high.iloc[0]
    af[0] = 0.02
    for i in range(1, len(df)):
        prev_sar, prev_ep, prev_af = sar[i-1], ep[i-1], af[i-1]
        if is_long:
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            if high.iloc[i] > prev_ep:
                ep[i] = high.iloc[i]
                af[i] = min(prev_af + 0.02, 0.2)
            else:
                ep[i] = prev_ep
                af[i] = prev_af
            if low.iloc[i] < sar[i]:
                is_long = False
                sar[i] = prev_ep
                ep[i] = low.iloc[i]
                af[i] = 0.02
        else:
            sar[i] = prev_sar - prev_af * (prev_sar - prev_ep)
            if low.iloc[i] < prev_ep:
                ep[i] = low.iloc[i]
                af[i] = min(prev_af + 0.02, 0.2)
            else:
                ep[i] = prev_ep
                af[i] = prev_af
            if high.iloc[i] > sar[i]:
                is_long = True
                sar[i] = prev_ep
                ep[i] = high.iloc[i]
                af[i] = 0.02
    r['SAR'] = sar
    
    # --- BIAS ---
    for n in [6, 12, 24]:
        ma = close.rolling(n).mean()
        r[f'BIAS{n}'] = (close - ma) / ma * 100
    
    # --- OBV ---
    obv = np.zeros(len(df))
    obv[0] = volume.iloc[0]
    for i in range(1, len(df)):
        if close.iloc[i] > close.iloc[i-1]:
            obv[i] = obv[i-1] + volume.iloc[i]
        elif close.iloc[i] < close.iloc[i-1]:
            obv[i] = obv[i-1] - volume.iloc[i]
        else:
            obv[i] = obv[i-1]
    r['OBV'] = obv
    
    # --- VR ---
    n = 26
    up_vol = volume.where(close > close.shift(1), 0)
    down_vol = volume.where(close < close.shift(1), 0)
    flat_vol = volume.where(close == close.shift(1), 0)
    avs = up_vol.rolling(n).sum()
    bvs = down_vol.rolling(n).sum()
    cvs = flat_vol.rolling(n).sum()
    r['VR'] = (avs + cvs/2) / (bvs + cvs/2) * 100
    
    # --- MFI ---
    n = 14
    typical = (high + low + close) / 3
    money_flow = typical * volume
    positive_flow = money_flow.where(typical > typical.shift(1), 0)
    negative_flow = money_flow.where(typical < typical.shift(1), 0)
    positive_mf = positive_flow.rolling(n).sum()
    negative_mf = negative_flow.rolling(n).sum()
    r['MFI'] = 100 - (100 / (1 + positive_mf / negative_mf))
    
    # --- 量比 ---
    r['VOL_RATIO'] = volume / volume.rolling(5).mean()
    
    # --- 股价位置百分位 ---
    r['PRICE_PCT_60'] = close.rolling(60).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100 if len(x) >= 30 else np.nan, raw=False
    )
    r['PRICE_PCT_120'] = close.rolling(120).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100 if len(x) >= 60 else np.nan, raw=False
    )
    
    # --- 200日均线乖离率 ---
    r['MA200'] = close.rolling(200).mean()
    r['BIAS200'] = (close - r['MA200']) / r['MA200'] * 100
    
    # --- ATR ---
    n = 14
    tr_atr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    r['ATR'] = tr_atr.rolling(n).mean()
    r['NATR'] = r['ATR'] / close * 100
    
    # --- PSY ---
    n = 12
    up_days = (close > close.shift(1)).rolling(n).sum()
    r['PSY'] = up_days / n * 100
    
    return r


# ============================================================
# 第三部分：见顶诊断核心逻辑
# ============================================================

def diagnose_peak(indicators, quote=None, sector_indicators=None):
    """
    见顶诊断主函数
    indicators: 个股技术指标
    quote: 实时行情数据（可选）
    sector_indicators: 板块指数技术指标（可选）
    返回：总分、各维度得分、详细信号列表
    """
    if indicators is None or len(indicators) < 30:
        return None
    
    # 取最新数据
    latest = indicators.iloc[-1]
    prev5 = indicators.iloc[-6] if len(indicators) >= 6 else None
    prev10 = indicators.iloc[-11] if len(indicators) >= 11 else None
    
    # 近5天数据（包含当天）
    last5 = indicators.iloc[-5:]
    
    # 前5天数据（不含当天），用于判断是否创新高
    last5_prev = indicators.iloc[-6:-1] if len(indicators) >= 6 else None
    
    signals = []
    total_score = 0
    adjustments = []  # 记录所有加权、降权操作
    
    # ============================================================
    # 维度一：超买程度（25分）
    # 逻辑：超买越严重，分数越高
    # ============================================================
    overbought_score = 0
    
    # RSI超买（最高8分）
    rsi6 = latest['RSI6']
    if rsi6 >= 85:
        overbought_score += 8
        signals.append(('RSI6极度超买', f'RSI6={rsi6:.1f}', 8, '超买'))
    elif rsi6 >= 75:
        overbought_score += 6
        signals.append(('RSI6超买', f'RSI6={rsi6:.1f}', 6, '超买'))
    elif rsi6 >= 65:
        overbought_score += 3
        signals.append(('RSI6偏热', f'RSI6={rsi6:.1f}', 3, '超买'))
    
    # KDJ超买（最高6分）
    j_val = latest['J']
    if j_val >= 100:
        overbought_score += 6
        signals.append(('KDJ极度超买', f'J值={j_val:.1f}', 6, '超买'))
    elif j_val >= 85:
        overbought_score += 4
        signals.append(('KDJ严重超买', f'J值={j_val:.1f}', 4, '超买'))
    elif j_val >= 70:
        overbought_score += 2
        signals.append(('KDJ超买', f'J值={j_val:.1f}', 2, '超买'))
    
    # WR超买（最高4分）
    wr14 = latest['WR14']
    if wr14 <= 15:
        overbought_score += 4
        signals.append(('WR极度超买', f'WR14={wr14:.1f}', 4, '超买'))
    elif wr14 <= 30:
        overbought_score += 2
        signals.append(('WR超买', f'WR14={wr14:.1f}', 2, '超买'))
    
    # CCI超买（最高4分）
    cci14 = latest['CCI14']
    if cci14 >= 200:
        overbought_score += 4
        signals.append(('CCI极度超买', f'CCI14={cci14:.1f}', 4, '超买'))
    elif cci14 >= 150:
        overbought_score += 3
        signals.append(('CCI严重超买', f'CCI14={cci14:.1f}', 3, '超买'))
    elif cci14 >= 100:
        overbought_score += 1
        signals.append(('CCI偏热', f'CCI14={cci14:.1f}', 1, '超买'))
    
    # MFI资金超买（最高4分）
    mfi = latest['MFI']
    if mfi >= 80:
        overbought_score += 4
        signals.append(('MFI极度超买', f'MFI={mfi:.1f}', 4, '超买'))
    elif mfi >= 70:
        overbought_score += 3
        signals.append(('MFI超买', f'MFI={mfi:.1f}', 3, '超买'))
    elif mfi >= 60:
        overbought_score += 1
        signals.append(('MFI偏热', f'MFI={mfi:.1f}', 1, '超买'))
    
    # PSY心理线（最高4分）
    psy = latest['PSY']
    if psy >= 83.3:  # 10/12天上涨
        overbought_score += 4
        signals.append(('PSY极度亢奋', f'PSY={psy:.1f}%', 4, '超买'))
    elif psy >= 75:  # 9/12天上涨
        overbought_score += 3
        signals.append(('PSY高度亢奋', f'PSY={psy:.1f}%', 3, '超买'))
    elif psy >= 66.7:  # 8/12天上涨
        overbought_score += 2
        signals.append(('PSY偏热', f'PSY={psy:.1f}%', 2, '超买'))
    
    # 短期涨幅过大（最高8分）- 新增
    if len(indicators) >= 10:
        pct_5d = (latest['close'] - indicators.iloc[-6]['close']) / indicators.iloc[-6]['close'] * 100
        pct_10d = (latest['close'] - indicators.iloc[-11]['close']) / indicators.iloc[-11]['close'] * 100
        
        if pct_5d >= 50:
            overbought_score += 8
            signals.append(('5日涨幅极端', f'5天涨{pct_5d:.1f}%', 8, '超买'))
        elif pct_5d >= 30:
            overbought_score += 6
            signals.append(('5日涨幅过大', f'5天涨{pct_5d:.1f}%', 6, '超买'))
        elif pct_5d >= 20:
            overbought_score += 4
            signals.append(('5日涨幅偏快', f'5天涨{pct_5d:.1f}%', 4, '超买'))
        elif pct_5d >= 15:
            overbought_score += 2
            signals.append(('5日涨幅较大', f'5天涨{pct_5d:.1f}%', 2, '超买'))
        
        if pct_10d >= 80:
            overbought_score += 5
            signals.append(('10日涨幅极端', f'10天涨{pct_10d:.1f}%', 5, '超买'))
        elif pct_10d >= 50:
            overbought_score += 3
            signals.append(('10日涨幅过大', f'10天涨{pct_10d:.1f}%', 3, '超买'))
    
    # BOLL位置修正：价格离上轨越远，超买可信度越低
    # 超买用当天最高价计算布林位置（因为超买关注当天最高涨到什么位置）
    boll_pct = latest['BOLL_PCT_HIGH']
    original_overbought = overbought_score
    if boll_pct >= 100:
        boll_factor = 1.2  # 突破上轨，超买信号最可信
        boll_desc = '最高价突破上轨，超买信号最可信'
    elif boll_pct >= 50:
        # 中轨到上轨之间，从0.7线性升到1.2
        boll_factor = 0.7 + (boll_pct - 50) / 50 * 0.5
        boll_desc = '最高价在中轨到上轨，超买信号较可信'
    elif boll_pct >= 0:
        # 下轨到中轨之间，从0.4线性升到0.7
        boll_factor = 0.4 + boll_pct / 50 * 0.3
        boll_desc = '最高价在下轨到中轨，超买信号可信度低'
    else:
        # 下轨及以下，超买信号最不可信
        boll_factor = 0.4
        boll_desc = '最高价在下轨及以下，超买信号最不可信'
    
    overbought_score *= boll_factor
    
    # 保存BOLL修正后的原始值（用于加速冲顶降权判断）
    overbought_after_boll = overbought_score
    
    if boll_factor != 1.0 and original_overbought > 0:
        adjustments.append(f'BOLL位置修正（最高价）：布林位置{boll_pct:.1f}%（{boll_desc}），超买{original_overbought:.1f}分×{boll_factor:.2f}，调整为{overbought_score:.1f}分')
    
    overbought_score = min(overbought_score, 25)
    total_score += overbought_score
    
    # ============================================================
    # 维度二：成交活跃度（20分）
    # 三个独立维度：换手率（绝对水平）+ 成交量分位（相对历史）+ 量比（短期放量）
    # ============================================================
    turnover_score = 0
    
    # 优先用实时行情数据，没有的话用K线数据估算
    if quote and quote.get('turnover_rate'):
        turnover_rate = quote['turnover_rate']
    else:
        turnover_rate = None
    
    # 1. 换手率绝对水平（最高6分）
    if turnover_rate is not None:
        if turnover_rate >= 30:
            turnover_score += 6
            signals.append(('换手率极度异常', f'换手率={turnover_rate:.1f}%', 6, '成交'))
        elif turnover_rate >= 20:
            turnover_score += 5
            signals.append(('换手率极高', f'换手率={turnover_rate:.1f}%', 5, '成交'))
        elif turnover_rate >= 15:
            turnover_score += 4
            signals.append(('换手率很高', f'换手率={turnover_rate:.1f}%', 4, '成交'))
        elif turnover_rate >= 10:
            turnover_score += 3
            signals.append(('换手率偏高', f'换手率={turnover_rate:.1f}%', 3, '成交'))
        elif turnover_rate >= 5:
            turnover_score += 2
            signals.append(('换手率偏热', f'换手率={turnover_rate:.1f}%', 2, '成交'))
        elif turnover_rate >= 3:
            turnover_score += 1
            signals.append(('换手率温和', f'换手率={turnover_rate:.1f}%', 1, '成交'))
    
    # 2. 成交量分位（最高5分）- 相对历史水平
    if len(indicators) >= 20:
        last20_vol = indicators.iloc[-20:]['volume']
        vol_percentile = (last20_vol < latest['volume']).sum() / len(last20_vol) * 100
        if vol_percentile >= 90:
            turnover_score += 5
            signals.append(('成交量极高', f'20日分位{vol_percentile:.0f}%', 5, '成交'))
        elif vol_percentile >= 80:
            turnover_score += 4
            signals.append(('成交量很高', f'20日分位{vol_percentile:.0f}%', 4, '成交'))
        elif vol_percentile >= 70:
            turnover_score += 3
            signals.append(('成交量偏高', f'20日分位{vol_percentile:.0f}%', 3, '成交'))
        elif vol_percentile >= 60:
            turnover_score += 2
            signals.append(('成交量偏热', f'20日分位{vol_percentile:.0f}%', 2, '成交'))
        elif vol_percentile >= 50:
            turnover_score += 1
            signals.append(('成交量温和', f'20日分位{vol_percentile:.0f}%', 1, '成交'))
    
    # 3. 量比（最高4分）- 短期放量程度
    vol_ratio = latest['VOL_RATIO']
    if vol_ratio >= 5:
        turnover_score += 4
        signals.append(('巨量成交', f'量比={vol_ratio:.1f}', 4, '成交'))
    elif vol_ratio >= 3:
        turnover_score += 3
        signals.append(('明显放量', f'量比={vol_ratio:.1f}', 3, '成交'))
    elif vol_ratio >= 2:
        turnover_score += 2
        signals.append(('放量', f'量比={vol_ratio:.1f}', 2, '成交'))
    elif vol_ratio >= 1.5:
        turnover_score += 1
        signals.append(('温和放量', f'量比={vol_ratio:.1f}', 1, '成交'))
    
    # BOLL修正：价格在上轨或之上时，成交活跃度信号更可信，乘以1.2
    boll_pct = latest['BOLL_PCT']
    if boll_pct >= 100 and turnover_score > 0:
        turnover_score *= 1.2
    
    turnover_score = min(turnover_score, 20)
    total_score += turnover_score
    
    # ============================================================
    # 维度三：量价异常（15分）
    # 逻辑：成交量异常、量价关系异常，分数越高
    # ============================================================
    divergence_score = 0
    
    # 1. 近期出现天量（最高4分）- 最近N天内出现过20日天量
    if len(indicators) >= 20:
        last20 = indicators.iloc[-20:]
        max_vol_idx = last20['volume'].idxmax()
        days_since_max = len(last20) - 1 - (max_vol_idx - last20.index[0])
        
        if days_since_max <= 5:
            # 最近5天内出现过天量
            price_now_high = latest['close'] >= last20['high'].max() * 0.95  # 价格接近20日新高
            if price_now_high and days_since_max >= 1:
                divergence_score += 4
                signals.append(('天量后新高', f'{days_since_max}天前天量，价创新高', 4, '背离'))
            elif days_since_max == 0:
                divergence_score += 3
                signals.append(('天量天价', '量价同创20日新高', 3, '背离'))
            else:
                divergence_score += 2
                signals.append(('近期天量', f'{days_since_max}天前出现天量', 2, '背离'))
    
    # 2. 量比过大（最高3分）- 成交量异常放大
    vol_ratio = latest['VOL_RATIO']
    if vol_ratio >= 4:
        divergence_score += 3
        signals.append(('巨量成交', f'量比={vol_ratio:.1f}', 3, '背离'))
    elif vol_ratio >= 2.5:
        divergence_score += 2
        signals.append(('明显放量', f'量比={vol_ratio:.1f}', 2, '背离'))
    elif vol_ratio >= 1.8:
        divergence_score += 1
        signals.append(('放量', f'量比={vol_ratio:.1f}', 1, '背离'))
    
    # 3. 价格新高量未新高（最高4分）- 经典量价背离
    if last5_prev is not None and len(last5_prev) >= 5:
        price_new_high = latest['close'] >= last5_prev['high'].max()
        vol_new_high = latest['volume'] >= last5_prev['volume'].max()
        if price_new_high and not vol_new_high:
            vol_ratio_5d = latest['volume'] / last5_prev['volume'].max()
            if vol_ratio_5d < 0.7:
                divergence_score += 4
                signals.append(('量价背离', f'价新高量仅{vol_ratio_5d*100:.0f}%', 4, '背离'))
            elif vol_ratio_5d < 0.85:
                divergence_score += 3
                signals.append(('明显背离', f'价新高量仅{vol_ratio_5d*100:.0f}%', 3, '背离'))
            else:
                divergence_score += 2
                signals.append(('轻度背离', '价新高量未新高', 2, '背离'))
    
    # 4. 价涨量缩趋势（最高4分）- 价格涨但量能萎缩
    if len(indicators) >= 10:
        last10 = indicators.iloc[-10:]
        price_recent = last5['close'].mean()
        price_prev = last10.iloc[:5]['close'].mean()
        price_up = price_recent > price_prev * 1.02  # 价格涨2%以上
        
        vol_recent = last5['volume'].mean()
        vol_prev = last10.iloc[:5]['volume'].mean()
        vol_down = vol_recent < vol_prev
        
        if price_up and vol_down:
            vol_ratio_trend = vol_recent / vol_prev
            if vol_ratio_trend < 0.6:
                divergence_score += 4
                signals.append(('价涨量缩', f'量缩{((1-vol_ratio_trend)*100):.0f}%', 4, '背离'))
            elif vol_ratio_trend < 0.75:
                divergence_score += 3
                signals.append(('价涨量平', f'量缩{((1-vol_ratio_trend)*100):.0f}%', 3, '背离'))
            else:
                divergence_score += 2
                signals.append(('量能未跟随', '量能未跟随上涨', 2, '背离'))
    
    # 5. VR容量比率过高（最高2分）
    vr = latest['VR']
    if vr >= 250:
        divergence_score += 2
        signals.append(('VR过热', f'VR={vr:.0f}', 2, '背离'))
    elif vr >= 180:
        divergence_score += 1
        signals.append(('VR偏热', f'VR={vr:.0f}', 1, '背离'))
    
    # 6. 连续放量天数过多（最高2分）- 新增
    if len(indicators) >= 10:
        # 统计近10天内放量（量比>1.5）的天数
        vol_up_days = 0
        for i in range(-10, 0):
            if len(indicators) + i >= 0:
                if indicators.iloc[i]['VOL_RATIO'] > 1.5:
                    vol_up_days += 1
        if vol_up_days >= 7:
            divergence_score += 2
            signals.append(('持续放量', f'近10天{vol_up_days}天放量', 2, '背离'))
        elif vol_up_days >= 5:
            divergence_score += 1
            signals.append(('频繁放量', f'近10天{vol_up_days}天放量', 1, '背离'))
    
    # 7. 高位天量（最高4分）- 价格在高位+天量，典型见顶信号
    if len(indicators) >= 60:
        # 判断是否在高位
        pct60 = latest.get('PRICE_PCT_60', 50)
        is_high_price = pct60 >= 90
        
        # 判断是否天量
        vol_ma20 = indicators.iloc[-20:]['volume'].mean()
        vol_ratio_20 = latest['volume'] / vol_ma20 if vol_ma20 > 0 else 0
        is_sky_vol = vol_ratio_20 >= 2.5
        
        if is_high_price and is_sky_vol:
            divergence_score += 4
            signals.append(('高位天量', f'量是20日均量{vol_ratio_20:.1f}倍，价格{pct60:.0f}%分位', 4, '背离'))
        elif is_high_price and vol_ratio_20 >= 1.8:
            divergence_score += 2
            signals.append(('高位放量', f'量是20日均量{vol_ratio_20:.1f}倍', 2, '背离'))
    
    # 9. 黄金分割位量价背离（最高5分）- 上涨到关键位时量价背离，更容易见顶
    # 量价背离本身就说明是在上涨，所以不需要额外判断趋势
    if len(indicators) >= 60:
        pct60 = latest.get('PRICE_PCT_60', 50)
        current_price = latest['close']
        
        # 自适应选择低点：高位用60日低点（趋势行情），低位用20日低点（反弹行情）
        if pct60 >= 70:
            # 趋势性上涨，用60日低点
            low_ref = indicators['low'].iloc[-60:].min()
            ref_period = '60日'
        else:
            # 超跌反弹，用20日低点
            low_ref = indicators['low'].iloc[-20:].min()
            ref_period = '20日'
        
        rise_pct = (current_price - low_ref) / low_ref * 100
        
        # 只有在有量价背离基础信号时才加
        has_basic_divergence = divergence_score >= 2
        
        if has_basic_divergence and rise_pct >= 61.8:
            divergence_score += 5
            signals.append(('黄金分割0.618背离', f'从{ref_period}低点涨{rise_pct:.1f}%', 5, '背离'))
        elif has_basic_divergence and rise_pct >= 50:
            divergence_score += 3
            signals.append(('黄金分割0.5背离', f'从{ref_period}低点涨{rise_pct:.1f}%', 3, '背离'))
        elif has_basic_divergence and rise_pct >= 38.2:
            divergence_score += 2
            signals.append(('黄金分割0.382背离', f'从{ref_period}低点涨{rise_pct:.1f}%', 2, '背离'))
    
    # 8. 高位放量长上影（最高3分）- 量价+形态结合的见顶信号
    high = latest['high']
    low = latest['low']
    close = latest['close']
    open_price = latest['open']
    body = abs(close - open_price)
    upper_shadow = high - max(close, open_price)
    total_range = high - low if high > low else 0.01
    
    # 判断是否在高位
    pct60 = latest.get('PRICE_PCT_60', 50)
    is_high_price = pct60 >= 85
    
    # 判断是否放量
    vol_ratio = latest['VOL_RATIO']
    is_vol_up = vol_ratio >= 1.5
    
    if is_high_price and is_vol_up and upper_shadow > body * 2 and upper_shadow / total_range > 0.5:
        divergence_score += 3
        signals.append(('高位放量长上影', f'上影占比{upper_shadow/total_range*100:.0f}%，量比{vol_ratio:.1f}', 3, '背离'))
    elif is_high_price and upper_shadow > body * 1.5:
        divergence_score += 1
        signals.append(('高位长上影', f'上影是实体{upper_shadow/body:.1f}倍', 1, '背离'))
    
    # BOLL修正：价格在上轨或之上时，量价背离信号更可信，乘以1.2
    boll_pct = latest['BOLL_PCT']
    if boll_pct >= 100:
        divergence_score *= 1.2
    
    divergence_score = min(divergence_score, 15)
    total_score += divergence_score
    
    # ============================================================
    # 维度四：趋势衰竭（20分）
    # 逻辑：趋势指标出现衰竭信号，分数越高
    # ============================================================
    exhaustion_score = 0
    
    # MACD顶背离检测（最高6分）
    if prev10 is not None and len(indicators) >= 30:
        # 在最近30天内找两个最高价的点进行比较
        last30 = indicators.iloc[-30:].copy().reset_index(drop=True)
        
        # 找最高价（第一个高点）
        first_high_idx = last30['high'].idxmax()
        first_high_price = last30.loc[first_high_idx, 'high']
        first_high_dif = last30.loc[first_high_idx, 'DIF']
        first_high_macd = last30.loc[first_high_idx, 'MACD']
        first_high_close = last30.loc[first_high_idx, 'close']
        
        # 找第二高价（第二个高点）- 排除第一个高点附近
        remaining = last30.drop(first_high_idx)
        second_high_idx = remaining['high'].idxmax()
        second_high_price = remaining.loc[second_high_idx, 'high']
        second_high_dif = remaining.loc[second_high_idx, 'DIF']
        second_high_macd = remaining.loc[second_high_idx, 'MACD']
        second_high_close = remaining.loc[second_high_idx, 'close']
        
        # 两个高点间隔至少5天
        days_between = abs(second_high_idx - first_high_idx)
        
        if days_between >= 5:
            # 确定哪个是早高点，哪个是晚高点
            if first_high_idx < second_high_idx:
                early_high_price = first_high_price
                early_high_dif = first_high_dif
                early_high_macd = first_high_macd
                early_high_close = first_high_close
                late_high_price = second_high_price
                late_high_dif = second_high_dif
                late_high_macd = second_high_macd
                late_high_close = second_high_close
            else:
                early_high_price = second_high_price
                early_high_dif = second_high_dif
                early_high_macd = second_high_macd
                early_high_close = second_high_close
                late_high_price = first_high_price
                late_high_dif = first_high_dif
                late_high_macd = first_high_macd
                late_high_close = first_high_close
            
            # 计算红柱缩小比例
            macd_shrink_ratio = 0
            if early_high_macd > 0:
                macd_shrink_ratio = (early_high_macd - late_high_macd) / early_high_macd
            
            # 最高价顶背离：晚高点最高价创新高，但DIF未创新高
            if late_high_price > early_high_price and late_high_dif < early_high_dif:
                exhaustion_score += 6
                signals.append(('MACD顶背离', '价新高DIF未新高', 6, '衰竭'))
            # 最高价红柱顶背离：晚高点最高价创新高，但MACD红柱缩小超过20%
            elif late_high_price > early_high_price and late_high_macd < early_high_macd and macd_shrink_ratio >= 0.2:
                exhaustion_score += 5
                signals.append(('MACD顶背离', f'价新高红柱缩小{macd_shrink_ratio*100:.0f}%', 5, '衰竭'))
            elif late_high_price > early_high_price and abs(late_high_dif - early_high_dif) / abs(early_high_dif) < 0.1:
                exhaustion_score += 3
                signals.append(('MACD疑似顶背离', 'DIF走平', 3, '衰竭'))
            # 收盘价顶背离：晚高点收盘价创新高，但DIF未创新高
            elif late_high_close > early_high_close and late_high_dif < early_high_dif:
                exhaustion_score += 5
                signals.append(('MACD顶背离', '收盘价新高DIF未新高', 5, '衰竭'))
            # 收盘价红柱顶背离：晚高点收盘价创新高，但MACD红柱缩小超过20%
            elif late_high_close > early_high_close and late_high_macd < early_high_macd and macd_shrink_ratio >= 0.2:
                exhaustion_score += 4
                signals.append(('MACD顶背离', f'收盘价新高红柱缩小{macd_shrink_ratio*100:.0f}%', 4, '衰竭'))
            elif late_high_close > early_high_close and abs(late_high_dif - early_high_dif) / abs(early_high_dif) < 0.1:
                exhaustion_score += 3
                signals.append(('MACD疑似顶背离', '收盘价新高DIF走平', 3, '衰竭'))
    
    # RSI顶背离检测（最高5分）
    if len(indicators) >= 20:
        recent = indicators.iloc[-20:]
        price_high_idx = recent['high'].idxmax()
        price_high_rsi = recent.loc[price_high_idx, 'RSI6']
        
        earlier = indicators.iloc[-40:-20] if len(indicators) >= 40 else indicators.iloc[:-20]
        if len(earlier) > 5:
            earlier_high_idx = earlier['high'].idxmax()
            earlier_high_price = earlier.loc[earlier_high_idx, 'high']
            earlier_high_rsi = earlier.loc[earlier_high_idx, 'RSI6']
            
            current_high = recent['high'].max()
            
            if current_high > earlier_high_price and price_high_rsi < earlier_high_rsi:
                exhaustion_score += 5
                signals.append(('RSI顶背离', '价新高RSI未新高', 5, '衰竭'))
            elif current_high > earlier_high_price and price_high_rsi < earlier_high_rsi * 0.95:
                exhaustion_score += 3
                signals.append(('RSI疑似顶背离', 'RSI明显走弱', 3, '衰竭'))
    
    # MACD红柱缩短（最高4分）
    if prev5 is not None:
        if latest['MACD'] > 0 and latest['MACD'] < prev5['MACD']:
            shrink_ratio = (prev5['MACD'] - latest['MACD']) / prev5['MACD']
            if shrink_ratio > 0.5:
                exhaustion_score += 4
                signals.append(('MACD红柱大幅缩短', f'缩短{shrink_ratio*100:.0f}%', 4, '衰竭'))
            elif shrink_ratio > 0.2:
                exhaustion_score += 2
                signals.append(('MACD红柱缩短', f'缩短{shrink_ratio*100:.0f}%', 2, '衰竭'))
    
    # ADX过高后拐头（最高3分）
    adx = latest['ADX']
    if prev5 is not None:
        if adx > 50 and adx < prev5['ADX']:
            exhaustion_score += 3
            signals.append(('ADX高位拐头', f'ADX={adx:.1f}开始下降', 3, '衰竭'))
        elif adx > 60:
            exhaustion_score += 2
            signals.append(('ADX极端高位', f'ADX={adx:.1f}', 2, '衰竭'))
        elif adx > 50:
            exhaustion_score += 1
            signals.append(('ADX高位', f'ADX={adx:.1f}', 1, '衰竭'))
    
    # 5日线斜率下降（最高3分）
    if prev5 is not None:
        ma5_slope_now = (latest['MA5'] - indicators.iloc[-3]['MA5']) / indicators.iloc[-3]['MA5'] * 100
        ma5_slope_prev = (prev5['MA5'] - indicators.iloc[-8]['MA5']) / indicators.iloc[-8]['MA5'] * 100
        
        if ma5_slope_now < ma5_slope_prev * 0.5 and ma5_slope_prev > 1:
            exhaustion_score += 3
            signals.append(('5日线斜率骤降', f'从{ma5_slope_prev:.1f}%降至{ma5_slope_now:.1f}%', 3, '衰竭'))
        elif ma5_slope_now < ma5_slope_prev:
            exhaustion_score += 1
            signals.append(('5日线斜率下降', '上涨动能减弱', 1, '衰竭'))
    
    # BIAS乖离率过大（最高3分）
    bias6 = latest['BIAS6']
    if abs(bias6) >= 20:
        exhaustion_score += 3
        signals.append(('BIAS极度乖离', f'BIAS6={bias6:.1f}%', 3, '衰竭'))
    elif abs(bias6) >= 15:
        exhaustion_score += 2
        signals.append(('BIAS严重乖离', f'BIAS6={bias6:.1f}%', 2, '衰竭'))
    elif abs(bias6) >= 10:
        exhaustion_score += 1
        signals.append(('BIAS偏大', f'BIAS6={bias6:.1f}%', 1, '衰竭'))
    
    exhaustion_score = min(exhaustion_score, 20)
    total_score += exhaustion_score
    
    # ============================================================
    # 维度五：形态破位（20分）
    # 逻辑：出现破位信号，分数越高
    # ============================================================
    breakdown_score = 0
    
    # 判断当前趋势：上涨趋势中，股价在均线之上是突破，不是破位
    # 上涨趋势定义：收盘价在20日线之上，且5日线在10日线之上（短期多头）
    is_uptrend = latest['close'] > latest['MA20'] and latest['MA5'] > latest['MA10']
    
    # 跌破5日线（最高4分）- 按跌破幅度分档
    if latest['close'] < latest['MA5']:
        break_pct = (latest['MA5'] - latest['close']) / latest['MA5'] * 100
        if break_pct >= 3:
            breakdown_score += 4
            signals.append(('跌破5日线', f'有效跌破，跌{break_pct:.1f}%', 4, '破位'))
        elif break_pct >= 1:
            breakdown_score += 2
            signals.append(('跌破5日线', f'明显跌破，跌{break_pct:.1f}%', 2, '破位'))
        else:
            breakdown_score += 1
            signals.append(('跌破5日线', f'轻微跌破，跌{break_pct:.1f}%', 1, '破位'))
    elif latest['close'] < latest['MA5'] * 1.02 and not is_uptrend:
        # 只有在非上涨趋势中，逼近5日线才算破位预警
        breakdown_score += 2
        signals.append(('逼近5日线', '即将考验支撑', 2, '破位'))
    
    # 跌破10日线（最高4分）- 按跌破幅度分档
    if latest['close'] < latest['MA10']:
        break_pct = (latest['MA10'] - latest['close']) / latest['MA10'] * 100
        if break_pct >= 3:
            breakdown_score += 4
            signals.append(('跌破10日线', f'有效跌破，跌{break_pct:.1f}%', 4, '破位'))
        elif break_pct >= 1:
            breakdown_score += 2
            signals.append(('跌破10日线', f'明显跌破，跌{break_pct:.1f}%', 2, '破位'))
        else:
            breakdown_score += 1
            signals.append(('跌破10日线', f'轻微跌破，跌{break_pct:.1f}%', 1, '破位'))
    elif latest['close'] < latest['MA10'] * 1.02 and not is_uptrend:
        # 只有在非上涨趋势中，逼近10日线才算破位预警
        breakdown_score += 1
        signals.append(('逼近10日线', '中期支撑考验', 1, '破位'))
    
    # 跌破20日线（最高5分）- 中期趋势破位
    if latest['close'] < latest['MA20']:
        break_pct = (latest['MA20'] - latest['close']) / latest['MA20'] * 100
        if break_pct >= 5:
            breakdown_score += 5
            signals.append(('跌破20日线', f'深度跌破，跌{break_pct:.1f}%', 5, '破位'))
        elif break_pct >= 3:
            breakdown_score += 4
            signals.append(('跌破20日线', f'有效跌破，跌{break_pct:.1f}%', 4, '破位'))
        elif break_pct >= 1:
            breakdown_score += 2
            signals.append(('跌破20日线', f'明显跌破，跌{break_pct:.1f}%', 2, '破位'))
        else:
            breakdown_score += 1
            signals.append(('跌破20日线', f'轻微跌破，跌{break_pct:.1f}%', 1, '破位'))
    
    # 跌破60日线（最高6分）- 长期趋势破位
    ma60_breakdown_score = 0
    if latest['close'] < latest['MA60']:
        break_pct = (latest['MA60'] - latest['close']) / latest['MA60'] * 100
        if break_pct >= 10:
            ma60_breakdown_score = 6
            signals.append(('跌破60日线', f'深度跌破，跌{break_pct:.1f}%', 6, '破位'))
        elif break_pct >= 5:
            ma60_breakdown_score = 4
            signals.append(('跌破60日线', f'有效跌破，跌{break_pct:.1f}%', 4, '破位'))
        elif break_pct >= 2:
            ma60_breakdown_score = 2
            signals.append(('跌破60日线', f'明显跌破，跌{break_pct:.1f}%', 2, '破位'))
        else:
            ma60_breakdown_score = 1
            signals.append(('跌破60日线', f'轻微跌破，跌{break_pct:.1f}%', 1, '破位'))
        breakdown_score += ma60_breakdown_score
    
    # 跌破布林中轨（最高6分）- 按布林带位置分档
    boll_pct = latest['BOLL_PCT']
    if boll_pct < 50:
        # 跌破中轨，按跌破程度给分
        if boll_pct < 20:
            breakdown_score += 6
            signals.append(('跌破布林中轨', f'接近下轨，位置{boll_pct:.0f}%', 6, '破位'))
        elif boll_pct < 40:
            breakdown_score += 4
            signals.append(('跌破布林中轨', f'通道下半部，位置{boll_pct:.0f}%', 4, '破位'))
        else:
            breakdown_score += 2
            signals.append(('跌破布林中轨', f'刚跌破中轨，位置{boll_pct:.0f}%', 2, '破位'))
    elif boll_pct < 60:
        breakdown_score += 1
        signals.append(('布林位置下移', '接近中轨', 1, '破位'))
    
    # MACD死叉（最高5分）
    if latest['DIF'] < latest['DEA']:
        # 判断是否刚死叉
        if len(indicators) >= 3 and indicators.iloc[-2]['DIF'] >= indicators.iloc[-2]['DEA']:
            breakdown_score += 5
            signals.append(('MACD刚死叉', '趋势确认反转', 5, '破位'))
        else:
            breakdown_score += 3
            signals.append(('MACD死叉中', '空头趋势', 3, '破位'))
    
    # MACD零轴下运行（最高5分）- 中期趋势走坏
    if latest['DIF'] < 0 and latest['DEA'] < 0:
        breakdown_score += 5
        signals.append(('MACD零轴下', '中期趋势走弱', 5, '破位'))
    
    # 均线空头排列（最高10分）- 5日<10日<20日，下跌趋势确认，和多头排列对称
    if latest['MA5'] < latest['MA10'] and latest['MA10'] < latest['MA20']:
        # RSI阈值判断：RSI越低，空头排列的加分越少
        # 因为RSI极低的时候，即使是空头排列，也可能是超跌反弹，风险没那么高
        rsi6 = latest['RSI6']
        if rsi6 <= 20:
            # RSI极低，超卖严重，空头排列也没那么危险，可能随时反弹，不加分
            bearish_bonus = 0.0
        elif rsi6 <= 30:
            # RSI偏低，加分打5折
            bearish_bonus = 0.5
        else:
            # RSI正常，正常加分
            bearish_bonus = 1.0
        
        bearish_score = 10 * bearish_bonus
        if bearish_score > 0:
            breakdown_score += bearish_score
            if bearish_bonus == 1.0:
                signals.append(('均线空头排列', '5日<10日<20日，下跌趋势确认', bearish_score, '破位'))
            elif bearish_bonus == 0.5:
                signals.append(('均线空头排列', f'RSI={rsi6:.1f}偏低，加分打5折', bearish_score, '破位'))
    
    # 均线向下发散（最高3分）- 短期均线在长期均线下方且距离扩大
    elif latest['MA5'] < latest['MA10'] and latest['MA5'] < latest['MA20']:
        # 计算均线间距
        ma5_ma10_gap = (latest['MA10'] - latest['MA5']) / latest['MA10'] * 100
        if ma5_ma10_gap >= 3:
            breakdown_score += 3
            signals.append(('均线向下发散', f'5日偏离10日{ma5_ma10_gap:.1f}%', 3, '破位'))
        elif ma5_ma10_gap >= 1:
            breakdown_score += 2
            signals.append(('均线向下', f'5日偏离10日{ma5_ma10_gap:.1f}%', 2, '破位'))
    
    # KDJ死叉（最高4分）
    if latest['K'] < latest['D']:
        if len(indicators) >= 3 and indicators.iloc[-2]['K'] >= indicators.iloc[-2]['D']:
            breakdown_score += 4
            signals.append(('KDJ刚死叉', '短期回调开始', 4, '破位'))
        else:
            breakdown_score += 2
            signals.append(('KDJ死叉中', '短期偏弱', 2, '破位'))
    
    # RSI死叉（最高2分）
    if latest['RSI6'] < latest['RSI12']:
        breakdown_score += 2
        signals.append(('RSI死叉', '动量转弱', 2, '破位'))
    
    # SAR跌破（最高2分）
    if latest['close'] < latest['SAR']:
        breakdown_score += 2
        signals.append(('跌破SAR', '抛物线转空', 2, '破位'))
    
    # 放量下跌（最高3分）
    if latest['pct_chg'] < -3 and latest['VOL_RATIO'] > 1.5:
        breakdown_score += 3
        signals.append(('放量下跌', f'跌{latest["pct_chg"]:.1f}%，量比{latest["VOL_RATIO"]:.1f}', 3, '破位'))
    elif latest['pct_chg'] < -5:
        breakdown_score += 2
        signals.append(('大跌', f'跌{latest["pct_chg"]:.1f}%', 2, '破位'))
    
    # 高位长上影线（最高6分）- 典型见顶K线形态，权重提高
    high = latest['high']
    low = latest['low']
    close = latest['close']
    open_price = latest['open']
    body = abs(close - open_price)
    upper_shadow = high - max(close, open_price)
    lower_shadow = min(close, open_price) - low
    total_range = high - low if high > low else 0.01
    
    # 判断是否在高位
    pct60 = latest.get('PRICE_PCT_60', 50)
    is_high_price = pct60 >= 85
    
    if is_high_price and upper_shadow / total_range > 0.8:
        breakdown_score += 6
        signals.append(('高位超长上影', f'上影占比{upper_shadow/total_range*100:.0f}%，冲顶失败', 6, '破位'))
    elif is_high_price and upper_shadow / total_range > 0.6:
        breakdown_score += 4
        signals.append(('高位长上影', f'上影占比{upper_shadow/total_range*100:.0f}%', 4, '破位'))
    elif is_high_price and upper_shadow > body * 2 and upper_shadow > lower_shadow:
        breakdown_score += 3
        signals.append(('高位上影线很长', f'上影是实体{upper_shadow/body:.1f}倍', 3, '破位'))
    elif is_high_price and upper_shadow > body * 1.5:
        breakdown_score += 2
        signals.append(('高位上影线偏长', f'上影是实体{upper_shadow/body:.1f}倍', 2, '破位'))
    
    # 高位十字星（最高3分）- 多空分歧加剧
    if is_high_price and body < total_range * 0.1 and total_range > 0:
        breakdown_score += 3
        signals.append(('高位十字星', f'振幅{total_range/open_price*100:.1f}%，实体极小', 3, '破位'))
    elif is_high_price and body < total_range * 0.2 and total_range > 0:
        breakdown_score += 1
        signals.append(('高位小实体', '多空分歧加大', 1, '破位'))
    
    # 冲高回落（最高4分）- 最高价创新高但收盘价没创新高，典型见顶信号
    if len(indicators) >= 20:
        last20 = indicators.iloc[-20:-1]  # 前20天（不含当天）
        max_high_20d = last20['high'].max()
        max_close_20d = last20['close'].max()
        
        high_new_high = high >= max_high_20d
        close_not_new_high = close < max_close_20d
        
        if high_new_high and close_not_new_high and is_high_price:
            # 冲高回落，最高价创新高但收盘价没创新高
            upper_shadow_ratio = upper_shadow / total_range
            if upper_shadow_ratio > 0.7:
                breakdown_score += 4
                signals.append(('冲高回落', f'最高价创新高，收{upper_shadow_ratio*100:.0f}%上影', 4, '破位'))
            elif upper_shadow_ratio > 0.5:
                breakdown_score += 3
                signals.append(('冲高回落', '最高价创新高，上影明显', 3, '破位'))
            else:
                breakdown_score += 2
                signals.append(('冲高回落', '最高价创新高，收盘回落', 2, '破位'))
    
    # 二次冲顶/M头（最高8分）- 两个高点接近，第二个量能不足，强烈见顶信号
    if len(indicators) >= 30 and is_high_price:
        # 找最近30天内的两个最高的高点
        last30 = indicators.iloc[-30:].copy()
        last30 = last30.reset_index(drop=True)
        
        # 找最高价（第一个高点）
        first_high_idx = last30['high'].idxmax()
        first_high_price = last30.loc[first_high_idx, 'high']
        first_high_volume = last30.loc[first_high_idx, 'volume']
        first_high_close = last30.loc[first_high_idx, 'close']
        
        # 找第二高价（第二个高点）- 排除第一个高点附近
        remaining = last30.drop(first_high_idx)
        second_high_idx = remaining['high'].idxmax()
        second_high_price = remaining.loc[second_high_idx, 'high']
        second_high_volume = remaining.loc[second_high_idx, 'volume']
        second_high_close = remaining.loc[second_high_idx, 'close']
        
        # 两个高点的间隔天数
        days_between = abs(second_high_idx - first_high_idx)
        
        # 判断是否符合M头/二次冲顶
        if days_between >= 5:
            # 两个高点价格接近（差距在5%以内）
            price_diff = abs(second_high_price - first_high_price) / first_high_price * 100
            if price_diff <= 5:
                # 确定哪个是第一个高点（时间早的），哪个是第二个（时间晚的）
                if first_high_idx < second_high_idx:
                    early_high_price = first_high_price
                    early_high_volume = first_high_volume
                    late_high_price = second_high_price
                    late_high_volume = second_high_volume
                    late_high_close = second_high_close
                    late_high_low = remaining.loc[second_high_idx, 'low']
                else:
                    early_high_price = second_high_price
                    early_high_volume = second_high_volume
                    late_high_price = first_high_price
                    late_high_volume = first_high_volume
                    late_high_close = first_high_close
                    late_high_low = last30.loc[first_high_idx, 'low']
                
                m_head_score = 0
                m_head_desc = []
                
                # 双顶高度接近
                if price_diff <= 2:
                    m_head_score += 3
                    m_head_desc.append('双顶高度几乎一致')
                else:
                    m_head_score += 2
                    m_head_desc.append('双顶高度接近')
                
                # 第二个高点量能更低（量价背离）
                if late_high_volume < early_high_volume:
                    m_head_score += 2
                    m_head_desc.append('二次量能不足')
                
                # 第二个高点收长上影
                late_upper_shadow = late_high_price - max(late_high_close, last30.loc[first_high_idx if first_high_idx > second_high_idx else second_high_idx, 'open'])
                late_total_range = late_high_price - late_high_low
                late_upper_ratio = late_upper_shadow / late_total_range if late_total_range > 0 else 0
                
                if late_upper_ratio > 0.5:
                    m_head_score += 3
                    m_head_desc.append('二次冲顶留长上影')
                
                if m_head_score >= 4:
                    breakdown_score += min(m_head_score, 8)
                    signals.append(('二次冲顶/M头', '，'.join(m_head_desc), min(m_head_score, 8), '破位'))
    
    breakdown_score = min(breakdown_score, 20)
    total_score += breakdown_score
    
    # ============================================================
    # 维度六：板块情绪（已移除）
    # ============================================================
    sector_score = 0
    
    # ============================================================
    # 见底信号（减分项）- 超卖 + 底背离
    # 逻辑：出现底部信号，从总分中减去，避免下跌到底部还显示高危
    # 最高减30分，和超买对应
    # ============================================================
    bottom_score = 0
    
    # 判断是否还在下跌趋势/低位，只有下跌时才算见底信号
    pct60 = latest.get('PRICE_PCT_60', 50)
    is_downtrend = breakdown_score >= 5 or pct60 < 50
    
    # 1. RSI超卖（最高7分）
    rsi6 = latest['RSI6']
    if rsi6 <= 15:
        bottom_score += 7
        signals.append(('RSI极度超卖', f'RSI6={rsi6:.1f}', -7, '见底'))
    elif rsi6 <= 20:
        bottom_score += 5
        signals.append(('RSI严重超卖', f'RSI6={rsi6:.1f}', -5, '见底'))
    elif rsi6 <= 30:
        bottom_score += 3
        signals.append(('RSI超卖', f'RSI6={rsi6:.1f}', -3, '见底'))
    
    # 2. KDJ超卖（最高5分）
    if latest['J'] <= 0:
        bottom_score += 5
        signals.append(('KDJ极度超卖', f'J值={latest["J"]:.1f}', -5, '见底'))
    elif latest['J'] <= 10:
        bottom_score += 3
        signals.append(('KDJ严重超卖', f'J值={latest["J"]:.1f}', -3, '见底'))
    elif latest['J'] <= 20:
        bottom_score += 2
        signals.append(('KDJ超卖', f'J值={latest["J"]:.1f}', -2, '见底'))
    
    # 3. WR超卖（最高3分）
    wr = latest['WR14']
    if wr >= 90:
        bottom_score += 3
        signals.append(('WR极度超卖', f'WR14={wr:.1f}', -3, '见底'))
    elif wr >= 80:
        bottom_score += 2
        signals.append(('WR超卖', f'WR14={wr:.1f}', -2, '见底'))
    
    # 4. BIAS负乖离过大（最高3分）
    bias6 = latest['BIAS6']
    if bias6 <= -20:
        bottom_score += 3
        signals.append(('BIAS极度负乖离', f'BIAS6={bias6:.1f}%', -3, '见底'))
    elif bias6 <= -15:
        bottom_score += 2
        signals.append(('BIAS严重负乖离', f'BIAS6={bias6:.1f}%', -2, '见底'))
    elif bias6 <= -10:
        bottom_score += 1
        signals.append(('BIAS负乖离偏大', f'BIAS6={bias6:.1f}%', -1, '见底'))
    
    # 5. MACD底背离（最高4分）
    if len(indicators) >= 30:
        # 在最近30天内找两个最低价的点进行比较
        last30 = indicators.iloc[-30:].copy().reset_index(drop=True)
        
        # 找最低价（第一个低点）
        first_low_idx = last30['low'].idxmin()
        first_low_price = last30.loc[first_low_idx, 'low']
        first_low_dif = last30.loc[first_low_idx, 'DIF']
        first_low_macd = last30.loc[first_low_idx, 'MACD']
        first_low_close = last30.loc[first_low_idx, 'close']
        
        # 找第二低价（第二个低点）
        remaining = last30.drop(first_low_idx)
        second_low_idx = remaining['low'].idxmin()
        second_low_price = remaining.loc[second_low_idx, 'low']
        second_low_dif = remaining.loc[second_low_idx, 'DIF']
        second_low_macd = remaining.loc[second_low_idx, 'MACD']
        second_low_close = remaining.loc[second_low_idx, 'close']
        
        # 两个低点间隔至少5天
        days_between = abs(second_low_idx - first_low_idx)
        
        if days_between >= 5:
            # 确定哪个是早低点，哪个是晚低点
            if first_low_idx < second_low_idx:
                early_low_price = first_low_price
                early_low_dif = first_low_dif
                early_low_macd = first_low_macd
                early_low_close = first_low_close
                late_low_price = second_low_price
                late_low_dif = second_low_dif
                late_low_macd = second_low_macd
                late_low_close = second_low_close
            else:
                early_low_price = second_low_price
                early_low_dif = second_low_dif
                early_low_macd = second_low_macd
                early_low_close = second_low_close
                late_low_price = first_low_price
                late_low_dif = first_low_dif
                late_low_macd = first_low_macd
                late_low_close = first_low_close
            
            # 计算绿柱缩小比例（MACD为负数时，绝对值缩小）
            macd_green_shrink_ratio = 0
            if early_low_macd < 0 and late_low_macd < 0:
                macd_green_shrink_ratio = (abs(early_low_macd) - abs(late_low_macd)) / abs(early_low_macd)
            
            # 最低价底背离：晚低点最低价创新低，但DIF未创新低
            if late_low_price < early_low_price and late_low_dif > early_low_dif:
                bottom_score += 4
                signals.append(('MACD底背离', '价新低DIF未新低', -4, '见底'))
            # 最低价绿柱底背离：晚低点最低价创新低，但绿柱缩小超过20%
            elif late_low_price < early_low_price and late_low_macd > early_low_macd and macd_green_shrink_ratio >= 0.2:
                bottom_score += 3
                signals.append(('MACD底背离', f'价新低绿柱缩小{macd_green_shrink_ratio*100:.0f}%', -3, '见底'))
            # 收盘价底背离：晚低点收盘价创新低，但DIF未创新低
            elif late_low_close < early_low_close and late_low_dif > early_low_dif:
                bottom_score += 3
                signals.append(('MACD底背离', '收盘价新低DIF未新低', -3, '见底'))
            # 收盘价绿柱底背离：晚低点收盘价创新低，但绿柱缩小超过20%
            elif late_low_close < early_low_close and late_low_macd > early_low_macd and macd_green_shrink_ratio >= 0.2:
                bottom_score += 2
                signals.append(('MACD底背离', f'收盘价新低绿柱缩小{macd_green_shrink_ratio*100:.0f}%', -2, '见底'))
    
    # 6. RSI底背离（最高3分）
    if len(indicators) >= 30:
        # 在最近30天内找两个最低价的点进行比较（与MACD底背离逻辑一致）
        last30 = indicators.iloc[-30:].copy().reset_index(drop=True)
        
        # 找最低价（第一个低点）
        first_low_idx = last30['low'].idxmin()
        first_low_price = last30.loc[first_low_idx, 'low']
        first_low_rsi = last30.loc[first_low_idx, 'RSI6']
        
        # 找第二低价（第二个低点）
        remaining = last30.drop(first_low_idx)
        second_low_idx = remaining['low'].idxmin()
        second_low_price = remaining.loc[second_low_idx, 'low']
        second_low_rsi = remaining.loc[second_low_idx, 'RSI6']
        
        # 两个低点间隔至少5天
        days_between = abs(second_low_idx - first_low_idx)
        
        if days_between >= 5:
            # 确定哪个是早低点，哪个是晚低点
            if first_low_idx < second_low_idx:
                early_low_price = first_low_price
                early_low_rsi = first_low_rsi
                late_low_price = second_low_price
                late_low_rsi = second_low_rsi
            else:
                early_low_price = second_low_price
                early_low_rsi = second_low_rsi
                late_low_price = first_low_price
                late_low_rsi = first_low_rsi
            
            # 最低价底背离：晚低点最低价创新低，但RSI未创新低
            if late_low_price < early_low_price and late_low_rsi > early_low_rsi:
                bottom_score += 3
                signals.append(('RSI底背离', '价新低RSI未新低', -3, '见底'))
    
    # 7. 黄金分割跌幅（最高5分）- 从高点下跌到关键支撑位
    if len(indicators) >= 60:
        high_60d = indicators['high'].iloc[-60:].max()
        current_price = latest['close']
        decline_pct = (high_60d - current_price) / high_60d * 100
        
        if decline_pct >= 61.8:
            bottom_score += 5
            signals.append(('黄金分割0.618', f'从高点下跌{decline_pct:.1f}%', -5, '见底'))
        elif decline_pct >= 50:
            bottom_score += 3
            signals.append(('黄金分割0.5', f'从高点下跌{decline_pct:.1f}%', -3, '见底'))
        elif decline_pct >= 38.2:
            bottom_score += 2
            signals.append(('黄金分割0.382', f'从高点下跌{decline_pct:.1f}%', -2, '见底'))
    
    # 如果不是下跌趋势，清空间底信号（已经反弹了就不算见底了）
    if not is_downtrend:
        bottom_score = 0
        signals = [s for s in signals if s[3] != '见底']
    
    # BOLL修正：价格越低，见底信号越可信（和超买反过来）
    # 超卖用当天最低价计算布林位置（因为超卖关注当天最低跌到什么位置）
    boll_pct = latest['BOLL_PCT_LOW']
    if bottom_score > 0:
        original_bottom = bottom_score
        if boll_pct <= 0:
            # 下轨及以下，见底信号最可信
            boll_multiplier = 1.2
            boll_desc = '最低价跌破下轨，见底信号最可信'
        elif boll_pct < 50:
            # 下轨到中轨，从1.2线性降到0.7
            boll_multiplier = 1.2 - boll_pct / 50 * 0.5
            boll_desc = '最低价在下轨到中轨，见底信号较可信'
        elif boll_pct < 100:
            # 中轨到上轨，从0.7线性降到0.4
            boll_multiplier = 0.7 - (boll_pct - 50) / 50 * 0.3
            boll_desc = '最低价在中轨到上轨，见底信号可信度较低'
        else:
            # 上轨及以上，见底信号最不可信
            boll_multiplier = 0.4
            boll_desc = '最低价在上轨及以上，见底信号最不可信'
        
        bottom_score *= boll_multiplier
        if boll_multiplier != 1.0:
            adjustments.append(f'BOLL位置修正（最低价）：布林位置{boll_pct:.1f}%（{boll_desc}），见底信号{original_bottom:.1f}分×{boll_multiplier:.2f}，调整为{bottom_score:.1f}分')
    
    # 趋势健康信号（减分项）- 上涨趋势健康时，风险降低
    # 和空头排列对称
    is_bullish = latest['MA5'] > latest['MA10'] and latest['MA10'] > latest['MA20']
    price_above_ma5 = latest['close'] > latest['MA5']
    
    # 只有当股价还在5日线之上时，多头排列才减分
    # 如果已经跌破5日线，说明短期趋势走坏，即使均线还是多头排列也不减分
    if is_bullish and price_above_ma5:
        health_score = 0
        
        # RSI阈值判断：RSI越高，多头排列的减分越少
        # 因为RSI极高的时候，即使是多头排列，也可能是加速冲顶，风险很高
        rsi6 = latest['RSI6']
        if rsi6 >= 80:
            # RSI极高，超买严重，多头排列也不安全，不减分
            bullish_discount = 0.0
        elif rsi6 >= 70:
            # RSI偏高，减分打5折
            bullish_discount = 0.5
        else:
            # RSI正常，正常减分
            bullish_discount = 1.0
        
        # 均线多头排列（-6分）- 和空头排列对称
        bullish_score = 6 * bullish_discount
        if bullish_score > 0:
            health_score += bullish_score
            if bullish_discount == 1.0:
                signals.append(('均线多头排列', '5日>10日>20日，上涨趋势健康', -bullish_score, '见底'))
            elif bullish_discount == 0.5:
                signals.append(('均线多头排列', f'RSI={rsi6:.1f}偏高，减分打5折', -bullish_score, '见底'))
        
        # MACD零轴上（-3分）- 和MACD零轴下对称
        if latest['DIF'] > 0 and latest['DEA'] > 0:
            macd_score = 3 * bullish_discount
            if macd_score > 0:
                health_score += macd_score
                signals.append(('MACD零轴上', '中期趋势健康', -macd_score, '见底'))
        
        # 股价在所有均线之上（-2分）
        if latest['close'] > latest['MA5'] and latest['close'] > latest['MA10'] and latest['close'] > latest['MA20']:
            ma_score = 2 * bullish_discount
            if ma_score > 0:
                health_score += ma_score
                signals.append(('站稳所有均线', '股价在5/10/20日线上方', -ma_score, '见底'))
        
        bottom_score += health_score
    
    bottom_score = min(bottom_score, 35)
    # 见底缓冲已移除（2026-08-18）：见底信号不再从总分中扣除，仅保留趋势状态判断与降权参考
    
    # ============================================================
    # 根据见底信号动态调整跌破60日线的权重
    # 逻辑：跌得越多，见底信号越强，跌破60日线的权重越低
    # 因为刚跌破60日线风险高，已经跌了很多在底部反弹风险低
    # ============================================================
    if ma60_breakdown_score > 0:
        if bottom_score >= 15:
            # 严重超卖/底部区域，跌破60日线的权重打3折
            ma60_discount = 0.3
        elif bottom_score >= 10:
            # 明显超卖，跌破60日线的权重打5折
            ma60_discount = 0.5
        elif bottom_score >= 5:
            # 开始超卖，跌破60日线的权重打7折
            ma60_discount = 0.7
        else:
            # 还没怎么超卖，正常权重
            ma60_discount = 1.0
        
        # 调整跌破60日线的分数
        adjusted_ma60_score = ma60_breakdown_score * ma60_discount
        breakdown_score = breakdown_score - ma60_breakdown_score + adjusted_ma60_score
        total_score = total_score - ma60_breakdown_score + adjusted_ma60_score
        if ma60_discount < 1.0:
            adjustments.append(f'跌破60日线降权：见底信号{bottom_score:.0f}分，跌破60日线{ma60_breakdown_score:.1f}分打{ma60_discount:.1f}折，调整为{adjusted_ma60_score:.1f}分')
    
    # ============================================================
    # 风险等级判定 + 状态诊断
    # ============================================================
    total_score = min(total_score, 100)
    
    # 特殊判断：加速冲顶阶段
    # 触发条件：5日涨幅≥30% + 超买≥15分 + 破位<5分
    is_parabolic = False
    if len(indicators) >= 6:
        pct_5d = (latest['close'] - indicators.iloc[-6]['close']) / indicators.iloc[-6]['close'] * 100
        if pct_5d >= 30 and overbought_score >= 11 and breakdown_score < 3:
            is_parabolic = True
    
    # 趋势状态判断（阈值按新满分设计：破位20分 / 超买25分）
    if bottom_score >= 20 and breakdown_score >= 12:
        # 严重超卖 + 形态破位严重 → 超跌见底，强支撑位
        trend_status = '超跌见底'
        trend_color = '🟣'
    elif bottom_score >= 15 and breakdown_score >= 8:
        # 明显超卖 + 形态破位 → 底部区域
        trend_status = '底部区域'
        trend_color = '🟢'
    elif bottom_score >= 10 and latest['pct_chg'] > 3:
        # 超卖 + 大涨 → 见底反弹
        trend_status = '见底反弹'
        trend_color = '🟢'
    elif breakdown_score >= 12 and overbought_score < 7:
        # 形态破位严重，超买已经消化，处于下跌趋势
        trend_status = '下跌趋势'
        trend_color = '🔵'
    elif breakdown_score >= 8:
        # 形态已经破位，见顶确认
        trend_status = '见顶确认'
        trend_color = '🔴'
    elif is_parabolic:
        # 加速冲顶
        trend_status = '加速冲顶'
        trend_color = '🟠'
    elif overbought_score >= 14:
        # 严重超买，见顶风险高
        trend_status = '高位超买'
        trend_color = '🟡'
    elif overbought_score >= 7:
        # 轻度超买
        trend_status = '上涨趋势'
        trend_color = '🟢'
    else:
        # 超买不高，趋势健康
        trend_status = '趋势健康'
        trend_color = '🟢'
    
    # 风险等级判断 - 不仅看总分，还要看形态破位程度
    # 形态破位是确认信号，权重更高
    effective_score = total_score
    
    # 多头排列降权：如果均线还是多头排列，说明中期趋势没走坏
    # 可能只是正常回调，形态破位的权重打6折
    # 但如果当日大跌超过6%，说明跌得很猛，不应该降权
    # 只有当日下跌且形态破位>0时才降权（上涨时多头排列是正常的，不需要降权）
    is_bullish = latest['MA5'] > latest['MA10'] and latest['MA10'] > latest['MA20']
    pct_chg = latest.get('pct_chg', 0)
    if is_bullish and pct_chg < 0 and pct_chg >= -6 and breakdown_score > 0:
        old_bd = breakdown_score
        breakdown_score = breakdown_score * 0.6
        total_score += (breakdown_score - old_bd)
        adjustments.append(f'多头排列降权：均线多头排列且当日跌幅{pct_chg:.2f}%<6%，形态破位{old_bd:.1f}分打6折，调整为{breakdown_score:.1f}分')
    
    # 加速上涨后放量大跌加权：
    # 逻辑：加速上涨后突然放量大跌，往往是见顶信号，形态破位权重应该更高
    if len(indicators) >= 20:
        # 找到前面一个高点（20日内，不包括最近3天）
        high_series = indicators['high'].iloc[-20:-3]
        high_idx = high_series.idxmax()
        current_idx = len(indicators) - 1
        days_after_high = current_idx - high_idx
        
        # 找到高点之后5日内的最低点
        low_after_high = indicators['low'].iloc[high_idx:high_idx+5].min()
        
        # 从低点算起的涨幅
        pct_from_low = (latest['close'] - low_after_high) / low_after_high * 100
        is_accelerating = pct_from_low > 20
        
        # 高点后3天内放量大跌
        is_drop_from_high = days_after_high <= 3
        
        vol_ratio = latest.get('VOL_RATIO', 1)
        # 计算成交量20日分位
        vol_20 = indicators['volume'].iloc[-20:]
        vol_percentile = (vol_20 < latest['volume']).sum() / len(vol_20) * 100
        is_high_volume = vol_ratio >= 1.5 or vol_percentile >= 70
        
        is_big_drop = pct_chg <= -6
        
        # 满足任一条件就加权：加速上涨后放量大跌，或高点后3天内放量大跌
        if (is_accelerating or is_drop_from_high) and is_high_volume and is_big_drop:
            old_bd = breakdown_score
            # 形态破位按跌幅加权
            if pct_chg <= -10:
                weight = 1.5
                breakdown_score = breakdown_score * 1.5
            elif pct_chg <= -9:
                weight = 1.4
                breakdown_score = breakdown_score * 1.4
            elif pct_chg <= -8:
                weight = 1.3
                breakdown_score = breakdown_score * 1.3
            elif pct_chg <= -7:
                weight = 1.2
                breakdown_score = breakdown_score * 1.2
            else:  # <= -6
                weight = 1.1
                breakdown_score = breakdown_score * 1.1
            total_score += (breakdown_score - old_bd)
            reason = '加速上涨后放量大跌' if is_accelerating else '高点后3天内放量大跌'
            adjustments.append(f'放量大跌加权：{reason}（跌幅{pct_chg:.2f}%，量比{vol_ratio:.2f}/成交量分位{vol_percentile:.0f}%），形态破位{old_bd:.1f}分×{weight}，调整为{breakdown_score:.1f}分')
    
    # 严重超卖降权：如果已经严重超卖，说明已经跌了很多，形态破位的风险降低
    # 严重超卖降权：线性降权
    # 见底信号越强，形态破位权重越低
    # <10分：不打折；10~20分：线性从0.7降到0.5；>=20分：打5折（最低）
    if bottom_score >= 10:
        old_breakdown = breakdown_score
        if bottom_score >= 20:
            discount = 0.5
        else:
            discount = 0.7 - (bottom_score - 10) / 10 * 0.2  # 10分→0.7, 20分→0.5
        breakdown_score = breakdown_score * discount
        total_score += (breakdown_score - old_breakdown)
        adjustments.append(f'严重超卖降权：见底信号{bottom_score:.1f}分，形态破位{old_breakdown:.1f}分×{discount:.2f}（线性降权），调整为{breakdown_score:.1f}分')
    
    effective_score = total_score
    
    # 形态破位加权：不硬拉分数，而是设置最低风险等级
    # 形态破位是确认信号，权重最高
    # 但如果已经出现见底信号（超卖），说明已经跌了很多，风险降低
    # 等级数值：安全=1, 预警=2, 高危=3, 极度危险=4
    min_level_num = 0
    bottom_abs = abs(bottom_score)
    if breakdown_score >= 16:
        # 严重破位（80%）
        if exhaustion_score >= 8 and bottom_abs < 3:
            # 刚破位，趋势衰竭，还没超卖 → 极度危险
            min_level_num = 4
        elif bottom_abs < 5:
            # 破位严重，但还没怎么超卖 → 高危
            min_level_num = 3
        elif bottom_abs < 10:
            # 破位严重，已经开始超卖 → 预警（下跌中期）
            min_level_num = 2
        else:
            # 破位严重，严重超卖 → 安全（下跌后期，可能反弹）
            min_level_num = 1
    elif breakdown_score >= 12:
        # 中度破位（60%）
        if bottom_abs < 5:
            min_level_num = 3  # 接近高危
        elif bottom_abs < 10:
            min_level_num = 2  # 已经超卖，风险降低
        else:
            min_level_num = 1  # 严重超卖
    elif breakdown_score >= 7:
        # 轻度破位（35%）
        # 形态已经破位，至少是预警
        min_level_num = 2
    
    if min_level_num > 0:
        level_names = {1: '安全', 2: '预警', 3: '高危', 4: '极度危险'}
        adjustments.append(f'形态破位加权：形态破位{breakdown_score:.1f}分（{"严重" if breakdown_score>=16 else "中度" if breakdown_score>=12 else "轻度"}），见底信号{bottom_score:.0f}分，最低风险等级设为{level_names[min_level_num]}（分数不硬拉）')
    
    # 加速冲顶阶段降权：
    # 逻辑：5日涨幅大 + 超买严重 + 形态没破位 → 虽然风险高但可能还会继续涨
    # 不要过早给高危，等形态破位了再升级
    # 但如果超买BOLL修正后超过40分，说明超买极其严重，加速冲顶降权失效，直接正常评级
    if len(indicators) >= 6:
        pct_5d = (latest['close'] - indicators.iloc[-6]['close']) / indicators.iloc[-6]['close'] * 100
        if pct_5d >= 20 and overbought_score >= 14 and breakdown_score < 7 and overbought_after_boll <= 28:
            # 加速冲顶阶段，最高风险等级是预警，不到高危
            old_eff = effective_score
            effective_score = min(effective_score, 49)
            if effective_score != old_eff:
                adjustments.append(f'加速冲顶降权：5日涨幅{pct_5d:.1f}%，超买{overbought_score:.1f}分（BOLL修正后{overbought_after_boll:.1f}分≤28），形态未破位，有效评分从{old_eff:.1f}限制为{effective_score:.1f}（最高预警）')
    
    if effective_score >= 70:
        level = '极度危险'
        level_color = '🔴'
    elif effective_score > 49:
        level = '高危'
        level_color = '🟠'
    elif effective_score >= 44 or (is_parabolic and effective_score >= 44):
        level = '红色预警'
        level_color = '🔴'
    elif effective_score >= 37 or (is_parabolic and effective_score >= 37):
        level = '黄色预警'
        level_color = '🟡'
    elif effective_score > 29 or is_parabolic:
        level = '蓝色预警'
        level_color = '🔵'
    else:
        level = '安全'
        level_color = '🟢'
    
    # 形态破位最低等级升级：如果min_level比当前等级高，就升级
    # 等级数值：安全=1, 预警=2（蓝/黄/红）, 高危=3, 极度危险=4
    if min_level_num > 0:
        level_to_num = {'安全': 1, '蓝色预警': 2, '黄色预警': 2, '红色预警': 2, '高危': 3, '极度危险': 4}
        num_to_level = {1: ('安全', '🟢'), 2: ('黄色预警', '🟡'), 3: ('高危', '🟠'), 4: ('极度危险', '🔴')}
        current_level_num = level_to_num.get(level, 1)
        if min_level_num > current_level_num:
            old_level = level
            level, level_color = num_to_level[min_level_num]
            adjustments.append(f'风险等级升级：形态破位要求最低{level}，从{old_level}升级为{level}（分数保持{effective_score:.1f}分不变）')
    
    return {
        'total_score': effective_score,
        'raw_score': total_score,
        'level': level,
        'level_color': level_color,
        'dimensions': {
            '超买程度': overbought_score,
            '成交活跃度': turnover_score,
            '量价背离': divergence_score,
            '趋势衰竭': exhaustion_score,
            '形态破位': breakdown_score,
            '见底信号': -bottom_score,
        },
        'signals': signals,
        'adjustments': adjustments,
        'latest_price': latest['close'],
        'latest_date': latest['date'].strftime('%Y-%m-%d'),
        'quote': quote,
        'trend_status': trend_status,
        'trend_color': trend_color,
        'is_parabolic': is_parabolic,
    }


# ============================================================
# 第四部分：输出展示
# ============================================================

def print_diagnosis(result, stock_code, stock_name=''):
    """打印诊断结果"""
    if result is None:
        print("诊断失败：数据不足")
        return
    
    print("\n" + "=" * 60)
    print(f"  股票诊断报告")
    print(f"  {stock_code} {stock_name}")
    print(f"  数据日期：{result['latest_date']}")
    print(f"  最新价格：{result['latest_price']:.2f}")
    print("=" * 60)
    
    # 总分和等级
    print(f"\n  【综合评分】 {result['total_score']:.1f} / 100")
    print(f"  【风险等级】 {result['level_color']} {result['level']}")
    print(f"  【趋势状态】 {result['trend_color']} {result['trend_status']}")
    
    # 各维度得分（五维，见底缓冲已移除，见底信号见触发信号列表）
    print(f"\n  ── 五维评分 ──")
    dims = result['dimensions']
    max_scores = {'超买程度': 25, '成交活跃度': 20, '量价背离': 15, '趋势衰竭': 20, '形态破位': 20}
    for dim, score in dims.items():
        if dim == '见底信号':
            continue  # 见底缓冲已移除，不参与总分，仅保留在触发信号里参考
        max_s = max_scores.get(dim, 20)
        bar_len = int(score / max_s * 20)
        bar = '█' * bar_len + '░' * (20 - bar_len)
        print(f"  {dim:6s} |{bar}| {score:5.1f} / {max_s}")
    
    # 加权降权明细
    adjustments = result.get('adjustments', [])
    if adjustments:
        print(f"\n  ── 加权降权明细 ({len(adjustments)}项) ──")
        for i, adj in enumerate(adjustments, 1):
            print(f"  {i}. {adj}")
    else:
        print(f"\n  ── 加权降权明细 ──")
        print(f"  无特殊加权降权调整")
    
    # 详细信号
    print(f"\n  ── 触发信号 ({len(result['signals'])}个) ──")
    
    # 按类型分组
    by_type = {}
    for sig in result['signals']:
        name, desc, score, sig_type = sig
        if sig_type not in by_type:
            by_type[sig_type] = []
        by_type[sig_type].append(sig)
    
    type_names = {'超买': '超买信号', '成交': '成交信号', '背离': '背离信号', '衰竭': '衰竭信号', '破位': '破位信号', '见底': '见底信号'}
    type_icons = {'超买': '📈', '成交': '💰', '背离': '📉', '衰竭': '⚠️', '破位': '🔻', '见底': '🟢'}
    
    for sig_type in ['超买', '成交', '背离', '衰竭', '破位', '见底']:
        if sig_type in by_type:
            sigs = by_type[sig_type]
            total_s = sum(s[2] for s in sigs)
            if sig_type == '见底':
                print(f"\n  {type_icons[sig_type]} {type_names[sig_type]}（{total_s:.1f}分）")
            else:
                print(f"\n  {type_icons[sig_type]} {type_names[sig_type]}（{total_s:.1f}分）")
            for name, desc, score, _ in sigs:
                print(f"     {score:+.1f}分  {name:12s} - {desc}")
    
    print("\n" + "=" * 60)
    print(f"  免责声明：本诊断基于历史数据统计，仅供参考，不构成投资建议")
    print("=" * 60 + "\n")


# ============================================================
# 第五部分：主程序
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("用法: python peak_detector.py <股票代码> [日期] [股票名称]")
        print("示例: python peak_detector.py 600118 中国卫星")
        print("      python peak_detector.py 001309 德明利")
        print("      python peak_detector.py 300308 2026.7.14")
        print("      python peak_detector.py 300308 2026-07-14 中际旭创")
        return
    
    stock_code = sys.argv[1]
    
    # 判断第二个参数是日期还是股票名称
    # 日期格式：包含点号(.)或横杠(-)，如 2026.7.14 或 2026-07-14
    target_date = None
    stock_name = ''
    
    if len(sys.argv) > 2:
        arg2 = sys.argv[2]
        if '.' in arg2 or '-' in arg2:
            # 是日期
            target_date = arg2.replace('.', '-')
            # 统一格式为 YYYY-MM-DD
            parts = target_date.split('-')
            if len(parts) == 3:
                target_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            if len(sys.argv) > 3:
                stock_name = sys.argv[3]
        else:
            # 是股票名称
            stock_name = arg2
    
    if target_date:
        print(f"正在回测 {stock_code} {stock_name} 在 {target_date} 的情况...")
        # 指定日期时，获取更多历史数据（250天，确保有足够的历史数据计算指标）
        df = get_kline(stock_code, days=250)
    else:
        print(f"正在获取 {stock_code} {stock_name} 的数据...")
        df = get_kline(stock_code, days=120)
    if df is None:
        print(f"获取 {stock_code} 数据失败")
        return
    
    df['date'] = pd.to_datetime(df['date'])
    
    # 如果指定了日期，截取到该日期
    if target_date:
        target_dt = pd.to_datetime(target_date)
        df = df[df['date'] <= target_dt].copy()
        if len(df) == 0:
            print(f"错误：{target_date} 之前没有数据")
            return
        print(f"截取到 {target_date}，共 {len(df)} 条数据")
        print(f"最后一条数据：{df['date'].iloc[-1].strftime('%Y-%m-%d')}，收盘价 {df['close'].iloc[-1]:.2f}")
    
    print(f"计算技术指标...")
    
    indicators = calc_indicators(df)
    if indicators is None:
        print("指标计算失败")
        return
    
    quote = None
    if not target_date:
        # 实时模式才获取实时行情
        print(f"获取实时行情数据...")
        quote = get_realtime_quote(stock_code)
        if quote:
            print(f"  最新价: {quote['price']}元")
            print(f"  涨跌幅: {quote['change_pct']}%")
            print(f"  换手率: {quote['turnover_rate']:.2f}%")
            print(f"  成交额: {quote['amount']/100000000:.2f}亿")
            print(f"  流通市值: {quote['circ_mv']/100000000:.2f}亿")
            
            # 把今天的实时行情数据加到K线数据里，这样诊断的就是今天的实时情况
            today = datetime.now().strftime('%Y-%m-%d')
            new_row = {
                'date': today,
                'open': quote['open'],
                'close': quote['price'],
                'high': quote['high'],
                'low': quote['low'],
                'volume': quote['volume'],
                'pct_chg': quote['change_pct'],
                'amplitude': quote['amplitude'],
            }
            df_today = pd.DataFrame([new_row])
            df_today['date'] = pd.to_datetime(df_today['date'])
            
            # 检查最后一条数据是否已经是今天，如果是则替换，避免重复
            last_date = df.iloc[-1]['date'].strftime('%Y-%m-%d') if len(df) > 0 else ''
            if last_date == today:
                # 替换最后一条数据
                df.iloc[-1] = df_today.iloc[0]
            else:
                # 追加新数据
                df = pd.concat([df, df_today], ignore_index=True)
            
            # 重新计算指标（包含今天的数据）
            print(f"重新计算指标（包含今日实时数据）...")
            indicators = calc_indicators(df)
        else:
            print(f"  实时行情获取失败，将使用历史数据诊断")
    
    print(f"进行见顶诊断...")
    
    result = diagnose_peak(indicators, quote)
    if result is None:
        print("诊断失败")
        return
    
    print_diagnosis(result, stock_code, stock_name)


if __name__ == '__main__':
    main()
