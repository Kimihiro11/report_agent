# -*- coding: utf-8 -*-
"""缠论推演报告（独立于日报）。

从日报拆出：日报只做「今日操作指引」，缠论属于结构技术分析、更新频率与阅读节奏
都与日报不同，故独立成报，按需生成。

数据源：data/chan/chan_forecast_<DATE8>.json（由 chan_analysis.py 生成，czsc 引擎，真实K线）。
渲染：自包含 HTML（内联 templates/style.css），输出到 reports/专题/。
缺文件/解析失败时明确报错，绝不编造。

用法：
  python chan_report.py [--date YYYY-MM-DD]      # 默认取当日已生成的 chan_forecast
  python chan_report.py --date 2026-09-18 --run  # 先生成数据再出报告
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from html import escape as _html_escape

from ra.paths import ROOT as BASE_DIR  # 包化后统一根路径
CSS_PATH = BASE_DIR / "templates" / "style.css"
OUT_DIR = BASE_DIR / "reports" / "专题"


# ======================================================================
# 基础工具
# ======================================================================

def _esc(s) -> str:
    """HTML 转义 + 轻量粗体（与 build_report._esc 口径一致）。"""
    out = _html_escape(str(s if s is not None else ""))
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.S)
    return out.replace("**", "")


def load_css() -> str:
    try:
        return CSS_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def load_forecast(date8: str) -> dict:
    p = BASE_DIR / "data" / "derived" / "chan" / f"chan_forecast_{date8}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ======================================================================
# 单级别渲染
# ======================================================================

def _chan_operation(signal, pos, beichi, horizon="短线"):
    """缠论信号 → 操作含义（horizon 为该级别的操作视界：短线/波段）。"""
    if signal == "三买候选":
        return ('站上中枢上沿后回踩不破则三买，' + horizon + '偏多——可关注回踩企稳的低吸机会；'
                + ("但上涨段出现力度衰减，追高需谨慎。" if beichi and beichi.get("dir") == "up" else ""))
    if signal == "一买候选":
        return (f"下跌背驰+价格在中枢下方，若底分型企稳则一买——{horizon}超跌反弹博弈，"
                f"严格止损于中枢下沿下方。")
    if signal == "二买观察":
        return f"中枢内回抽不破前低则二买——{horizon}中枢内高抛低吸，突破上沿转强、跌破下沿离场。"
    if signal == "一卖候选":
        return f"上涨背驰+价格在中枢上方，若顶分型则一卖——注意冲高回落，{horizon}减仓/回避追高。"
    if signal == "三卖观察":
        return f"跌破中枢下沿后反抽不收回则三卖——{horizon}偏空，反弹减仓。"
    return f"中枢震荡，等待方向选择——{horizon}跌破下沿防守、突破上沿看多。"


def _chan_stance(sig_label):
    """信号 → 多/空/中性（多级别共振研判用）。"""
    s = str(sig_label or "")
    if "买" in s:
        return "偏多"
    if "卖" in s:
        return "偏空"
    return "震荡"


def _chan_level_block(lv):
    """渲染单个级别的缠论推演表（chan_analysis levels[] 元素）。"""
    sig = lv.get("signal") or {}
    zs = lv.get("zhongshu")
    beichi = lv.get("beichi")
    pos = lv.get("pos", "")
    pos_cls = {"中枢上方": "b-red", "中枢下方": "b-green", "中枢内": "b-blue"}.get(pos, "b-gray")
    sig_cls = sig.get("cls", "b-blue")
    sig_label = sig.get("signal", "—")
    horizon = lv.get("horizon", "短线")
    beichi_html = ""
    if beichi:
        bdir = "上涨" if beichi["dir"] == "up" else "下跌"
        bcls = "b-green" if beichi["dir"] == "up" else "b-red"
        _lr = beichi.get("leave_range")
        _lr_txt = f"（离开段 {_lr}）" if _lr else ""
        beichi_html = (f'<tr><td><b>背驰</b><br><span class="muted" style="font-size:11px">同向力度衰减</span></td>'
                       f'<td><span class="badge {bcls}">{bdir}段力度衰减（{beichi["level"]}）</span></td>'
                       f'<td>进入段力度 {beichi["enter_power"]} → 离开段 {beichi["leave_power"]}{_lr_txt}'
                       f'（离开段不足进入段 90% 视为背驰，提示原方向动能减弱、可能转势）</td></tr>')
    zs_html = ""
    if zs:
        zs_html = (f'<tr><td><b>最近中枢</b><br><span class="muted" style="font-size:11px">多空成本密集区</span></td>'
                   f'<td><span class="badge b-blue">[{zs["zd"]:.2f}, {zs["zg"]:.2f}]</span></td>'
                   f'<td>下沿 ZD <b>{zs["zd"]:.2f}</b>（支撑）／上沿 ZG <b>{zs["zg"]:.2f}</b>（压力）；'
                   f'区间宽 {zs["range"]:.2f} 点，{zs["start_time"]} 起确认（GG {zs["gg"]:.2f} / DD {zs["dd"]:.2f}）</td></tr>')
    bis_html = ""
    rb = lv.get("recent_bis") or []
    if rb:
        rows = "".join(
            f'<tr><td><span class="badge {"b-red" if b["dir"] == "up" else "b-green"}">'
            f'{"上" if b["dir"] == "up" else "下"}</span></td>'
            f'<td>{b["start_time"]} → {b["end_time"]}</td>'
            f'<td>{b["start_price"]:.2f} → {b["end_price"]:.2f}</td></tr>' for b in rb)
        bis_html = (f'<tr><td><b>最近5笔</b></td><td colspan="2">'
                    f'<table style="margin:2px 0"><thead><tr><th>方向</th><th>时间</th><th>价格</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table></td></tr>')
    ubi_html = ""
    ubi = lv.get("ubi")
    if isinstance(ubi, dict) and ubi.get("start_price") is not None:
        u_dir = ubi.get("dir")
        u_cls = "b-red" if u_dir == "up" else "b-green"
        u_ext = f"{ubi['extreme_price']:.2f}（{ubi.get('extreme_time', '')}）" if ubi.get("extreme_price") is not None else "—"
        ubi_html = (f'<tr><td><b>未完成笔</b></td><td><span class="badge {u_cls}">{"向上延伸" if u_dir == "up" else "向下延伸"}</span></td>'
                    f'<td>起点 {ubi["start_price"]:.2f}（{ubi.get("start_time", "")}）→ 极值 {u_ext}；'
                    f'该笔尚未走完，分型确认前方向仍可能变化。</td></tr>')
    op = _chan_operation(sig_label, pos, beichi, horizon)
    fx_n = lv.get("fractals")
    fx_txt = f" ｜ 分型 {fx_n}" if isinstance(fx_n, int) else ""
    thin = lv.get("thin_note") or ""
    thin_html = f'<p class="muted" style="font-size:11px;margin:2px 0 0">{_esc(thin)}</p>' if thin else ""
    return f'''
      <div class="chan-lv-head">▸ {lv.get("level", "?")}级别<span class="muted" style="font-weight:400;font-size:11px">（{horizon}视界 ｜ 数据 {lv.get("data_range", "")}{fx_txt} ｜ 笔 {lv.get("bis", 0)} ｜ 最新价 <b>{lv.get("last_price", 0):.2f}</b>）</span></div>
      <table><thead><tr><th style="width:18%">维度</th><th style="width:26%">状态</th><th>说明</th></tr></thead><tbody>
        <tr><td><b>当前位置</b></td><td><span class="badge {pos_cls}">{_esc(pos)}</span></td><td>当前价相对最近中枢的位置：上方偏多、下方偏空、区间内为震荡整理</td></tr>
        {zs_html}
        <tr><td><b>缠论信号</b></td><td><span class="badge {sig_cls}">{_esc(sig_label)}</span></td><td>{_esc(sig.get("text", ""))}</td></tr>
        {beichi_html}
        {bis_html}
        {ubi_html}
        <tr><td><b>操作含义</b></td><td colspan="2">{_esc(op)}</td></tr>
      </tbody></table>{thin_html}'''


# ======================================================================
# 整页渲染
# ======================================================================

def render(date_str: str) -> tuple[str, str]:
    """返回 (html, 状态说明)。数据缺失时返回 ("", 原因)。"""
    date8 = date_str.replace("-", "")
    c = load_forecast(date8)
    if not c:
        return "", f"未找到 data/chan/chan_forecast_{date8}.json（先跑 chan_analysis.py）"
    if c.get("error"):
        return "", f"数据文件含错误: {c.get('error')}"

    levels = c.get("levels")
    if not levels and c.get("signal"):
        levels = [c]
    ok_levels = [lv for lv in (levels or []) if not lv.get("error")]
    if not ok_levels:
        return "", "全部级别无有效数据"

    engine = str(c.get("engine") or ok_levels[0].get("engine") or "stdlib")
    engine_note = ("分型/笔/中枢由开源库 czsc（Rust 核心）识别；背驰与买卖点为适配层口径，判定有主观性，不构成精确预测"
                   if engine.startswith("czsc") else
                   "缠论风格简化实现（czsc 未安装，标准库兜底）；背驰与买卖点判定有主观性，不构成精确预测")
    lv_labels = "+".join(str(lv.get("level", "?")) for lv in ok_levels)

    cut_txt = " ｜ ".join(
        f"{lv.get('level', '?')} {str(lv.get('last_time') or '').replace(' 00:00', '')}"
        for lv in ok_levels)
    _d_stale = any(str(lv.get("level", "")).startswith("日线")
                   and str(lv.get("last_time", ""))[:10] != date_str for lv in ok_levels)
    stale_note = "（日线只取已收盘 K 线：当日未收盘不纳入，避免用未完成的 K 线判分型/笔）" if _d_stale else ""

    _from = c.get("from_time")
    anchor_html = ""
    if _from:
        _roots = " ｜ ".join(f"{lv.get('level', '?')} {lv.get('bars', '?')} 根" for lv in ok_levels)
        anchor_html = (f'<p class="muted" style="font-size:12px">起点锚定：<b>{_esc(_from)}</b>'
                       f'（本轮日线中枢最高点 GG 所在 K 线；分钟级别自此截取，日线保持全量）'
                       f'｜ 实际根数：{_esc(_roots)}</p>')

    readme_html = (
        '<div class="muted" style="font-size:11.5px;background:#fafbff;border:1px solid #e8eaf6;'
        'border-radius:8px;padding:8px 12px;margin:8px 0 4px;line-height:1.75">'
        '<b>怎么读这份报告：</b>「中枢」是多空反复争夺的价格密集区——'
        '上沿 ZG 为压力、下沿 ZD 为支撑；「笔」是一段明确的方向（上/下）；'
        '「位置」是当前价相对中枢的位置（上方偏多、下方偏空、区间内为震荡）；'
        '「二买」指回调不破前低后的右侧买点；「背驰」指同向力度衰减、提示可能反转。'
        '<b>三级别联立：</b>日线定波段方向、30分钟定短线结构、5分钟找精确买卖点——'
        '低级别中枢嵌套在高级别中枢内属「盘整中的盘整」（方向未定）；'
        '低级别先转、高级别未转时，短线可博弈但需高级别笔转向确认。'
        '</div>')

    blocks = "".join(_chan_level_block(lv) for lv in ok_levels)

    # 多级别联立研判
    synth_html = ""
    _syn = c.get("synthesis") or {}
    if _syn.get("text"):
        _badge = ('<span class="badge b-blue">盘整中的盘整</span>' if _syn.get("nested")
                  else '<span class="badge b-orange">结构切换</span>')
        synth_html = (f'<div class="alert-orange" style="margin:10px 0 0;font-size:12.5px;line-height:1.75">'
                      f'<b>多级别联立研判</b> {_badge}<br>{_esc(_syn["text"])}</div>')
    elif len(ok_levels) >= 2:
        stances = [(str(lv.get("level", "?")), _chan_stance((lv.get("signal") or {}).get("signal"))) for lv in ok_levels]
        total = "、".join(f"{n}{s}" for n, s in stances)
        synth_html = (f'<div class="alert-orange" style="margin:10px 0 0;font-size:12.5px">'
                      f'<b>多级别研判：</b>{total}——高级别定方向、低级别找买卖点；'
                      f'以日线中枢上下沿为关键位，低级别信号服从高级别结构。</div>')

    # 30分钟 ↔ 5分钟 关联计算
    link_html = ""
    _lk = c.get("link") or {}
    if _lk:
        _lo_n, _hi_n = _lk.get("low", "5分钟"), _lk.get("high", "30分钟")
        _seg = _lk.get("seg") or {}
        _seg_dir_cn = "上涨" if _seg.get("dir") == "up" else "下跌"
        _qjt = ('<span class="badge b-red">成立</span>' if _lk.get("qujiantao")
                else '<span class="badge b-gray">未成立</span>')
        _nest = ('<span class="badge b-blue">已嵌套</span>' if _lk.get("zs_nested")
                 else '<span class="badge b-orange">未嵌套</span>')
        _link_rows = [
            ("笔递归倍率", f'<b>{_lk.get("bi_ratio")}:1</b>', _esc(_lk.get("bi_ratio_text", ""))),
            (f"高级别最近走势段<br><span class='muted' style='font-size:11px'>({_hi_n})</span>",
             f'{_seg_dir_cn}<br><span class="muted" style="font-size:11px">{_esc(_seg.get("start",""))}<br>~ {_esc(_seg.get("end",""))}</span>',
             _esc(_lk.get("completion_text", ""))),
            ("区间套背驰", _qjt, _esc(_lk.get("qujiantao_text", ""))),
            ("中枢递归", _nest, _esc(_lk.get("zs_text", ""))),
        ]
        _rows = "".join(
            f'<tr><td>{k}</td><td style="text-align:center">{v}</td><td>{d}</td></tr>'
            for k, v, d in _link_rows)
        link_html = (
            f'<div class="chan-lv-head">▸ {_hi_n} ↔ {_lo_n} 关联计算（结构联动，非文本对比）</div>'
            f'<table><thead><tr><th>关联维度</th><th style="text-align:center">结果</th>'
            f'<th>解读</th></tr></thead><tbody>{_rows}</tbody></table>'
            f'<div class="alert-orange" style="margin:8px 0 0;font-size:12.5px;line-height:1.7">'
            f'<b>关联结论：</b>{_esc(_lk.get("verdict", ""))}</div>')

    css = load_css()
    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>缠论推演 · 上证指数（{lv_labels}）· {date_str}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>缠论推演 · 上证指数多级别结构分析</h1>
    <p class="muted">报告日期 <b>{date_str}</b> ｜ 级别 {lv_labels} ｜ 引擎 {_esc(engine)} ｜ 生成于 {gen_at}</p>
  </div>

  <div class="card" id="sec-chan">
    <h2>缠论推演（上证指数 {lv_labels} · 操作指引）</h2>
    <p class="muted" style="font-size:12px">引擎 <b>{_esc(engine)}</b> ｜ 级别：{lv_labels}（高级别定方向，低级别找买卖点）</p>
    <p class="muted" style="font-size:12px">数据截至：<b>{_esc(cut_txt)}</b>{_esc(stale_note)}</p>
    {anchor_html}
    {readme_html}
    {blocks}
    {link_html}
    {synth_html}
    <p class="muted" style="font-size:11px;margin:6px 0 0">{_esc(engine_note)}。</p>
  </div>

  <div class="card">
    <p class="muted" style="font-size:11.5px">
      <b>免责声明：</b>本报告基于公开行情数据与量化规则自动生成，缠论结构识别存在主观性，
      仅供参考，不构成投资建议。市场有风险，投资需谨慎。
    </p>
  </div>
</div>
</body>
</html>'''
    return html, "ok"


def main():
    ap = argparse.ArgumentParser(description="生成缠论推演报告（独立于日报）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="报告日期 YYYY-MM-DD")
    ap.add_argument("--run", action="store_true",
                    help="先生成数据（chan_analysis.py --from auto）再出报告")
    ap.add_argument("--from", dest="from_time", default="auto",
                    help="起点锚定：auto（默认，日线中枢 GG）或具体时间；仅 --run 时生效")
    args = ap.parse_args()

    if args.run:
        cmd = [sys.executable, str(BASE_DIR / "chan_analysis.py"),
               "000001", "1000", "600", "--from", args.from_time]
        print(f"[缠论报告] 生成数据: {' '.join(cmd[1:])}")
        subprocess.run(cmd, cwd=str(BASE_DIR))
        # chan_analysis 按运行日写文件；若报告日期非当日，做一次对齐
        today8 = datetime.now().strftime("%Y%m%d")
        want8 = args.date.replace("-", "")
        if today8 != want8:
            src = BASE_DIR / "data" / "derived" / "chan" / f"chan_forecast_{today8}.json"
            dst = BASE_DIR / "data" / "derived" / "chan" / f"chan_forecast_{want8}.json"
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[缠论报告] 数据文件已对齐: {today8} → {want8}")

    html, status = render(args.date)
    if not html:
        print(f"[缠论报告] ❌ {status}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"缠论推演-{args.date}.html"
    out.write_text(html, encoding="utf-8")
    print(f"报告已生成: {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
