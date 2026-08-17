#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
限时关注的重点数据解析（focus_monitor）

监控三类日元主导传导链末端的宏观引爆信号（任一被确认即触发危险告警）：
  1) 抛美债       —— 日本/中国/海外持有的美国国债被主动抛售/减持（TIC 数据、官方表态）
  2) FIMA 工具    —— 美联储 FIMA 回购便利工具被启用 / 用量激增（外国央行押美债借美元干预汇率）
  3) 大机构日元加息 —— 高盛/摩根/三菱日联/野村等主流机构上调日本央行加息预测

数据来源：外网新闻（Google News RSS 优先，含 pubDate 时间窗；Bing News RSS 兜底）。
           代理开启时 Google 可达，本模块「代理感知」——读取 HTTPS_PROXY/HTTP_PROXY 环境变量，
           或在 config.json 的 focus_monitor.proxy 显式配置。

检测逻辑：若任一信号在近端时间窗（window_days，默认 3 天）内检获「动作型确认报道」
          （mention+action 双命中且非否定表述），即判定为「触发（TRIGGERED）」，
          弹出全屏危险告警：危险 危险 危险⚠️

用法：
    python focus_monitor.py            # 实时爬取 + 检测 + 存 JSON + 生成独立 HTML
    python focus_monitor.py --no-fetch # 不爬取，仅用上次缓存 state JSON 渲染（离线模式）
    python focus_monitor.py --days 2   # 设置近端时间窗（天），用于「近端」展示与热度提示

也可被其他模块 import：
    from focus_monitor import run_focus_monitor, render_focus_html
