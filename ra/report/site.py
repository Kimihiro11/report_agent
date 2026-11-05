#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史报告站点（静态生成）。

为什么要有它：
    日报原本是「一次性自包含 HTML」，83 个文件散在 reports/ 各目录里，翻历史只能靠
    文件管理器。库里的 `daily_reports` 反而更好查，但缺少浏览层。

做法：
    从 PG `daily_reports` 读**唯一版本**（`is_superseded=FALSE`），生成一个静态站点：

        site/index.html            时间线（可筛选 早报/晚报/周报）
        site/r/<date>-<type>.html  单份报告（统一外壳 + 原报告内容）
        site/assets/site.css       外壳样式

关键设计：
    1. **不改 build_report 渲染**。原报告 HTML 原样复用，只把它的 <style> 做**选择器
       前缀隔离**（`.rp`），避免 `*`/`body` 全局规则污染外壳 —— 报告 CSS 无 @media /
       @keyframes，132 条扁平规则，前缀化安全可控。
    2. **不重复存储**。站点只是视图，数据源仍是 PG。
    3. 缺字段显示「—」，不编造。

用法：
    python cli.py site                 # 生成到 site/
    python cli.py site --limit 30      # 只生成最近 30 份
    python cli.py site --out D:/tmp/x  # 指定目录
