#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 A股操作指引 9 章节报告（参数化日期与类型），数据全部来自实时快照。

用法: python build_report.py --date 2026-08-17 --type 早报|晚报|周报

数据来源（全部实时，无任何写死行情/宏观/原油/ETF 数值）：
  - 数据引擎 a_stock_agent.py 采集写入 data/snapshots/fetched_YYYYMMDD_HHMMSS.json
    （微博/大V、宏观新闻、事件因子、日本传导链、技术、A股指数、隔夜美股、ETF资金流）
  - 见顶诊断 stock_diagnosis.run_all(自选股) 或 diagnosis_YYYYMMDD.json
任何数据缺口均渲染为「实时数据缺失」占位，绝不出现假数据。
"""
import json
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---- 参数：--date 报告日期(YYYY-MM-DD) / --type 报告类型(决定写入目录) ----
ap = argparse.ArgumentParser(description="生成 A股操作指引 9 章节报告（实时数据）")
ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="报告日期 YYYY-MM-DD")
ap.add_argument("--type", default="早报", choices=["早报", "晚报", "周报"],
                help="报告类型：早报/晚报/周报，决定写入 reports/<类型>/ 目录")
_args = ap.parse_args()
TODAY = _args.date
DATE8 = TODAY.replace("-", "")
REPORT_TYPE = _args.type
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

_TYPE_LABEL = {"早报": "盘前版", "晚报": "盘后版", "周报": "周度回顾"}
_TYPE_STATE = {"早报": "盘前", "晚报": "盘后", "周报": "周报"}
REPORT_LABEL = _TYPE_LABEL.get(REPORT_TYPE, "盘前版")
REPORT_STATE = _TYPE_STATE.get(REPORT_TYPE, "盘前")

# ---- 加载配置（自选股 + 大V 源，避免写死） ----
import a_stock_agent as agent
cfg = agent.load_config()
WATCHLIST = cfg.get("watchlist_stocks", []) or []
# 稳定分类元数据（行业归属，非行情数据）：code -> 行业
SECTOR = {
    "688668": "连接器+液冷", "688409": "半导体设备零部件", "600641": "离子注入机",
    "000725": "面板+AI封装", "301392": "PVD设备", "688530": "靶材", "600580": "机器人电机",
}
VS_NAMES = [s.get("name", "") for s in cfg.get("weibo_sources", [])]

# ---- 加载实时快照（数据引擎产出，位于 data/snapshots/，取当日最新一份） ----
PLACEHOLDER = '<p class="muted" style="font-size:12px;">实时数据缺失（请先运行 `python a_stock_agent.py` 采集后再生成报告）。</p>'
_snap_dir = BASE_DIR / "data" / "snapshots"
_candidates = sorted(_snap_dir.glob(f"fetched_{DATE8}*.json")) if _snap_dir.exists() else []
if not _candidates:
    # 向后兼容：旧路径 data/fetched_YYYYMMDD.json
    _old = BASE_DIR / "data" / f"fetched_{DATE8}.json"
    if _old.exists():
        _candidates = [_old]
snap_path = _candidates[-1] if _candidates else None
snapshot = {}
if snap_path is not None and snap_path.exists():
    try:
        snapshot = json.load(open(snap_path, encoding="utf-8"))
        print(f"[读取] 使用快照: {snap_path.name}")
    except Exception as e:
        print(f"[警告] 快照解析失败 {snap_path}: {e}")
else:
    print(f"[警告] 未找到 {DATE8} 的实时快照，请先运行 `python a_stock_agent.py` 采集数据。报告仅含占位。")

weibo_data = snapshot.get("weibo_data", {})
quotes = snapshot.get("quotes", {})
us_market = snapshot.get("us_market", []) or []
etf = snapshot.get("etf", []) or []
market_state = snapshot.get("market_state", "neutral")
matched_sectors = snapshot.get("matched_sectors", []) or []


def wb_texts(name):
    return [p.get("text", "") for p in weibo_data.get(name, []) if p.get("text")]


# 实时大V / 各类信源文本
tangshi = wb_texts("唐史主任司马迁")
touxing_asset = wb_texts("投星资产")
touxing_yeye = wb_texts("投星大爷")
macro_items = [t for k in weibo_data if k.startswith("[宏观]") for t in wb_texts(k)]
event_items = [t for k in weibo_data if k.startswith("[事件]") for t in wb_texts(k)]
japan_items = [t for k in weibo_data if k.startswith("[日本]") for t in wb_texts(k)]
tech_items = [t for k in weibo_data if k.startswith("[技术]") for t in wb_texts(k)]
global_items = [t for k in weibo_data if k.startswith("[全球]") for t in wb_texts(k)]

# ---- 个股诊断（实时） ----
diag_raw = []
diag_path = BASE_DIR / "data" / "diagnosis" / f"diagnosis_{DATE8}.json"
if diag_path.exists():
    try:
        diag_raw = json.load(open(diag_path, encoding="utf-8"))
    except Exception as e:
        print(f"[诊断] 读取 {diag_path} 失败: {e}")
else:
    try:
        import stock_diagnosis as sd
        diag_raw = sd.run_all(WATCHLIST)
        # 缓存到当日文件，便于回溯与回测
        diag_path.parent.mkdir(parents=True, exist_ok=True)
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(diag_raw, f, ensure_ascii=False, indent=2)
        print(f"[诊断] 实时诊断完成并缓存: {diag_path}")
    except Exception as e:
        print(f"[诊断] 实时诊断失败: {e}")


def diag_for(code):
    for d in diag_raw:
        if d.get("code") == code:
            return d
    return None


def action_from_diag(d):
    if not d:
        return ("观望", "b-gray")
    lvl = d.get("level", "")
    if "安全" in lvl:
        return ("持有/加仓", "b-red")
    if "预警" in lvl or "见顶" in lvl:
        return ("谨慎/回避", "b-green")
    return ("持有", "b-blue")


def watch_name(code):
    d = diag_for(code)
    if d and d.get("name"):
        return d["name"]
    return code


# ---------- 工具函数 ----------
def up_down(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "muted"
    return "up" if f > 0 else ("down" if f < 0 else "muted")


def sign(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "+" if f > 0 else ""


def fmt(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def points(items, maxn=10):
    items = [t.strip() for t in (items or []) if t and t.strip()]
    if not items:
        return PLACEHOLDER
    return "".join(f'<div class="point"><div class="pt-body">{t}</div></div>' for t in items[:maxn])


def us_bar(name, pct, val, sig):
    try:
        pf = float(pct)
    except (TypeError, ValueError):
        pf = 0.0
    denom = max((abs(float(x[1])) for x in us_market if isinstance(x[1], (int, float))), default=1) or 1
    w = abs(pf) / denom * 100 if denom else 0
    color = "#d63031" if pf > 0 else "#00a865"
    if pf == 0:
        color = "#888"
    return (f'<div class="bar-row"><div class="bar-name">{name}</div>'
            f'<div class="bar-wrap"><div class="bar-fill" style="width:{w:.1f}%;background:{color}"></div></div>'
            f'<div class="bar-val {up_down(pf)}">{sign(pf)}{pf}%</div></div>')


def stock_card(code):
    sector = SECTOR.get(code, "")
    d = diag_for(code)
    if d:
        score = fmt(d.get("total_score", "—"))
        level = d.get("level", "—")
        trend = d.get("trend_status", "—")
        action, cls = action_from_diag(d)
        sig = d.get("signals", "")
        if isinstance(sig, str):
            sig = sig[:80]
        logic = f"{sector}；诊断：{level}·{trend}。" + (f"信号：{sig}" if sig else "")
    else:
        score, level, trend = "—", "—", "—"
        action, cls = "观望", "b-gray"
        logic = f"{sector}（实时诊断缺失）"
    return f'''
    <div class="stock-card">
      <div class="stock-title">
        <div class="name">{watch_name(code)} <span class="muted">{code}</span>　<span class="tag">{sector}</span></div>
        <span class="badge {cls}">{action}</span>
      </div>
      <p class="stock-logic">{logic}</p>
      <p class="stock-meta">见顶诊断：评分 {score} | {level} | {trend}</p>
    </div>'''


def chain_svg():
    nodes = [
        ("原油(触发)", "上游地缘/供需"),
        ("日本加息", "输入型通胀→被迫加息"),
        ("抛美债压力", "巨额美债持仓"),
        ("FIMA回购", "押美债借美元干预"),
        ("日元干预", "联合干预汇率"),
        ("套息平仓", "unwind 黑天鹅"),
        ("A股传导", "风险偏好"),
    ]
    n = len(nodes)
    bw, gap = 128, 18
    w = 16 + n * bw + (n - 1) * gap + 16
    h = 110
    x0 = 16
    svg = ['<svg class="chain-svg" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'.format(w=w, h=h)]
    for i, (name, val) in enumerate(nodes):
        x = x0 + i * (bw + gap)
        if any(k in name for k in ("加息", "平仓", "干预")):
            color = "#d63031"
        elif any(k in name for k in ("原油", "美债", "FIMA")):
            color = "#e67e22"
        else:
            color = "#1967d2"
        svg.append(f'<rect x="{x}" y="20" width="{bw}" height="54" rx="8" fill="{color}" opacity="0.92"/>')
        svg.append(f'<text x="{x+bw/2}" y="42" fill="#fff" font-size="12" font-weight="700" text-anchor="middle">{name}</text>')
        svg.append(f'<text x="{x+bw/2}" y="60" fill="#fff" font-size="10" text-anchor="middle">{val}</text>')
        if i < len(nodes) - 1:
            ax = x + bw + 4
            svg.append(f'<line x1="{ax}" y1="47" x2="{ax+gap-4}" y2="47" stroke="#bbb" stroke-width="2" marker-end="url(#ar)"/>')
    svg.append('<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#bbb"/></marker></defs>')
    svg.append('</svg>')
    note = f'<p class="muted" style="font-size:12px;">传导链主线由日元主导；当前实时日本信号 {len(japan_items)} 条（详见下方文本）。</p>'
    return "".join(svg) + note


def us_section():
    if not us_market:
        return PLACEHOLDER
    rows = "".join(
        f'<tr><td>{n}</td><td class="{up_down(p)}">{sign(p)}{p}%</td><td>{sig}（{v}）</td></tr>'
        for n, p, v, sig in us_market)
    bars = "".join(us_bar(n, p, v, s) for n, p, v, s in us_market)
    return f'<table><thead><tr><th>标的</th><th>涨跌</th><th>关键信号</th></tr></thead><tbody>{rows}</tbody></table><div style="margin-top:12px;">{bars}</div>'


def etf_section():
    if not etf:
        return PLACEHOLDER
    rows = "".join(
        f'<tr><td>{e[0]}</td><td>{e[1]}</td><td><span class="badge {e[3]}">{e[2]}</span></td><td>{e[4]}</td></tr>'
        for e in etf)
    return f'<table><thead><tr><th>ETF</th><th>代码</th><th>方向</th><th>信号</th></tr></thead><tbody>{rows}</tbody></table>'


def index_section():
    if not quotes:
        return PLACEHOLDER
    rows = "".join(
        f'<tr><td>{name}</td><td>{q.get("price")}</td><td class="{up_down(q.get("chg_pct"))}">{sign(q.get("chg_pct"))}{q.get("chg_pct")}%</td></tr>'
        for name, q in quotes.items())
    return f'<table><thead><tr><th>指数</th><th>最新</th><th>涨跌幅</th></tr></thead><tbody>{rows}</tbody></table>'


def conclusion_grid():
    state_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    sectors = "、".join(matched_sectors) or "—"
    up_us = sum(1 for x in us_market if (x[1] if isinstance(x[1], (int, float)) else 0) > 0)
    us_txt = f"{up_us}/{len(us_market)} 上涨" if us_market else "实时数据缺失"
    risk_txt = f"日本加息/套息平仓预警（实时信号 {len(japan_items)} 条）" if japan_items else "暂无日本传导链实时预警"
    items = [
        ("市场状态", state_label, "#1967d2"),
        ("命中板块", sectors, "#d63031"),
        ("隔夜美股", us_txt, "#e67e22"),
        ("主线", sectors, "#d63031"),
        ("风险", risk_txt, "#d63031" if japan_items else "#00a865"),
    ]
    cells = ""
    for label, value, color in items:
        cells += f'''<div class="conclusion-item">
    <div class="label">{label}</div>
    <div class="value" style="color:{color};">{value}</div>
  </div>'''
    return cells


def resonance_section():
    rows = ""
    if matched_sectors:
        rows += f'<tr><td><b>板块共振</b></td><td><span class="badge b-red">实时</span></td><td>{"、".join(matched_sectors)}</td></tr>'
    up_us = [x[0] for x in us_market if (x[1] if isinstance(x[1], (int, float)) else 0) > 0]
    if up_us:
        rows += f'<tr><td><b>美股映射</b></td><td><span class="badge b-red">实时</span></td><td>{"、".join(up_us[:6])}</td></tr>'
    net_etf = [e[0] for e in etf if "净申购" in e[2]]
    if net_etf:
        rows += f'<tr><td><b>资金确认</b></td><td><span class="badge b-red">实时</span></td><td>{"、".join(net_etf)}</td></tr>'
    if japan_items:
        rows += f'<tr><td><b>风险项</b></td><td><span class="badge b-green">实时</span></td><td>日本传导链 {len(japan_items)} 条信号（详见传导链章节）</td></tr>'
    if tech_items:
        rows += f'<tr><td><b>技术</b></td><td><span class="badge b-blue">实时</span></td><td>{len(tech_items)} 个标的量价信号</td></tr>'
    if not rows:
        return PLACEHOLDER
    return f'<table><thead><tr><th>方向</th><th>共振</th><th>来源交叉（实时）</th></tr></thead><tbody>{rows}</tbody></table>'


def strategy_section():
    state_label = {"bullish": "偏多积极", "bearish": "防御为主", "neutral": "中性偏谨慎"}.get(market_state, "中性")
    idx_line = "；".join(f"{n} {q.get('chg_pct')}%" for n, q in quotes.items() if q.get("chg_pct") is not None) if quotes else "指数实时数据缺失"
    avoid = [watch_name(c) for c in WATCHLIST if diag_for(c) and ("预警" in diag_for(c).get("level", "") or "见顶" in diag_for(c).get("level", ""))]
    rows = f'''
    <tr><td><b>大盘</b></td><td>实时状态 {state_label}；主要指数：{idx_line}。</td></tr>
    <tr><td><b>仓位</b></td><td>依据实时市场状态 {state_label} 调节，不追高。</td></tr>
    <tr><td><b>主线</b></td><td>{"、".join(matched_sectors) if matched_sectors else "实时板块信号缺失"}。</td></tr>
    <tr><td><b>风险</b></td><td>{"日本传导链有实时预警信号，关注日元/套息平仓。" if japan_items else "暂无日本传导链实时预警。"}</td></tr>
    <tr><td><b>回避</b></td><td>{"、".join(avoid) if avoid else "当前自选股诊断无预警/见顶项。"}</td></tr>'''
    return f'<table><thead><tr><th style="width:18%">维度</th><th>策略（实时驱动）</th></tr></thead><tbody>{rows}</tbody></table>'


def focus_section():
    """限时关注的重点数据解析（嵌入日报，置于「今日操作策略」之前）。

    数据优先级：① 当日已抓取的缓存 state（data/focus/focus_state_<DATE8>.json）；
    ② 缺失则尝试实时运行 focus_monitor 抓取真实数据；③ 均不可用则按项目规则
    渲染「实时数据缺失」占位，绝不出现假数据。
    """
    try:
        import focus_monitor as fm
        import json as _json
        from html import escape as _escape
        fm_dir = BASE_DIR / "data" / "focus"
        cached = fm_dir / f"focus_state_{DATE8}.json"
        state = None
        if cached.exists():
            try:
                state = _json.loads(cached.read_text(encoding="utf-8"))
            except Exception:
                state = None
        if state is None:
            try:
                state = fm.run_focus_monitor(no_fetch=False)
            except Exception:
                state = None
        if state is None:
            return ('<div class="card"><h2>限时关注的重点数据解析（实时）</h2>'
                    '<p class="muted" style="font-size:12px;">实时数据缺失（外网/代理不可达，'
                    '未能获取「抛美债 / FIMA工具 / 大机构日元加息」三类信号研判。'
                    '请先运行 `python focus_monitor.py` 采集后再生成报告）。</p></div>')
        frag = fm.render_focus_html(state, standalone=False, embed=True)
        return f'<div class="card"><h2>限时关注的重点数据解析（实时）</h2>{frag}</div>'
    except Exception as e:
        return ('<div class="card"><h2>限时关注的重点数据解析（实时）</h2>'
                f'<p class="muted" style="font-size:12px;">模块加载失败：{_escape(str(e))}</p></div>')


# 页眉实时涨跌
def header_market():
    if not quotes:
        return "实时指数数据缺失"
    parts = []
    for n in ("上证指数", "深证成指", "创业板指"):
        q = quotes.get(n)
        if q and q.get("chg_pct") is not None:
            parts.append(f"{n.replace('指数','')}{q['chg_pct']}%")
    return "　".join(parts) if parts else "实时指数数据缺失"


# ================= 组装 HTML =================
# ================= 微博舆情解构（第六节用，不在核心结论展开） =================
# 多空词库：把大V / 宏观 / 事件原文「解构」成可研判的多空信号，而非流水账
BULL_WORDS = ["看多", "看好", "利好", "机会", "上涨", "突破", "加仓", "买入", "牛市", "底部",
              "反弹", "复苏", "景气", "超预期", "积极", "乐观", "主线", "确定性", "配置", "布局",
              "修复", "上行", "走强", "领涨", "拐点", "困境反转", "戴维斯", "净流入", "资金流入"]
BEAR_WORDS = ["看空", "利空", "风险", "下跌", "回调", "减仓", "卖出", "熊市", "顶部", "见顶",
              "泡沫", "警惕", "谨慎", "防御", "避险", "承压", "收缩", "降温", "拖累", "爆雷",
              "不确定", "观望", "走弱", "下行", "破位", "杀跌", "踩踏", "净流出", "资金流出", "暴雷"]
NEG_WORDS = ["不", "没", "没有", "无", "未", "别", "勿", "非", "未必", "不可", "不要", "难", "缺乏", "缺少", "尚未"]


def _score_text(text):
    """对单条文本做多空打分（含就近否定翻转），返回净分。"""
    if not text:
        return 0
    score = 0
    for w in BULL_WORDS:
        idx = 0
        while True:
            i = text.find(w, idx)
            if i < 0:
                break
            window = text[max(0, i - 3):i]
            score -= 1 if any(n in window for n in NEG_WORDS) else 1
            idx = i + len(w)
    for w in BEAR_WORDS:
        idx = 0
        while True:
            i = text.find(w, idx)
            if i < 0:
                break
            window = text[max(0, i - 3):i]
            score += 1 if any(n in window for n in NEG_WORDS) else -1
            idx = i + len(w)
    return score


def deconstruct_weibo():
    """解构微博舆情：提炼大V对大盘与个股的多空研判。返回结构化 dict。"""
    vs_sources = {
        "唐史主任司马迁": tangshi,
        "投星资产": touxing_asset,
        "投星大爷": touxing_yeye,
    }
    src_scores = {}
    all_vs = []
    for name, texts in vs_sources.items():
        s = sum(_score_text(t) for t in texts)
        src_scores[name] = s
        all_vs.extend(texts)
    total = sum(src_scores.values())
    if total > 1:
        consensus = ("偏多", "b-red")
    elif total < -1:
        consensus = ("偏空", "b-green")
    elif total == 0:
        consensus = ("中性", "b-blue")
    else:
        consensus = ("分歧", "b-orange")

    # 个股 / 板块提及解构：名称或行业命中即视为被大V覆盖，记录多空净分
    stock_mentions = {}
    for code in WATCHLIST:
        nm = watch_name(code)
        sec = SECTOR.get(code, "")
        for t in all_vs:
            kw = (nm if (nm and nm in t) else None) or (sec if (sec and sec in t) else None)
            if not kw:
                continue
            sc = _score_text(t)
            info = stock_mentions.setdefault(code, {"name": nm, "score": 0, "snippets": []})
            info["score"] += sc
            i = t.find(kw)
            info["snippets"].append(t[max(0, i - 12):i + len(kw) + 24])

    # 关键论点：净分绝对值最高的代表性观点（已解构，非原文堆砌）
    key = []
    for t in all_vs:
        sc = _score_text(t)
        if sc != 0:
            key.append((abs(sc), sc, t[:78]))
    key.sort(key=lambda x: -x[0])
    key = key[:3]

    # 风险点：日本传导链 / 事件因子中的利空信号
    risks = [t[:78] for t in (japan_items + event_items) if _score_text(t) <= -1][:3]

    return {"src_scores": src_scores, "total": total, "consensus": consensus,
            "stock_mentions": stock_mentions, "key": key, "risks": risks}


def core_conclusion():
    """核心结论：一句话研判 + 状态徽章 + 数据速览。舆情解构细节见第六节。"""
    d = deconstruct_weibo()
    consensus_label, consensus_cls = d["consensus"]
    idx_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    up_n = sum(1 for q in quotes.values() if q.get("chg_pct", 0) > 0) if quotes else 0
    idx_n = len(quotes) if quotes else 0

    # 用解构结果驱动一句话研判（不在本节展开解构细节）
    if consensus_label == "偏多" and idx_label in ("偏多", "震荡"):
        stance = "情绪与指数共振偏多，可积极但不追高"
    elif consensus_label == "偏空" and idx_label == "偏多":
        stance = "指数走强但情绪偏空，注意背离与回调"
    elif consensus_label == "偏空":
        stance = "情绪与指数双弱，以防御为主"
    else:
        stance = "多空交织，震荡格局下重结构轻指数"

    main_line = "、".join(matched_sectors) if matched_sectors else "板块主线信号缺失"
    risk_label = "日本传导链有预警" if japan_items else "暂无日本传导链预警"
    risk_cls = "b-green" if japan_items else "b-blue"

    verdict = (f"综合实时指数与微博舆情解构：大盘 <b>{idx_label}</b>（{idx_n} 个主要指数中 {up_n} 个上涨），"
               f"大V意见领袖共识 <b class='{consensus_cls}'>{consensus_label}</b>。{stance}。"
               f"主线聚焦「{main_line}」；{risk_label}。")

    badges = (
        f'<span class="badge {consensus_cls}">大V共识 {consensus_label}</span>'
        f'<span class="badge b-red">指数 {idx_label}</span>'
        f'<span class="badge b-orange">主线 {main_line}</span>'
        f'<span class="badge {risk_cls}">风险 {risk_label}</span>'
    )

    return f'''
    <div class="cc-body" style="font-size:14px;line-height:1.9;margin-bottom:12px">{verdict}</div>
    <div class="cc-stocks">{badges}</div>
    <div class="cc-grid-title">实时数据速览</div>
    <div class="conclusion-grid">{conclusion_grid()}</div>
    '''


def vs_summary():
    """第六节：微博舆情解构。把大V/宏观/事件原文解构为可研判信号（不贴原文）。"""
    d = deconstruct_weibo()
    consensus_label, consensus_cls = d["consensus"]
    idx_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    up_n = sum(1 for q in quotes.values() if q.get("chg_pct", 0) > 0) if quotes else 0
    idx_n = len(quotes) if quotes else 0

    src_bits = []
    for name, s in d["src_scores"].items():
        if not s:
            continue
        tag = "看多" if s > 0 else ("看空" if s < 0 else "中性")
        cls = "b-red" if s > 0 else ("b-green" if s < 0 else "b-blue")
        src_bits.append(f'<span class="badge {cls}">{name}：{tag}（{"+" if s > 0 else ""}{s}）</span>')
    consensus_html = (f'大V整体共识 <b class="{consensus_cls}">{consensus_label}</b>；'
                      + (" ".join(src_bits) if src_bits else "实时大V观点缺失，无法解构"))

    diverge = (consensus_label == "偏空" and idx_label == "偏多")
    market_view = (f"指数层面 <b>{idx_label}</b>（{idx_n} 指 {up_n} 涨）与大V共识 <b class='{consensus_cls}'>{consensus_label}</b> "
                   f"—— {'两者背离，需警惕情绪拖累' if diverge else '相互印证'}。"
                   f"舆情已解构为研判信号，不为原文堆砌。")

    stock_bits = []
    for code in WATCHLIST:
        nm = watch_name(code)
        info = d["stock_mentions"].get(code)
        if info and info["snippets"]:
            if info["score"] > 0:
                cls, tone = "b-red", "偏多"
            elif info["score"] < 0:
                cls, tone = "b-green", "偏空"
            else:
                cls, tone = "b-blue", "中性"
            stock_bits.append(f'<span class="badge {cls}">{nm}·{tone}</span>')
        else:
            # 大V未点名，但板块主题被舆情共振命中时标注「主题覆盖」
            sec = SECTOR.get(code, "")
            sec_hit = bool(sec) and any(k in sec for k in matched_sectors)
            if sec_hit:
                stock_bits.append(f'<span class="badge b-blue">{nm}·主题覆盖</span>')
            else:
                stock_bits.append(f'<span class="tag">{nm}·未提及</span>')
    stock_line = " ".join(stock_bits)

    key_html = "".join(
        f'<div class="{"alert-red" if sc < 0 else "alert-green" if sc > 0 else "alert-blue"}">'
        f'<b>[{ "看空" if sc < 0 else "看多" if sc > 0 else "中性"}]</b> {t}…</div>'
        for _, sc, t in d["key"]) or '<p class="muted">实时大V观点未提取到强多空信号。</p>'

    risk_html = "".join(f'<div class="alert-red">⚠ {t}…</div>' for t in d["risks"]) or \
        '<p class="muted">实时风险因子（日本传导链 / 事件）未提取到明确利空信号。</p>'

    return f'''
    <div class="cc-view">
      <div class="cc-head">大V共识解构</div>
      <div class="cc-body">{consensus_html}</div>
      <div class="cc-head">大盘研判（解构信号 → 指数确认）</div>
      <div class="cc-body">{market_view}</div>
      <div class="cc-head">自选股解构（大V提及与多空倾向）</div>
      <div class="cc-body cc-stocks">{stock_line}</div>
      <div class="cc-head">关键论点（来自实时大V观点，已解构）</div>
      {key_html}
      <div class="cc-head">风险预警（实时因子解构）</div>
      {risk_html}
    </div>
    '''


CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1d2129;line-height:1.8}
.wrap{max-width:980px;margin:0 auto;padding:24px 20px 60px}
.header{background:linear-gradient(135deg,#3C3489 0%,#534AB7 100%);color:#fff;border-radius:14px;padding:28px 30px;margin-bottom:22px}
.header h1{font-size:24px;font-weight:700;margin-bottom:6px}
.header .sub{font-size:13px;opacity:.85}
.header .meta{display:flex;gap:12px;margin-top:14px;font-size:12px;flex-wrap:wrap}
.header .meta span{background:rgba(255,255,255,.16);padding:4px 14px;border-radius:20px}
.card{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}
.card h2{font-size:16px;font-weight:700;color:#3C3489;margin-bottom:14px;padding-left:10px;border-left:4px solid #534AB7}
.card h3{font-size:14px;font-weight:600;color:#333;margin:16px 0 8px}
.card p{font-size:13px;color:#444;margin-bottom:8px}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin:2px}
.b-red{background:#fde8e8;color:#d63031}
.b-blue{background:#e8f0fe;color:#1967d2}
.b-orange{background:#fff3e0;color:#e67e22}
.b-green{background:#e8f8f0;color:#00a865}
.b-gray{background:#f0f2f5;color:#666}
.tag{display:inline-block;background:#f0f2f5;color:#555;padding:2px 8px;border-radius:10px;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
th{background:#f0f2f5;color:#555;font-weight:600;padding:8px 10px;text-align:left;border-bottom:2px solid #e0e3e8}
td{padding:8px 10px;border-bottom:1px solid #eef0f3}
.up{color:#d63031;font-weight:600}
.down{color:#00a865;font-weight:600}
.muted{color:#999}
.point{padding:12px 14px;background:#f8f7ff;border-radius:8px;margin-bottom:10px;border-left:3px solid #AFA9EC}
.point .pt-title{font-size:14px;font-weight:600;color:#3C3489;margin-bottom:4px}
.point .pt-body{font-size:13px;color:#444}
.point .pt-action{font-size:12px;color:#e67e22;margin-top:4px;font-weight:500}
.alert{padding:12px 14px;border-radius:8px;margin-bottom:10px;font-size:13px}
.alert-red{background:#fde8e8;border-left:3px solid #d63031;color:#b03a2e}
.alert-orange{background:#fff3e0;border-left:3px solid #e67e22;color:#b3541a}
.alert-green{background:#e8f8f0;border-left:3px solid #00a865;color:#1e8449}
.alert-blue{background:#e8f0fe;border-left:3px solid #1967d2;color:#1f4e8c}
.conclusion-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.conclusion-item{background:#fafbfc;border-radius:10px;padding:14px;border:1px solid #eef0f3}
.conclusion-item .label{font-size:12px;color:#888;margin-bottom:4px}
.conclusion-item .value{font-size:14px;font-weight:600;color:#1a2b4a}
.cc-view{background:#fafbff;border:1px solid #e8eaf6;border-radius:10px;padding:14px 16px;margin-bottom:14px}
.cc-head{font-size:13px;font-weight:700;color:#3C3489;margin:12px 0 6px;padding-left:8px;border-left:3px solid #534AB7}
.cc-head:first-child{margin-top:0}
.cc-body{font-size:13px;color:#333;line-height:1.8}
.cc-stocks .badge,.cc-stocks .tag{margin:3px 5px 3px 0;display:inline-block}
.cc-grid-title{font-size:12px;color:#888;margin:6px 0 8px;font-weight:600}
.stock-card{border:1px solid #eef0f3;border-radius:10px;padding:16px;margin-bottom:12px}
.stock-title{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.stock-title .name{font-size:15px;font-weight:700;color:#1a2b4a}
.stock-logic{font-size:13px;color:#444;margin-bottom:6px}
.stock-meta{font-size:12px;color:#888;margin-bottom:8px}
.op-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.op-row .op{font-size:12px;padding:3px 10px;border-radius:8px}
.op-buy{background:#fde8e8;color:#d63031}
.op-hold{background:#fff3e0;color:#e67e22}
.op-sell{background:#e8f8f0;color:#00a865}
.op-watch{background:#e8f0fe;color:#1967d2}
.disclaimer{background:#fff;border:1px solid #e8eaed;border-radius:10px;padding:18px 22px;font-size:12px;color:#666;line-height:1.8}
.disclaimer strong{color:#d63031}
.chain-svg{width:100%;height:auto;display:block;margin:8px 0}
.toc{background:#fff;border-radius:12px;padding:18px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}
.toc h2{font-size:15px;color:#3C3489;margin-bottom:10px}
.toc ol{margin-left:20px;font-size:13px;color:#444}
.toc li{margin:4px 0}
.bar-row{display:flex;align-items:center;margin:5px 0;font-size:12.5px}
.bar-name{width:150px;text-align:right;padding-right:10px;color:#555}
.bar-wrap{flex:1;background:#f0f2f5;border-radius:4px;height:16px;overflow:hidden}
.bar-fill{height:100%}
.bar-val{width:70px;padding-left:10px}
"""

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股舆情操作指引 · 9章节 · {TODAY}（{REPORT_LABEL}）</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<div class="header">
<h1>A股舆情操作指引 · 9章节完整报告</h1>
<div class="sub">微博舆情 + 全球人物 + 宏观 + 事件因子 + 行情资金 + 技术走势 + 国家队 + 自选股（{REPORT_LABEL}）</div>
<div class="meta">
<span>报告日期：{TODAY}（{REPORT_LABEL}）</span>
<span>生成时间：{NOW}</span>
<span>{len(WATCHLIST)}只自选股</span>
<span>实时指数：{header_market()}</span>
</div>
</div>

