#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 A股操作指引 9 章节报告（参数化日期与类型），并写入数据库。
用法: python build_report_20260816.py --date 2026-08-17 --type 早报|晚报|周报
"""
import json
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---- 参数：--date 报告日期(YYYY-MM-DD) / --type 报告类型(决定写入目录) ----
ap = argparse.ArgumentParser(description="生成 A股操作指引 9 章节报告")
ap.add_argument("--date", default="2026-08-17", help="报告日期 YYYY-MM-DD")
ap.add_argument("--type", default="早报", choices=["早报", "晚报", "周报"],
                help="报告类型：早报/晚报/周报，决定写入 reports/<类型>/ 目录")
_args = ap.parse_args()
TODAY = _args.date
REPORT_TYPE = _args.type
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

# 类型 → 盘前/盘后/周度标签（用于页眉与入库状态）
_TYPE_LABEL = {"早报": "盘前版", "晚报": "盘后版", "周报": "周度回顾"}
_TYPE_STATE = {"早报": "盘前", "晚报": "盘后", "周报": "周报"}
REPORT_LABEL = _TYPE_LABEL.get(REPORT_TYPE, "盘前版")
REPORT_STATE = _TYPE_STATE.get(REPORT_TYPE, "盘前")

# ---- 加载实时抓取结果 ----
with open(BASE_DIR / "weibo_posts.json", encoding="utf-8") as f:
    weibo_data = json.load(f)
with open(BASE_DIR / "diagnosis_20260816.json", encoding="utf-8") as f:
    diag_raw = json.load(f)

def diag_for(code):
    for d in diag_raw:
        if d["code"] == code:
            return d
    return None

# ---------- 数据：隔夜美股（8/15 凌晨收盘） ----------
us_market = [
    ("道琼斯", -0.20, "53732.41点", "三大指数集体收跌"),
    ("纳斯达克", -0.28, "26729.16点", "科技股涨跌不一"),
    ("标普500", -0.17, "7785.76点", "结束连续上涨"),
    ("费城半导体", -0.31, "—", "半导体板块整体承压"),
    ("闪迪(SanDisk)", 7.39, "—", "存储强势延续，本周累涨35.38%"),
    ("希捷科技", 5.65, "—", "存储链共振"),
    ("西部数据", 4.41, "—", "存储链共振"),
    ("美光科技", 2.30, "—", "存储链共振"),
    ("SK海力士", 0.40, "—", "存储龙头微涨"),
    ("博通(Broadcom)", -5.94, "—", "半导体设备/网络芯片走弱"),
    ("应用材料", -5.12, "—", "半导体设备股走弱"),
    ("Applied Optoelectronics", 15.00, "—", "光通信走高"),
    ("Lumentum", 5.00, "—", "光通信走高"),
    ("康宁(Corning)", 4.00, "—", "光通信走高"),
    ("英伟达", -0.06, "—", "基本平收"),
    ("特斯拉", 0.68, "—", "七巨头中少数上涨"),
    ("中概科技龙头", 1.27, "—", "腾讯ADR+2.23% 网易+2.01% 阿里+1.30%"),
]

# ---------- 数据：宏观 ----------
macro = [
    ("CPI同比(7月)", "3.4%", "3.5%", "回落·符合预期", "up"),
    ("CPI环比(7月)", "0.1%", "—", "环比恢复正增", "muted"),
    ("核心CPI同比", "2.5%", "2.6%", "2021年来最低·偏鸽", "up"),
    ("7月PPI", "环比持平", "预期+0.2%", "批发通胀温和", "up"),
    ("9月维持利率不变概率", "59.9%~68%", "—", "加息紧迫性下降", "up"),
    ("9月累计加息25bp概率", "40.1%", "—", "仍有加息风险", "down"),
    ("央行政策", "适度宽松", "—", "降息降准预期升温", "up"),
]

# ---------- 数据：宏观传导链（日元主导） ----------
# 主线：原油(触发) → 日本输入型通胀 → 央行加息(50-75bp,非25bp) → 抛美债压力 → FIMA押美债借美元干预 → 日元/套息平仓 → A股
macro_chain = [
    ("原油(触发)", "WTI82.4/布油88.8", "震荡", "伊朗地缘推升→日本能源90%依赖进口→输入型通胀", "b-orange"),
    ("日本加息预期", "9月76%/10月96%", "偏鹰", "三菱日联:25bp不够止跌日元,单次或50-75bp,尾端100bp", "b-red"),
    ("日本抛美债压力", "持美债>1.1万亿", "警戒", "单边干预筹资需抛美债→10Y美债YTD+57bp,长端承压", "b-orange"),
    ("FIMA回购工具", "押美债借美元", "启用", "8/3财务省宣布用FIMA干预,免抛美债=借美款干预汇率", "b-blue"),
    ("日元/联合干预", "USD/JPY 159", "警戒", "7月底-8/3美日韩联合干预163.99→155.2,涨势未守回159", "b-red"),
    ("美债收益率", "10Y 4.69%", "偏鹰", "长端上行压制科技估值;FIMA大量使用或反增美债供给", "b-green"),
    ("VIX", "14.66", "偏鸽", "恐慌低位,但套息平仓黑天鹅尚未被定价", "b-blue"),
]

# ---------- 数据：地缘/原油 ----------
geo = [
    ("霍尔木兹海峡", "仍受阻·未全面恢复", "伊朗要求赔偿、特朗普称已扫雷但通航未恢复", "b-red"),
    ("中东供应中断", "约60万桶/日", "EIA预计持续至2027年底；Q3停产或扩至660万桶/日", "b-red"),
    ("美国SPR", "降至2.987亿桶", "低于3亿桶，战略储备持续消耗", "b-orange"),
    ("OPEC月报(8/12)", "下调需求至58万桶/日", "2026全球石油需求增长连续第四次下调", "b-orange"),
    ("美油/布油(8/14)", "82.4 / 88.82美元", "美油+1.42% 布油+2.01%", "b-orange"),
]

# ---------- 数据：ETF资金流（ETF/代码/方向/方向cls/信号） ----------
etf = [
    ("沪深300ETF", "510300", "净赎回", "b-green", "宽基连续7日净流出超900亿，蓝筹减仓"),
    ("芯片ETF", "159995", "净申购", "b-red", "资金逆势布局"),
    ("半导体设备ETF国泰", "159516", "连续4日净流入超17亿", "b-red", "科技主线资金坚守"),
    ("科创50ETF", "588280", "五日转正", "b-red", "硬科技持续吸金"),
]

# ---------- 数据：自选股 ----------
stock_info = {
    "688668": {"name":"鼎通科技","sector":"连接器+液冷","action":"加仓首选","cls":"b-red",
        "logic":"1.6T连接器+液冷，终端英伟达/思科/戴尔；H1净利+59%，Q2环比+28%；外部光通信(AO+15%/Lumentum+5%)与800V催化共振。"},
    "688409": {"name":"富创精密","sector":"半导体设备零部件","action":"持有/回调加仓","cls":"b-blue",
        "logic":"入选MSCI中国指数；晶圆扩产带动设备/零部件需求；交付周期延长+价格提升直接受益；诊断趋势健康。"},
    "600641": {"name":"先导基电","sector":"离子注入机","action":"持有","cls":"b-blue",
        "logic":"半导体设备国产替代核心；中期扩产+设备订单增加。诊断评分最低(7.2)趋势健康，但个股波动大。"},
    "000725": {"name":"京东方A","sector":"面板+AI封装","action":"谨慎","cls":"b-orange",
        "logic":"面板周期回升+玻璃基载板对接AI先进封装；但诊断黄色预警·见顶确认，短期规避追高。"},
    "301392": {"name":"汇成真空","sector":"PVD设备","action":"观望","cls":"b-orange",
        "logic":"此前5日暴涨47%严重过热，非核心算力标的，独立性弱；诊断评分25.3仍安全但性价比下降。"},
    "688530": {"name":"欧莱新材","sector":"靶材","action":"回避","cls":"b-green",
        "logic":"题材与基本面脱节，PE远超行业；公告明确暂无磷化铟相关产品；仅题材驱动。"},
    "600580": {"name":"卧龙电驱","sector":"机器人电机","action":"谨慎","cls":"b-orange",
        "logic":"利好兑现阶段，投星提醒无新icon科技难涨；技术面多头但估值高，等待新催化。"},
}

# ---------- 唐史主任等微博 ----------
tangshi_today = [
    "【8/16 21:25】坚持AI是解决方案的观点不动摇，美国的数据开出来也是K型，后续可能越来越像Y型。越市场化，资源越向效率高的地方流动。生产关系会对生产力发展形成反扑，但无阻碍已经形成的浪潮。市场第一波普反接近尾声，后续更多需要注重阿尔法而不是寻求贝塔。",
    "【8/16 11:07】出去学习交流的忠告：1.董事长的听一半，公司概念多的、历史上图上不厚道的，再打对折；2.不要盯着漂亮的销售，忍不住就念一遍『再看吃跌停板』；3.不要轻易下结论，回来整理消化一下。",
]
touxing_asset = [
    "【8/17 10:00】全世界半导体都是周期股、AI驱动，至少2030年；三万亿外汇储备不如一把梭哈三星海力士，淡马锡就开始这么干了。",
    "【8/17 09:00】闪迪明确了回报计划，三星海力士/镁光接下来统统要发布史上最大回购计划，推动第二波主升浪。",
    "【8/17 07:30】台湾CCL龙头涨价20%已证实，电子布8/9月继续涨价，日本味之素ABF对华断供30%，PCB上游全产业链重新起航。",
    "【8/13 20:08】deepseek定价暴涨十倍，最大利好算力租赁。",
]
touxing_yeye = [
    "【8/15 06:10】镁光闪迪继续上涨，新云牛逼继续大涨。",
    "【8/14 08:15】闪迪FCF占市值FY27-30累计约1240亿(53-61%)，5年现金买下整家公司——美光所说。",
]

# ---------- 共振信号 ----------
resonance = [
    ("存储链", "5重共振", "b-red",
     "闪迪+7.4%/本周+35% + 美光+2.3% + 大爷『镁光闪迪继续上涨』 + 唐史AI主线不动摇 + 半导体/科创ETF持续吸金"),
    ("CPO/光通信", "5重共振", "b-red",
     "Applied Optoelectronics+15% + Lumentum+5% + A股CPO概念涨停(共进/亨通) + 投星『算力租赁最大利好』 + 800V/sic催化"),
    ("国产算力/设备", "3重共振", "b-blue",
     "富创精密入选MSCI + 先导基电国产替代 + 半导体设备ETF吸金"),
    ("风险项", "2重风险", "b-green",
     "半导体设备股走弱(博通-5.94%/应用材料-5.12%) + 美股三大指数收跌 + 日元159.3逼近160警戒"),
]

# ---------- A股指数（8/14 收盘） ----------
a_share = [
    ("上证指数", "3927.18", "0.01"),
    ("深证成指", "14354.31", "0.45"),
    ("创业板指", "3626.30", "1.12"),
]

def up_down(v):
    return "up" if v > 0 else ("down" if v < 0 else "muted")

def sign(v):
    return "+" if v > 0 else ""

def us_bar(name, pct, val, sig):
    w = abs(pct) / 7.39 * 100
    color = "#d63031" if pct > 0 else "#00a865"
    if pct == 0:
        color = "#888"
    return (f'<div class="bar-row"><div class="bar-name">{name}</div>'
            f'<div class="bar-wrap"><div class="bar-fill" style="width:{w:.1f}%;background:{color}"></div></div>'
            f'<div class="bar-val {up_down(pct)}">{sign(pct)}{pct}%</div></div>')

def stock_card(code):
    info = stock_info[code]
    d = diag_for(code)
    if d:
        score = f"{d['total_score']:.1f}"
        level = d["level"]
        trend = d["trend_status"]
    else:
        score, level, trend = "—", "—", "—"
    return f'''
    <div class="stock-card">
      <div class="stock-title">
        <div class="name">{info["name"]} <span class="muted">{code}</span>　<span class="tag">{info["sector"]}</span></div>
        <span class="badge {info["cls"]}">{info["action"]}</span>
      </div>
      <p class="stock-logic">{info["logic"]}</p>
      <p class="stock-meta">见顶诊断：评分 {score} | {level} | {trend}</p>
    </div>'''

# ---------- 宏观传导链 SVG 流程状态图（日元主导） ----------
def chain_svg():
    nodes = [
        ("原油(触发)","布油88.8","#e67e22"),
        ("日本加息","50-75bp","#d63031"),
        ("抛美债压力",">1.1万亿","#e67e22"),
        ("FIMA回购","押债借美元","#1967d2"),
        ("日元干预","159","#d63031"),
        ("套息平仓","unwind","#d63031"),
        ("A股传导","风险偏好","#1a2b4a"),
    ]
    n = len(nodes)
    bw, gap = 128, 18
    w = 16 + n*bw + (n-1)*gap + 16
    h = 110
    x0 = 16
    svg = [f'<svg class="chain-svg" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">']
    for i,(name,val,color) in enumerate(nodes):
        x = x0 + i*(bw+gap)
        svg.append(f'<rect x="{x}" y="20" width="{bw}" height="54" rx="8" fill="{color}" opacity="0.92"/>')
        svg.append(f'<text x="{x+bw/2}" y="44" fill="#fff" font-size="13" font-weight="700" text-anchor="middle">{name}</text>')
        svg.append(f'<text x="{x+bw/2}" y="62" fill="#fff" font-size="11" text-anchor="middle">{val}</text>')
        if i < len(nodes)-1:
            ax = x+bw+4
            svg.append(f'<line x1="{ax}" y1="47" x2="{ax+gap-4}" y2="47" stroke="#bbb" stroke-width="2" marker-end="url(#ar)"/>')
    svg.append('<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#bbb"/></marker></defs>')
    svg.append('</svg>')
    return "".join(svg)

# ================= 组装 HTML =================
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股舆情操作指引 · 9章节 · {TODAY}（{REPORT_LABEL}）</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1d2129;line-height:1.8}}
.wrap{{max-width:980px;margin:0 auto;padding:24px 20px 60px}}
.header{{background:linear-gradient(135deg,#3C3489 0%,#534AB7 100%);color:#fff;border-radius:14px;padding:28px 30px;margin-bottom:22px}}
.header h1{{font-size:24px;font-weight:700;margin-bottom:6px}}
.header .sub{{font-size:13px;opacity:.85}}
.header .meta{{display:flex;gap:12px;margin-top:14px;font-size:12px;flex-wrap:wrap}}
.header .meta span{{background:rgba(255,255,255,.16);padding:4px 14px;border-radius:20px}}
.card{{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
.card h2{{font-size:16px;font-weight:700;color:#3C3489;margin-bottom:14px;padding-left:10px;border-left:4px solid #534AB7}}
.card h3{{font-size:14px;font-weight:600;color:#333;margin:16px 0 8px}}
.card p{{font-size:13px;color:#444;margin-bottom:8px}}
.badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin:2px}}
.b-red{{background:#fde8e8;color:#d63031}}
.b-blue{{background:#e8f0fe;color:#1967d2}}
.b-orange{{background:#fff3e0;color:#e67e22}}
.b-green{{background:#e8f8f0;color:#00a865}}
.b-gray{{background:#f0f2f5;color:#666}}
.tag{{display:inline-block;background:#f0f2f5;color:#555;padding:2px 8px;border-radius:10px;font-size:11px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}}
th{{background:#f0f2f5;color:#555;font-weight:600;padding:8px 10px;text-align:left;border-bottom:2px solid #e0e3e8}}
td{{padding:8px 10px;border-bottom:1px solid #eef0f3}}
.up{{color:#d63031;font-weight:600}}
.down{{color:#00a865;font-weight:600}}
.muted{{color:#999}}
.point{{padding:12px 14px;background:#f8f7ff;border-radius:8px;margin-bottom:10px;border-left:3px solid #AFA9EC}}
.point .pt-title{{font-size:14px;font-weight:600;color:#3C3489;margin-bottom:4px}}
.point .pt-body{{font-size:13px;color:#444}}
.point .pt-action{{font-size:12px;color:#e67e22;margin-top:4px;font-weight:500}}
.alert{{padding:12px 14px;border-radius:8px;margin-bottom:10px;font-size:13px}}
.alert-red{{background:#fde8e8;border-left:3px solid #d63031;color:#b03a2e}}
.alert-orange{{background:#fff3e0;border-left:3px solid #e67e22;color:#b3541a}}
.alert-green{{background:#e8f8f0;border-left:3px solid #00a865;color:#1e8449}}
.alert-blue{{background:#e8f0fe;border-left:3px solid #1967d2;color:#1f4e8c}}
.conclusion-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.conclusion-item{{background:#fafbfc;border-radius:10px;padding:14px;border:1px solid #eef0f3}}
.conclusion-item .label{{font-size:12px;color:#888;margin-bottom:4px}}
.conclusion-item .value{{font-size:14px;font-weight:600;color:#1a2b4a}}
.stock-card{{border:1px solid #eef0f3;border-radius:10px;padding:16px;margin-bottom:12px}}
.stock-title{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}}
.stock-title .name{{font-size:15px;font-weight:700;color:#1a2b4a}}
.stock-logic{{font-size:13px;color:#444;margin-bottom:6px}}
.stock-meta{{font-size:12px;color:#888;margin-bottom:8px}}
.op-row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}}
.op-row .op{{font-size:12px;padding:3px 10px;border-radius:8px}}
.op-buy{{background:#fde8e8;color:#d63031}}
.op-hold{{background:#fff3e0;color:#e67e22}}
.op-sell{{background:#e8f8f0;color:#00a865}}
.op-watch{{background:#e8f0fe;color:#1967d2}}
.disclaimer{{background:#fff;border:1px solid #e8eaed;border-radius:10px;padding:18px 22px;font-size:12px;color:#666;line-height:1.8}}
.disclaimer strong{{color:#d63031}}
.chain-svg{{width:100%;height:auto;display:block;margin:8px 0}}
.toc{{background:#fff;border-radius:12px;padding:18px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
.toc h2{{font-size:15px;color:#3C3489;margin-bottom:10px}}
.toc ol{{margin-left:20px;font-size:13px;color:#444}}
.toc li{{margin:4px 0}}
.bar-row{{display:flex;align-items:center;margin:5px 0;font-size:12.5px}}
.bar-name{{width:150px;text-align:right;padding-right:10px;color:#555}}
.bar-wrap{{flex:1;background:#f0f2f5;border-radius:4px;height:16px;overflow:hidden}}
.bar-fill{{height:100%}}
.bar-val{{width:70px;padding-left:10px}}
</style>
</head>
<body>
<div class="wrap">

<div class="header">
<h1>A股舆情操作指引 · 9章节完整报告</h1>
<div class="sub">微博舆情 + 全球人物 + 宏观 + 事件因子 + 行情资金 + 技术走势 + 国家队 + 自选股（{REPORT_LABEL}）</div>
<div class="meta">
<span>报告日期：{TODAY}（{REPORT_LABEL}）</span>
<span>生成时间：{NOW}</span>
<span>7只自选股</span>
<span>今日高开：沪+0.07% 半导体领涨</span>
</div>
</div>

<div class="toc">
<h2>目录</h2>
<ol>
<li>隔夜美股（8/15凌晨收盘）</li>
<li>CPI与宏观</li>
<li>宏观传导链监控（独立因子）</li>
<li>地缘政治与原油（事件因子）</li>
<li>ETF资金流向</li>
<li>唐史主任长文分析</li>
<li>共振信号（多源交叉验证）</li>
<li>7只自选股操作指引</li>
<li>今日操作策略（周一预案）</li>
</ol>
</div>

<div class="card">
<h2>核心结论</h2>
<div class="conclusion-grid">
  <div class="conclusion-item">
    <div class="label">最强主线</div>
    <div class="value" style="color:#d63031;">存储链+PCB上游共振</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">闪迪本周+35%；投星8/17称三星海力士/镁光将发史上最大回购推动第二波主升浪；台湾CCL涨价20%+味之素ABF断供30%→PCB上游起航；A股周一高开半导体拉升</div>
  </div>
  <div class="conclusion-item">
    <div class="label">次强主线</div>
    <div class="value" style="color:#e67e22;">CPO/光通信内外共振</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">AO+15%/Lumentum+5% + A股CPO概念涨停(共进/亨通) + 投星『算力租赁最大利好』</div>
  </div>
  <div class="conclusion-item">
    <div class="label">操作基调</div>
    <div class="value" style="color:#1967d2;">注重阿尔法·不追高</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">唐史『第一波普反接近尾声，后续注重阿尔法』；仓位中性偏积极，等回踩</div>
  </div>
  <div class="conclusion-item">
    <div class="label">风险</div>
    <div class="value" style="color:#d63031;">日本加息75-100bp+套息平仓</div>
    <div style="font-size:12px;color:#666;margin-top:4px;">日本9月加息概率76%（7月仅24%），三菱日联称25bp不够或单次50-75bp；8/3美日韩联合干预并启用FIMA（押美债借美元）防日本抛>1.1万亿美债；若加息超预期叠加套息平仓→全球流动性收紧压制A股</div>
  </div>
</div>
</div>

<div class="card">
<h2>一、隔夜美股（8/15凌晨收盘）</h2>
<p style="font-size:13px;color:#666;margin-bottom:12px;">北京时间8月15日凌晨，美股三大指数集体收跌，结束连续上涨。存储板块逆势走强（闪迪本周累涨35.38%），但半导体设备股走弱，光通信走高，中概股涨跌不一。</p>
<table>
<thead><tr><th>标的</th><th>涨跌</th><th>关键信号</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{n}</td><td class="{up_down(p)}">{sign(p)}{p}%</td><td>{sig}（{v}）</td></tr>' for n,p,v,sig in [(x[0],x[1],x[2],x[3]) for x in us_market])}
</tbody>
</table>
<div style="margin-top:12px;">{''.join(us_bar(n,p,v,s) for n,p,v,s in us_market)}</div>
</div>

<div class="card">
<h2>二、CPI与宏观</h2>
<table>
<thead><tr><th>指标</th><th>最新值</th><th>前值/预期</th><th>判断</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{m[0]}</td><td><b>{m[1]}</b></td><td>{m[2]}</td><td class="{m[4]}">{m[3]}</td></tr>' for m in macro)}
</tbody>
</table>
<div class="point" style="margin-top:14px;">
<div class="pt-title">CPI风险解除 — 宏观面偏鸽</div>
<div class="pt-body">7月CPI同比3.4%符合预期、核心CPI降至2.5%（2021年来最低），PPI环比持平低于预期。加息紧迫性下降，9月维持利率不变概率约60%-68%。央行适度宽松、降息降准预期升温，利好科技成长估值。</div>
</div>
</div>

<div class="card">
<h2>三、宏观传导链监控（独立因子）</h2>
{chain_svg()}
<table>
<thead><tr><th>节点</th><th>当前值</th><th>方向</th><th>对A股影响</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{c[0]}</td><td>{c[1]}</td><td><span class="badge {c[4]}">{c[2]}</span></td><td>{c[3]}</td></tr>' for c in macro_chain)}
</tbody>
</table>
<div class="point" style="margin-top:14px;">
<div class="pt-title">综合判断</div>
<div class="pt-body">传导链主线由日元主导：原油上行→日本输入型通胀（能源90%依赖进口）→央行被迫加息。三菱日联认为25bp不足以止跌日元，单次或50-75bp、尾端100bp。为避免抛售>1.1万亿美债冲击长端收益率（10Y YTD+57bp），8/3美日韩联合干预并启用美联储FIMA回购工具——即日本押美债向美联储借美元干预汇率（借美款干预）。若加息超预期(75-100bp)叠加套息平仓，全球流动性收紧将压制A股风险偏好；FIMA大量使用或反增美债供给。防御信号：USD/JPY跌破155或10Y美债破4.8%。</div>
</div>
</div>

<div class="card">
<h2>四、地缘政治与原油（事件因子）</h2>
<table>
<thead><tr><th>事件</th><th>状态</th><th>影响</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{g[0]}</td><td><span class="badge {g[3]}">{g[1]}</span></td><td>{g[2]}</td></tr>' for g in geo)}
</tbody>
</table>
<div class="point" style="margin-top:14px;">
<div class="pt-title">风险定性</div>
<div class="pt-body">地缘风险溢价仍存，霍尔木兹通航未全面恢复、中东供应中断EIA预计持续至2027年底。但美国SPR消耗+OPEC下调需求预期形成对冲，WTI维持82美元区间，对A股油化/航空/化工影响分化，不构成系统性利空。</div>
</div>
</div>

<div class="card">
<h2>五、ETF资金流向</h2>
<table>
<thead><tr><th>ETF</th><th>代码</th><th>方向</th><th>信号</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{e[0]}</td><td>{e[1]}</td><td><span class="badge {e[3]}">{e[2]}</span></td><td>{e[4]}</td></tr>' for e in etf)}
</tbody>
</table>
<div class="point" style="margin-top:14px;">
<div class="pt-title">资金信号：宽基减仓 vs 硬科技吸金</div>
<div class="pt-body">宽基ETF连续7日净流出超900亿（沪深300ETF净赎回），蓝筹减仓；但芯片ETF净申购、半导体设备ETF连续4日净流入超17亿、科创50五日转正——科技主线资金坚守，存量博弈下流动性向硬科技集中。</div>
</div>
</div>

<div class="card">
<h2>六、唐史主任长文分析</h2>
<h3>唐史主任司马迁（8/16 今日新发）</h3>
{''.join(f'<div class="point"><div class="pt-body">{t}</div></div>' for t in tangshi_today)}
<h3>投星资产 / 投星大爷观点</h3>
{''.join(f'<div class="point"><div class="pt-body">{t}</div></div>' for t in touxing_asset)}
{''.join(f'<div class="point"><div class="pt-body">{t}</div></div>' for t in touxing_yeye)}
<div class="point">
<div class="pt-title">核心提炼</div>
<div class="pt-body">唐史明确：AI是解决方案不动摇，但市场第一波普反接近尾声，后续重阿尔法轻贝塔；对外交流忠告"董事长的话听一半、不盯漂亮销售、不下轻率结论"。投星体系延续"达链（光模块/PCB/存储）最大赢家"判断，并加码算力租赁（deepseek涨价）+800V/sic（充电桩十五五）+存储（镁光闪迪继续涨）。</div>
<div class="pt-action">操作映射：存储链、CPO/光通信、算力租赁为共振主线；纯概念小票与见顶确认个股（京东方A）规避</div>
</div>
</div>

<div class="card">
<h2>七、共振信号（多源交叉验证）</h2>
<table>
<thead><tr><th>方向</th><th>共振重数</th><th>来源交叉</th></tr></thead>
<tbody>
{''.join(f'<tr><td><b>{r[0]}</b></td><td><span class="badge {r[2]}">{r[1]}</span></td><td>{r[3]}</td></tr>' for r in resonance)}
</tbody>
</table>
</div>

<div class="card">
<h2>八、7只自选股操作指引</h2>
{''.join(stock_card(c) for c in ["688668","688409","600641","000725","301392","688530","600580"])}
</div>

<div class="card">
<h2>九、今日盘前策略（周一早报）</h2>
<table>
<thead><tr><th style="width:18%">维度</th><th>策略</th></tr></thead>
<tbody>
<tr><td><b>大盘</b></td><td>隔夜美股8/14收跌(道-0.20%/纳-0.28%)但存储/光通信逆势走强；A股周一高开(沪+0.07%/深+0.31%/创业+0.18%)、半导体产业链拉升。唐史称第一波普反接近尾声、注重阿尔法。支撑3900，压力3960-3970。</td></tr>
<tr><td><b>仓位</b></td><td>中性偏积极（约60-70%）。CPI偏鸽+科技ETF吸金，但日本加息75-100bp套息平仓黑天鹅未定价、美股收跌，不追高、等回踩确认。</td></tr>
<tr><td><b>存储链</b></td><td>5重共振最高置信度。闪迪/美光强势延续+史上最大回购预期(投星)，关注A股存储映射，回踩加仓。</td></tr>
<tr><td><b>CPO/光通信/PCB</b></td><td>5重共振。AO+15%/Lumentum+5%+台湾CCL涨价20%+味之素ABF断供，中际旭创/新易盛/PCB上游持有为主。</td></tr>
<tr><td><b>鼎通科技</b></td><td>加仓首选。1.6T连接器+液冷+800V催化，H1业绩最强，诊断上涨趋势。</td></tr>
<tr><td><b>京东方A</b></td><td>谨慎。回购57亿+玻璃基AI封装利好，但诊断黄色预警·见顶确认，等回调企稳。</td></tr>
<tr><td><b>回避</b></td><td>白酒(茅台半年报Q2环比-36%、早盘-4%)、半导体设备高位波动(博通-6%)、纯情绪小票、题材脱节股(欧莱新材)。</td></tr>
</tbody>
</table>
</div>

<div class="card">
<h2>主要指数（A股 8/14收盘 · 8/17高开）</h2>
<table>
<thead><tr><th>指数</th><th>8/14收盘</th><th>涨跌幅</th><th>8/17高开</th></tr></thead>
<tbody>
{''.join(f'<tr><td>{n}</td><td>{p}</td><td class="{up_down(float(c))}">{sign(float(c))}{c}%</td><td class="muted">—</td></tr>' for n,p,c in a_share)}
<tr><td>上证指数</td><td>3927.18</td><td class="muted">—</td><td class="up">+0.07%（3930.10）</td></tr>
<tr><td>深证成指</td><td>14354.31</td><td class="muted">—</td><td class="up">+0.31%（14399.20）</td></tr>
<tr><td>创业板指</td><td>3626.30</td><td class="muted">—</td><td class="up">+0.18%（3632.78）</td></tr>
</tbody>
</table>
<p class="muted" style="font-size:12px;margin-top:8px;">8/17周一高开：超2600只个股飘红，半导体产业链拉升(硅片/磷化铟/玻璃纤维)，CPO活跃(中石科技20cm2连板)，存储/长鑫概念走强；白酒调整(茅台-4%，半年报Q2环比-36%)；商品端原油涨超4%、集运+10%。港股恒指+1.38%/恒科+1.68%。</p>
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
    import a_stock_agent as agent
    cfg = agent.load_config()
    dc = cfg.get("database")
    if dc:
        db = StockAgentDB(host=dc.get("host","localhost"), port=dc.get("port",5432),
                          user=dc.get("user","postgres"), password=dc.get("password",""),
                          dbname=dc.get("dbname","a_stock_agent"))
        td = datetime.now().date()
        db.save_report(td, REPORT_STATE, "A股操作指引·9章节", html)
        db.save_sentiment_batch(td, weibo_data)
        print("[DB] 舆情数据与报告入库完成")
    else:
        print("[DB] 未配置 database，跳过入库")
except Exception as e:
    print(f"[DB] 入库失败: {e}")