"""
import argparse
import html as _html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # ra/report/site.py → 项目根
sys.path.insert(0, str(ROOT))

TYPE_BADGE = {"早报": "s-b-morning", "盘中": "s-b-intraday", "晚报": "s-b-evening", "周报": "s-b-weekly"}
TYPE_ORDER = ["早报", "盘中", "晚报", "周报"]


# ---------------------------------------------------------------- 数据

def _db():
    from ra.infra.db import StockAgentDB
    pwd = ""
    cfg_p = ROOT / "config.json"
    if cfg_p.exists():
        try:
            pwd = (json.loads(cfg_p.read_text(encoding="utf-8")).get("database") or {}).get("password", "")
        except Exception:  # noqa: BLE001
            pwd = ""
    return StockAgentDB(password=pwd)


def load_reports(limit=0):
    """读唯一版本报告（按日期倒序）。库不可用时直接报错，不静默返回空。"""
    db = _db()
    sql = """SELECT report_date, report_type, market_state, core_summary,
                    data_date, created_at, html_content
             FROM daily_reports
             WHERE is_superseded IS NOT TRUE AND report_type IS NOT NULL
             ORDER BY report_date DESC, report_type"""
    with db._cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    out = []
    for d, rtype, mstate, core, ddate, ts, html in rows:
        out.append({"date": d, "type": rtype, "state": mstate or "",
                    "core": (core or "").strip(), "data_date": ddate,
                    "created": ts, "html": html or ""})
    if limit:
        out = out[:limit]
    return out


def load_metrics(dates):
    """指数与宽度（仅取站点涉及的日期，缺失为 None）。"""
    if not dates:
        return {}, {}
    db = _db()
    with db._cursor() as cur:
        cur.execute("""SELECT quote_date, index_name, price, change_pct FROM index_quotes
                       WHERE quote_date = ANY(%s::date[])""", (list(dates),))
        idx = {}
        for d, name, price, chg in cur.fetchall():
            idx.setdefault(d, {})[name] = (float(price) if price is not None else None,
                                           float(chg) if chg is not None else None)
        cur.execute("""SELECT width_date, up, down FROM market_width
                       WHERE width_date = ANY(%s::date[])""", (list(dates),))
        width = {d: (int(u or 0), int(dn or 0)) for d, u, dn in cur.fetchall()}
    return idx, width


def load_dashboard():
    """数据看板数据：指数收盘、市场宽度、ETF 资金流（均按日期升序）。"""
    db = _db()
    with db._cursor() as cur:
        cur.execute("""SELECT quote_date, index_name, price FROM index_quotes
                       WHERE index_name IN ('上证指数', '创业板指') ORDER BY quote_date""")
        idx_rows = cur.fetchall()
        cur.execute("SELECT width_date, up, down FROM market_width ORDER BY width_date")
        width_rows = cur.fetchall()
        cur.execute("""SELECT flow_date, name, amount FROM etf_flows
                       ORDER BY flow_date, name""")
        etf_rows = cur.fetchall()

    # 指数：日期并集 + 每指数序列
    dates = sorted({r[0] for r in idx_rows})
    pos = {d: i for i, d in enumerate(dates)}
    series = {}
    for d, name, price in idx_rows:
        s = series.setdefault(name, [None] * len(dates))
        # 库内存在 price=0 的坏点（2026-08-31 创业板指），直接画会把折线拽到 0，
        # 按缺失处理（缺口断线），不猜真实值。
        if price is not None and float(price) > 0:
            s[pos[d]] = float(price)

    wdates = [r[0] for r in width_rows]
    wseries = [round(r[1] * 100.0 / (r[1] + r[2]), 1) if (r[1] + r[2]) else None
               for r in width_rows]

    edates = sorted({r[0] for r in etf_rows})
    epos = {d: i for i, d in enumerate(edates)}
    eseries = {}
    for d, name, amt in etf_rows:
        s = eseries.setdefault(name, [None] * len(edates))
        s[epos[d]] = float(amt) if amt is not None else None

    return {"dates": dates, "series": series,
            "wdates": wdates, "wseries": wseries,
            "edates": edates, "eseries": eseries}


def render_data(dash, reports):
    from ra.infra.charts import line_chart

    def _d(xs):
        return [str(x)[5:] for x in xs]          # MM-DD

    charts = []
    if len(dash["dates"]) > 1 and dash["series"]:
        charts.append(line_chart({
            "dates": _d(dash["dates"]),
            "lines": [{"name": k, "values": v} for k, v in dash["series"].items()],
            "title": "指数收盘（库内 index_quotes）",
            "note": "缺口不连线：采集未覆盖的日期留空",
            "width": 880, "plot_h": 230,
        }))
    if len(dash["wdates"]) > 1:
        charts.append(line_chart({
            "dates": _d(dash["wdates"]),
            "lines": [{"name": "上涨家数占比", "values": dash["wseries"], "color": "#d63031"}],
            "title": "市场宽度（上涨家数 %）",
            "note": "50% 为多空分界；库内 market_width 表",
            "y_unit": "%", "width": 880, "plot_h": 180,
        }))
    if len(dash["edates"]) > 1 and dash["eseries"]:
        charts.append(line_chart({
            "dates": _d(dash["edates"]),
            "lines": [{"name": k, "values": v} for k, v in dash["eseries"].items()],
            "title": "ETF 资金流（亿元，正为净流入）",
            "note": "核心池 4 只；不含 510050/510310（国家队主力标的，待补）",
            "y_unit": "亿", "zero_line": True, "width": 880, "plot_h": 200,
        }))

    body = "".join(f'<div class="s-panel">{c}</div>' for c in charts) or \
           '<div class="s-empty">库内数据不足，暂无法绘图</div>'

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据看板 · A股操作指引</title><style>{SHELL_CSS}</style></head><body>
<div class="s-layout">
{_sidebar(reports, page="data")}
<main><div class="s-wrapidx">
<div class="s-hero"><h2>数据看板</h2>
<div class="s-meta">指数 {len(dash["dates"])} 个交易日 ｜ 宽度 {len(dash["wdates"])} 日 ｜ ETF {len(dash["edates"])} 日</div></div>
{body}
</div></main></div></body></html>'''


# ---------------------------------------------------------------- CSS 隔离

def scope_css(css: str, scope: str = ".rp") -> str:
    """把报告 CSS 的所有选择器前缀化，避免全局 `*`/`body` 规则污染外壳。

    报告 CSS 由本项目生成，格式稳定：无 @media / @keyframes，扁平规则。
    `*` / `html` / `body` 三条映射到 scope 本身（字体、背景、盒模型继承）。
    """
    if not css:
        return ""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel_raw, decl = m.group(1), m.group(2)
        sel_raw = sel_raw.strip()
        if not sel_raw or sel_raw.startswith("@"):
            continue
        parts = []
        for sel in sel_raw.split(","):
            sel = sel.strip()
            if not sel:
                continue
            if sel in ("*", "html", "body"):
                parts.append(scope + (" *" if sel == "*" else ""))
            elif sel.startswith(scope):
                parts.append(sel)
            else:
                parts.append(f"{scope} {sel}")
        if parts:
            out.append(f"{', '.join(parts)}{{{decl}}}")
    return "\n".join(out)


def split_report_html(html: str):
    """拆成 (内联 CSS, body 内容)。取不到 <body> 时整段当内容。"""
    if not html:
        return "", ""
    css = ""
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)
    if m:
        css = m.group(1)
    b = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    body = b.group(1) if b else html
    body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.S)
    return css, body.strip()


# ---------------------------------------------------------------- 渲染

SHELL_CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#1d2129;--muted:#86909c;--line:#e5e6eb;
--brand:#3C3489;--brand2:#534AB7;--up:#d63031;--down:#00a865}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.7}
a{color:inherit;text-decoration:none}
.s-layout{display:flex;min-height:100vh}
.s-side{width:264px;flex:0 0 264px;background:#fff;border-right:1px solid var(--line);
  padding:20px 0;position:sticky;top:0;height:100vh;overflow-y:auto}
.s-side h1{font-size:15px;padding:0 20px 4px;color:var(--brand)}
.s-side .s-sub{font-size:11.5px;color:var(--muted);padding:0 20px 14px;border-bottom:1px solid var(--line);margin-bottom:10px}
.s-side .s-filters{display:flex;flex-wrap:wrap;gap:6px;padding:0 16px 12px}
.s-side .s-filters button{font:inherit;font-size:12px;padding:3px 10px;border:1px solid var(--line);
  background:#fff;border-radius:20px;cursor:pointer;color:#4e5969}
.s-side .s-filters button.s-on{background:var(--brand);color:#fff;border-color:var(--brand)}
.s-side .s-list{padding:0 10px}
.s-side .s-month{font-size:11px;color:var(--muted);padding:10px 10px 4px;letter-spacing:.5px}
.s-side .s-item{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:8px;font-size:13px}
.s-side .s-item:hover{background:#f2f3f5}
.s-side .s-item.cur{background:#eef0ff;color:var(--brand);font-weight:600}
.s-side .s-item .s-d{font-variant-numeric:tabular-nums;color:#4e5969}
.s-side .s-item.cur .s-d{color:var(--brand)}
.s-dot{width:6px;height:6px;border-radius:50%;flex:0 0 6px;background:#c9cdd4}
.b-morning{background:#534AB7}.b-intraday{background:#e67e22}
.b-evening{background:#2f6fd0}.b-weekly{background:#8a5a2b}
main{flex:1;min-width:0;padding:0 0 60px}
.s-topbar{position:sticky;top:0;z-index:9;background:rgba(244,245,247,.94);
  backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:10px 26px;
  display:flex;align-items:center;gap:14px;font-size:13px}
.s-topbar .s-nav{display:flex;gap:8px;margin-left:auto}
.s-topbar .s-nav a{padding:4px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;color:#4e5969}
.s-topbar .s-nav a:hover{border-color:var(--brand2);color:var(--brand)}
.s-tag{display:inline-block;font-size:11.5px;padding:1px 9px;border-radius:20px;color:#fff;background:#86909c}
.s-wrapidx{max-width:1000px;margin:0 auto;padding:0 26px}
.s-hero{background:linear-gradient(135deg,#3C3489,#534AB7);color:#fff;border-radius:14px;
  padding:22px 26px;margin:20px 0 18px}
.s-hero h2{font-size:19px;margin-bottom:6px}
.s-hero .s-meta{font-size:12.5px;opacity:.88}
.s-stats{display:flex;gap:26px;margin-top:14px;flex-wrap:wrap}
.s-stats .s{font-size:12px;opacity:.85}
.s-stats .s b{display:block;font-size:20px;font-weight:700;opacity:1;margin-top:2px}
.s-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 18px;
  margin-bottom:10px;display:flex;gap:16px;align-items:flex-start}
.s-card:hover{border-color:#c9cdd4;box-shadow:0 2px 10px rgba(0,0,0,.04)}
.s-card .s-date{flex:0 0 88px;font-variant-numeric:tabular-nums}
.s-card .s-date b{font-size:15px;display:block}
.s-card .s-date span{font-size:11.5px;color:var(--muted)}
.s-card .s-body{flex:1;min-width:0}
.s-card .s-body p{font-size:13px;color:#4e5969;margin-top:5px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.s-card .s-nums{flex:0 0 128px;text-align:right;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.s-up{color:var(--up);font-weight:600}.s-down{color:var(--down);font-weight:600}
.s-empty{color:var(--muted);font-size:13px;padding:40px 0;text-align:center}
.s-panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px;overflow-x:auto}
.s-side .s-nav{margin:0 10px 12px;display:flex;gap:6px}
.s-side .s-nav a{flex:1;text-align:center;font-size:12.5px;padding:6px 0;border:1px solid var(--line);border-radius:8px;color:#4e5969}
.s-side .s-nav a.s-on{background:var(--brand);color:#fff;border-color:var(--brand)}
.rp{max-width:1000px;margin:0 auto;padding:0 26px}
@media(max-width:900px){.s-side{display:none}.s-card .s-nums{display:none}}
"""