<div class="toc">
<h2>目录</h2>
<ol>
<li>核心结论（实时）</li>
<li>隔夜美股（实时）</li>
<li>CPI与宏观（实时新闻）</li>
<li>宏观传导链监控（独立因子·实时）</li>
<li>地缘政治与原油（事件因子·实时）</li>
<li>ETF资金流向（实时）</li>
<li>微博舆情解构（唐史 / 投星 · 实时）</li>
<li>共振信号（多源交叉·实时）</li>
<li>7只自选股操作指引（实时诊断）</li>
<li>限时关注的重点数据解析（实时）</li>
<li>今日操作策略（实时驱动）</li>
<li>主要指数（实时）</li>
</ol>
</div>

<div class="card">
<h2>核心结论（实时）</h2>
{core_conclusion()}
</div>

<div class="card">
<h2>一、隔夜美股（实时）</h2>
{us_section()}
</div>

<div class="card">
<h2>二、CPI与宏观（实时新闻）</h2>
{points(macro_items)}
</div>

<div class="card">
<h2>三、宏观传导链监控（独立因子·实时）</h2>
{chain_svg()}
{points(japan_items)}
</div>

<div class="card">
<h2>四、地缘政治与原油（事件因子·实时）</h2>
{points(event_items)}
</div>

<div class="card">
<h2>五、ETF资金流向（实时）</h2>
{etf_section()}
</div>

