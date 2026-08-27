# -*- coding: utf-8 -*-
"""缠论结构分析（chan_analysis）——上证指数分钟级别推演，用于操作指引。

引擎（按优先级）：
  1. **czsc**（https://github.com/waditu/czsc，Rust 核心）：分型/笔/中枢识别由开源库
     完成（CZSC 对象：fx_list / bi_list / zs_list），笔力度直接采用 BI.power；
     背驰与三类买卖点判定为本项目适配层（口径：进入段 vs 离开段力度衰减 >=10%）。
  2. czsc 不可用时回退纯标准库简化实现（保持管线不断，输出契约一致）。

输出契约（data/chan/chan_forecast_YYYYMMDD.json，build_report.chan_section() 消费）：
  level / data_range / bars / fractals / bis / last_price / last_time
  zhongshu: {zd, zg, gg, dd, range, start_time, end_time}
  beichi:   {dir(up|down), enter_power, leave_power, level(强|中)}
  signal:   {signal, cls, text}   recent_bis: [{dir, start_time, end_time, ...}]
  pos: 中枢上方|中枢下方|中枢内|结构未成    engine: czsc-x.y.z|stdlib
"""
import json
import urllib.request
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

# 东财指数 secid：上证 1.000001 / 深证 0.399001 / 创业板 0.399006 / 科创50 1.000688
INDEX_SECID = {"000001": "1.000001", "399001": "0.399001", "399006": "0.399006", "000688": "1.000688"}

FREQ_LABEL = {1: "1分钟", 5: "5分钟", 15: "15分钟", 30: "30分钟", 60: "60分钟"}