def _e(s):
    return _html.escape(str(s if s is not None else ""))


def _fmt_chg(v):
    if v is None:
        return '<span style="color:#86909c">—</span>'
    cls = "s-up" if v > 0 else ("s-down" if v < 0 else "")
    sign = "+" if v > 0 else ""
    return f'<span class="{cls}">{sign}{v:.2f}%</span>'


def _sidebar(reports, cur_key=None, with_filter=False, page="index", prefix=""):
    """左侧历史列表（按月份分组）。cur_key = (date, type)。"""
    by_month = {}
    for r in reports:
        by_month.setdefault(str(r["date"])[:7], []).append(r)
    out = ['<aside class="s-side">',
           '<h1>A股操作指引 · 历史</h1>',
           f'<div class="s-sub">{len(reports)} 份报告</div>',
           '<div class="s-nav">',
           f'<a href="{prefix}index.html" class="{"s-on" if page == "index" else ""}">报告列表</a>',
           f'<a href="{prefix}data.html" class="{"s-on" if page == "data" else ""}">数据看板</a>',
           '</div>']
    if with_filter:
        out.append('<div class="s-filters">'
                   '<button class="s-on" data-f="all">全部</button>'
                   + "".join(f'<button data-f="{t}">{t}</button>' for t in TYPE_ORDER)
                   + '</div>')
    out.append('<div class="s-list">')
    for month in sorted(by_month, reverse=True):
        out.append(f'<div class="s-month">{month}</div>')
        for r in sorted(by_month[month], key=lambda x: (str(x["date"]), x["type"]), reverse=True):
            key = (str(r["date"]), r["type"])
            cur = " cur" if key == cur_key else ""
            href = f'{prefix}r/{r["date"]}-{r["type"]}.html'
            out.append(
                f'<a class="s-item{cur}" href="{href}" data-t="{r["type"]}">'
                f'<i class="s-dot {TYPE_BADGE.get(r["type"], "")}"></i>'
                f'<span class="s-d">{str(r["date"])[5:]}</span>'
                f'<span>{r["type"]}</span></a>')
    out.append("</div></aside>")
    return "".join(out)


