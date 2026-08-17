#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股舆情操作指引 · 晚报生成器（动态数据驱动版）

优化要点：
  1. 移除所有硬编码市场数据，改为从 JSON 数据文件读取
  2. 数据采集与报告渲染完全解耦——采集由 WorkBuddy WebSearch 完成
  3. 支持 --data 参数指定数据文件路径
  4. 自动计算涨跌颜色、条形图比例、共振重数等

用法:
    python generate_evening_report.py --data report_data.json
    python generate_evening_report.py              # 使用默认 data/evening_data.json

数据文件格式见 create_data_template()
"""
import json
import sys
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_DATA_PATH = BASE_DIR / "data" / "evening_data.json"

# 涨跌颜色（中国惯例：红涨绿跌）
COLOR_UP = "#d63031"
COLOR_DOWN = "#00a865"
COLOR_NEUTRAL = "#e67e22"


def load_data(data_path):
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def up_down_cls(v):
    if v > 0:
        return "up"
    elif v < 0:
        return "down"
    return "muted"


def sign(v):
    return "+" if v > 0 else ""


def bar_color(v, invert=False):
    """返回条形图颜色。invert=True 时用于中概股等反向标的。"""
    if invert:
        return COLOR_DOWN if v > 0 else COLOR_UP
    return COLOR_UP if v > 0 else COLOR_DOWN


def render_us_market(us_market):
    """渲染隔夜美股条形图"""
    if not us_market:
        return '<p class="muted">无数据</p>'
    max_abs = max(abs(x["pct"]) for x in us_market)
    bars = ""
    for item in us_market:
        name, pct, signal = item["name"], item["pct"], item.get("signal", "")
        w = abs(pct) / max_abs * 100 if max_abs else 0
        invert = item.get("invert", False)
        color = bar_color(pct, invert)
        bars += (
            f'<div class="bar-row">'
            f'<div class="bar-name">{name}</div>'
            f'<div class="bar-wrap"><div class="bar-fill" style="width:{w:.1f}%;background:{color};"></div></div>'
            f'<div class="bar-val {up_down_cls(pct)}">{sign(pct)}{pct}%</div>'
            f'</div>'
        )
    # 表格
    rows = ""
    for item in us_market:
        name, pct, signal = item["name"], item["pct"], item.get("signal", "")
        rows += f'<tr><td>{name}</td><td class="{up_down_cls(pct)}">{sign(pct)}{pct}%</td><td>{signal}</td></tr>'
    return f'<table><thead><tr><th>标的</th><th>涨跌</th><th>关键信号</th></tr></thead><tbody>{rows}</tbody></table><div style="margin-top:12px;">{bars}</div>'


def render_macro(macro):
    """渲染宏观指标表"""
    if not macro:
        return '<p class="muted">无数据</p>'
    rows = ""
    for item in macro:
        name = item["name"]
        current = item.get("current", "—")
        prev = item.get("prev", "—")
        judgment = item.get("judgment", "—")
        jcls = "up" if "鸽" in judgment or "降" in judgment or "回落" in judgment else ("down" if "鹰" in judgment or "升" in judgment else "muted")
        rows += f'<tr><td>{name}</td><td><b>{current}</b></td><td>{prev}</td><td class="{jcls}">{judgment}</td></tr>'
    return f'<table><thead><tr><th>指标</th><th>最新值</th><th>前值</th><th>判断</th></tr></thead><tbody>{rows}</tbody></table>'


def render_macro_chain(chain):
    """渲染宏观传导链"""
    if not chain:
        return '<p class="muted">无数据</p>'
    rows = ""
    for node in chain:
        name = node["name"]
        value = node.get("value", "—")
        direction = node.get("direction", "—")
        impact = node.get("impact", "—")
        dcls = {"偏鸽": "b-blue", "偏鹰": "b-green", "警戒": "b-red", "震荡": "b-orange"}.get(direction, "b-purple")
        rows += f'<tr><td>{name}</td><td>{value}</td><td><span class="badge {dcls}">{direction}</span></td><td>{impact}</td></tr>'
    return f'<table><thead><tr><th>节点</th><th>当前值</th><th>方向</th><th>对A股影响</th></tr></thead><tbody>{rows}</tbody></table>'


def render_events(events):
    """渲染地缘事件表"""
    if not events:
        return '<p class="muted">无数据</p>'
    rows = ""
    for ev in events:
        name = ev["name"]
        status = ev.get("status", "—")
        impact = ev.get("impact", "—")
        scls = {"危险": "b-red", "活跃": "b-red", "偏空": "b-green", "偏多": "b-red", "中性": "b-orange", "利好": "b-red"}.get(status, "b-orange")
        rows += f'<tr><td>{name}</td><td><span class="badge {scls}">{status}</span></td><td>{impact}</td></tr>'
    return f'<table><thead><tr><th>事件</th><th>状态</th><th>影响</th></tr></thead><tbody>{rows}</tbody></table>'


def render_etf_flows(flows):
    """渲染ETF资金流向"""
    if not flows:
        return '<p class="muted">无数据</p>'
    rows = ""
    for item in flows:
        name = item["name"]
        value = item.get("value", "—")
        signal = item.get("signal", "—")
        scls = {"净流入": "b-red", "净流出": "b-orange", "蓝筹减仓": "b-orange", "科技主线确认": "b-red", "硬科技持续吸金": "b-red"}.get(signal, "b-purple")
        rows += f'<tr><td>{name}</td><td><b>{value}</b></td><td><span class="badge {scls}">{signal}</span></td></tr>'
    return f'<table><thead><tr><th>指标</th><th>数据</th><th>信号</th></tr></thead><tbody>{rows}</tbody></table>'


def render_sectors(sectors):
    """渲染板块涨跌"""
    if not sectors:
        return '<p class="muted">无数据</p>'
    up_items = "".join(
        f'<span class="badge b-red">{s["name"]} {sign(s["pct"])}{s["pct"]}%</span>'
        for s in sectors if s["pct"] > 0
    )
    down_items = "".join(
        f'<span class="badge b-green">{s["name"]} {s["pct"]}%</span>'
        for s in sectors if s["pct"] < 0
    )
    return f'<p style="margin:6px 0;"><b style="color:#d63031;">领涨：</b>{up_items or "<span class=\"muted\">无</span>"}</p><p style="margin:6px 0;"><b style="color:#00a865;">领跌：</b>{down_items or "<span class=\"muted\">无</span>"}</p>'


def render_resonance(signals):
    """渲染共振信号"""
    if not signals:
        return '<p class="muted">无数据</p>'
    rows = ""
    for sig in signals:
        name = sig["name"]
        level = sig.get("level", 0)
        sources = sig.get("sources", "")
        confidence = sig.get("confidence", "")
        if level >= 5:
            bcls = "b-red"
        elif level >= 4:
            bcls = "b-blue"
        elif level >= 3:
            bcls = "b-orange"
        else:
            bcls = "b-green"
        rows += f'<tr><td><b>{name}</b></td><td><span class="badge {bcls}">{level}重共振</span></td><td>{sources}</td></tr>'
    return f'<table><thead><tr><th>方向</th><th>共振重数</th><th>来源交叉</th></tr></thead><tbody>{rows}</tbody></table>'


def render_stocks(stocks):
    """渲染自选股卡片"""
    if not stocks:
        return '<p class="muted">无数据</p>'
    cards = ""
    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", "")
        sector = s.get("sector", "")
        price = s.get("price", 0)
        pct = s.get("pct", 0)
        logic = s.get("logic", "")
        action = s.get("action", "—")
        action_cls = {"加仓首选": "b-red", "持有/回调加仓": "b-red", "持有": "b-blue", "观望": "b-orange", "回避": "b-green", "谨慎": "b-green"}.get(action, "b-blue")
        pe = s.get("pe", 0)
        d5 = s.get("d5", 0)
        d20 = s.get("d20", 0)
        cards += f'''
        <div class="stock-card">
          <div class="stock-title">
            <div><b>{name}</b> <span class="muted">{code}</span>　<span class="tag">{sector}</span></div>
            <span class="badge {action_cls}">{action}</span>
          </div>
          <div style="font-size:13px;margin:4px 0;">现价 <b>{price}</b> <span class="{up_down_cls(pct)}">{sign(pct)}{pct}%</span>　PE {pe}　5日{sign(d5)}{d5}%　20日{d20}%</div>
          <p class="stock-logic">{logic}</p>
        </div>'''
    return cards


def render_index_table(indices):
    """渲染A股指数表"""
    if not indices:
        return '<p class="muted">无数据</p>'
    rows = ""
    for idx in indices:
        name = idx["name"]
        price = idx.get("price", 0)
        pct = idx.get("pct", 0)
        rows += f'<tr><td>{name}</td><td>{price}</td><td class="{up_down_cls(pct)}">{sign(pct)}{pct}%</td></tr>'
    return f'<table><thead><tr><th>指数</th><th>收盘</th><th>涨跌幅</th></tr></thead><tbody>{rows}</tbody></table>'


def render_strategy(strategy):
    """渲染操作策略"""
    if not strategy:
        return '<p class="muted">无数据</p>'
    rows = ""
    for item in strategy:
        dim = item["dimension"]
        tactic = item["tactic"]
        rows += f'<tr><td><b>{dim}</b></td><td>{tactic}</td></tr>'
    return f'<table><thead><tr><th style="width:18%">维度</th><th>策略</th></tr></thead><tbody>{rows}</tbody></table>'


def render_conclusion(conclusion):
    """渲染核心结论"""
    if not conclusion:
        return ""
    items = ""
    for c in conclusion:
        label = c.get("label", "")
        value = c.get("value", "")
        color = c.get("color", "#1a2b4a")
        detail = c.get("detail", "")
        items += f'''
        <div class="conclusion-item">
          <div class="label">{label}</div>
          <div class="value" style="color:{color};">{value}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">{detail}</div>
        </div>'''
    return f'<div class="conclusion-grid">{items}</div>'


def render_kol_views(kol_views):
    """渲染大V观点"""
    if not kol_views:
        return '<p class="muted">无数据</p>'
    html = ""
    for v in kol_views:
        title = v.get("title", "")
        body = v.get("body", "")
        action = v.get("action", "")
        action_html = f'<div class="pt-action">{action}</div>' if action else ""
        html += f'<div class="point"><div class="pt-title">{title}</div><div class="pt-body">{body}</div>{action_html}</div>'
    return html


def load_diagnosis_fragment():
    """加载个股见顶诊断 HTML 片段（由 run_diagnosis.py 生成）"""
    frag_path = BASE_DIR / "data" / "diagnosis_fragment.html"
    if frag_path.exists():
        with open(frag_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def generate_html(data):
    """从数据字典生成完整 HTML 晚报"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_date = data.get("report_date", today)
    report_title = data.get("report_title", "A股舆情操作指引 · 晚报")

    meta_tags = "".join(f'<span>{t}</span>' for t in data.get("meta_tags", []))

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report_title} · {report_date}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1d2129;line-height:1.8}}
.wrap{{max-width:960px;margin:0 auto;padding:24px 20px 60px}}
.header{{background:linear-gradient(135deg,#3C3489 0%,#534AB7 100%);color:#fff;border-radius:14px;padding:28px 30px;margin-bottom:22px}}
.header h1{{font-size:24px;font-weight:700;margin-bottom:6px}}
.header .sub{{font-size:13px;opacity:.82}}
.header .meta{{display:flex;gap:14px;margin-top:14px;font-size:12px;flex-wrap:wrap}}
.header .meta span{{background:rgba(255,255,255,.14);padding:4px 14px;border-radius:20px}}
.card{{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
.card h2{{font-size:16px;font-weight:700;color:#3C3489;margin-bottom:14px;padding-left:10px;border-left:4px solid #534AB7}}
.card h3{{font-size:14px;font-weight:600;color:#333;margin:14px 0 8px}}
.badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin:2px}}
.b-red{{background:#fde8e8;color:#d63031}}
.b-blue{{background:#e8f0fe;color:#1967d2}}
.b-orange{{background:#fff3e0;color:#e67e22}}
.b-green{{background:#e8f8f0;color:#00a865}}
.b-purple{{background:#EEEDFE;color:#3C3489}}
.tag{{display:inline-block;background:#f0f2f5;color:#555;padding:2px 8px;border-radius:10px;font-size:11px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}}
th{{background:#f0f2f5;color:#555;font-weight:600;padding:8px 10px;text-align:left;border-bottom:2px solid #e0e3e8}}
td{{padding:8px 10px;border-bottom:1px solid #eef0f3}}
.up{{color:#d63031;font-weight:600}}
.down{{color:#00a865;font-weight:600}}
.muted{{color:#999}}
.point{{padding:12px 14px;background:#f8f7ff;border-radius:8px;margin-bottom:10px;border-left:3px solid #AFA9EC}}
.point .pt-title{{font-size:14px;font-weight:600;color:#3C3489;margin-bottom:4px}}
.point .pt-body{{font-size:13px;color:#444}}
.point .pt-action{{font-size:12px;color:#e67e22;margin-top:4px;font-weight:500}}
.conclusion-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.conclusion-item{{background:#fafbfc;border-radius:10px;padding:14px;border:1px solid #eef0f3}}
.conclusion-item .label{{font-size:12px;color:#888;margin-bottom:4px}}
.conclusion-item .value{{font-size:14px;font-weight:600;color:#1a2b4a}}
.stock-card{{border:1px solid #eef0f3;border-radius:10px;padding:16px;margin-bottom:12px}}
.stock-title{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}}
.stock-logic{{font-size:13px;color:#444;margin-bottom:6px}}
.bar-row{{display:flex;align-items:center;margin:5px 0;font-size:13px}}
.bar-name{{width:110px;text-align:right;padding-right:10px}}
.bar-wrap{{flex:1;background:#f0f2f5;border-radius:4px;height:16px;overflow:hidden}}
.bar-fill{{height:100%}}
.bar-val{{width:80px;padding-left:10px}}
.disclaimer{{background:#fff;border:1px solid #e8eaed;border-radius:10px;padding:18px 22px;font-size:12px;color:#666;line-height:1.8}}
.disclaimer strong{{color:#d63031}}
</style>
</head>
<body>
<div class="wrap">

<div class="header">
<h1>{report_title}</h1>
<div class="sub">{data.get("subtitle", "微博舆情 + 全球 + 宏观 + 事件 + 资金 + 技术 + 共振 + 自选股")}</div>
<div class="meta">
<span>报告日期：{report_date}（盘后）</span>
<span>生成时间：{now}</span>
{meta_tags}
</div>
</div>

<div class="card">
<h2>核心结论</h2>
{render_conclusion(data.get("conclusion", []))}
</div>

<div class="card">
<h2>一、隔夜美股</h2>
<p style="font-size:13px;color:#666;margin-bottom:12px;">{data.get("us_market_summary", "")}</p>
{render_us_market(data.get("us_market", []))}
</div>

<div class="card">
<h2>二、CPI与宏观</h2>
{render_macro(data.get("macro", []))}
{render_kol_views(data.get("macro_views", []))}
</div>

<div class="card">
<h2>三、宏观传导链监控</h2>
{render_macro_chain(data.get("macro_chain", []))}
{render_kol_views(data.get("macro_chain_views", []))}
</div>

<div class="card">
<h2>四、地缘政治与原油</h2>
{render_events(data.get("events", []))}
{render_kol_views(data.get("event_views", []))}
</div>

<div class="card">
<h2>五、ETF资金流向</h2>
{render_etf_flows(data.get("etf_flows", []))}
{render_kol_views(data.get("etf_views", []))}
</div>

<div class="card">
<h2>六、A股板块表现</h2>
{render_sectors(data.get("sectors", []))}
{render_kol_views(data.get("sector_views", []))}
</div>

<div class="card">
<h2>七、大V观点提炼</h2>
{render_kol_views(data.get("kol_views", []))}
</div>

<div class="card">
<h2>八、共振信号（多源交叉验证）</h2>
{render_resonance(data.get("resonance", []))}
</div>

<div class="card">
<h2>九、个股见顶诊断（5维度评分）</h2>
{load_diagnosis_fragment() or '<p class="muted">诊断数据未生成，请先运行 run_diagnosis.py</p>'}
</div>

<div class="card">
<h2>十、自选股操作指引</h2>
{render_stocks(data.get("stocks", []))}
</div>

<div class="card">
<h2>十一、今日操作策略</h2>
{render_strategy(data.get("strategy", []))}
</div>

<div class="card">
<h2>A股主要指数（{report_date} 收盘）</h2>
{render_index_table(data.get("indices", []))}
<p class="muted" style="font-size:12px;margin-top:8px;">{data.get("index_note", "")}</p>
</div>

<div class="disclaimer">
<strong>免责声明</strong>：以上内容基于公开数据、大V观点及量化规则自动生成，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
</body>
</html>'''
    return html


def main():
    data_path = DEFAULT_DATA_PATH
    if "--data" in sys.argv:
        idx = sys.argv.index("--data")
        if idx + 1 < len(sys.argv):
            data_path = sys.argv[idx + 1]

    if not os.path.exists(data_path):
        print(f"错误：数据文件不存在: {data_path}")
        print("请先通过 WorkBuddy WebSearch 采集数据并生成 JSON 文件。")
        sys.exit(1)

    print(f"加载数据: {data_path}")
    data = load_data(data_path)
    print(f"数据加载完成: {len(data.get('us_market', []))} 个美股标的, {len(data.get('stocks', []))} 只自选股")

    html = generate_html(data)

    today = datetime.now().strftime("%Y%m%d")
    output_path = BASE_DIR.parent / "reports" / f"A股舆情操作指引-晚报-{today}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n晚报已生成: {output_path}")
    print(f"文件大小: {output_path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
