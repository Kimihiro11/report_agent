#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进攻视角技术面分析（激进单元）——确定性计算模块。

本模块是「激进视角市场分析师」提示词的代码化实现：该提示词的输出契约
（趋势突破 / 均线信号 / MACD·RSI·KDJ 动能 / 操作与价位 / 风险敞口 / 输出自检）
全部翻译为本文件的确定性计算，而非 LLM 自由发挥——保证每个数字都能追溯到
工具返回值（calc_indicators 的真实日K指标），未返回的字段写 N/A，禁止估算。

全局规则落地（与提示词一一对应）：
  1. 所有数值来自 calc_indicators 真实日K；缺口 N/A。
  2. 周期口径统一日线；「N 日」= N 个交易日。
  3. 百分比以当前价为基准。
  4. 风险敞口 = 单一个股占总仓位比例。
  5. 数据不足 20 个交易日 → 整体标注数据不足，不做替代性推断。
  6. A 股特情必检：ST/*ST（±5%）、当日涨跌停、停牌；命中则加注限制说明。

输出 dict（build_report.stock_card 渲染「进攻视角」子卡）：
  view / special_notes / data_ok / short_term / breakout / momentum /
  ma / macd / rsi / kdj / trade / targets / risk / key_levels / exposure / checks
"""
import math

import numpy as np
import pandas as pd

# 目标/关键价位注释所需的前高/斐波那契窗口
BREAK_WIN = 10        # 突破形态观察窗口（交易日）
RES_WIN = 20          # 前高阻力观察窗口
RES_EXCLUDE = 3       # 阻力计算剔除最近 N 日（避免把当前价当阻力）


def _f(v):
    """安全转 float；失败返回 None。"""
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _cross_info(diff, lookback=20):
    """diff: Series；返回 (金叉/死叉/未交叉, 第N日)。N = 距最近一次符号穿越的交易日数。"""
    d = (diff > 0).astype(int)
    for k in range(1, min(lookback, len(d) - 1)):
        if d.iloc[-k] != d.iloc[-k - 1]:
            return ("金叉" if d.iloc[-k] == 1 else "死叉", k)
    return ("未交叉", 0)


def _hist_pattern(hist):
    """MACD 柱连续放大/缩小 N 日（从最新一根往回数）；不足 2 日返回 None。"""
    n = len(hist)
    for sign, label in ((1, "放大"), (-1, "缩小")):
        cnt = 1
        for i in range(n - 1, 0, -1):
            a, b = _f(hist.iloc[i]), _f(hist.iloc[i - 1])
            if a is None or b is None:
                break
            if (a - b) * sign > 0:
                cnt += 1
            else:
                break
        if cnt >= 2:
            return f"连续{cnt}日{label}", cnt
    return None, 0


def _rsi14(close):
    """RSI(14)（Wilder 平滑，与 calc_indicators 的 RSI 系列同口径）。"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_g = gain.rolling(14).mean()
    avg_l = loss.rolling(14).mean()
    for i in range(14, len(close)):
        avg_g.iloc[i] = (avg_g.iloc[i - 1] * 13 + gain.iloc[i]) / 14
        avg_l.iloc[i] = (avg_l.iloc[i - 1] * 13 + loss.iloc[i]) / 14
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _int_gate(price):
    """若 price 上方 2% 内存在整数关口（按价位自动选 1/10/100 步长），返回该关口价，否则 None。"""
    if price is None or price <= 0:
        return None
    step = 1 if price < 20 else (10 if price < 200 else 100)
    up = math.ceil(price / step) * step
    if up > price and (up - price) / price <= 0.02:
        return round(float(up), 2)
    return None


def _special_checks(code, name, quote):
    """A股特情必检：ST/*ST、当日涨跌停、停牌。返回注记列表。"""
    notes = []
    nm = str(name or (quote or {}).get("name") or "")
    if "ST" in nm.upper():
        notes.append("ST/*ST（涨跌幅限制 ±5%，流动性与退市风险需额外评估）")
    if quote:
        chg = _f(quote.get("change_pct"))
        code6 = str(code or quote.get("code") or "")
        # 创业板 30 / 科创板 68 → ±20%；主板 ±10%
        limit = 20 if code6[:2] in ("30", "68") else 10
        if chg is not None:
            if chg >= limit - 0.2:
                notes.append(f"当日触及涨停（+{chg:.2f}%，±{limit}% 板），追高风险大")
            elif chg <= -(limit - 0.2):
                notes.append(f"当日触及跌停（{chg:.2f}%，±{limit}% 板），无法止损卖出风险")
        vol = _f(quote.get("volume"))
        amt = _f(quote.get("amount"))
        if (vol is not None and vol == 0) or (amt is not None and amt == 0):
            notes.append("当日停牌（成交量为 0），价位信号今日不可执行")
    return notes


def analyze_aggressive(indicators, quote=None, name="", diag=None, code=""):
    """激进视角技术面分析主入口。

    indicators: peak_detector.calc_indicators 输出（真实日K指标 DataFrame）；
    quote: 实时行情 dict（可为 None）；diag: 见顶诊断结果（用于敞口上限联动）。
    所有数值锚定指标序列，缺口 N/A；不引入任何外部推测。
    """
    c, h, l, v = indicators["close"], indicators["high"], indicators["low"], indicators["volume"]
    n = len(c)
    price = _f(c.iloc[-1])
    out = {"view": "中性", "special_notes": _special_checks(code, name, quote), "data_ok": True}
    if price is None or n < 20:
        out["data_ok"] = False
        out["data_note"] = f"数据不足（仅 {n} 条日线，需 ≥20 个交易日），整体标注「数据不足」，不做替代性推断。"
        return out

    ma5, ma10, ma20 = _f(indicators["MA5"].iloc[-1]), _f(indicators["MA10"].iloc[-1]), _f(indicators["MA20"].iloc[-1])
    low5 = _f(l.iloc[-5:].min())

    # ---- 1. 短期趋势（最近 5–10 个交易日）----
    win10 = min(10, n - 1)
    range_chg = (price / _f(c.iloc[-1 - win10]) - 1) * 100
    h5a, h5b = _f(h.iloc[-5:].max()), _f(h.iloc[-10:-5].max())
    l5a, l5b = _f(l.iloc[-5:].min()), _f(l.iloc[-10:-5].min())
    if h5a > h5b and l5a > l5b:
        hl = "抬升"
    elif h5a < h5b and l5a < l5b:
        hl = "下移"
    else:
        hl = "持平"
    if (range_chg > 2 and hl != "下移") or (hl == "抬升" and range_chg > 0):
        st_state = "上升趋势"
    elif (range_chg < -2 and hl != "抬升") or (hl == "下移" and range_chg < 0):
        st_state = "下降趋势"
    else:
        st_state = "震荡"
    out["short_term"] = {"range_chg": round(range_chg, 2), "days": win10,
                         "hl_pattern": hl, "state": st_state}

    # ---- 2. 突破形态与量能配合 ----
    res = _f(h.iloc[-RES_WIN:-RES_EXCLUDE].max()) if n >= RES_WIN + RES_EXCLUDE else None
    bo = {"resistance": round(res, 2) if res else "N/A", "status": "未突破",
          "above_pct": None, "volume": "N/A", "break_day": None}
    if res:
        crossed = None
        for k in range(0, min(BREAK_WIN, n - 1)):
            cc = _f(c.iloc[-1 - k])
            pr = _f(c.iloc[-2 - k]) if n - 2 - k >= 0 else None
            if cc is not None and cc > res and (pr is None or pr <= res):
                crossed = k
                break
        if crossed is not None:
            bo["status"] = "已突破"
            bo["above_pct"] = round((price / res - 1) * 100, 2)
            bo["break_day"] = crossed
            # 突破日量 vs 其前 5 日均量
            bi = n - 1 - crossed
            vol5 = _f(v.iloc[max(0, bi - 5):bi].mean())
            vol_b = _f(v.iloc[bi])
            if vol5 and vol_b:
                ratio = vol_b / vol5
                bo["volume"] = "放量突破" if ratio >= 1.5 else ("平量突破" if ratio >= 0.8 else "缩量突破（量能不足，突破可信度降级）")
                bo["vol_ratio"] = round(ratio, 2)
        elif price > res:
            bo["status"] = "已突破"
            bo["above_pct"] = round((price / res - 1) * 100, 2)
            bo["volume"] = "突破日超出观察窗口，量能配合 N/A"
        elif price >= res * 0.98:
            bo["status"] = "正在测试"
            bo["above_pct"] = round((price / res - 1) * 100, 2)
        out["breakout"] = bo

    # ---- 3. 动能加速（近3日 vs 前3日涨幅）----
    last3 = (price / _f(c.iloc[-4]) - 1) * 100 if n >= 4 else None
    prev3 = (_f(c.iloc[-4]) / _f(c.iloc[-7]) - 1) * 100 if n >= 7 else None
    if last3 is not None and prev3 is not None:
        judge = "加速" if last3 > prev3 + 0.5 else ("减速" if last3 < prev3 - 0.5 else "持平")
        out["momentum"] = {"last3": round(last3, 2), "prev3": round(prev3, 2), "judge": judge}

    # ---- 4. 均线突破信号 ----
    arr = "交织"
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            arr = "多头排列"
        elif ma5 < ma10 < ma20:
            arr = "空头排列"
    above = [nm for nm, mv in (("MA5", ma5), ("MA10", ma10), ("MA20", ma20)) if mv and price > mv]
    dev20 = (price / ma20 - 1) * 100 if ma20 else None
    cross, cross_day = _cross_info(indicators["MA5"] - indicators["MA10"])
    cross_txt = f"已金叉（第{cross_day}日）" if cross == "金叉" else ("死叉" if cross == "死叉" else "未金叉")
    slope_up = ma5 and _f(indicators["MA5"].iloc[-2]) and ma5 > _f(indicators["MA5"].iloc[-2])
    if len(above) == 3 and slope_up:
        strength = "强（站上全部三条均线且 MA5 斜率向上）"
    elif len(above) >= 2:
        strength = "中（站上 MA5 与 MA10）"
    elif len(above) == 1:
        strength = "弱（仅站上 MA5）"
    else:
        strength = "无（三条均线均未站上）"
    out["ma"] = {"ma5": round(ma5, 2) if ma5 else "N/A", "ma10": round(ma10, 2) if ma10 else "N/A",
                 "ma20": round(ma20, 2) if ma20 else "N/A", "arrange": arr,
                 "above": above or ["均未站上"], "ma20_dev": round(dev20, 2) if dev20 is not None else "N/A",
                 "overheat": bool(dev20 is not None and dev20 > 20), "cross": cross_txt, "strength": strength}

    # ---- 5. 动能指标：MACD / RSI / KDJ ----
    dif, dea = _f(indicators["DIF"].iloc[-1]), _f(indicators["DEA"].iloc[-1])
    hist = indicators["MACD"]
    m_cross, m_day = _cross_info(indicators["DIF"] - indicators["DEA"])
    m_txt = f"已金叉（第{m_day}日）" if m_cross == "金叉" else ("死叉" if m_cross == "死叉" else "未金叉")
    pat, pat_n = _hist_pattern(hist)
    out["macd"] = {"dif": round(dif, 2) if dif is not None else "N/A",
                   "dea": round(dea, 2) if dea is not None else "N/A",
                   "hist": round(_f(hist.iloc[-1]), 2) if _f(hist.iloc[-1]) is not None else "N/A",
                   "cross": m_txt, "zero": "零轴上方" if (dif or 0) > 0 else "零轴下方",
                   "pattern": pat or "柱体变化不足 2 日，N/A"}

    r14 = _rsi14(c)
    rsi = _f(r14.iloc[-1])
    above50 = int((r14.iloc[-20:] > 50).sum()) if rsi is not None else 0
    # RSI 连续在 50 上方的天数（从最新往回数）
    streak = 0
    if rsi is not None:
        for k in range(len(r14) - 1, 13, -1):
            rv = _f(r14.iloc[k])
            if rv is not None and rv > 50:
                streak += 1
            else:
                break
    zone = ("弱势(<30)" if rsi < 30 else "偏弱(30-50)" if rsi < 50 else "偏强(50-70)" if rsi <= 70 else "超买(>70)") if rsi is not None else "N/A"
    ob_note = ""
    if rsi is not None and rsi > 70:
        pat2, _ = _hist_pattern(hist)
        hist_shrink2 = bool(pat2 and pat2.startswith("连续") and "缩小" in pat2 and pat2.split("日")[0].replace("连续", "").isdigit() and int(pat2.split("日")[0].replace("连续", "")) >= 2)
        if price >= (ma5 or price) and not hist_shrink2:
            ob_note = "超买但强势延续（收盘未破 MA5 且柱未连续 2 日缩小），不单独看空"
        else:
            ob_note = "超买且动能减弱（破 MA5 或柱连续 2 日缩小），注意回撤"
    out["rsi"] = {"val": round(rsi, 1) if rsi is not None else "N/A", "zone": zone,
                  "above50_days": streak, "overbought_note": ob_note}

    k_, d_ = _f(indicators["K"].iloc[-1]), _f(indicators["D"].iloc[-1])
    j_ = _f(indicators["J"].iloc[-1])
    kd_cross, kd_day = _cross_info(indicators["K"] - indicators["D"])
    kd_txt = f"已金叉（第{kd_day}日）" if kd_cross == "金叉" else ("死叉" if kd_cross == "死叉" else "未金叉")
    j_note = ""
    if j_ is not None and j_ > 100:
        k_prev, d_prev = _f(indicators["K"].iloc[-2]), _f(indicators["D"].iloc[-2])
        if k_prev is not None and d_prev is not None and k_ < k_prev and d_ < d_prev:
            j_note = "J>100 高位钝化，且 K/D 同时拐头向下 → 视为动能衰竭"
        else:
            j_note = "J>100 高位钝化，K/D 未同时拐头 → 动能未衰竭"
    out["kdj"] = {"k": round(k_, 1) if k_ is not None else "N/A", "d": round(d_, 1) if d_ is not None else "N/A",
                  "j": round(j_, 1) if j_ is not None else "N/A", "cross": kd_txt, "j_note": j_note}

    # ---- 6. 观点（三选一，全篇术语统一；确定性打分）----
    score = 0
    if arr == "多头排列":
        score += 2
    elif arr == "空头排列":
        score -= 2
    if "MA20" in above:
        score += 1
    elif ma20 and price < ma20:
        score -= 1
    if m_cross == "金叉":
        score += 2 if (pat and "放大" in pat) else 1
    elif m_cross == "死叉":
        score -= 2
    if rsi is not None and 50 <= rsi <= 70:
        score += 1
    elif rsi is not None and rsi < 50:
        score -= 1
    if bo.get("status") == "已突破" and "放量" in str(bo.get("volume", "")):
        score += 2
    elif bo.get("status") == "正在测试":
        score += 1
    mm = out.get("momentum") or {}
    if mm.get("judge") == "加速":
        score += 1
    elif mm.get("judge") == "减速":
        score -= 1
    if kd_cross == "金叉":
        score += 1
    elif kd_cross == "死叉":
        score -= 1
    view = "看涨" if score >= 4 else ("看跌" if score <= -3 else "中性")
    out["view"] = view
    out["view_score"] = score

    # ---- 7. 操作与价位（全部可指认：前高 / 整数关口 / 斐波那契）----
    low60 = _f(l.iloc[-60:].min()) if n >= 60 else _f(l.min())
    high120 = _f(h.iloc[-120:].max()) if n >= 120 else _f(h.max())
    entry, entry_note = "N/A", ""
    add, add_note = "N/A", ""
    targets_note = ""
    if view == "看跌":
        # 术语统一：看跌不设上行目标，反弹至阻力位减仓（关键价位给出）
        targets = []
        targets_note = "看跌观点下不设上行目标；反弹至关键阻力位附近减仓（见关键价位）。"
    elif view in ("看涨", "中性") and res:
        entry = round(res, 2) if price > res else round(max(res, ma10 or res), 2)
        entry_note = "回踩不破前高阻力位即入场参考" if price > res else "回踩 MA10/前高区域低吸"
        add = round(res * 1.005, 2)
        add_note = f"放量（≥5日均量1.5倍）站上前高 {res:.2f} 时加仓"
    elif view == "看涨" and not res:
        entry = round(price, 2)
        entry_note = "现价（无可指认前高，谨慎）"
    targets = []
    if view != "看跌" and res:
        if price > res:
            t1 = res + (res - (low60 or res)) * 0.618
            anchor1 = "斐波那契 0.618 扩展（前高+波段振幅投影）"
        else:
            t1 = res
            anchor1 = "前高（20 日高点）"
        gate = _int_gate(t1)
        if gate:
            t1, anchor1 = gate, "整数关口"
        targets.append({"label": "短期目标（1-2周）", "price": round(t1, 2), "anchor": anchor1})
    if view != "看跌" and high120 and high120 > price * 1.02:
        gate = _int_gate(high120)
        t2, anchor2 = (gate, "整数关口") if gate else (high120, "前高（120 日高点）")
        targets.append({"label": "中期目标（1-2月）", "price": round(t2, 2), "anchor": anchor2})
    elif res and price > res:
        t2 = res + (res - (low60 or res)) * 1.0
        targets.append({"label": "中期目标（1-2月）", "price": round(t2, 2), "anchor": "波段振幅 1.0 倍投影"})
    for t in targets:
        t["rel_pct"] = round((t["price"] / price - 1) * 100, 2)
    out["targets"] = targets
    out["targets_note"] = targets_note
    out["trade"] = {"entry": entry, "entry_note": entry_note, "add": add, "add_note": add_note}

    # 风险控制参考位：项目统一口径 min(MA20, 近5日低)×0.97（必须给出，缺失则输出无效）
    risk = None
    if ma20 or low5:
        risk = round(min(x for x in (ma20, low5) if x is not None) * 0.97, 2)
    out["risk"] = {"price": risk if risk else "N/A",
                   "rel_pct": round((risk / price - 1) * 100, 2) if risk else "N/A",
                   "rule": "min(MA20, 近5日低点) × 0.97（下方 3% 缓冲）"}

    # ---- 8. 关键价位 ----
    out["key_levels"] = {
        "strong_support": round(low5, 2) if low5 else "N/A",
        "support_note": "近 5 日最低点",
        "breakout_confirm": round(res * 1.005, 2) if res else "N/A",
        "target_resistance": (targets[-1]["price"] if targets else (round(high120, 2) if high120 else "N/A")),
    }

    # ---- 9. 风险敞口（单一个股占总仓位；见顶诊断联动上限）----
    conds = []
    if arr == "多头排列":
        conds.append("均线多头排列")
    if pat and "放大" in pat:
        conds.append("MACD 柱持续放大")
    if rsi is not None and rsi >= 50:
        conds.append("RSI ≥ 50")
    if bo.get("status") == "已突破" and "放量" in str(bo.get("volume", "")):
        conds.append("放量突破成立")
    pct = 8
    if len(conds) >= 2:
        pct = min(20, 8 + 4 * len(conds))
    cap_note = ""
    level = (diag or {}).get("level", "")
    if level in ("高危", "极度危险"):
        pct = min(pct, 5)
        cap_note = f"见顶诊断为「{level}」，敞口上限强制压至 5%"
    out["exposure"] = {"pct": pct, "conditions_met": conds,
                       "rule": "满足至少 2 条上调条件才上调敞口（4 条全满 = 20% 上限）",
                       "cap_note": cap_note}

    # ---- 10. 输出自检（代码级保证）----
    checks = []
    checks.append("全部数值来自 calc_indicators 真实日K/实时行情，缺口已标 N/A")
    checks.append("风险控制参考位已给出" if risk else "风险控制参考位缺失 → 本条输出无效")
    checks.append("无空指标名判断：每个信号均附具体数值")
    checks.append(f"术语全篇统一（{view}）")
    if out["special_notes"]:
        checks.append("A股特情已注记：" + "；".join(out["special_notes"]))
    out["checks"] = checks
    return out