def fetch_min_kline(secid="1.000001", klt=5, lmt=600):
    """分钟 K 线（真实数据，东财优先→新浪兜底）。返回 [{time, open, close, high, low, volume}]，升序。"""
    import time
    last_err = None
    # 1) 东财
    u = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt={klt}"
         f"&fqt=1&lmt={lmt}&end=20500101&fields1=f1,f2,f3,f4,f5,f6"
         f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58")
    for _ in range(2):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0",
                                                     "Referer": "https://quote.eastmoney.com/"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
            klines = (d.get("data") or {}).get("klines") or []
            if not klines:
                last_err = "东财空"
                time.sleep(1.2)
                continue
            out = []
            for line in klines:
                p = line.split(",")
                out.append({"time": p[0], "open": float(p[1]), "close": float(p[2]),
                            "high": float(p[3]), "low": float(p[4]), "volume": float(p[5])})
            return out
        except Exception as e:
            last_err = f"东财 {str(e)[:60]}"
            time.sleep(1.5)
    # 2) 新浪兜底（symbol 映射：secid 1.xxxxxx -> sh + 6位；0.xxxxxx -> sz + 6位）
    code6 = secid.split(".")[1]
    sym = ("sh" if secid.startswith("1.") else "sz") + code6
    scale = {1: 1, 5: 5, 15: 15, 30: 30, 60: 60}.get(klt, 5)
    u2 = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/CN_MarketDataService.getKLineData"
          f"?symbol={sym}&scale={scale}&ma=no&datalen={lmt}")
    try:
        req = urllib.request.Request(u2, headers={"User-Agent": "Mozilla/5.0",
                                                  "Referer": "https://finance.sina.com.cn/"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        s = raw[raw.find("["):raw.rfind("]") + 1]
        kl = json.loads(s)
        if not kl:
            raise RuntimeError("新浪空")
        out = []
        for k in kl:
            out.append({"time": k["day"][:16], "open": float(k["open"]),
                        "close": float(k["close"]), "high": float(k["high"]),
                        "low": float(k["low"]), "volume": float(k.get("volume") or 0)})
        return out
    except Exception as e:
        last_err = f"{last_err}; 新浪 {str(e)[:60]}"
    raise RuntimeError(f"分钟K线获取失败: {last_err}")


# ======================================================================
# 引擎一：czsc（开源库，Rust 核心；分型/笔/中枢由库识别）
# ======================================================================

def _czsc_engine(kl, klt=5, level_label="5分钟"):
    """用 czsc 做结构识别，适配层产出背驰与三类买卖点。kl 为升序分钟K线 dict 列表。"""
    import czsc
    from czsc import CZSC, Freq, RawBar

    freq = getattr(Freq, f"F{klt}", None)
    if freq is None:
        return None
    bars = []
    for k in kl:
        dt = k["time"]
        if len(dt) <= 16:  # "2026-08-27 15:00" → 补秒
            dt += ":00"
        bars.append(RawBar(symbol="INDEX", freq=freq, dt=datetime.datetime.fromisoformat(dt),
                           open=k["open"], close=k["close"], high=k["high"], low=k["low"],
                           vol=k.get("volume") or 0, amount=0))
    c = CZSC(bars, max_bi_num=100)

    def _dir(bi):
        return "up" if str(bi.direction) == "向上" else "down"

    last = kl[-1]
    price = last["close"]
    bis = list(c.bi_list)
    zs_list = list(c.zs_list)
    valid_zs = [z for z in zs_list if z.is_valid()] if zs_list and callable(getattr(zs_list[-1], "is_valid", None)) else list(zs_list)
    zs = valid_zs[-1] if valid_zs else None

    # ---- 中枢（最近一个有效中枢）----
    zs_dict = None
    zs_bi_idx0 = None
    if zs is not None:
        zd, zg = float(zs.zd), float(zs.zg)
        # 定位中枢首笔在 bi_list 中的下标（用端点分型时间双重匹配，避免同刻歧义）
        for i, b in enumerate(bis):
            if str(b.fx_a.dt) == str(zs.bis[0].fx_a.dt) and str(b.fx_b.dt) == str(zs.bis[0].fx_b.dt):
                zs_bi_idx0 = i
                break
        zs_dict = {
            "zd": round(zd, 2), "zg": round(zg, 2),
            "gg": round(float(zs.gg), 2), "dd": round(float(zs.dd), 2),
            "range": round(zg - zd, 2),
            "start_time": str(zs.sdt)[:16] if zs.sdt else "",
            "end_time": str(zs.edt)[:16] if zs.edt else "",
            "bis_in_zs": len(zs.bis),
        }

    # ---- 背驰（进入段 vs 离开段，BI.power 力度衰减 >=10% 判背驰）----
    beichi = None
    if zs is not None and zs_bi_idx0 is not None:
        i0, n_in = zs_bi_idx0, len(zs.bis)
        leave_idx = i0 + n_in
        if i0 >= 1 and leave_idx < len(bis):
            enter_bi, leave_bi = bis[i0 - 1], bis[leave_idx]
            if _dir(enter_bi) == _dir(leave_bi):  # 同向才可比
                ep, lp = float(enter_bi.power), float(leave_bi.power)
                if lp < ep * 0.9:
                    beichi = {"dir": _dir(enter_bi),
                              "enter_power": round(ep, 1), "leave_power": round(lp, 1),
                              "level": "强" if lp < ep * 0.6 else "中"}

    # ---- 买卖点（价格相对中枢位置 + 背驰 + 末笔方向；与旧口径一致的适配层）----
    last_bi_dir = _dir(bis[-1]) if bis else "up"
    sig = _classify_signal(price, zs_dict, beichi, last_bi_dir)

    # ---- 最近 5 笔（fx_a→fx_b 端点）----
    recent_bis = []
    for b in bis[-5:]:
        d = _dir(b)
        recent_bis.append({
            "dir": d,
            "start_time": str(b.fx_a.dt)[5:16], "end_time": str(b.fx_b.dt)[5:16],
            "start_price": round(float(b.low if d == "up" else b.high), 2),
            "end_price": round(float(b.high if d == "up" else b.low), 2),
        })

    pos = ("中枢上方" if price > zs_dict["zg"] else
           ("中枢下方" if price < zs_dict["zd"] else "中枢内")) if zs_dict else "结构未成"

    # ---- 未完成笔（czsc ubi：正在延伸、尚未确认的笔，操作上最贴近当下）----
    ubi = None
    try:
        u = c.ubi
        if isinstance(u, dict) and u.get("direction") is not None:
            udir = "up" if str(u["direction"]) == "向上" else "down"
            fx_a = u.get("fx_a")
            extreme_bar = u.get("high_bar" if udir == "up" else "low_bar")
            ubi = {"dir": udir,
                   "start_time": str(fx_a.dt)[:16] if fx_a else "",
                   "start_price": round(float(fx_a.fx), 2) if fx_a else None,
                   "extreme_price": round(float(u["high"] if udir == "up" else u["low"]), 2),
                   "extreme_time": str(extreme_bar.dt)[:16] if extreme_bar is not None else ""}
    except Exception:
        ubi = None

    return {
        "level": level_label,
        "data_range": f"{kl[0]['time']} ~ {last['time']}",
        "bars": len(kl),
        "fractals": len(c.fx_list),
        "bis": len(bis),
        "last_price": price,
        "last_time": last["time"],
        "zhongshu": zs_dict,
        "beichi": beichi,
        "signal": sig,
        "recent_bis": recent_bis,
        "pos": pos,
        "ubi": ubi,
        "engine": f"czsc-{czsc.__version__}",
    }


# ======================================================================
# 引擎二：纯标准库兜底（czsc 不可用时；实现为缠论风格简化）
# ======================================================================

def merge_inclusion(kl):
    """K线包含处理：相邻包含合并（前向合并），返回处理后 K 线（含原索引）。"""
    merged = [dict(kl[0], idx=0)]
    for i in range(1, len(kl)):
        k = kl[i]
        last = merged[-1]
        # 包含：一根的高低完全覆盖另一根
        if (k["high"] >= last["high"] and k["low"] <= last["low"]) or \
           (k["high"] <= last["high"] and k["low"] >= last["low"]):
            # 方向：取前两根判断（用 last 相对更前一根的高低关系）
            up = last["high"] >= last.get("_prev_high", last["high"])
            if up:
                merged[-1] = {"time": last["time"], "high": max(k["high"], last["high"]),
                              "low": max(k["low"], last["low"]), "open": last["open"],
                              "close": k["close"], "volume": last["volume"] + k["volume"],
                              "idx": last["idx"], "_prev_high": last["high"], "_prev_low": last["low"]}
            else:
                merged[-1] = {"time": last["time"], "high": min(k["high"], last["high"]),
                              "low": min(k["low"], last["low"]), "open": last["open"],
                              "close": k["close"], "volume": last["volume"] + k["volume"],
                              "idx": last["idx"], "_prev_high": last["high"], "_prev_low": last["low"]}
        else:
            merged.append(dict(k, idx=i, _prev_high=last["high"], _prev_low=last["low"]))
    return merged


def find_fractals(merged):
    """分型识别：顶分型（高最高且两侧低）、底分型（低最低且两侧高）。

    返回 [(idx, type, high, low)]，type='top'/'bottom'。
    """
    fr = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["high"] > a["high"] and b["high"] > c["high"] and \
           b["low"] > a["low"] and b["low"] > c["low"]:
            fr.append((i, "top", b["high"], b["low"]))
        elif b["low"] < a["low"] and b["low"] < c["low"] and \
             b["high"] < a["high"] and b["high"] < c["high"]:
            fr.append((i, "bottom", b["high"], b["low"]))
    return fr


def build_bi(merged, fr):
    """笔划分：相邻有效分型交替连接（顶底/底顶），顶高>底低，且分型间至少间隔 1 根合并K线。

    返回笔序列 [(start_idx, end_idx, dir)]。
    """
    bis = []
    prev = None
    for f in fr:
        if prev is None:
            prev = f
            continue
        # 分型间必须至少间隔 1 根合并K线（idx 差 >= 2）
        if f[0] - prev[0] < 2:
            # 距离太近：保留更极端者
            if f[1] == prev[1]:
                if f[1] == "top" and f[2] > prev[2]:
                    prev = f
                elif f[1] == "bottom" and f[3] < prev[3]:
                    prev = f
            continue
        # 必须交替
        if f[1] == prev[1]:
            # 同向分型：保留更极端者
            if f[1] == "top" and f[2] > prev[2]:
                prev = f
            elif f[1] == "bottom" and f[3] < prev[3]:
                prev = f
            continue
        # 交替且有效（顶高>底低）
        if f[1] == "top":
            if f[2] > prev[3]:
                bis.append((prev[0], f[0], "up" if prev[1] == "bottom" else "down"))
                prev = f
        else:  # bottom
            if f[3] < prev[2]:
                bis.append((prev[0], f[0], "down" if prev[1] == "top" else "up"))
                prev = f
    return bis


def find_zhongshu(merged, bis):
    """中枢识别：滑动取连续 3 笔，若 max(三笔低点) < min(三笔高点) 且区间达到最小幅度则构成中枢。

    最小幅度 = 最近 60 根合并K线平均振幅 × 0.5（过滤横盘微波动伪中枢）。
    返回最近一个有效中枢 {start_idx, end_idx, zd, zg, gg, dd, bi_dir}；无则 None。
    """
    if len(bis) < 3:
        return None
    recent = merged[-60:]
    avg_amp = (sum(k["high"] - k["low"] for k in recent) / len(recent)) if recent else 0
    min_range = avg_amp * 0.5
    zs = None
    for i in range(len(bis) - 2):
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]
        lows = [merged[b[1]]["low"] for b in (b1, b2, b3)]
        highs = [merged[b[1]]["high"] for b in (b1, b2, b3)]
        zd = max(lows)
        zg = min(highs)
        if zd < zg and (zg - zd) >= min_range:  # 三笔重叠且幅度足够
            gg = max(merged[b[1]]["high"] for b in (b1, b2, b3))
            dd = min(merged[b[1]]["low"] for b in (b1, b2, b3))
            zs = {"start_idx": b1[0], "end_idx": b3[1], "zd": zd, "zg": zg,
                  "gg": gg, "dd": dd, "enter_dir": b1[2], "bi_idx": i}
    return zs


def macd(kl, fast=12, slow=26, signal=9):
    """标准 MACD（EMA 递推），返回每根K线 {dif, dea, hist}。"""
    ema_f = ema_s = dif = dea = 0.0
    out = []
    for i, k in enumerate(kl):
        c = k["close"]
        ema_f = c if i == 0 else ema_f + (fast + 1) ** -1 * (c - ema_f)
        ema_s = c if i == 0 else ema_s + (slow + 1) ** -1 * (c - ema_s)
        dif = ema_f - ema_s
        dea = dif if i == 0 else dea + (signal + 1) ** -1 * (dif - dea)
        out.append({"dif": dif, "dea": dea, "hist": (dif - dea) * 2})
    return out


def seg_power(kl, macd_vals, s, e):
    """段力度：价格位移幅度（%）+ MACD 柱面积。s/e 为 K 线索引。"""
    if e <= s:
        return 0.0
    price_move = abs(kl[e]["close"] - kl[s]["close"]) / kl[s]["close"] * 100
    hist_area = sum(abs(m["hist"]) for m in macd_vals[s:e])
    return price_move + hist_area * 0.05  # 面积加权（量纲调节）


def detect_beichi(merged, bis, zs, macd_vals, kl):
    """背驰：比较进入中枢笔与离开中枢笔（中枢后第一笔）的同向段力度。

    返回 {dir, enter_power, leave_power, level} 或 None。
    """
    if zs is None:
        return None
    i = zs.get("bi_idx")
    if i is None or i < 1 or i + 3 >= len(bis):
        return None
    enter_bi = bis[i - 1]  # 中枢前一笔（进入）
    leave_bi = bis[i + 3]  # 中枢后第一笔（离开）
    if enter_bi[2] != leave_bi[2]:  # 必须同向才有可比性
        return None
    ep = seg_power(kl, macd_vals, enter_bi[0], enter_bi[1])
    lp = seg_power(kl, macd_vals, leave_bi[0], leave_bi[1])
    if lp < ep * 0.9:  # 离开段力度衰减 >=10%
        return {"dir": enter_bi[2], "enter_power": round(ep, 1), "leave_power": round(lp, 1),
                "level": "强" if lp < ep * 0.6 else "中"}
    return None


def _classify_signal(price, zs, beichi, last_bi_dir):
    """缠论买卖点判定（两引擎共用）：价格相对中枢位置 + 背驰方向 + 末笔方向。"""
    if zs is None:
        return {"signal": "中枢未成", "cls": "b-gray", "text": "笔结构未构成有效中枢，暂不判定买卖点。"}
    zg, zd = zs["zg"], zs["zd"]
    beichi_dir = beichi["dir"] if beichi else None

    # 三买：向上突破中枢后回踩不破 ZG（价格在中枢上方且最近一笔向下未破 ZG）
    if price > zg and beichi_dir != "down":
        warn = ("；但上涨段力度出现衰减（背驰），注意冲高回落" if beichi_dir == "up" else "")
        return {"signal": "三买候选", "cls": "b-red",
                "text": f"价格 {price:.2f} 站上中枢上沿 {zg:.2f}；若回踩不破 {zg:.2f} 则三买成立，短线偏多{warn}。"}
    # 一买：下跌背驰（离开段力度衰减）且价格在中枢下方
    if beichi_dir == "down" and price < zd:
        return {"signal": "一买候选", "cls": "b-red",
                "text": f"下跌段力度衰减（背驰），价格 {price:.2f} 在中枢下沿 {zd:.2f} 下方；"
                        f"若出现底分型企稳则一买成立，关注超跌反弹。"}
    # 二买：一买后回抽不破前低（价格在中枢内偏下 + 最近一笔向上）
    if last_bi_dir == "up" and zd <= price <= zg:
        return {"signal": "二买观察", "cls": "b-blue",
                "text": f"价格 {price:.2f} 处于中枢 [{zd:.2f}, {zg:.2f}] 内且最近一笔向上；"
                        f"回抽不破前低则二买，中枢内高抛低吸。"}
    # 一卖：上涨背驰 + 价格在中枢上方
    if beichi_dir == "up" and price > zg:
        return {"signal": "一卖候选", "cls": "b-green",
                "text": f"上涨段力度衰减（背驰），价格 {price:.2f} 在中枢上沿 {zg:.2f} 上方；"
                        f"若出现顶分型则一卖成立，注意冲高回落。"}
    # 三卖：向下突破中枢后反抽不破 ZD
    if price < zd and last_bi_dir == "down":
        return {"signal": "三卖观察", "cls": "b-green",
                "text": f"价格 {price:.2f} 跌破中枢下沿 {zd:.2f}；若反抽不收回 {zd:.2f} 则三卖，短线偏空。"}
    return {"signal": "中枢震荡", "cls": "b-blue",
            "text": f"价格 {price:.2f} 处于中枢 [{zd:.2f}, {zg:.2f}] 内震荡，等待方向选择。"}


def _stdlib_engine(kl, level_label="5分钟"):
    """纯标准库兜底（缠论风格简化实现）。"""
    if len(kl) < 30:
        return {"error": "K线数据不足"}
    merged = merge_inclusion(kl)
    fr = find_fractals(merged)
    bis = build_bi(merged, fr)
    zs = find_zhongshu(merged, bis)
    macd_vals = macd(kl)
    beichi = detect_beichi(merged, bis, zs, macd_vals, kl) if zs else None
    last = kl[-1]
    price = last["close"]
    last_bi_dir = bis[-1][2] if bis else "up"
    sig = _classify_signal(price, {"zg": zs["zg"], "zd": zs["zd"]} if zs else None, beichi, last_bi_dir)
    recent_bis = []
    for b in bis[-5:]:
        recent_bis.append({"dir": b[2], "start_time": merged[b[0]]["time"][5:16],
                           "end_time": merged[b[1]]["time"][5:16],
                           "start_price": round(merged[b[0]]["low" if b[2] == "down" else "high"], 2),
                           "end_price": round(merged[b[1]]["low" if b[2] == "down" else "high"], 2)})
    return {
        "level": level_label,
        "data_range": f"{kl[0]['time']} ~ {last['time']}",
        "bars": len(kl),
        "merged_bars": len(merged),
        "fractals": len(fr),
        "bis": len(bis),
        "last_price": price,
        "last_time": last["time"],
        "zhongshu": {"zd": round(zs["zd"], 2), "zg": round(zs["zg"], 2),
                     "gg": round(zs["gg"], 2), "dd": round(zs["dd"], 2),
                     "range": round(zs["zg"] - zs["zd"], 2),
                     "start_time": merged[zs["start_idx"]]["time"],
                     "end_time": merged[zs["end_idx"]]["time"]} if zs else None,
        "beichi": beichi,
        "signal": sig,
        "recent_bis": recent_bis,
        "pos": "中枢上方" if zs and price > zs["zg"] else
               ("中枢下方" if zs and price < zs["zd"] else
                ("中枢内" if zs else "结构未成")),
        "engine": "stdlib",
    }


def chan_analyze(kl, klt=5):
    """完整缠论推演：czsc 引擎优先，异常/未安装时回退标准库实现。kl: 分钟K线（升序）。"""
    level_label = FREQ_LABEL.get(klt, f"{klt}分钟")
    try:
        r = _czsc_engine(kl, klt, level_label)
        if r and not r.get("error"):
            return r
    except ImportError:
        pass
    except Exception as e:
        print(f"[缠论] czsc 引擎异常，回退标准库实现: {e}")
    return _stdlib_engine(kl, level_label)


def run(index_code="000001", klt=5, lmt=600):
    """主流程：拉取上证指数 5 分钟 K 线 → 缠论推演 → 写 data/chan/chan_forecast_YYYYMMDD.json。"""
    secid = INDEX_SECID.get(index_code, "1.000001")
    kl = fetch_min_kline(secid, klt, lmt)
    if not kl:
        return {"error": "K线获取失败"}
    result = chan_analyze(kl, klt)
    today = datetime.datetime.now().strftime("%Y%m%d")
    out_dir = BASE_DIR / "data" / "chan"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"chan_forecast_{today}.json"
    result["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["index_code"] = index_code
    result["period_min"] = klt
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[缠论] {index_code} {klt}分钟推演（引擎 {result.get('engine', '?')}）已写入: {path}")
    return result


if __name__ == "__main__":
    import sys
    index = sys.argv[1] if len(sys.argv) > 1 else "000001"
    period = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    bars = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    r = run(index, period, bars)
    print(json.dumps(r, ensure_ascii=False, indent=2))