<div class="card">
<h2>六、微博舆情解构（唐史主任 / 投星 · 实时）</h2>
{vs_summary()}
</div>

<div class="card">
<h2>七、共振信号（多源交叉·实时）</h2>
{resonance_section()}
</div>

<div class="card">
<h2>八、7只自选股操作指引（实时诊断）</h2>
{''.join(stock_card(c) for c in WATCHLIST)}
</div>

{focus_section()}

<div class="card">
<h2>九、今日操作策略（实时驱动）</h2>
{strategy_section()}
</div>

<div class="card">
<h2>主要指数（实时）</h2>
{index_section()}
</div>

<div class="disclaimer">
<strong>免责声明</strong>：以上内容基于公开数据、大V观点及量化规则自动生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
</body>
</html>'''

out_dir = BASE_DIR / "reports" / REPORT_TYPE
out_dir.mkdir(parents=True, exist_ok=True)
out = out_dir / f"{REPORT_TYPE}-{TODAY}.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"报告已生成: {out} ({out.stat().st_size // 1024} KB)")

# ---------- 写入数据库 ----------
try:
    from db import StockAgentDB
    dc = cfg.get("database")
    if dc:
        db = StockAgentDB(host=dc.get("host", "localhost"), port=dc.get("port", 5432),
                          user=dc.get("user", "postgres"), password=dc.get("password", ""),
                          dbname=dc.get("dbname", "a_stock_agent"))
        td = datetime.now().date()
        db.save_report(td, REPORT_STATE, "A股操作指引·9章节", html)
        db.save_sentiment_batch(td, weibo_data)
        print("[DB] 舆情数据与报告入库完成")
    else:
        print("[DB] 未配置 database，跳过入库")
except Exception as e:
    print(f"[DB] 入库失败: {e}")