def render_index(reports, idx, width):
    cards = []
    for r in reports:
        d = r["data_date"] or r["date"]   # 盘前报告锚定上一交易日，指标按基准日取
        q = (idx.get(d) or {})
        sh = q.get("上证指数") or q.get("上证") or None
        w = width.get(d)
        nums = []
        if sh and sh[0] is not None:
            nums.append(f'{sh[0]:.2f} {_fmt_chg(sh[1])}')
        if w:
            tot = (w[0] + w[1]) or 1
            nums.append(f'宽度 <b>{w[0]}/{w[1]}</b>（{w[0] * 100 // tot}% 涨）')
        cards.append(f'''<a class="s-card" href="r/{d}-{r["type"]}.html" data-t="{r["type"]}">
  <div class="s-date"><b>{str(d)[5:]}</b><span>{str(d)[:4]}</span></div>
  <div class="s-body">
    <span class="s-tag {TYPE_BADGE.get(r["type"], "")}">{r["type"]}</span>
    <p>{_e(r["core"] or "（该期无核心结论摘要）")}</p>
  </div>
  <div class="s-nums">{"<br>".join(nums) if nums else "—"}</div>
</a>''')

    dates = [str(r["date"]) for r in reports]
    span = f"{min(dates)} ~ {max(dates)}" if dates else "—"
    stat = {}
    for r in reports:
        stat[r["type"]] = stat.get(r["type"], 0) + 1
    stat_html = "".join(f'<div class="s-stat">{k}<b>{v}</b></div>' for k, v in stat.items())

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股操作指引 · 历史报告</title><style>{SHELL_CSS}</style></head><body>
<div class="s-layout">
{_sidebar(reports, with_filter=True, page="index")}
<main>
  <div class="s-wrapidx">
    <div class="s-hero">
      <h2>A股操作指引 · 历史报告</h2>
      <div class="s-meta">{_e(span)} ｜ 共 {len(reports)} 份 ｜ 数据源：PostgreSQL daily_reports</div>
      <div class="s-stats">{stat_html}</div>
    </div>
    <div id="list">{"".join(cards) if cards else '<div class="s-empty">库内暂无报告</div>'}</div>
  </div>