"""
import json
import os
import re
import sys
import html
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
OUTPUT_DIR = Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# 默认配置（config.json 中无 focus_monitor 时回退）
DEFAULT_CONFIG = {
    "enabled": True,
    "window_days": 3,
    "signal_max_age_days": 15,
    "proxy": "",
    # 每个信号三类关键词：
    #   mention_keywords  —— 命中主题/机构才计入该信号（避免其他话题串味）
    #   action_keywords   —— 正面「动作型」表述（抛售/启用/上调…）
    #   negation_keywords —— 否定表述（未使用/推迟…），命中即排除该条，杜绝误报
    "signals": {
        "ust_dump": {
            "label": "抛美债",
            "queries_zh": [
                "日本 抛售 美国国债", "中国 减持 美国国债",
                "海外 抛售 美债 创纪录", "美国国债 遭抛售",
            ],
            "queries_en": [
                "Japan sell US Treasuries", "foreign holders dump US Treasuries",
                "TIC data US Treasuries holdings decline",
            ],
            "mention_keywords": ["美国国债", "美债", "UST", "Treasuries", "美国政府债券"],
            "action_keywords": [
                "抛售", "减持", "抛售美债", "抛美债", "大幅减持", "创纪录", "清仓",
                "抛售美国国债", "sell", "dump", "offload", "reduced holdings", "divest",
            ],
            "negation_keywords": [],
        },
        "fima": {
            "label": "FIMA工具",
            "queries_zh": [
                "美联储 FIMA 回购工具 用量", "FIMA 余额 激增",
                "外国央行 FIMA 借美元", "FIMA repo 规模",
            ],
            "queries_en": [
                "FIMA repo facility usage surge", "Federal Reserve FIMA reverse repo balance",
                "foreign central banks FIMA borrow dollars",
            ],
            "mention_keywords": ["FIMA", "回购机制", "回购工具", "reverse repo"],
            "action_keywords": [
                "启用", "动用", "激增", "飙升", "创新高", "非零", "大幅", "余额上升",
                "借美元", "borrow", "surge", "record", "facility",
            ],
            "negation_keywords": [
                "未使用", "零使用", "没有进行任何操作", "未被使用", "仍未使用",
                "连续.*周未使用", "连续.*未使用",
            ],
        },
        "boj_hike_inst": {
            "label": "大机构日元加息",
            "queries_zh": [
                "高盛 摩根 日本央行 加息 预测", "三菱日联 野村 上调 日本 加息",
                "机构 上调 日元 加息预期", "日本央行 加息 75bp 机构预测",
            ],
            "queries_en": [
                "Goldman Morgan Stanley BoJ rate hike forecast",
                "MUFG Nomura raise Japan rate hike forecast",
                "institutions expect BoJ rate hike yen",
            ],
            "mention_keywords": [
                "高盛", "摩根", "三菱日联", "野村", "瑞银", "巴克莱", "美银", "大和",
                "MUFG", "Mizuho", "Nomura", "Goldman", "Morgan", "JPMorgan",
            ],
            "action_keywords": [
                "上调", "加息", "提前", "加快", "风险上行", "预期上升", "三次",
                "将加息", "hike", "raise", "tighten",
            ],
            "negation_keywords": [
                "推迟", "延迟", "延后", "下调", "鸽派", "不加息", "不会加息", "暂缓",
            ],
        },
    },
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
BING_NEWS_RSS = "https://www.bing.com/news/search?q={q}&format=rss"
BING_NEWS_RSS_INT = "https://www.bing.com/news/search?q={q}&format=rss&setlang=en-us&cc=US"


# ----------------------------------------------------------------------------
# 代理感知的抓取
# ----------------------------------------------------------------------------
def _build_opener(proxy=None):
    """构造 urllib opener；若显式 proxy 或环境变量 HTTPS_PROXY/HTTP_PROXY 存在则走代理。"""
    px = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
        or os.environ.get("http_proxy")
    if px:
        handler = urllib.request.ProxyHandler({"http": px, "https": px})
        return urllib.request.build_opener(handler), px
    return urllib.request.build_opener(), None


def _fetch(url, opener, timeout=12, quiet=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        if not quiet:
            print(f"    [fetch error] {url[:70]} -> {type(e).__name__}: {e}")
        return None


def _parse_rss_items(text, max_items=6, max_len=320):
    """解析 RSS <item>，提取 title / description / pubDate。"""
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    out = []
    for it in items[:max_items]:
        tm = re.search(r"<title>(.*?)</title>", it, re.S)
        dm = re.search(r"<description>(.*?)</description>", it, re.S)
        pm = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        title = re.sub(r"<!\[CDATA\[|\]\]>", "", tm.group(1)).strip() if tm else ""
        desc = re.sub(r"<!\[CDATA\[|\]\]>", "", dm.group(1)).strip() if dm else ""
        pub = pm.group(1).strip() if pm else ""
        # 清理 HTML 实体/标签
        title = html.unescape(re.sub(r"<[^>]+>", "", title))
        desc = html.unescape(re.sub(r"<[^>]+>", "", desc))
        content = f"{title}。{desc}".strip() if desc else title
        content = re.sub(r"\s+", " ", content).strip()
        if content and len(content) > 8:
            out.append({"text": content[:max_len], "pub": pub})
    return out


def _parse_pubdate(pub):
    """RFC822 pubDate -> 带时区的 datetime；失败返回 None。"""
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 单信号抓取 + 检测
# ----------------------------------------------------------------------------
def _kw_hit(text, keyword):
    """关键词命中：ASCII 关键词小写子串匹配，中文原样匹配。"""
    if keyword.isascii():
        return keyword.lower() in text.lower()
    return keyword in text


def _crawl_signal(sig, opener, window_days, max_age_days):
    """抓取单个信号的所有查询词，按 mention+action 且排除 negation 判定「触发（TRIGGERED）」。

    - window_days：仅用于「近端」展示标签与热度提示（短窗口）。
    - max_age_days：触发判定的有效时效（这些宏观引爆信号半衰期以周计，
      不能按 3 天死卡，否则会漏掉仍在发酵的既成动作型报道）。无 pubDate 的
      报道视为「新鲜」（Google 已按时效/相关度排序）。
    """
    items_all = []
    seen = set()
    for q in sig.get("queries_zh", []) + sig.get("queries_en", []):
        eq = urllib.parse.quote(q)
        sources = [
            ("Google", GOOGLE_NEWS_RSS.format(q=eq)),
            ("Bing", BING_NEWS_RSS.format(q=eq)),
            ("Bing国际", BING_NEWS_RSS_INT.format(q=eq)),
        ]
        for name, url in sources:
            text = _fetch(url, opener, quiet=(name == "Google"))
            if not text:
                continue
            items = _parse_rss_items(text)
            if items:
                print(f"    [兜底] {sig['label']} 命中来源: {name}（{len(items)} 条）")
                break
        else:
            continue
        for it in items:
            key = it["text"][:60]
            if key in seen:
                continue
            seen.add(key)
            items_all.append(it)

    mention_kw = sig.get("mention_keywords", [])
    action_kw = sig.get("action_keywords", [])
    neg_kw = sig.get("negation_keywords", [])

    now = datetime.now(timezone.utc)
    recent = []
    fresh_action = 0
    action_hits = 0
    metrics = {}
    for it in items_all:
        it["_dt"] = _parse_pubdate(it["pub"])
        age = (now - it["_dt"]).days if it["_dt"] is not None else None
        is_recent = age is not None and age <= window_days
        is_fresh = age is None or age <= max_age_days
        it["_recent"] = is_recent
        if is_recent:
            recent.append(it)
        # 否定表述（支持正则，如「连续.*周未使用」）
        negated = any(re.search(nk, it["text"]) for nk in neg_kw)
        it["_negated"] = negated
        # mention + action 双命中才算动作型（且需在有效时效内）
        has_mention = any(_kw_hit(it["text"], k) for k in mention_kw)
        has_action = any(_kw_hit(it["text"], k) for k in action_kw)
        hit = 1 if (has_mention and has_action and not negated and is_fresh) else 0
        it["_action_hits"] = hit
        it["_mention"] = has_mention
        it["_action"] = has_action
        action_hits += hit
        if hit >= 1:
            fresh_action += 1
            _extract_metrics(it["text"], metrics)

    triggered = fresh_action >= 1
    # 热点：近端存在较多讨论（即便未确认动作），用于「关注」提示
    hot = len(recent) >= 3
    return {
        "label": sig["label"],
        "triggered": triggered,
        "hot": hot,
        "total_count": len(items_all),
        "recent_count": len(recent),
        "recent_action_count": fresh_action,
        "action_hits": action_hits,
        "items": items_all[:8],
        "metrics": metrics,
        "reachable": len(items_all) > 0,
    }


_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(亿|万亿|万)?\s*(美元|美债|亿美债)", re.I)
_BP_RE = re.compile(r"加息\s*(?:至)?\s*([\d.]+)\s*(bp|个基点|%)", re.I)


def _extract_metrics(text, metrics):
    """轻量抽取关键数值（FIMA余额 / 减持规模 / 加息幅度），首个命中存入 metrics。"""
    if "amount" not in metrics:
        m = _AMOUNT_RE.search(text)
        if m:
            val = m.group(1).replace(",", "")
            unit = m.group(2) or ""
            metrics["amount"] = f"{val}{unit}{m.group(3)}"
    if "hike" not in metrics:
        m = _BP_RE.search(text)
        if m:
            metrics["hike"] = f"{m.group(1)}{m.group(2)}"


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def _load_latest_state():
    """加载 data/focus/ 下最新的 focus_state_*.json（供 --no-fetch 渲染）。"""
    d = OUTPUT_DIR / "data" / "focus"
    if not d.exists():
        return None
    files = sorted(d.glob("focus_state_*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full = json.load(f)
        fm = full.get("focus_monitor", {})
        # 合并默认配置（缺项补默认）
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in fm.items() if k != "signals"})
        if fm.get("signals"):
            cfg["signals"] = fm["signals"]
        return cfg
    return dict(DEFAULT_CONFIG)


def build_state(config, no_fetch=False, days=None):
    window = int(days) if days else int(config.get("window_days", 3))
    max_age = int(config.get("signal_max_age_days", 15))
    date8 = datetime.now().strftime("%Y%m%d")
    state = {
        "module": "限时关注的重点数据解析",
        "date": date8,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": window,
        "danger": False,
        "severity": 0,
        "signals": {},
        "banner": "",
        "reachable": False,
        "note": "",
    }

    opener, px = _build_opener(config.get("proxy"))
    print(f"[焦点监控] 代理: {px or '（未检测到代理环境变量，直连）'} | 时间窗: {window} 天")

    if no_fetch:
        cached = _load_latest_state()
        if cached:
            cached["no_fetch"] = True
            print("[焦点监控] --no-fetch 模式：加载缓存 state 渲染。")
            return cached
        print("[焦点监控] --no-fetch 模式：未找到缓存 state JSON，返回空状态。")
        return state

    signals_cfg = config.get("signals", {})
    any_reachable = False
    triggered_labels = []
    for key, sig in signals_cfg.items():
        print(f"[焦点监控] 抓取信号: {sig['label']} ...")
        res = _crawl_signal(sig, opener, window, max_age)
        state["signals"][key] = res
        if res["reachable"]:
            any_reachable = True
        if res["triggered"]:
            triggered_labels.append(sig["label"])
        print(f"    总条目 {res['total_count']} | 近端 {res['recent_count']} | "
              f"动作型 {res['recent_action_count']} | 触发: {res['triggered']}")

    state["reachable"] = any_reachable
    state["severity"] = len(triggered_labels)
    if triggered_labels:
        state["danger"] = True
        state["banner"] = "危险 危险 危险⚠️"
        state["note"] = "以下信号已触发（动作型确认报道）：" + "、".join(triggered_labels)
    else:
        if not any_reachable:
            state["note"] = "外网不可达（代理未开启或 Google/Bing 均超时），无法完成信号研判，请检查网络/代理后重跑。"
        else:
            state["note"] = "近端时间窗内未检获三类信号的动作型确认报道，当前平静。"
    return state


def render_focus_html(state, standalone=False):
    """渲染「限时关注的重点数据解析」章节（或独立页面）。"""
    danger = state.get("danger")
    banner = state.get("banner", "")
    sev = state.get("severity", 0)
    window = state.get("window_days", 3)
    note = state.get("note", "")
    color_up = "#d63031"  # 涨/危险 红
    color_down = "#00a865"
    color_neutral = "#636e72"

    # 危险横幅
    if danger:
        banner_html = f'''
  <div style="background:{color_up};color:#fff;padding:18px 22px;border-radius:10px;
              margin:14px 0;text-align:center;font-size:26px;font-weight:800;
              letter-spacing:2px;box-shadow:0 4px 14px rgba(214,48,49,.35);">
    ⚠️ {banner} ⚠️
    <div style="font-size:13px;font-weight:500;margin-top:6px;opacity:.95;">
      日元主导传导链末端引爆信号已触发，警惕全球流动性收紧 → A股风险偏好承压
    </div>
  </div>'''
    else:
        banner_html = f'''
  <div style="background:#f1f2f6;color:{color_neutral};padding:12px 18px;border-radius:10px;
              margin:14px 0;text-align:center;font-size:15px;font-weight:600;">
    当前三类危险信号处于平静区间（近端 {window} 天未检出动作型报道）
  </div>'''

    cards = []
    for key, s in state.get("signals", {}).items():
        if not s.get("reachable"):
            status = "数据缺失"
            scolor = color_neutral
            badge = f'<span style="background:#dfe6e9;color:#636e72;padding:2px 8px;border-radius:6px;font-size:12px;">外网不可达</span>'
        elif s.get("triggered"):
            status = "触发 · 动作型确认"
            scolor = color_up
            badge = f'<span style="background:{color_up};color:#fff;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:700;">危险</span>'
        elif s.get("hot"):
            status = "近期活跃（未确认动作）"
            scolor = "#e17055"
            badge = f'<span style="background:#fdcb6e;color:#5a3d00;padding:2px 8px;border-radius:6px;font-size:12px;">关注</span>'
        else:
            status = "平静"
            scolor = color_down
            badge = f'<span style="background:{color_down};color:#fff;padding:2px 8px;border-radius:6px;font-size:12px;">正常</span>'

        metrics = s.get("metrics", {})
        metric_str = " · ".join(f"{k}:{v}" for k, v in metrics.items()) if metrics else ""
        items_html = ""
        for it in s.get("items", [])[:5]:
            raw = it.get("_dt")
            dtstr = None
            if isinstance(raw, datetime):
                dtstr = raw
            elif isinstance(raw, str) and raw:
                try:
                    dtstr = parsedate_to_datetime(raw)
                except Exception:
                    try:
                        dtstr = datetime.fromisoformat(raw)
                    except Exception:
                        dtstr = None
            when = dtstr.strftime("%m-%d %H:%M") if dtstr else (it.get("pub", "")[:16] or "时间未知")
            ah = it.get("_action_hits", 0)
            negated = it.get("_negated", False)
            recent = it.get("_recent", False)
            if ah >= 1 and recent:
                tag = f'<span style="color:{color_up};font-weight:700;">●动作</span>'
            elif negated and recent:
                tag = '<span style="color:#b2bec3;">✕排除</span>'
            elif recent:
                tag = '<span style="color:#e17055;">○近端</span>'
            else:
                tag = '<span style="color:#dfe6e9;">·</span>'
            items_html += f'''
      <li style="margin:6px 0;font-size:13px;line-height:1.5;color:#2d3436;">
        {tag} <span style="color:#b2bec3;font-size:11px;">[{when}]</span> {html.escape(it.get("text",""))}
      </li>'''

        cards.append(f'''
    <div style="flex:1;min-width:280px;border:1px solid #e5e8ec;border-radius:10px;padding:14px;margin:6px;
                border-top:4px solid {scolor};background:#fff;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-size:16px;font-weight:700;color:{scolor};">{html.escape(s.get('label',''))}</div>
        {badge}
      </div>
      <div style="font-size:13px;color:#636e72;margin:4px 0 8px;">状态：{status}</div>
      {('<div style="font-size:12px;color:#0984e3;margin-bottom:6px;">🔢 ' + html.escape(metric_str) + '</div>') if metric_str else ''}
      <ul style="list-style:none;padding-left:0;margin:0;">{items_html or '<li style="font-size:13px;color:#b2bec3;">（无抓取数据）</li>'}</ul>
    </div>''')

    inner = f'''
  {banner_html}
  <p style="font-size:13px;color:#636e72;margin:0 0 10px;">
    监控三类日元主导传导链末端引爆信号（近端 {window} 天）；数据缺失≠安全，请确认外网/代理可用。{html.escape(note) if note else ''}
  </p>
  <div style="display:flex;flex-wrap:wrap;">{''.join(cards)}</div>'''

    if standalone:
        return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>限时关注的重点数据解析 - {state.get('date','')}</title>
<style>body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#2d3436;}}</style>
</head><body>
<h2 style="color:#2d3436;">限时关注的重点数据解析 <span style="font-size:13px;color:#b2bec3;">{state.get('generated_at','')}</span></h2>
{inner}
</body></html>'''
    return f'<section style="margin:18px 0;"><h2 style="color:#2d3436;font-size:20px;border-left:4px solid #d63031;padding-left:10px;">限时关注的重点数据解析</h2>{inner}</section>'


def save_outputs(state):
    date8 = state.get("date") or datetime.now().strftime("%Y%m%d")
    # 状态 JSON
    focus_dir = OUTPUT_DIR / "data" / "focus"
    focus_dir.mkdir(parents=True, exist_ok=True)
    json_path = focus_dir / f"focus_state_{date8}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    # 独立 HTML
    rep_dir = OUTPUT_DIR / "reports" / "限时关注"
    rep_dir.mkdir(parents=True, exist_ok=True)
    html_path = rep_dir / f"focus-{date8}.html"
    html_path.write_text(render_focus_html(state, standalone=True), encoding="utf-8")
    return json_path, html_path


def run_focus_monitor(config_path=None, no_fetch=False, days=None):
    """对外入口：返回 state dict（含 danger/banner/signals）。"""
    cfg = load_config()
    state = build_state(cfg, no_fetch=no_fetch, days=days)
    if not no_fetch:
        save_outputs(state)
    return state


def main():
    no_fetch = "--no-fetch" in sys.argv
    days = None
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            try:
                days = int(sys.argv[i + 1])
            except ValueError:
                pass
    state = run_focus_monitor(no_fetch=no_fetch, days=days)
    print("\n" + "=" * 56)
    if state.get("danger"):
        print(f"⚠️  {state['banner']}  （危险信号数: {state['severity']}）")
        print(f"⚠️  {state['note']}")
    else:
        print("✅ 三类危险信号当前平静（或未检出动作型报道）。")
        if state.get("note"):
            print(f"   {state['note']}")
    print("=" * 56)
    # 同时打印可嵌入报告的 HTML 片段（供 build_report 复用）
    frag = render_focus_html(state, standalone=False)
    out_dir = OUTPUT_DIR / "reports" / "限时关注"
    out_dir.mkdir(parents=True, exist_ok=True)
    frag_path = out_dir / f"focus-fragment-{state.get('date')}.html"
    frag_path.write_text(frag, encoding="utf-8")
    print(f"[焦点监控] 章节片段已写: {frag_path}")


if __name__ == "__main__":
    main()
