#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 A股操作指引 9 章节报告（参数化日期与类型），数据全部来自实时快照。

用法: python build_report.py --date 2026-08-17 --type 早报|盘中|晚报|周报

数据来源（全部实时，无任何写死行情/宏观/原油/ETF 数值）：
  - 数据引擎 a_stock_agent.py 采集写入 data/snapshots/fetched_YYYYMMDD_HHMMSS.json
    （微博/大V、宏观新闻、事件因子、日本传导链、技术、A股指数、隔夜美股、ETF资金流）
  - 见顶诊断 stock_diagnosis.run_all(自选股) 或 diagnosis_YYYYMMDD.json
任何数据缺口均渲染为「实时数据缺失」占位，绝不出现假数据。

import 本模块无副作用（不解析 argv / 不联网 / 不写盘）；执行入口为 main()，
仅在 `python build_report.py`（__main__）时运行。
"""
import json
import argparse
from datetime import datetime
from html import escape as _esc
from pathlib import Path

import a_stock_agent as agent
import news_intel as _ni

# 研判文本模板外置到 templates/prompts.py
try:
    from templates.prompts import ConclusionPrompts, StrategyPrompts, WeiboPrompts
except ImportError:
    # 若模板文件缺失，使用本地最小回退类，避免报告生成中断
    class ConclusionPrompts:
        @staticmethod
        def stance(updated_any, consensus_label, idx_label):
            return "多空交织，震荡格局下重结构轻指数"

        @staticmethod
        def verdict_no_update(idx_label, idx_n, up_n, main_line, risk_label):
            return f"综合实时指数：大盘 {idx_label}。主线聚焦「{main_line}」；{risk_label}。"

        @staticmethod
        def verdict_with_consensus(idx_label, idx_n, up_n, consensus_label, consensus_cls,
                                   stance, main_line, risk_label):
            return f"综合实时指数与微博舆情解构：大盘 {idx_label}，大V共识 {consensus_label}。{stance}。"

    class StrategyPrompts:
        STATE_LABEL = {"bullish": "偏多积极", "bearish": "防御为主", "neutral": "中性偏谨慎"}

        @staticmethod
        def risk_line(has_japan_items):
            return "日本传导链有实时预警信号，关注日元/套息平仓。" if has_japan_items else "暂无日本传导链实时预警。"

        @staticmethod
        def avoid_line(avoid_names):
            return "、".join(avoid_names) if avoid_names else "当前自选股诊断无预警及以上风险项。"

    class WeiboPrompts:
        NO_UPDATE_CONSENSUS = "当日大V均未更新微博，无新增舆情可解构。"
        CONSENSUS_PREFIX = "大V整体共识"
        NO_STOCK_MENTION = "当日自选股均无大V点名、主题共振或唐史主线关联，暂无相关舆情。"
        NO_UPDATE_STOCK = "当日无大V更新，自选股无新增舆情信号。"
        NO_UPDATE_KEY = "当日无大V更新，无关键论点可解构。"
        NO_KEY_SIGNAL = "当日大V观点未提取到强多空信号。"
        NO_RISK_SIGNAL = "实时风险因子未提取到明确利空信号。"

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"

_TYPE_LABEL = {"早报": "盘前版", "盘中": "盘中版", "晚报": "盘后版", "周报": "周度回顾"}
_TYPE_STATE = {"早报": "盘前", "盘中": "盘中", "晚报": "盘后", "周报": "周报"}

# 稳定分类元数据（行业归属，非行情数据）：code -> 行业
# 优先从 config.json 的 watchlist_sectors 读取；缺失时回退本表
SECTOR = {
    "688668": "连接器+液冷", "688409": "半导体设备零部件", "600641": "离子注入机",
    "000725": "面板+AI封装", "301392": "PVD设备", "688530": "靶材", "600580": "机器人电机",
    "600498": "光通信",
}


def _load_sector(code):
    """从 config 读取自选股行业标签，config 缺失时回退 SECTOR 表。"""
    sec = (cfg.get("watchlist_sectors") or {}).get(code)
    if sec:
        return sec
    return SECTOR.get(code, "")

def _load_css():
    """加载外部 CSS；缺失时回退极简默认样式，保证报告仍可渲染。"""
    try:
        p = TEMPLATES_DIR / "style.css"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return "*{margin:0;padding:0;box-sizing:border-box}body{font-family:sans-serif;background:#f4f5f7;padding:20px}"


def _load_sentiment_words():
    """加载外部多空词库；缺失时回退空词库（打分恒为0）。"""
    try:
        p = TEMPLATES_DIR / "sentiment_words.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"bull": [], "bear": [], "negation": []}


# 模块级加载；文件缺失时安全回退
_SENTIMENT_WORDS = _load_sentiment_words()
BULL_WORDS = _SENTIMENT_WORDS.get("bull", [])
BEAR_WORDS = _SENTIMENT_WORDS.get("bear", [])
NEG_WORDS = _SENTIMENT_WORDS.get("negation", [])


# 数据缺口统一占位（绝不编造数值）
PLACEHOLDER = '<p class="muted" style="font-size:12px;">实时数据缺失（请先运行 `python a_stock_agent.py` 采集后再生成报告）。</p>'

# ---- 模块级状态：由 load_context() 填充；import 本模块无副作用（不解析 argv / 不联网 / 不写盘） ----
TODAY = datetime.now().strftime("%Y-%m-%d")
DATE8 = TODAY.replace("-", "")
REPORT_TYPE = "早报"
NOW = ""
REPORT_LABEL = _TYPE_LABEL[REPORT_TYPE]
REPORT_STATE = _TYPE_STATE[REPORT_TYPE]
cfg = {}
WATCHLIST = []
VS_NAMES = []
VS_SOURCES = []  # 大V 源配置：name/tier(等级)/description(角色描述)
snap_path = None
snapshot = {}
weibo_data = {}
quotes = {}
us_market = []
etf = []
fund_flows = {}
market_width = {}
market_state = "neutral"
matched_sectors = []
intel = {}
_intel_topics = {}
tangshi = []
touxing_asset = []
touxing_yeye = []
macro_items = []
event_items = []
japan_items = []
tech_items = []
global_items = []
diag_raw = []


def load_context():
    """解析命令行参数，并加载全部实时数据上下文（配置/快照/外网解析/个股诊断）到模块全局。"""
    global TODAY, DATE8, REPORT_TYPE, NOW, REPORT_LABEL, REPORT_STATE
    global cfg, WATCHLIST, VS_NAMES, VS_SOURCES
    global snap_path, snapshot, weibo_data, quotes, us_market, etf, fund_flows, market_state, matched_sectors
    global market_width
    global intel, _intel_topics
    global tangshi, touxing_asset, touxing_yeye
    global macro_items, event_items, japan_items, tech_items, global_items
    global diag_raw

    # ---- 参数：--date 报告日期(YYYY-MM-DD) / --type 报告类型(决定写入目录) ----
    ap = argparse.ArgumentParser(description="生成 A股操作指引 9 章节报告（实时数据）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="报告日期 YYYY-MM-DD")
    ap.add_argument("--type", default="早报", choices=["早报", "盘中", "晚报", "周报"],
                    help="报告类型：早报/盘中/晚报/周报，决定写入 reports/<类型>/ 目录")
    _args = ap.parse_args()
    TODAY = _args.date
    DATE8 = TODAY.replace("-", "")
    REPORT_TYPE = _args.type
    NOW = datetime.now().strftime("%Y-%m-%d %H:%M")
    REPORT_LABEL = _TYPE_LABEL.get(REPORT_TYPE, "盘前版")
    REPORT_STATE = _TYPE_STATE.get(REPORT_TYPE, "盘前")

    # ---- 加载配置（自选股 + 大V 源，避免写死） ----
    cfg = agent.load_config()
    WATCHLIST = cfg.get("watchlist_stocks", []) or []
    VS_NAMES = [s.get("name", "") for s in cfg.get("weibo_sources", [])]
    VS_SOURCES = cfg.get("weibo_sources", []) or []

    # ---- 加载实时快照（数据引擎产出，位于 data/snapshots/，取当日最新一份） ----
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
            snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
            print(f"[读取] 使用快照: {snap_path.name}")
        except Exception as e:
            print(f"[警告] 快照解析失败 {snap_path}: {e}")
    else:
        print(f"[警告] 未找到 {DATE8} 的实时快照，请先运行 `python a_stock_agent.py` 采集数据。报告仅含占位。")

    weibo_data = snapshot.get("weibo_data", {})
    quotes = snapshot.get("quotes", {})
    us_market = snapshot.get("us_market", []) or []
    etf = snapshot.get("etf", []) or []
    fund_flows = snapshot.get("fund_flows") or {}
    market_width = snapshot.get("market_width") or {}
    market_state = snapshot.get("market_state", "neutral")
    matched_sectors = snapshot.get("matched_sectors", []) or []

    # ---- 外网资讯解析（英文源抓取 + 正文解析，Agent 总结为中文结论） ----
    intel = _ni.load_intel(TODAY)
    _intel_topics = intel.get("topics", {}) if isinstance(intel, dict) else {}

    # 实时大V / 各类信源文本
    tangshi = wb_texts("唐史主任司马迁", lookback_days=2)  # T1 周一早报结合周末/昨日观点
    touxing_asset = wb_texts("投星资产")
    touxing_yeye = wb_texts("投星大爷")
    macro_items = [t for k in weibo_data if k.startswith("[宏观]") for t in wb_texts(k)]
    event_items = [t for k in weibo_data if k.startswith("[事件]") for t in wb_texts(k)]
    japan_items = [t for k in weibo_data if k.startswith("[日本]") for t in wb_texts(k)]
    tech_items = [t for k in weibo_data if k.startswith("[技术]") for t in wb_texts(k)]
    global_items = [t for k in weibo_data if k.startswith("[全球]") for t in wb_texts(k)]

    # ---- LLM舆情解构：配置远程LLM时自动刷新；内置能力模式复用已注入缓存 ----
    try:
        import weibo_llm as _wlm
        _watch_names = {code: (cfg.get("watchlist_names") or {}).get(code, code) for code in WATCHLIST}
        _, llm_mode = _wlm.run_for_date(TODAY, weibo_data, cfg, _watch_names)
        print(f"[微博LLM] 解构模式: {llm_mode}")
    except Exception as e:
        print(f"[微博LLM] 解构准备失败，报告将回退规则打分: {e}")

    # ---- 个股诊断（实时；缓存自愈：自选股增减后自动重跑） ----
    diag_raw = []
    diag_path = BASE_DIR / "data" / "diagnosis" / f"diagnosis_{DATE8}.json"

    def _load_cached(path):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            results = raw.get("results", raw) if isinstance(raw, dict) else raw
            meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
            return {"results": results, "meta": meta}
        except Exception as e:
            print(f"[诊断] 读取 {path} 失败: {e}")
            return None

    def _write_cache(path, diag_result):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(diag_result, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[诊断] 写缓存失败: {e}")
            return False

    report_is_today = TODAY == datetime.now().strftime("%Y-%m-%d")
    target_date = None if report_is_today else TODAY

    def _run_diagnosis_via_venv(watchlist, diag_path, cached_results, target):
        """本进程缺 numpy 时，用项目 .venv 子进程跑诊断；历史报告传 target_date。"""
        import subprocess
        venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
        if not venv_py.exists():
            return cached_results or []
        try:
            code = ("import sys,json; sys.path.insert(0,'.'); import stock_diagnosis as sd; "
                    f"r=sd.run_all({watchlist!r}, target_date={target!r}); "
                    "print(json.dumps(r, ensure_ascii=False))")
            out = subprocess.check_output([str(venv_py), "-c", code],
                                         cwd=str(BASE_DIR), text=True, timeout=600)
            # stock_diagnosis 会输出逐股日志，最后一行才是 JSON。
            json_line = next((line for line in reversed(out.splitlines()) if line.lstrip().startswith("{")), "")
            result = json.loads(json_line)
            _write_cache(diag_path, result)
            return result.get("results", [])
        except Exception as e:
            print(f"[诊断] .venv 子进程诊断失败: {e}")
            return cached_results or []

    cached_bundle = _load_cached(diag_path) if diag_path.exists() else None
    cached_results = (cached_bundle or {}).get("results") or []
    cached_meta = (cached_bundle or {}).get("meta") or {}
    cached_codes = {r.get("code") for r in cached_results}
    missing_codes = set(WATCHLIST) - cached_codes
    cache_target = cached_meta.get("target_date") or (cached_results[0].get("target_date") if cached_results else "")
    target_mismatch = bool(cached_results) and cache_target != TODAY
    cache_stale = False
    if report_is_today and cached_results:
        stamp = cached_meta.get("batch_diagnosed_at") or cached_results[0].get("diagnosed_at", "")
        try:
            age_min = (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            ttl_min = int((cfg.get("diagnosis") or {}).get("cache_minutes", 120))
            cache_stale = age_min > ttl_min
        except (TypeError, ValueError):
            cache_stale = True
    need_refresh = not cached_results or bool(missing_codes) or target_mismatch or cache_stale

    if not need_refresh:
        diag_raw = cached_results
        print(f"[诊断] 使用缓存: {diag_path.name}（{len(diag_raw)} 只，target={cache_target}）")
    else:
        reasons = []
        if missing_codes:
            reasons.append(f"缺股 {sorted(missing_codes)}")
        if target_mismatch:
            reasons.append(f"日期不匹配 {cache_target}->{TODAY}")
        if cache_stale:
            reasons.append("同日缓存过期")
        print(f"[诊断] 缓存需刷新（{'；'.join(reasons) or '无可用缓存'}）...")
        try:
            import stock_diagnosis as sd
            import numpy  # 触发 ImportError 以便降级到 .venv
            diag_result = sd.run_all(WATCHLIST, target_date=target_date)
            _write_cache(diag_path, diag_result)
            diag_raw = diag_result.get("results", [])
            print(f"[诊断] 完成并缓存: {diag_result['meta']['succeeded']}/{diag_result['meta']['total']}")
        except Exception as e:
            print(f"[诊断] 本进程不可用（{type(e).__name__}），尝试 .venv 子进程...")
            diag_raw = _run_diagnosis_via_venv(WATCHLIST, diag_path, cached_results, target_date)
            if not diag_raw and cached_results:
                print("[诊断] 刷新失败，降级使用旧缓存")
                diag_raw = cached_results


def _parse_weibo_time(created_at):
    """解析微博 created_at（如 'Mon Aug 17 19:30:00 +0800 2026'）→ date；失败返回 None。"""
    if not created_at:
        return None
    try:
        import email.utils
        import time as _time
        parts = created_at.split()
        if len(parts) == 6:
            dt = email.utils.parsedate_tz(f"{parts[1]} {parts[2]} {parts[4]} {parts[3]} {parts[5]}")
            if dt:
                return datetime.fromtimestamp(email.utils.mktime_tz(dt)).date()
        return None
    except Exception:
        return None


def load_tangshi_deep():
    """读取唐史主任当日深度解读（Agent 研读注入，data/weibo_deep/tangshi_<DATE8>.json）。

    唐史为 T1 大局方向掌控者，其博文不做简单多空打分，而由 Agent 研读近几日观点链后
    解构为结构化深度解读（核心逻辑/方向/主线/回避/操作/风险）。文件缺失返回 None。
    """
    try:
        p = BASE_DIR / "data" / "weibo_deep" / f"tangshi_{DATE8}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def wb_posts(name, lookback_days=1):
    """返回信号源条目；微博源默认限报告当日，唐史主任（T1）可回溯昨日。

    `[宏观]/[事件]/[日本]/[技术]/[全球]/[国家队]` 等条目由本次快照实时生成，部分没有
    created_at，不能套用微博时间过滤；普通微博源仍严格剔除旧帖/置顶帖。
    lookback_days=2 时允许昨日帖子（周一早盘结合周末观点）。
    """
    report_date = datetime.strptime(TODAY, "%Y-%m-%d").date()
    is_snapshot_signal = name.startswith("[")
    out = []
    for p in weibo_data.get(name, []) or []:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        raw_time = p.get("time", "")
        d = _parse_weibo_time(raw_time)
        if is_snapshot_signal and d is None:
            out.append({"text": text, "time": raw_time, "date": report_date})
            continue
        if d is None or (report_date - d).days >= lookback_days or d > report_date:
            continue
        out.append({"text": text, "time": raw_time, "date": d})
    return out


def wb_texts(name, lookback_days=1):
    """某大V当日更新的纯文本列表（供解构打分）。"""
    return [p["text"] for p in wb_posts(name, lookback_days=lookback_days)]


def diag_for(code):
    for d in diag_raw:
        if d.get("code") == code:
            return d
    return None


# peak_detector 风险等级（数值越大越危险）：安全=1 < 蓝/黄/红色预警=2 < 高危=3 < 极度危险=4
_LEVEL_RANK = {"安全": 1, "蓝色预警": 2, "黄色预警": 2, "红色预警": 2, "高危": 3, "极度危险": 4}


def _level_rank(d):
    return _LEVEL_RANK.get((d or {}).get("level", ""), 0)


def action_from_diag(d):
    """根据见顶诊断风险等级返回（动作, 徽章类）。

    颜色细分：安全=红（持有/加仓）；蓝/黄预警=蓝（关注/谨慎）；
             红色预警=橙（减仓）；高危=绿（回避）；极度危险=深绿（强烈回避）。
    """
    if not d:
        return ("观望", "b-gray")
    level = (d or {}).get("level", "")
    if level == "极度危险":
        return ("强烈回避", "b-green")
    if level == "高危":
        return ("回避", "b-green")
    if level == "红色预警":
        return ("减仓", "b-orange")
    if level in ("黄色预警", "蓝色预警"):
        return ("谨慎", "b-blue")
    if level == "安全":
        return ("持有/加仓", "b-red")
    return ("持有", "b-blue")


def watch_name(code):
    d = diag_for(code)
    if d and d.get("name"):
        return d["name"]
    return (cfg.get("watchlist_names") or {}).get(code, code)


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


def _pct_val(x):
    try:
        return float(x[1])
    except (TypeError, ValueError):
        return 0.0


# ================= 个股诊断信号渲染（第八节用） =================
# 信号元素结构：(name, desc, score, sig_type)；sig_type 决定配色与分组
SIG_TYPE_COLOR = {
    '超买': '#d63031', '成交': '#e67e22', '背离': '#c0392b',
    '衰竭': '#8e44ad', '破位': '#d63031', '见底': '#00a865',
}
# 见底为负分（安全缓冲），其余为正分（风险累加）；按风险→缓冲顺序排列
SIG_TYPE_ORDER = ['超买', '成交', '背离', '衰竭', '破位', '见底']


def render_signals(sigs):
    """把个股诊断 signals 渲染为「按类型分组」的彩色信号块（替代原始列表倾倒）。"""
    if not sigs:
        return '<p class="muted" style="font-size:11px;color:#999;">无显著信号</p>'
    groups = {}
    for s in sigs:
        if not (isinstance(s, (list, tuple)) and len(s) >= 4):
            continue
        name, desc, score, st = s[0], s[1], s[2], s[3]
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0
        groups.setdefault(st, []).append((str(name), str(desc), score))
    if not groups:
        return '<p class="muted" style="font-size:11px;color:#999;">无显著信号</p>'
    ordered = [t for t in SIG_TYPE_ORDER if t in groups] + \
              [t for t in groups if t not in SIG_TYPE_ORDER]
    html = '<div class="sig-block">'
    for st in ordered:
        color = SIG_TYPE_COLOR.get(st, '#666')
        lst = sorted(groups[st], key=lambda x: -abs(x[2]))
        chips = ''
        for name, desc, score in lst:
            sign = '+' if score > 0 else ''
            chips += (f'<span class="sig-chip" style="background:{color}14;color:{color};'
                      f'border-color:{color}55;"><b>{name}</b>'
                      f'<span class="sig-desc">{desc}</span>'
                      f'<span class="sig-score">{sign}{score:.1f}</span></span>')
        html += (f'<div class="sig-group">'
                 f'<span class="sig-label" style="color:{color};background:{color}14;'
                 f'border-color:{color}55;">{st} · {len(lst)}项</span>'
                 f'<div class="sig-chips">{chips}</div></div>')
    html += '</div>'
    return html


def position_advice(d):
    """参考仓位区间：市场状态基础仓位 + 诊断 level 修正（真实规则，非预测）。"""
    base = {"bullish": (0.6, 0.8), "neutral": (0.4, 0.6), "bearish": (0.1, 0.3)}.get(market_state, (0.4, 0.6))
    adj = {"安全": 0.10, "黄色预警": 0.0, "高危": -0.10, "见顶确认": -0.15,
           "见底反弹": 0.05, "趋势健康": 0.05}.get(d.get("level", ""), 0.0)
    lo = max(0.0, min(0.85, base[0] + adj))
    hi = max(lo + 0.1, min(0.9, base[1] + adj))
    return f"{int(round(lo * 100))}-{int(round(hi * 100))}%"


def stop_loss_price(d):
    """止损位：min(MA20, 近5日低点) × 0.97（下方 3% 缓冲），基于真实日K。"""
    try:
        import backtest as _bt
        kl = _bt.fetch_kline(d.get("code"))
        if kl and len(kl) >= 5:
            closes = [k["close"] for k in kl]
            ma20 = sum(closes[-20:]) / min(20, len(closes))
            low5 = min(k["low"] for k in kl[-5:])
            return round(min(ma20, low5) * 0.97, 2)
    except Exception:
        pass
    return None


def stock_card(code):
    sector = _load_sector(code)
    d = diag_for(code)
    if d:
        score = fmt(d.get("total_score", "—"))
        level = d.get("level", "—")
        trend = d.get("trend_status", "—")
        action, cls = action_from_diag(d)
        sig = d.get("signals", "")
        if isinstance(sig, list):
            sig_html = render_signals(sig)
        elif sig:
            sig_html = f'<p class="muted" style="font-size:12px;">{sig[:120]}</p>'
        else:
            sig_html = ""
        logic = f"{sector}；诊断：{level}·{trend}。"
        # 当日收盘/最新价 + 涨跌幅（涨红跌绿）
        price = d.get("latest_price")
        chg = (d.get("quote") or {}).get("change_pct")
        chg = chg if isinstance(chg, (int, float)) else None
        if price is not None:
            chg_disp = f"{'+' if chg >= 0 else ''}{chg:.2f}%" if chg is not None else ""
            chg_cls = "up" if (chg or 0) >= 0 else "down"
            price_html = (f'<div class="stock-price">收盘 <b>{fmt(price)}</b> 元　'
                          f'<span class="{chg_cls}">{chg_disp}</span></div>')
        else:
            price_html = ""
    else:
        score, level, trend = "—", "—", "—"
        action, cls = "观望", "b-gray"
        logic = f"{sector}（实时诊断缺失）"
        sig_html = ""
        price_html = ""
    # 参考仓位 + 止损位（真实规则 + 真实日K，诊断缺失则不展示）
    pos_txt = stop_txt = ""
    if d:
        pos_txt = position_advice(d)
        sl = stop_loss_price(d)
        if sl:
            stop_txt = f"｜ 止损位 <b>{sl}</b>元"
    pos_html = f'<p class="stock-meta">参考仓位 {pos_txt}（市场{ {"bullish":"偏多","neutral":"震荡","bearish":"偏空"}.get(market_state,"震荡") } + 诊断修正）{stop_txt}</p>' if pos_txt else ""
    return f'''
    <div class="stock-card">
      <div class="stock-title">
        <div class="name">{watch_name(code)} <span class="muted">{code}</span>　<span class="tag">{sector}</span></div>
        <span class="badge {cls}">{action}</span>
      </div>
      {price_html}
      <p class="stock-logic">{logic}</p>
      {sig_html}
      <p class="stock-meta">见顶诊断：评分 {score} | {level} | {trend}</p>
      {pos_html}
    </div>'''


def chain_svg():
    uy = (snapshot or {}).get("us_yield") or {}
    ty = uy.get("ten_year")
    se = uy.get("short_end")
    se_label = uy.get("short_end_label") or "3M"
    yield_sub = f"10Y {ty:.2f}%" if ty is not None else "巨额美债持仓"
    nodes = [
        ("原油(触发)", "上游地缘/供需"),
        ("日本加息", "输入型通胀→被迫加息"),
        ("抛美债压力", yield_sub),
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


def fima_highlight():
    """临时高亮：FIMA（外国央行美债托管/持有）数据实证卡，置于传导链章节内。

    数据口径（真实可溯源，截至 2026-08-31）：
    - 外国官方机构 2026 年内净卖出约 1000 亿美元美债（去年同期约 +1000 亿，方向一年逆转约 2000 亿，截至 6 月底）
    - 外国政府持有美债占比降至约 12%（2008 危机前后约 40%）
    - 日本仍为最大外国持有人 1.117 万亿美元；英国约 0.94 万亿；中国约 0.633 万亿（较峰值 1.3 万亿近乎腰斩）
    - 8/31 美股「股债汇三杀」收官：10Y 美债 4.76%+ 创 2025/1 以来新高，美元指数 -0.26%，布油破 90 美元
    """
    stats = [
        ("外国央行年内净卖美债", "≈ -1000 亿美元", "b-green", "去年同期为 +1000 亿买家，一年内方向逆转约 2000 亿"),
        ("外国政府持有美债占比", "≈ 12%", "b-green", "2008 危机前后约 40%，美债需求结构剧变"),
        ("日本持有美债（最大持有人）", "1.117 万亿美元", "b-orange", "日本 9 月加息若触发套息平仓，抛售压力沿日元→美债→FIMA 链放大"),
        ("中国持有美债", "≈ 6330 亿美元", "b-orange", "自峰值 1.3 万亿近乎腰斩，十年持续减持"),
        ("8/31 美债 10Y 收益率", "4.76%+", "b-red", "创 2025 年 1 月以来新高，长端利率上行压制全球成长股估值"),
    ]
    rows = ""
    for name, val, cls, note in stats:
        rows += (f'<tr><td><b>{name}</b></td><td><b class="{cls}" style="font-size:14px;">{val}</b></td>'
                 f'<td style="font-size:12px;color:#666;">{note}</td></tr>')
    verdict = (
        '<div style="margin-top:10px;padding:8px 12px;background:#fff8e6;border-left:3px solid #d97706;'
        'border-radius:4px;font-size:13px;color:#7c4a03;">'
        '<b>研判：</b>外国官方资金从「美债的稳定买家」转为「净卖方」，叠加日本 9 月加息在即，'
        'FIMA 托管流出 → 美债长端利率上行 → 全球估值锚上移，是 A 股成长（科创/半导体/光模块）'
        '短期承压的核心外部链路；8/31 美股股债汇三杀已先行走出一轮预演。'
        '</div>'
    )
    return (
        f'<div class="card" id="sec-fima" style="margin-top:12px;background:linear-gradient(180deg,#fffdf5,#fff8e6);'
        f'border:1px solid #eab308;box-shadow:0 2px 8px rgba(234,179,8,.15);">'
        f'<h2 style="color:#92400e;">★ FIMA 高亮 · 外国央行美债托管（截至 2026-08-31）</h2>'
        f'<table style="margin-top:4px;"><thead><tr><th>指标</th><th>最新值</th><th>解读</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        f'{verdict}'
        f'<p class="muted" style="font-size:11px;margin-top:8px;">口径说明：FIMA = 外国官方及国际货币当局（纽约联储托管）；'
        f'净卖数据截至 2026-06 底，10Y 美债为 8/31 收盘。临时高亮模块，可随时移除。</p>'
        f'</div>'
    )


def us_yield_panel():
    """美债收益率最新可用面板（传导链核心锚）：10Y + 短端，附科技估值研判。"""
    uy = (snapshot or {}).get("us_yield") or {}
    ty = uy.get("ten_year")
    se = uy.get("short_end")
    se_label = uy.get("short_end_label") or "2Y"
    data_date = uy.get("data_date") or "时点缺失"
    source = uy.get("source") or "数据源未记录"
    if ty is None and se is None:
        return PLACEHOLDER
    # 科技估值研判（10Y 为锚）
    if ty is not None:
        if ty >= 4.5:
            lvl, judge = "b-red", "高位，显著压制成长股估值（尤其科创/创业板高估值）"
        elif ty >= 4.0:
            lvl, judge = "b-orange", "仍处高位，对科技估值偏压制"
        else:
            lvl, judge = "b-blue", "回落至 4% 下方，科技估值压制缓解"
    else:
        lvl, judge = "b-blue", "10Y 数据缺失，短端参考"
    ty_html = f'<tr><td><b>美债 10Y 收益率</b></td><td><b>{ty:.2f}%</b></td><td><span class="badge {lvl}">科技估值锚</span></td><td>{judge}</td></tr>' if ty is not None else ""
    se_html = f'<tr><td>美债 {se_label} 收益率</td><td><b>{se:.2f}%</b></td><td><span class="badge b-blue">短端利率</span></td><td>收益率曲线短端，套息资金成本参考</td></tr>' if se is not None else ""
    return (
        '<table><thead><tr><th>指标</th><th>最新可用值</th><th>属性</th><th>对 A 股影响</th></tr></thead>'
        f'<tbody>{ty_html}{se_html}</tbody></table>'
        f'<p class="muted" style="font-size:11px;margin-top:6px;">数据时点：{data_date}；{source}日度收益率曲线。</p>'
    )


def us_section():
    if not us_market:
        return PLACEHOLDER
    # 汇总行：三大指数方向徽章（不含点位）+ 涨跌家数 + 领涨/领跌标的（一眼读出美股格局）
    up_n = sum(1 for x in us_market if _pct_val(x) > 0)
    sum_parts = []
    for x in us_market[:3]:
        pf = _pct_val(x)
        cls = "b-red" if pf > 0 else ("b-green" if pf < 0 else "b-gray")
        short = str(x[0]).replace("纳斯达克", "纳指").replace("标普500", "标普")
        sum_parts.append(f'<span class="badge {cls}">{_esc(short)} {sign(pf)}{pf}%</span>')
    sum_parts.append(f'<span class="tag">上涨 {up_n}/{len(us_market)}</span>')
    _valid = [x for x in us_market if _pct_val(x) != 0]
    if _valid:
        best = max(_valid, key=_pct_val)
        worst = min(_valid, key=_pct_val)
        sum_parts.append(f'<span class="tag">领涨 {_esc(str(best[0]))} {sign(_pct_val(best))}{_pct_val(best)}%</span>')
        sum_parts.append(f'<span class="tag">领跌 {_esc(str(worst[0]))} {sign(_pct_val(worst))}{_pct_val(worst)}%</span>')
    summary = f'<div class="us-sum">{"".join(sum_parts)}</div>'
    bars = "".join(us_bar(n, p, v, s) for n, p, v, s in us_market)
    return f'<div style="margin:2px 0">{summary}{bars}</div>'


def _etf_history_chart():
    """近5日 ETF 主力净流入分组柱状图（数据来自 PostgreSQL etf_flows 历史，纯 CSS 渲染）。

    返回空串表示数据库不可用或无历史数据（不影响当日表格渲染）。
    """
    try:
        from db import StockAgentDB
        dc = (cfg or {}).get("database", {})
        db = StockAgentDB(host=dc.get("host", "localhost"), port=dc.get("port", 5432),
                          user=dc.get("user", "postgres"), password=dc.get("password", ""),
                          dbname=dc.get("dbname", "a_stock_agent"))
        hist = db.get_etf_flows_history(days=5)
    except Exception:
        return ""
    if not hist:
        return ""
    dates = list(hist.keys())
    names = list(dict.fromkeys(n for day in hist.values() for n in day))
    max_abs = max((abs(v) for day in hist.values() for v in day.values()), default=1) or 1
    cols = ""
    for d in dates:
        cells = ""
        for n in names:
            v = hist[d].get(n)
            if v is None:
                cells += '<div class="ef-bar-cell"><div class="ef-bar-empty"></div></div>'
                continue
            h = max(4.0, abs(v) / max_abs * 100)
            color = "#d63031" if v > 0 else "#00a865"
            cells += (f'<div class="ef-bar-cell" title="{_esc(n)} {v:+.2f}亿元">'
                      f'<div class="ef-bar" style="height:{h:.1f}%;background:{color}"></div>'
                      f'<span class="ef-bar-val">{v:+.1f}</span></div>')
        cols += f'<div class="ef-col"><div class="ef-date">{_esc(d[5:])}</div><div class="ef-bars">{cells}</div></div>'
    legend = " · ".join(_esc(n) for n in names)
    return (f'<div class="ef-chart-wrap"><div class="ef-title">近5日 ETF 主力净流入（亿元）· 数据库历史</div>'
            f'<div class="ef-chart">{cols}</div>'
            f'<div class="ef-legend"><span class="ef-leg-dot ef-in"></span>净流入&nbsp;&nbsp;'
            f'<span class="ef-leg-dot ef-out"></span>净流出&nbsp;&nbsp;|&nbsp;&nbsp;{legend}</div></div>')


def etf_section():
    if not etf:
        return PLACEHOLDER
    rows = "".join(
        f'<tr><td>{e[0]}</td><td>{e[1]}</td><td><span class="badge {e[3]}">{e[2]}</span></td><td>{e[4]}</td></tr>'
        for e in etf)
    chart = _etf_history_chart()
    return f'<table><thead><tr><th>ETF</th><th>代码</th><th>方向</th><th>信号</th></tr></thead><tbody>{rows}</tbody></table>{chart}'


def _stock_hit_rate_map():
    """自选股个股维度的回测方向命中率（真实数据：seeds/backtest_results.json）。

    仅统计 status=done 且方向为 bullish/bearish 且 direction_hit 已判定的判断
    （neutral 不计，与 resonance_hit_rate 口径一致）。返回 {code: {rate, hit, total}}。
    """
    try:
        import json as _json
        p = Path(__file__).parent / "seeds" / "backtest_results.json"
        if not p.exists():
            return {}
        data = _json.loads(p.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", [])
        agg = {}
        for it in items:
            if it.get("status") != "done" or it.get("direction") not in ("bullish", "bearish"):
                continue
            hit = it.get("direction_hit")
            if hit is None:
                continue  # 未判定（窗口未走完等）不统计
            a = agg.setdefault(it.get("stock_code"), {"hit": 0, "total": 0})
            a["total"] += 1
            if hit:
                a["hit"] += 1
        return {c: {"rate": round(a["hit"] / a["total"] * 100), "hit": a["hit"], "total": a["total"]}
                for c, a in agg.items() if a["total"]}
    except Exception:
        return {}


def fund_section():
    """资金面信号（自选股两融 + 北向持仓，合并一张表）。

    数据来自 agent 经 westock 连接器取的真实数据（data_fund_margin 日频、
    data_north_holding 季频），写入 westock_fund_override.json 由快照带入；
    另附个股维度回测历史胜率（seeds/backtest_results.json，真实收盘验证）；
    缺失渲染占位，绝不伪造。
    """
    if not fund_flows:
        return ""
    margin = {m.get("code"): m for m in (fund_flows.get("margin") or [])}
    north = {n.get("code"): n for n in (fund_flows.get("north_holding") or [])}
    data_date = fund_flows.get("data_date", "")
    codes = sorted(margin.keys() or north.keys(),
                   key=lambda c: -(margin.get(c, {}).get("balance") or 0))
    if not codes:
        return ""
    hit_map = _stock_hit_rate_map()
    rows = ""
    for c in codes:
        m = margin.get(c, {})
        n = north.get(c, {})
        name = m.get("name") or n.get("name") or c
        # 两融
        if m:
            m_bal = f'{m.get("balance", 0) / 1e8:.1f}亿'
            m_dod = m.get("dod_pct", 0) or 0
            m_txt = f'<td class="{up_down(m_dod)}">{"+" if m_dod > 0 else ""}{m_dod}%</td>'
        else:
            m_bal, m_txt = "—", '<td class="muted">—</td>'
        # 北向
        ratio = n.get("ratio")
        cap = n.get("cap")
        chg = n.get("shares_chg_q")
        if ratio is not None and cap is not None:
            n_ratio = f'{ratio:.2f}%'
            n_cap = f'{cap / 1e8:.1f}亿'
            chg = chg or 0
            n_chg = f'<td class="{up_down(chg)}">{"+" if chg > 0 else ""}{chg / 1e4:.0f}万股</td>'
        else:
            n_ratio = n_cap = "—"
            n_chg = '<td class="muted">—</td>'
        # 回测历史胜率（个股维度；code 归一化为 6 位数字匹配，无已兑现判断则显示 —）
        h = hit_map.get(str(c)[-6:])
        if h:
            hit_html = (f'<span class="badge {rate_badge(h["rate"])}" '
                        f'title="方向判断命中 {h["hit"]} / 共 {h["total"]} 条（回测收盘验证）">{h["rate"]}%</span>'
                        f'<span class="muted" style="font-size:10.5px">（{h["hit"]}/{h["total"]}）</span>')
        else:
            hit_html = '<span class="muted">—</span>'
        rows += (f'<tr><td><b>{_esc(name)}</b></td>'
                 f'<td>{m_bal}</td>{m_txt}'
                 f'<td>{n_ratio}</td><td>{n_cap}</td>{n_chg}'
                 f'<td>{hit_html}</td></tr>')
    n_hit = len(hit_map)
    hit_note = (f'历史胜率=该股历次报告方向判断经回测收盘验证的命中率（命中/共判定条数，越高越可信）；'
                f'样本为 {sum(h["total"] for h in hit_map.values())} 条已兑现判断（{n_hit} 只个股有记录）。'
                if hit_map else '历史胜率暂无已兑现判断样本。')
    return (f'<div style="margin-top:12px;border-top:1px dashed #e0e3e8;padding-top:6px">'
            f'<h3 style="font-size:13px;margin:6px 0 4px">自选股资金面（两融日频 + 北向季频）</h3>'
            f'<table><thead><tr><th>个股</th><th>融资余额</th><th>融资较前日</th>'
            f'<th>北向持股比例</th><th>北向持股市值</th><th>北向季度增减</th><th>回测历史胜率</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<p class="muted" style="font-size:11px;margin-top:4px">融资余额日频（{_esc(str(data_date)[:10])}）；'
            f'北向每日资金流自 2024-08 起不再实时公开，此处为最新季度披露（Q2 2026-06-30，真实数据）；'
            f'{hit_note}"—"表示无数据。</p></div>')


def _adl_line_chart(points):
    """ADL 累计线图（SVG 折线，无第三方库）。

    points: [{d: 'MM-DD', v: 当日净差, adl: 累计值}] 升序。
    折线=ADL 累计；圆点按当日净差正红负绿；Y 轴按数据 min/max 自适应，含 0 轴虚线。
    """
    if len(points) < 2:
        return ""
    W, H, pad_l, pad_r, pad_t, pad_b = 640, 150, 34, 14, 18, 22
    vals = [p["adl"] for p in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    lo -= span * 0.15
    hi += span * 0.15
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    def X(i):
        return pad_l + (plot_w * i / (len(points) - 1))
    def Y(v):
        return pad_t + plot_h * (hi - v) / (hi - lo)
    # 网格 + 0 轴
    grids = ""
    for gy in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = pad_t + plot_h * gy
        val = hi - gy * (hi - lo)
        grids += f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W-pad_r}" y2="{y:.1f}" stroke="#eef0f4" stroke-width="1"/>'
        grids += f'<text x="{pad_l-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="#9aa3b2">{val:+,.0f}</text>'
    y0 = Y(0)
    zero_line = f'<line x1="{pad_l}" y1="{y0:.1f}" x2="{W-pad_r}" y2="{y0:.1f}" stroke="#c3cad6" stroke-width="1" stroke-dasharray="4,3"/>'
    # 折线 + 点
    coords = [(X(i), Y(p["adl"])) for i, p in enumerate(points)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    segs = ""
    for i in range(len(points) - 1):
        c = "#d63031" if points[i+1]["adl"] >= points[i]["adl"] else "#00a865"
        segs += f'<line x1="{coords[i][0]:.1f}" y1="{coords[i][1]:.1f}" x2="{coords[i+1][0]:.1f}" y2="{coords[i+1][1]:.1f}" stroke="{c}" stroke-width="2.2" stroke-linecap="round"/>'
    dots = ""
    for i, (x, y) in enumerate(coords):
        c = "#d63031" if points[i]["v"] >= 0 else "#00a865"
        dots += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#fff" stroke="{c}" stroke-width="2">'
                 f'<title>{points[i]["d"]} 净差{points[i]["v"]:+d} ADL={points[i]["adl"]:+d}</title></circle>')
        if i in (0, len(points) - 1):
            dots += (f'<text x="{x:.1f}" y="{y-7:.1f}" text-anchor="middle" font-size="9" font-weight="600" fill="#4a5568">'
                     f'{points[i]["adl"]:+,d}</text>')
    # X 轴日期标签（隔一个显示避免拥挤）
    labels = "".join(
        f'<text x="{x:.1f}" y="{H-6}" text-anchor="middle" font-size="9" fill="#9aa3b2">{p["d"]}</text>'
        for i, (x, p) in enumerate([(coords[i][0], points[i]) for i in range(len(points))])
        if i % 2 == 0 or i == len(points) - 1)
    return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto;display:block">'
            f'{grids}{zero_line}<polyline points="{poly}" fill="none" stroke="none"/>'
            f'{segs}{dots}{labels}</svg>')


def market_width_section():
    """市场宽度与腾落指数（ADL）。

    当日涨跌家数来自东财 getTopicZDFenBu（真实数据，快照 market_width 字段）；
    ADL 累计 = Σ(上涨家数 - 下跌家数)，从数据库 market_width 表首个记录日起累计
    （2026-08-27 起采集入库；历史不可得，绝不伪造）。返回 "" 表示无数据。
    """
    if not market_width:
        return ""
    up = market_width.get("up", 0)
    down = market_width.get("down", 0)
    flat = market_width.get("flat", 0)
    zt = market_width.get("zt", 0)
    dt = market_width.get("dt", 0)
    total = up + down + flat or 1
    up_ratio = up / total * 100
    # ADL 历史（DB 积累）
    adl_hist = []
    adl = 0
    try:
        import db as _db
        dc = (cfg or {}).get("database", {})
        db = _db.StockAgentDB(host=dc.get("host", "localhost"), port=dc.get("port", 5432),
                              user=dc.get("user", "postgres"), password=dc.get("password", ""),
                              dbname=dc.get("dbname", "a_stock_agent"))
        hist = db.get_market_width(days=15)
        adl = 0
        for r in hist:
            adl += (r["up"] or 0) - (r["down"] or 0)
            adl_hist.append({"d": str(r["width_date"])[5:], "v": (r["up"] or 0) - (r["down"] or 0), "adl": adl})
    except Exception:
        pass
    # 今日净差
    net_today = up - down
    # ADL 线图（近 10 个交易日累计曲线）
    chart = _adl_line_chart(adl_hist[-10:]) if adl_hist else ""
    if not chart:
        return ""
    width_badge = "偏多" if up_ratio >= 55 else ("偏空" if up_ratio <= 45 else "中性")
    wcolor = "#d63031" if up_ratio >= 55 else ("#00a865" if up_ratio <= 45 else "#1967d2")
    wcls = "b-red" if up_ratio >= 55 else ("b-green" if up_ratio <= 45 else "b-blue")
    adl_txt = f"{adl:+d}" if adl_hist else f"{net_today:+d}"
    return (f'<div style="margin-top:12px;border-top:1px dashed #e0e3e8;padding-top:6px">'
            f'<h3 style="font-size:13px;margin:6px 0 4px">市场宽度与腾落指数（实时 · 全市场）</h3>'
            f'<p style="font-size:12.5px;margin:4px 0">上涨 <b class="up">{up}</b> / 下跌 <b class="down">{down}</b>'
            f' / 平 {flat} 家 ｜ 涨停 {zt} / 跌停 {dt} ｜ 上涨占比 {up_ratio:.0f}%'
            f' <span class="badge {wcls}">{width_badge}</span></p>'
            f'<p style="font-size:12.5px;margin:4px 0">腾落指数(ADL) <b style="color:{wcolor}">{adl_txt}</b>'
            f' <span class="muted" style="font-size:11px">（Σ上涨-下跌，近{len(adl_hist)}个交易日累计；'
            f'基线 8/14-8/26 westock 全市场统计 + 每日入库积累，真实数据）</span></p>'
            f'<div style="margin-top:6px">{chart}</div>'
            f'<p class="muted" style="font-size:11px;margin:4px 0 0">'
            f'线=ADL 累计（上涨家数-下跌家数逐日累加），红点=当日上涨家数占优、绿点=下跌占优；'
            f'ADL 持续回升表示市场宽度修复。</p></div>')


_INTEL_DIR_CLS = {"偏多": "b-red", "偏空": "b-green", "中性": "b-blue", "利多": "b-red", "利空": "b-green"}


def _intel_structured_html(ss, summary):
    """把 summary_structured 渲染为结构化卡片：方向/置信度 → 核心结论 → 传导 → 已定价 → 关注点。

    置信度与方向仅展示已解析事实，绝不推断补齐；字段缺失的块整体跳过。
    """
    core = str(ss.get("core_conclusion") or "").strip() or summary
    parts = []
    if not core:
        return ""
    # 头部：方向徽章 + 置信度 + 时点
    head = '<div class="in-head">'
    d_label = str(ss.get("direction") or "").strip()
    if d_label:
        head += f'<span class="badge {_INTEL_DIR_CLS.get(d_label, "b-blue")}">{_esc(d_label)}</span>'
    conf = ss.get("confidence")
    if isinstance(conf, (int, float)) and 0 <= conf <= 1:
        head += f'<span class="muted" style="font-size:11.5px">置信度 {float(conf):.2f}</span>'
    as_of = str(ss.get("as_of") or "").strip()
    if as_of:
        head += f'<span class="muted" style="font-size:11.5px">截至 {as_of}</span>'
    head += "</div>"
    parts.append(head)
    # 核心结论
    parts.append(f'<div class="in-core">{_esc(core)}</div>')
    # 传导路径
    trans = [str(x).strip() for x in (ss.get("transmission") or []) if str(x).strip()]
    if trans:
        lis = "".join(f"<li>{_esc(t)}</li>" for t in trans)
        parts.append(f'<div class="in-sec"><span class="in-k">传导</span>对 A 股传导路径<ul>{lis}</ul></div>')
    # 是否已定价
    priced = str(ss.get("priced_in") or "").strip()
    if priced:
        parts.append(f'<div class="in-sec"><span class="in-k">已定价</span>{_esc(priced)}</div>')
    # 关注点
    watch = [str(x).strip() for x in (ss.get("watch") or []) if str(x).strip()]
    if watch:
        chips = "".join(f'<span class="in-chip">{_esc(w)}</span>' for w in watch[:6])
        parts.append(f'<div class="in-sec"><span class="in-k">关注</span><span class="in-watch">{chips}</span></div>')
    return '<div class="in-struct">' + "".join(parts) + "</div>"


def intel_block(key):
    """外网资讯解析块：优先渲染 summary_structured 结构化卡片（方向/传导/已定价/关注点），
    缺失时回退扁平 summary_zh 段落（已解析英文正文后总结，不展示英文原文）。

    数据来自 news_intel.py（英文源抓取 + 正文解析），绝不编造；缺口渲染占位。
    """
    t = _intel_topics.get(key)
    if not t:
        return ('<div class="intel-wrap"><p class="muted" style="font-size:12px;">'
                '外网资讯解析缺失（请先运行 `python news_intel.py` 抓取英文源并总结）。</p></div>')
    summary = t.get("summary_zh", "").strip()
    ss = t.get("summary_structured")
    if isinstance(ss, dict) and (ss.get("core_conclusion") or summary):
        body = _intel_structured_html(ss, summary)
        if body:
            return f'<div class="intel-wrap">{body}</div>'
    if summary:
        body = f'<div class="intel-summary">{_esc(summary)}</div>'
    elif t.get("raw"):
        body = '<p class="muted" style="font-size:12px;">外网资讯已抓取解析，中文结论待生成。</p>'
    else:
        return ('<div class="intel-wrap"><p class="muted" style="font-size:12px;">'
                '外网资讯解析缺失（请先运行 `python news_intel.py` 抓取英文源并总结）。</p></div>')
    return f'<div class="intel-wrap">{body}</div>'


def judgment_hit_rate(days=5, window=3):
    """近 days 个报告日的自选股方向判断兑现率（真实数据：daily_klines 收盘价）。

    方向命中口径与回测一致：bullish 且 window 日后收盘涨 → 命中；bearish 且跌 → 命中。
    仅统计窗口内行情已到期的判断；无判断/库不可用返回 None。
    """
    try:
        import backtest as _bt
        judgments = _bt.load_judgments(None)
        if not judgments:
            return None
        dates = sorted({j["report_date"] for j in judgments})[-days:]
        hit = total = 0
        for j in judgments:
            if j["report_date"] not in dates:
                continue
            if j.get("direction") not in ("bullish", "bearish"):
                continue
            kl = _bt.fetch_kline(j["stock_code"])
            if not kl:
                continue
            idx = _bt.idx_of_date(kl, j["report_date"])
            if idx < 0 or idx + window >= len(kl):
                continue  # 行情未到期，不统计
            entry = kl[idx]["close"]
            exit_c = kl[idx + window]["close"]
            ret = (exit_c / entry - 1) * 100
            hit_ok = ret > 0 if j["direction"] == "bullish" else ret < 0
            total += 1
            hit += 1 if hit_ok else 0
        if not total:
            return None
        return {"rate": round(hit / total * 100), "hit": hit, "total": total,
                "days": days, "window": window}
    except Exception:
        return None


def conclusion_grid():
    state_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    sectors = "、".join(matched_sectors) or "—"
    up_us = sum(1 for x in us_market if _pct_val(x) > 0)
    us_txt = f"{up_us}/{len(us_market)} 上涨" if us_market else "实时数据缺失"
    risk_txt = f"日本加息/套息平仓预警（实时信号 {len(japan_items)} 条）" if japan_items else "暂无日本传导链实时预警"
    items = [
        ("市场状态", state_label, "#1967d2"),
        ("主线板块", sectors, "#d63031"),
        ("隔夜美股", us_txt, "#e67e22"),
        ("风险", risk_txt, "#d63031" if japan_items else "#00a865"),
    ]
    # 市场宽度（真实全市场涨跌家数；缺失则不占格）
    up_n = market_width.get("up")
    down_n = market_width.get("down")
    if isinstance(up_n, (int, float)) and isinstance(down_n, (int, float)) and (up_n + down_n) > 0:
        up_ratio = up_n / (up_n + down_n) * 100
        zt, dt = market_width.get("zt"), market_width.get("dt")
        width_txt = f"{up_ratio:.0f}% 家上涨"
        if isinstance(zt, int) and isinstance(dt, int):
            width_txt += f"（涨停 {zt} / 跌停 {dt}）"
        w_color = "#d63031" if up_ratio >= 55 else ("#00a865" if up_ratio <= 45 else "#1967d2")
        items.append(("市场宽度", width_txt, w_color))
    # 近5交易日判断兑现率（3日窗口，真实收盘验证；库不可用则不展示）
    _hit = judgment_hit_rate()
    if _hit:
        _hr = _hit["rate"]
        _cls_color = "#00a865" if _hr >= 60 else ("#1967d2" if _hr >= 40 else "#d63031")
        items.append(("判断兑现率(3日)", f"{_hr}%（{_hit['hit']}/{_hit['total']}）", _cls_color))
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
    hrate = resonance_hit_rate()
    if hrate:
        rows += (f'<tr><td><b>历史胜率</b></td><td><span class="badge {rate_badge(hrate["rate"])}">{hrate["rate"]}%</span></td>'
                 f'<td>历史方向命中率：看多 {hrate["bull"]["rate"]}%（命中 {hrate["bull"]["hit"]} / 共 {hrate["bull"]["total"]} 条）'
                 f' · 看空 {hrate["bear"]["rate"]}%（命中 {hrate["bear"]["hit"]} / 共 {hrate["bear"]["total"]} 条）'
                 f'——共 {hrate["total"]} 条已兑现判断（真实收盘验证）</td></tr>')
    if not rows:
        return PLACEHOLDER
    return f'<table><thead><tr><th>方向</th><th>共振</th><th>来源交叉（实时）</th></tr></thead><tbody>{rows}</tbody></table>'


def resonance_hit_rate():
    """共振方向的历史兑现率（真实数据：seeds/backtest_results.json，方向命中由收盘验证）。

    仅统计 status=done 且有方向（bullish/bearish）的判断；neutral 不计方向命中。
    返回 {rate, hit, total, bull: {...}, bear: {...}}；无数据/异常返回 None。
    """
    try:
        import json as _json
        p = Path(__file__).parent / "seeds" / "backtest_results.json"
        if not p.exists():
            return None
        data = _json.loads(p.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", [])
        done = [it for it in items if it.get("status") == "done"]
        agg = {"bull": {"hit": 0, "total": 0}, "bear": {"hit": 0, "total": 0}}
        for it in done:
            d = it.get("direction")
            if d not in ("bullish", "bearish"):
                continue
            hit = it.get("direction_hit")
            if hit is None:
                continue  # 未判定（如方向中性窗口未走完）不统计
            agg["bull" if d == "bullish" else "bear"]["total"] += 1
            if hit:
                agg["bull" if d == "bullish" else "bear"]["hit"] += 1
        total = agg["bull"]["total"] + agg["bear"]["total"]
        if not total:
            return None
        hit = agg["bull"]["hit"] + agg["bear"]["hit"]
        for k in ("bull", "bear"):
            t = agg[k]["total"]
            agg[k]["rate"] = round(agg[k]["hit"] / t * 100) if t else 0
        return {"rate": round(hit / total * 100), "hit": hit, "total": total,
                "bull": agg["bull"], "bear": agg["bear"]}
    except Exception:
        return None


def rate_badge(rate):
    """兑现率徽章：>=60% 绿 / 40-60% 蓝 / <40% 红（数字越高越可信）。"""
    if rate >= 60:
        return "b-green"
    if rate >= 40:
        return "b-blue"
    return "b-red"


def concentration_warning():
    """自选股行业集中度提示：按行业归类，单一产业链占比过高时给出风险提示。

    基于 config.watchlist_sectors 的真实行业标签，只做归类统计与提示，不伪造。
    """
    try:
        groups = {}
        for code in WATCHLIST:
            sec = _load_sector(code) or "未分类"
            groups.setdefault(sec, []).append(code)
        total = len(WATCHLIST)
        if not total:
            return ""
        # 产业链归类：半导体设备/材料链（设备/零部件/离子注入/PVD/靶材）+ 算力/AI链 + 其他
        chain_map = {
            "半导体设备零部件": "半导体设备/材料",
            "离子注入机": "半导体设备/材料",
            "PVD设备": "半导体设备/材料",
            "靶材": "半导体设备/材料",
            "连接器+液冷": "算力/AI",
            "面板+AI封装": "算力/AI",
            "光通信": "算力/AI",
            "机器人电机": "机器人",
        }
        chain_count = {}
        for code in WATCHLIST:
            sec = _load_sector(code) or "未分类"
            chain = chain_map.get(sec, "其他")
            chain_count[chain] = chain_count.get(chain, 0) + 1
        top_chain = max(chain_count.items(), key=lambda x: x[1]) if chain_count else ("", 0)
        name, n = top_chain
        ratio = n / total * 100
        if ratio < 50:
            return ""  # 集中度不突出，不打扰
        detail = "、".join(f"{k}×{v}" for k, v in sorted(chain_count.items(), key=lambda x: -x[1]))
        warn_color = "#d63031" if ratio >= 62.5 else "#e67e22"
        return (f'<div class="alert-orange" style="margin:6px 0 10px;font-size:12.5px">'
                f'<b style="color:{warn_color}">组合集中度提示：</b>{name}链占自选股 {n}/{total}（{ratio:.0f}%），'
                f'行业 beta 高度相关（{detail}）——同涨同跌风险高，建议跨链分散配置以平滑组合波动。</div>')
    except Exception:
        return ""


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
        beichi_html = (f'<tr><td><b>背驰</b></td><td><span class="badge {bcls}">'
                       f'{bdir}段力度衰减（{beichi["level"]}）</span></td>'
                       f'<td>进入段 {beichi["enter_power"]} → 离开段 {beichi["leave_power"]}（力度不足90%判背驰）</td></tr>')
    zs_html = ""
    if zs:
        zs_html = (f'<tr><td><b>最近中枢</b></td><td><span class="badge b-blue">'
                   f'[{zs["zd"]:.2f}, {zs["zg"]:.2f}]</span></td>'
                   f'<td>区间 {zs["range"]:.2f} 点（{zs["start_time"]} 确认，GG {zs["gg"]:.2f} / DD {zs["dd"]:.2f}）</td></tr>')
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
    return f'''
      <div class="chan-lv-head">▸ {lv.get("level", "?")}级别<span class="muted" style="font-weight:400;font-size:11px">（{horizon}视界 ｜ 数据 {lv.get("data_range", "")}{fx_txt} ｜ 笔 {lv.get("bis", 0)} ｜ 最新价 <b>{lv.get("last_price", 0):.2f}</b>）</span></div>
      <table><thead><tr><th style="width:18%">维度</th><th style="width:26%">状态</th><th>说明</th></tr></thead><tbody>
        <tr><td><b>当前位置</b></td><td><span class="badge {pos_cls}">{pos}</span></td><td>相对最近中枢的位置</td></tr>
        {zs_html}
        <tr><td><b>缠论信号</b></td><td><span class="badge {sig_cls}">{sig_label}</span></td><td>{_esc(sig.get("text", ""))}</td></tr>
        {beichi_html}
        {bis_html}
        {ubi_html}
        <tr><td><b>操作含义</b></td><td colspan="2">{op}</td></tr>
      </tbody></table>'''


def _chan_stance(sig_label):
    """信号 → 多/空/中性（多级别共振研判用）。"""
    s = str(sig_label or "")
    if "买" in s:
        return "偏多"
    if "卖" in s:
        return "偏空"
    return "震荡"


def chan_section():
    """缠论推演（上证指数 30分钟+日线 多级别 · 操作指引）。

    读 data/chan/chan_forecast_<DATE8>.json（chan_analysis.py 生成，czsc 引擎，真实K线）。
    levels[] 每级一表；高级别（日线）定方向、低级别（30分钟）找买卖点，底部给共振/分歧研判。
    缺文件/解析失败返回空，不伪造。
    """
    p = BASE_DIR / "data" / "chan" / f"chan_forecast_{DATE8}.json"
    if not p.exists():
        return ""
    try:
        import json as _json
        c = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not c or c.get("error"):
        return ""
    # 新契约：levels 列表；兼容旧扁平契约（单级别）
    levels = c.get("levels")
    if not levels and c.get("signal"):
        levels = [c]
    if not levels:
        return ""
    ok_levels = [lv for lv in levels if not lv.get("error")]
    if not ok_levels:
        return ""
    engine = str(c.get("engine") or ok_levels[0].get("engine") or "stdlib")
    engine_note = ("分型/笔/中枢由开源库 czsc（Rust 核心）识别；背驰与买卖点为适配层口径，判定有主观性，不构成精确预测"
                   if engine.startswith("czsc") else
                   "缠论风格简化实现（czsc 未安装，标准库兜底）；背驰与买卖点判定有主观性，不构成精确预测")
    lv_labels = "+".join(str(lv.get("level", "?")) for lv in ok_levels)
    blocks = "".join(_chan_level_block(lv) for lv in ok_levels)
    # 多级别共振/分歧研判（>=2 个有效级别才给结论）
    synth_html = ""
    if len(ok_levels) >= 2:
        stances = [(str(lv.get("level", "?")), _chan_stance((lv.get("signal") or {}).get("signal"))) for lv in ok_levels]
        uniq = {s for _, s in stances}
        if len(uniq) == 1 and "震荡" not in uniq:
            total = "、".join(f"{n}{s}" for n, s in stances)
            cls = "b-red" if "偏多" in uniq else "b-green"
            synth_html = (f'<div class="alert-orange" style="margin:10px 0 0;font-size:12.5px">'
                          f'<b class="{cls if cls == "b-red" else "down"}">多级别共振：</b>{total}——'
                          f'方向一致性高，{ "可按低级别信号执行、高级别趋势持有" if "偏多" in uniq else "防御优先，反弹减仓" }。</div>')
        else:
            total = "、".join(f"{n}{s}" for n, s in stances)
            synth_html = (f'<div class="alert-orange" style="margin:10px 0 0;font-size:12.5px">'
                          f'<b>多级别研判：</b>{total}——高级别（日线）定方向、低级别（30分钟）找买卖点；'
                          f'以日线中枢上下沿为关键位，低级别信号服从高级别结构。</div>')
    return f'''
    <div class="card" id="sec-chan">
      <h2>缠论推演（上证指数 {lv_labels} · 操作指引）</h2>
      <p class="muted" style="font-size:12px">引擎 <b>{_esc(engine)}</b> ｜ 级别：{lv_labels}（高级别定方向，低级别找买卖点）</p>
      {blocks}
      {synth_html}
      <p class="muted" style="font-size:11px;margin:6px 0 0">{_esc(engine_note)}。</p>
    </div>'''


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


def focus_section():
    """限时关注的重点数据解析（嵌入日报，置于「今日操作策略」之前）。

    模块唯一目的：研判日本央行（BOJ）加息「程度」（幅度/节奏/终点利率）——抓取各大所
    英文研报与观点、解析正文、输出合理中文观点并合成一致预期。

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
        if state is None and TODAY == datetime.now().strftime("%Y-%m-%d"):
            try:
                state = fm.run_focus_monitor(no_fetch=False)
            except Exception:
                state = None
        if state is None:
            return ('<div class="card" id="sec-focus"><h2>限时关注的重点数据解析（实时）</h2>'
                    '<p class="muted" style="font-size:12px;">实时数据缺失（外网/代理不可达，'
                    '未能获取各大所日银加息研报研判。'
                    '请先运行 `python focus_monitor.py` 采集后再生成报告）。</p></div>')
        frag = fm.render_focus_html(state, standalone=False, embed=True)
        return f'<div class="card" id="sec-focus"><h2>限时关注的重点数据解析（实时）</h2>{frag}</div>'
    except Exception as e:
        return ('<div class="card" id="sec-focus"><h2>限时关注的重点数据解析（实时）</h2>'
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
# 多空词库已外置到 templates/sentiment_words.json，模块启动时加载为 BULL_WORDS/BEAR_WORDS/NEG_WORDS。


def _score_text(text):
    """对单条文本做多空打分（含就近否定翻转），返回净分。

    逻辑：BULL 词命中且无否定 → +1，有否定（如"不看好"）→ -1；
          BEAR 词命中且无否定 → -1，有否定（如"没有风险"）→ +1。
    """
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
            score += -1 if any(n in window for n in NEG_WORDS) else 1
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


def _build_vs_sources():
    """按 config.weibo_sources 构建大V源结构，返回 {name: meta} 与 all_vs 文本列表。"""
    vs_sources = {}
    for s in VS_SOURCES:
        nm = s.get("name", "")
        # T1 源（唐史主任）回溯近 2 天，与数据引擎抓取窗口一致
        lb = 2 if int(s.get("tier", 2) or 2) == 1 else 1
        texts = wb_texts(nm, lookback_days=lb)
        tier = int(s.get("tier", 2) or 2)
        vs_sources[nm] = {
            "texts": texts,
            "tier": tier,
            "weight": 1.5 if tier == 1 else 1.0,
            "description": s.get("description", ""),
            "signal_type": s.get("signal_type", ""),
        }

    src_scores = {}
    src_meta = {}
    all_vs = []
    for name, meta in vs_sources.items():
        texts = meta["texts"]
        if not texts:
            src_scores[name] = 0
            src_meta[name] = {"tier": meta["tier"], "weight": meta["weight"],
                              "description": meta["description"], "signal_type": meta["signal_type"],
                              "count": 0, "updated": False}
            continue
        raw = sum(_score_text(t) for t in texts)
        src_scores[name] = raw * meta["weight"]
        src_meta[name] = {"tier": meta["tier"], "weight": meta["weight"],
                          "description": meta["description"], "signal_type": meta["signal_type"],
                          "count": len(texts), "updated": True}
        all_vs.extend(texts)
    return src_scores, src_meta, all_vs


def _calc_consensus(src_scores):
    """根据加权总分计算大V共识标签与徽章类。"""
    total = sum(src_scores.values())
    if total > 1.5:
        return total, ("偏多", "b-red")
    if total < -1.5:
        return total, ("偏空", "b-green")
    if total == 0:
        return total, ("中性", "b-blue")
    return total, ("分歧", "b-orange")


def _deconstruct_stock_mentions(all_vs):
    """个股 / 板块提及解构：名称或行业命中即视为被大V覆盖，返回 {code: info}。"""
    tangshi_deep = load_tangshi_deep()
    ts_main = (tangshi_deep or {}).get("deep_view", {}).get("mainline", []) if tangshi_deep else []
    ts_map = [
        (["半导体", "设备", "离子注入", "靶材", "零部件", "PVD", "封装"], "国产替代/脱钩替代"),
        (["光", "算力", "液冷", "连接器", "超节点"], "AI算力（光模块/超节点）"),
        (["存储", "面板"], "存储（光弱则存强）"),
    ]
    stock_mentions = {}
    for code in WATCHLIST:
        nm = watch_name(code)
        sec = _load_sector(code)
        hits = []
        for t in all_vs:
            kw = (nm if (nm and nm in t) else None) or (sec if (sec and sec in t) else None)
            if not kw:
                continue
            sc = _score_text(t)
            i = t.find(kw)
            hits.append({"score": sc, "snippet": t[max(0, i - 12):i + len(kw) + 24]})
        sec_hit = bool(sec) and any(k in sec for k in matched_sectors)
        ts_hit = None
        if ts_main:
            for kws, main_name in ts_map:
                if any(k in sec for k in kws):
                    ts_hit = main_name
                    break
        if not (hits or sec_hit or ts_hit):
            continue
        total_sc = sum(h["score"] for h in hits)
        if ts_hit:
            ts_dir = (tangshi_deep or {}).get("deep_view", {}).get("direction", "偏多")
            tone = ts_dir
            cls = "b-red" if "偏多" in ts_dir else ("b-green" if "偏空" in ts_dir else "b-blue")
        elif total_sc > 0:
            tone, cls = "偏多", "b-red"
        elif total_sc < 0:
            tone, cls = "偏空", "b-green"
        else:
            tone, cls = "中性", "b-blue"
        if hits:
            src, one_liner = "大V点名", f"{nm}（{sec}）被大V提及：{tone}（{len(hits)}条相关）"
        elif sec_hit:
            src, one_liner = "主题覆盖", f"{nm}（{sec}）与板块共振「{'、'.join(matched_sectors)}」相关"
        else:
            src, one_liner = "唐史主线", f"{nm}（{sec}）贴合唐史主线「{ts_hit}」"
        stock_mentions[code] = {"name": nm, "score": total_sc, "snippets": [h["snippet"] for h in hits],
                                "tone": tone, "cls": cls, "src": src, "one_liner": one_liner,
                                "has_snippet": bool(hits)}
    return stock_mentions


def _extract_key_arguments(all_vs, top_n=3):
    """关键论点：净分绝对值最高的代表性观点（仅当日更新，已解构）。"""
    key = []
    for t in all_vs:
        sc = _score_text(t)
        if sc != 0:
            key.append((abs(sc), sc, t[:78]))
    key.sort(key=lambda x: -x[0])
    return key[:top_n]


def _extract_risks(top_n=3):
    """风险点：日本传导链 / 事件因子中的利空信号。"""
    return [t[:78] for t in (japan_items + event_items) if _score_text(t) <= -1][:top_n]


def deconstruct_weibo():
    """解构微博舆情：仅统计当日更新，按源配置（tier 等级 + description 角色）加权。
    返回结构化 dict：src_scores / src_meta / total / consensus / stock_mentions / key / risks。"""
    src_scores, src_meta, all_vs = _build_vs_sources()
    total, consensus = _calc_consensus(src_scores)
    stock_mentions = _deconstruct_stock_mentions(all_vs)
    key = _extract_key_arguments(all_vs)
    risks = _extract_risks()
    return {"src_scores": src_scores, "src_meta": src_meta, "total": total, "consensus": consensus,
            "stock_mentions": stock_mentions, "key": key, "risks": risks}


def _index_snapshot():
    """主要指数速览（整合进核心结论）：紧凑表格，最新+涨跌点数+涨跌幅，红涨绿跌。"""
    if not quotes:
        return ""
    rows = ""
    for name, q in quotes.items():
        chg = q.get("chg")
        chg_html = ""
        if isinstance(chg, (int, float)):
            chg_html = (f'<td class="{up_down(chg)}" style="text-align:right">'
                        f'{sign(chg)}{fmt(chg)}</td>')
        else:
            chg_html = '<td class="muted" style="text-align:right">—</td>'
        rows += (f'<tr><td>{_esc(name)}</td><td style="text-align:right">{q.get("price")}</td>{chg_html}'
                 f'<td class="{up_down(q.get("chg_pct"))}" style="text-align:right">{sign(q.get("chg_pct"))}{q.get("chg_pct")}%</td></tr>')
    return (f'<div style="margin-top:10px;border-top:1px dashed #e0e3e8;padding-top:6px">'
            f'<div class="cc-grid-title">主要指数（实时）</div>'
            f'<table style="min-width:320px"><thead><tr><th>指数</th><th style="text-align:right">最新</th>'
            f'<th style="text-align:right">涨跌</th>'
            f'<th style="text-align:right">涨跌幅</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _strategy_snapshot():
    """操作策略速览（整合进核心结论）：市场状态 + 参考仓位 + 主线 + 风险/回避。"""
    state_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    base = {"bullish": "60-80%", "neutral": "40-60%", "bearish": "10-30%"}.get(market_state, "40-60%")
    main_line = "、".join(matched_sectors) if matched_sectors else "实时板块信号缺失"
    risk_txt = f"日本传导链预警 {len(japan_items)} 条（详见传导链章节）" if japan_items else "暂无日本传导链预警"
    avoid = [watch_name(c) for c in WATCHLIST if _level_rank(diag_for(c)) >= 2]
    avoid_txt = "、".join(avoid) if avoid else "无高危标的"
    return (f'<div style="margin-top:10px;border-top:1px dashed #e0e3e8;padding-top:6px">'
            f'<div class="cc-grid-title">今日操作策略（实时）</div>'
            f'<table><thead><tr><th style="width:18%">维度</th><th>策略</th></tr></thead><tbody>'
            f'<tr><td><b>大盘</b></td><td>市场状态 <b>{state_label}</b>；参考仓位 <b class="up">{base}</b>（市场状态基础仓位，个股按诊断±修正，见第八节）</td></tr>'
            f'<tr><td><b>主线</b></td><td>{_esc(main_line)}</td></tr>'
            f'<tr><td><b>风险</b></td><td>{_esc(risk_txt)}</td></tr>'
            f'<tr><td><b>回避</b></td><td>{_esc(avoid_txt)}</td></tr>'
            f'</tbody></table></div>')


def core_conclusion():
    """核心结论：一句话研判 + 状态徽章 + 数据速览。舆情解构细节见第六节。"""
    d = deconstruct_weibo()
    try:
        from weibo_llm import input_hash as _llm_hash, load_llm as _lld
    except Exception:
        _lld = lambda d8, watchlist=None, expected_hash=None: None
        _llm_hash = lambda data, watchlist, date: None
    _wl_names = {code: (cfg.get("watchlist_names") or {}).get(code, code) for code in WATCHLIST}
    _llm = _lld(DATE8, _wl_names, _llm_hash(weibo_data, _wl_names, TODAY))
    updated_any = any(m.get("updated") for m in d["src_meta"].values())
    if _llm and updated_any:
        _c = _llm.get("consensus", {}) or {}
        consensus_label = _c.get("label") or _c.get("direction", "震荡")
        consensus_cls = _c.get("direction_cls", "b-blue")
    else:
        consensus_label, consensus_cls = d["consensus"]
    idx_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    up_n = sum(1 for q in quotes.values() if q.get("chg_pct", 0) > 0) if quotes else 0
    idx_n = len(quotes) if quotes else 0

    stance = ConclusionPrompts.stance(updated_any, consensus_label, idx_label)
    main_line = "、".join(matched_sectors) if matched_sectors else "板块主线信号缺失"
    risk_label = "日本传导链有预警" if japan_items else "暂无日本传导链预警"

    if not updated_any:
        verdict = ConclusionPrompts.verdict_no_update(idx_label, idx_n, up_n, main_line, risk_label)
        cons_badge_cls = "b-blue"
        cons_badge_label = "大V当日未更新"
    else:
        verdict = ConclusionPrompts.verdict_with_consensus(
            idx_label, idx_n, up_n, consensus_label, consensus_cls, stance, main_line, risk_label)
        cons_badge_cls = consensus_cls
        cons_badge_label = f"大V共识 {consensus_label}"

    badges = (
        f'<span class="badge {cons_badge_cls}">{cons_badge_label}</span>'
        f'<span class="badge b-red">指数 {idx_label}</span>'
        f'<span class="badge b-orange">主线 {main_line}</span>'
        f'<span class="badge {"b-green" if japan_items else "b-blue"}">风险 {risk_label}</span>'
    )

    return f'''
    <div class="cc-body" style="font-size:14px;line-height:1.9;margin-bottom:12px">{verdict}</div>
    <div class="cc-stocks">{badges}</div>
    <div class="cc-grid-title">实时数据速览</div>
    <div class="conclusion-grid">{conclusion_grid()}</div>
    {_strategy_snapshot()}
    {_index_snapshot()}
    '''


def vs_summary():
    """第六节：微博舆情解构。把大V/宏观/事件原文解构为可研判信号（不贴原文）。
    LLM 全源解构优先（weibo_llm 产出），缺失字段回退规则打分。"""
    d = deconstruct_weibo()
    try:
        from weibo_llm import input_hash as _llm_hash, load_llm as _load_llm_deconstruct
    except Exception:
        _load_llm_deconstruct = lambda d8, watchlist=None, expected_hash=None: None
        _llm_hash = lambda data, watchlist, date: None
    _wl_names = {code: (cfg.get("watchlist_names") or {}).get(code, code) for code in WATCHLIST}
    llm = _load_llm_deconstruct(DATE8, _wl_names, _llm_hash(weibo_data, _wl_names, TODAY))
    # 唐史 T1 深度解构：优先 LLM 全源解构（tangshi_deep 即 deep_view 内容），回退人工/旧版注入（含 deep_view 键）
    _llm_td = (llm or {}).get("tangshi_deep")
    _old_td = load_tangshi_deep()
    tangshi_deep = _llm_td if _llm_td else ((_old_td or {}).get("deep_view", {}) if _old_td else {})
    idx_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "震荡"}.get(market_state, "震荡")
    up_n = sum(1 for q in quotes.values() if q.get("chg_pct", 0) > 0) if quotes else 0
    idx_n = len(quotes) if quotes else 0

    # 各源状态表（当日更新才计入观点；未更新源单独标注）
    src_rows = []
    updated_any = False
    for name, s in d["src_scores"].items():
        meta = d["src_meta"].get(name, {})
        tier = meta.get("tier", 2)
        desc = meta.get("description", "")
        if not meta.get("updated"):
            src_rows.append(f'<tr><td><b>{_esc(name)}</b></td><td class="muted">T{tier} · {_esc(desc)}</td>'
                            f'<td class="muted">当日未更新</td></tr>')
            continue
        updated_any = True
        if tier == 1 and tangshi_deep:
            src_rows.append(f'<tr><td><b>{_esc(name)}</b></td><td class="muted">T{tier} · {_esc(desc)}</td>'
                            f'<td><span class="badge {tangshi_deep.get("direction_cls", "b-blue")}">{_esc(tangshi_deep.get("direction", "中性"))}</span>'
                            f' <span class="muted">{meta.get("count", 0)}条 · 深度解构见下</span></td></tr>')
            continue
        tag = "偏多" if s > 0 else ("偏空" if s < 0 else "中性")
        cls = "b-red" if s > 0 else ("b-green" if s < 0 else "b-blue")
        src_rows.append(f'<tr><td><b>{_esc(name)}</b></td><td class="muted">T{tier} · {_esc(desc)}</td>'
                        f'<td><span class="badge {cls}">{tag}</span> <span class="muted">{meta.get("count", 0)}条</span></td></tr>')
    src_table = (f'<table class="vs-src-table"><thead><tr><th>大V</th><th>等级 · 角色</th><th>当日观点</th></tr></thead>'
                 f'<tbody>{"".join(src_rows)}</tbody></table>') if src_rows else ""
    # 共识：LLM 优先（仅当日有更新时），否则回退规则打分
    if llm and updated_any:
        _c = llm.get("consensus", {}) or {}
        consensus_label = _c.get("label") or _c.get("direction", "震荡")
        consensus_cls = _c.get("direction_cls", "b-blue")
    else:
        consensus_label, consensus_cls = d["consensus"]
    if not updated_any and not (llm and (llm.get("consensus", {}) or {}).get("text")):
        consensus_html = WeiboPrompts.NO_UPDATE_CONSENSUS + src_table
    else:
        consensus_html = (f'<div style="margin-bottom:8px"><span class="badge {consensus_cls}" style="font-size:13px">{consensus_label}</span>'
                          f' <b>大V整体共识</b> <span class="muted" style="font-size:12px">（仅当日更新，T1权重1.5）</span></div>')
        if llm and (llm.get("consensus", {}) or {}).get("text"):
            consensus_html += f'<div class="cc-body" style="margin-bottom:8px">{_esc(llm["consensus"]["text"])}</div>'
        consensus_html += src_table

    diverge = (consensus_label != idx_label) and (consensus_label in {"偏多", "偏空"})
    if not updated_any:
        market_view = (f"指数层面 <b>{idx_label}</b>（{idx_n} 指 {up_n} 涨）。"
                       f"当日无大V更新，情绪面暂无新增信号，研判以指数与技术面为准。")
    else:
        market_view = (f"指数层面 <b>{idx_label}</b>（{idx_n} 指 {up_n} 涨）与大V共识 <b class='{consensus_cls}'>{consensus_label}</b> "
                       f"—— {'两者背离，需警惕情绪拖累' if diverge else '相互印证'}。")

    # 自选股舆情研判：LLM 点名优先；无点名则诚实标注（板块共振在第七节呈现，不在此凑数）
    stock_items = []
    if llm and updated_any and llm.get("stock_mentions"):
        for m in llm["stock_mentions"]:
            nm = m.get("name") or watch_name(m.get("code", ""))
            cls = m.get("cls", "b-blue")
            stance = m.get("stance", "中性")
            reason = m.get("reason", "")
            conf = m.get("confidence")
            stock_items.append(f'<div style="margin:5px 0"><span class="badge {cls}">{_esc(nm)} · {_esc(stance)}</span>'
                               f' <span style="font-size:12.5px;color:#444">{_esc(reason)}</span>'
                               + (f' <span class="muted" style="font-size:11px">置信度 {conf}</span>' if conf else '')
                               + '</div>')
    if not updated_any:
        stock_line = WeiboPrompts.NO_UPDATE_STOCK
    elif stock_items:
        stock_line = "".join(stock_items)
    else:
        stock_line = '<p class="muted">今日大V观点未直接点名或主题命中自选股（板块共振信号见第七节）。</p>'

    def _kp_card(source, stance, fact, inference="", horizon="", confidence=None):
        cls = "b-red" if stance == "偏多" else ("b-green" if stance == "偏空" else "b-blue")
        meta_bits = []
        if horizon:
            meta_bits.append(horizon)
        if confidence is not None:
            meta_bits.append(f"置信度 {confidence}")
        meta = f' <span class="muted" style="font-weight:400;font-size:11px">{" · ".join(meta_bits)}</span>' if meta_bits else ""
        inf = (f'<div class="kp-inf"><b>推断：</b>{_esc(inference)}</div>' if inference else "")
        return (f'<div class="kp-card">'
                f'<div class="kp-head"><span class="badge {cls}">{_esc(stance)}</span> <b>{_esc(source)}</b>{meta}</div>'
                f'<div class="kp-fact"><b>事实：</b>{_esc(fact)}</div>{inf}</div>')

    if not updated_any and not (llm and llm.get("key_points")):
        key_html = WeiboPrompts.NO_UPDATE_KEY
    elif llm and llm.get("key_points"):
        key_html = "".join(
            _kp_card(kp.get("source", ""), kp.get("stance", "中性"), kp.get("fact", kp.get("text", "")),
                     kp.get("inference", ""), kp.get("horizon", ""), kp.get("confidence"))
            for kp in llm["key_points"]) or WeiboPrompts.NO_KEY_SIGNAL
    else:
        key_html = "".join(
            f'<div class="kp-card"><div class="kp-head"><span class="badge {"b-red" if sc > 0 else "b-green" if sc < 0 else "b-blue"}">'
            f'{"偏多" if sc > 0 else "偏空" if sc < 0 else "中性"}</span> <b>实时大V观点</b></div>'
            f'<div class="kp-fact">{_esc(t)}…</div></div>'
            for _, sc, t in d["key"]) or WeiboPrompts.NO_KEY_SIGNAL

    if llm and llm.get("risks"):
        risk_html = "".join(
            f'<div class="risk-row"><span class="badge {"b-red" if r.get("level") == "高" else "b-blue"}">{_esc(r.get("level", "中"))}风险</span>'
            f'{_esc(r.get("text", ""))}'
            f' <span class="muted" style="font-size:11px">{_esc(r.get("horizon", ""))}</span></div>'
            for r in llm["risks"]) or WeiboPrompts.NO_RISK_SIGNAL
    else:
        risk_html = "".join(f'<div class="risk-row"><span class="badge b-red">风险</span>{_esc(t)}…</div>'
                            for t in d["risks"]) or WeiboPrompts.NO_RISK_SIGNAL

    # T1 唐史主任深度解读卡片（Agent 研读注入，非词库打分；summary 与核心逻辑重复，不再渲染）
    deep_html = ""
    if tangshi_deep and (updated_any or (llm and llm.get("tangshi_deep"))):
        dv = tangshi_deep
        mainline = "、".join(dv.get("mainline", []))
        avoid = "、".join(dv.get("avoid", []))
        conf = dv.get("confidence")
        deep_html = f'''
      <div class="cc-head">唐史主任深度解构（T1 · 研读近3日观点链）</div>
      <div class="cc-body deep-card">
        <div class="deep-grid">
          <div class="deep-row"><span class="deep-k">方向</span><span class="badge {dv.get("direction_cls", "b-blue")}">{_esc(dv.get("direction", ""))}</span>{f'<span class="muted" style="font-size:11px">置信度 {conf}</span>' if conf else ''}</div>
          <div class="deep-row"><span class="deep-k">主线</span><span>{_esc(mainline)}</span></div>
          <div class="deep-row"><span class="deep-k">回避</span><span>{_esc(avoid)}</span></div>
        </div>
        <div class="alert-blue" style="margin:8px 0"><b>核心逻辑：</b>{_esc(dv.get("core_logic", ""))}</div>
        <div class="alert-orange" style="margin:8px 0"><b>操作含义：</b>{_esc(dv.get("action", ""))}</div>
        <div class="alert-red"><b>风险点：</b>{_esc("；".join(dv.get("risks", [])))}</div>
      </div>'''

    return f'''
    <div class="cc-view">
      <div class="cc-head">大V共识解构</div>
      <div class="cc-body">{consensus_html}</div>
      {deep_html}
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


# CSS 已外置到 templates/style.css，运行时通过 _load_css() 加载。

def _render_html():
    css = _load_css()
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股舆情操作指引 · 9章节 · {TODAY}（{REPORT_LABEL}）</title>
<style>{css}</style>
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
<li><a href="#sec-core">核心结论（实时 · 含今日操作策略与主要指数）</a></li>
<li><a href="#sec-us">隔夜美股（实时 · 外网解析）</a></li>
<li><a href="#sec-macro">CPI与宏观（实时 · 外网解析）</a></li>
<li><a href="#sec-chain">宏观传导链监控（独立因子·实时）</a></li>
<li><a href="#sec-fima">FIMA 高亮（外国央行美债托管 · 临时）</a></li>
<li><a href="#sec-geo">地缘政治与原油（事件因子 · 外网解析）</a></li>
<li><a href="#sec-etf">ETF资金流向（实时）</a></li>
<li><a href="#sec-weibo">微博舆情解构（{" / ".join(s.get("name", "") for s in VS_SOURCES) or "大V"} · 实时）</a></li>
<li><a href="#sec-resonance">共振信号（多源交叉·实时）</a></li>
<li><a href="#sec-watchlist">{len(WATCHLIST)}只自选股操作指引（实时诊断）</a></li>
<li><a href="#sec-focus">限时关注的重点数据解析（实时）</a></li>
<li><a href="#sec-chan">缠论推演（上证指数 30分钟+日线 · 操作指引）</a></li>
</ol>
</div>

<div class="card" id="sec-core">
<h2>核心结论（实时 · 含今日操作策略与主要指数）</h2>
{core_conclusion()}
</div>

<div class="card" id="sec-us">
<h2>一、隔夜美股（实时 · 外网解析）</h2>
{us_section()}
{intel_block("us_market")}
</div>

<div class="card" id="sec-macro">
<h2>二、CPI与宏观（实时 · 外网解析）</h2>
{intel_block("macro")}
</div>

<div class="card" id="sec-chain">
<h2>三、宏观传导链监控（独立因子·实时）</h2>
{chain_svg()}
{fima_highlight()}
<div style="margin-top:12px;">{us_yield_panel()}</div>
{intel_block("japan")}
</div>

<div class="card" id="sec-geo">
<h2>四、地缘政治与原油（事件因子 · 外网解析）</h2>
{intel_block("geopolitics")}
</div>

<div class="card" id="sec-etf">
<h2>五、ETF资金流向（实时）</h2>
{etf_section()}
{fund_section()}
{market_width_section()}
</div>

<div class="card" id="sec-weibo">
<h2>六、微博舆情解构（{" / ".join(s.get("name", "") for s in VS_SOURCES) or "大V"} · 实时）</h2>
{vs_summary()}
</div>

<div class="card" id="sec-resonance">
<h2>七、共振信号（多源交叉·实时）</h2>
{resonance_section()}
</div>

<div class="card" id="sec-watchlist">
<h2>八、{len(WATCHLIST)}只自选股操作指引（实时诊断）</h2>
{concentration_warning()}
{''.join(stock_card(c) for c in WATCHLIST)}
</div>

{focus_section()}

{chan_section()}

<div class="disclaimer">
<strong>免责声明</strong>：以上内容基于公开数据、大V观点及量化规则自动生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
</body>
</html>'''

def main():
    load_context()
    html = _render_html()

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
            td = datetime.strptime(TODAY, "%Y-%m-%d").date()
            db.save_report(td, REPORT_STATE, "A股操作指引·9章节", html)
            print("[DB] 报告入库完成（舆情数据由数据引擎统一入库，避免重复）")
        else:
            print("[DB] 未配置 database，跳过入库")
    except Exception as e:
        print(f"[DB] 入库失败: {e}")


if __name__ == "__main__":
    main()