</main></div>
<script>
document.querySelectorAll('.filters button').forEach(function(b){{
  b.onclick=function(){{
    document.querySelectorAll('.filters button').forEach(function(x){{x.classList.remove('s-on')}});
    b.classList.add('s-on');
    var f=b.dataset.f;
    document.querySelectorAll('#list .card').forEach(function(c){{
      c.style.display=(f==='all'||c.dataset.t===f)?'':'none';
    }});
  }};
}});
</script></body></html>'''


def render_report(rep, reports, prev_r, next_r, idx, width):
    css, body = split_report_html(rep["html"])
    d = rep["data_date"] or rep["date"]
    q = idx.get(d) or {}
    sh = q.get("上证指数") or q.get("上证") or None
    meta = [f'{d} {rep["type"]}']
    if rep["data_date"]:
        meta.append(f'行情基准 {rep["data_date"]}')
    if sh and sh[0] is not None:
        meta.append(f'上证 {sh[0]:.2f}')
    if rep["created"]:
        meta.append(f'生成于 {rep["created"].strftime("%m-%d %H:%M")}')

    nav = []
    if prev_r:
        nav.append(f'<a href="{prev_r["date"]}-{prev_r["type"]}.html">← {str(prev_r["date"])[5:]} {prev_r["type"]}</a>')
    else:
        nav.append('<a href="../index.html">← 全部</a>')
    if next_r:
        nav.append(f'<a href="{next_r["date"]}-{next_r["type"]}.html">{str(next_r["date"])[5:]} {next_r["type"]} →</a>')

    side = _sidebar(reports, cur_key=(str(d), rep["type"]), page="", prefix="../")

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{d} {rep["type"]} · A股操作指引</title>
<style>{SHELL_CSS}</style>
<style>{scope_css(css)}</style>
<style>.rp{{padding:0 26px}}.rp .wrap{{max-width:none;padding:18px 0 40px;margin:0}}</style>
</head><body>
<div class="s-layout">
{side}
<main>
  <div class="s-topbar">
    <span class="s-tag {TYPE_BADGE.get(rep["type"], "")}">{rep["type"]}</span>
    <span style="color:#4e5969">{" ｜ ".join(_e(x) for x in meta)}</span>
    <span class="s-nav">{"".join(nav)}</span>
  </div>
  <div class="rp">{body}</div>
</main></div></body></html>'''


# ---------------------------------------------------------------- 入口

def build(out_dir="site", limit=0):
    reports = load_reports(limit=limit)
    if not reports:
        raise SystemExit("[site] daily_reports 里没有可用报告（需 report_type 非空且未标记重复）")
    idx, width = load_metrics({str(r["date"]) for r in reports})

    out = Path(out_dir)
    if not out.is_absolute():
        out = ROOT / out
    (out / "r").mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "assets" / "site.css").write_text(SHELL_CSS, encoding="utf-8")

    (out / "index.html").write_text(render_index(reports, idx, width), encoding="utf-8")
    try:
        dash = load_dashboard()
        (out / "data.html").write_text(render_data(dash, reports), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[site] ⚠️ 数据看板生成失败（不影响报告页）: {str(exc)[:80]}")

    ordered = sorted(reports, key=lambda r: (str(r["date"]), r["type"]))
    n = 1
    for i, rep in enumerate(ordered):
        prev_r = ordered[i - 1] if i > 0 else None
        next_r = ordered[i + 1] if i + 1 < len(ordered) else None
        html = render_report(rep, reports, prev_r, next_r, idx, width)
        (out / "r" / f'{rep["date"]}-{rep["type"]}.html').write_text(html, encoding="utf-8")
        n += 1
    print(f"[site] 已生成 {n} 个页面 → {out}")
    print(f"[site] 打开: {out / 'index.html'}")
    return out


def serve(out: Path, port=8765):
    """起本地 HTTP 服务。

    ⚠️ 为什么需要它：预览面板给每个文件单独的静态根，**相对链接会断**；
    站点的价值在「能点着翻」，所以用 http 服务提供整站。
    """
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"[site] 服务已启动: http://127.0.0.1:{port}/index.html")
        print("[site] Ctrl+C 结束")
        httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="生成历史报告静态站点")
    ap.add_argument("--out", default="site", help="输出目录，默认 site/")
    ap.add_argument("--limit", type=int, default=0, help="只生成最近 N 份，默认全部")
    ap.add_argument("--serve", action="store_true", help="生成后起本地服务（相对链接才可点）")
    ap.add_argument("--port", type=int, default=8765, help="--serve 端口，默认 8765")
    ap.add_argument("--no-build", action="store_true", help="配合 --serve：不重新生成，直接服务现有站点")
    args = ap.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if not args.no_build:
        out = build(args.out, args.limit)
    if args.serve:
        serve(out, args.port)


if __name__ == "__main__":
    main()
