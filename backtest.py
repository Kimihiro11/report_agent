#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股舆情Agent · 个股判断回测与交叉验证模块
========================================
功能：
  1. 从每日生成的 9章节报告 HTML 中提取「7只自选股操作指引」的判断（code/name/action/direction）
  2. 用新浪日K线（东方财富兜底）回测该判断在未来 1/3/5 个交易日后的实际涨跌幅与方向命中率
  3. 交叉验证：用「独立技术信号」（价格相对MA20 + MA20斜率）对照 agent 的判断方向，
     验证两类方法是否一致（agent 叙事判断 vs 纯量价技术信号）
  4. 生成回测 HTML 报告，并写入 stock_judgments / backtest_results 表

用法：
  python backtest.py --seed         # 解析工作区所有报告 HTML，写入 stock_judgments
  python backtest.py --run          # 读取判断 + 拉行情回测 + 生成报告 + 入库
  python backtest.py --all          # seed + run
"""
import re
import json
import argparse
import urllib.request
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
WINDOWS = [1, 3, 5]  # 回测窗口（交易日）
JUDG_FILE = BASE_DIR / "seeds" / "judgments.json"      # 个股判断本地存储（DB 不可用时的兜底）
RES_FILE = BASE_DIR / "seeds" / "backtest_results.json"  # 回测结果本地存储

# ----------------- 方向映射 -----------------
BULL_KW = ["加仓", "买入", "回补", "首选", "低吸", "做多", "关注", "重点"]
BEAR_KW = ["回避", "卖出", "减仓", "做空", "规避", "不参与"]
DIRECTION_CN = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}

# 自选股代码→名称映射（config 的 watchlist_stocks 仅含代码）
WATCHLIST_NAME = {
    "688668": "鼎通科技", "688409": "富创精密", "600641": "先导基电",
    "000725": "京东方A", "301392": "汇成真空", "688530": "欧莱新材", "600580": "卧龙电驱",
}


def action_to_direction(action: str) -> str:
    """将操作词映射为方向：bullish / bearish / neutral"""
    a = action or ""
    if any(k in a for k in BULL_KW):
        return "bullish"
    if any(k in a for k in BEAR_KW):
        return "bearish"
    return "neutral"


# ----------------- 报告日期解析 -----------------
def parse_report_date(fname: str):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4})(\d{2})(\d{2})", fname)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


# ----------------- 从 HTML 提取个股判断 -----------------
# op-row 类名 → (归一化操作词, 方向)。类名才是决策，文字仅作展示。
OP_MAP = {
    "buy": ("买入", "bullish"), "add": ("加仓", "bullish"), "jia": ("加仓", "bullish"),
    "chao": ("抄底", "bullish"), "duo": ("做多", "bullish"),
    "sell": ("卖出", "bearish"), "duo2": ("做空", "bearish"), "hui": ("回避", "bearish"),
    "watch": ("观望", "neutral"), "guan": ("观望", "neutral"),
    "hold": ("持有", "neutral"), "chi": ("持有", "neutral"),
    "cautious": ("谨慎", "neutral"), "jian": ("谨慎", "neutral"),
}


def _parse_card(c: str):
    """从单个 stock-card 片段解析 (code, name, action)。支持 badge / op-row 两种格式。"""
    m = re.search(r"(\d{6})", c)
    if not m:
        return None
    code = m.group(1)
    # 名称
    name = ""
    nm = re.search(r'class="name">([^<]+?)(?:\s*<span|—|\d{6}|$)', c)
    if nm:
        name = re.sub(r"\d{6}.*", "", nm.group(1)).strip().rstrip("—").strip()
    # 1) op-row：以类名为决策依据
    om = re.search(r'class="op op-(\w+)"[^>]*>([^<]*)', c)
    if om:
        cls, txt = om.group(1).lower(), om.group(2).strip()
        if cls in OP_MAP:
            act, direction = OP_MAP[cls]
            return code, name, f"{act}（{txt}）" if txt and txt != act else act
        # 类名未收录但文字含操作词
        if any(k in txt for k in BULL_KW + BEAR_KW + ["持有", "观望", "谨慎", "回避", "中性"]):
            return code, name, txt
    # 2) badge 含操作词
    bm = re.search(r'<span class="badge[^"]*">([^<]*?(?:加仓|买入|持有|观望|谨慎|回避|卖出|减仓|关注|低吸|做多)[^<]*?)</span>', c)
    if bm:
        return code, name, bm.group(1).strip()
    return None


def extract_judgments_from_html(path: Path):
    html = path.read_text(encoding="utf-8", errors="replace")
    rdate = parse_report_date(path.name)
    if not rdate:
        return []

    # 1) 解析所有 stock-card（同时兼容 badge 与 op-row 两种操作格式）
    judgs = []
    seen = set()
    for c in html.split('<div class="stock-card">')[1:]:
        parsed = _parse_card(c)
        if not parsed:
            continue
        code, name, act = parsed
        if code in seen:
            continue
        seen.add(code)
        logic = re.search(r'stock-logic">([^<]+)<', c)
        judgs.append({
            "report_date": rdate,
            "stock_code": code,
            "stock_name": name,
            "action": act,
            "direction": action_to_direction(act),
            "rationale": (logic.group(1).strip() if logic else ""),
            "source_file": path.name,
        })
    if judgs:
        return judgs

    # 2) 兜底：用自选股名单在全文就近扫描操作词
    try:
        import a_stock_agent as agent
        cfg = agent.load_config()
        wl = cfg.get("watchlist_stocks", [])
    except Exception:
        wl = []
    watch = []
    for w in wl:
        if isinstance(w, dict):
            code = str(w.get("code") or w.get("stock_code") or "")
            name = w.get("name") or w.get("stock_name") or WATCHLIST_NAME.get(code, "")
        else:
            code = str(w)
            name = WATCHLIST_NAME.get(code, "")
        if code:
            watch.append((code, name))
    if not watch:
        watch = [(c, n) for c, n in WATCHLIST_NAME.items()]
    for code, name in watch:
        if not name:
            continue
        idx = html.find(name)
        if idx < 0:
            continue
        seg = html[idx: idx + 400]
        hit = None
        for kw in BULL_KW + BEAR_KW + ["持有", "观望", "谨慎", "中性"]:
            if kw in seg:
                hit = kw
                break
        if hit:
            judgs.append({
                "report_date": rdate,
                "stock_code": code,
                "stock_name": name,
                "action": hit,
                "direction": action_to_direction(hit),
                "rationale": "",
                "source_file": path.name,
            })
    return judgs


def _load_db_cfg():
    import a_stock_agent as agent
    return agent.load_config().get("database", {})


def _db_available(cfg):
    try:
        from db import StockAgentDB
        db = StockAgentDB(host=cfg.get("host", "localhost"), port=cfg.get("port", 5432),
                          user=cfg.get("user", "postgres"), password=cfg.get("password", ""),
                          dbname=cfg.get("dbname", "a_stock_agent"))
        conn = db._conn()
        conn.close()
        return db
    except Exception as e:
        print(f"[DB] 不可用（兜底使用本地JSON）: {e}")
        return None


def seed_judgments(report_dir: Path = None):
    """解析 reports/ 下所有报告 HTML（含子目录），写入 stock_judgments（去重）"""
    # 仅扫描 reports/（早报|晚报|周报|回测），排除 archive/ 历史副本避免重复
    if report_dir is None:
        report_dir = BASE_DIR / "reports"
    pats = [
        "早报-*.html", "晚报-*.html", "周报-*.html",
        "A股操作指引*9章节*.html", "A股舆情操作指引*9维度*.html",
    ]
    files = []
    for p in pats:
        files += list(report_dir.rglob(p))
    # 去重
    seen, all_j = set(), []
    for f in sorted(set(files)):
        for j in extract_judgments_from_html(f):
            key = (j["report_date"], j["stock_code"])
            if key not in seen:
                seen.add(key)
                all_j.append(j)
            else:
                # 同日期同代码冲突时，优先保留有方向（非中性）的判断
                old = next((x for x in all_j if (x["report_date"], x["stock_code"]) == key), None)
                if old and old["direction"] == "neutral" and j["direction"] != "neutral":
                    old.update(j)
    if not all_j:
        print("[seed] 未从报告中解析到任何个股判断")
        return []
    # 空 stock_name 用自选股名单回填（早期报告卡片格式差异导致解析为空）
    for j in all_j:
        if not j.get("stock_name"):
            j["stock_name"] = WATCHLIST_NAME.get(j["stock_code"], "")
    # 本地 JSON 为主存储
    JUDG_FILE.write_text(json.dumps(all_j, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同步写库（可选）
    cfg = _load_db_cfg()
    db = _db_available(cfg)
    if db:
        try:
            db.save_judgments(all_j)
        except Exception as e:
            print(f"[DB] 判断入库跳过: {e}")
    print(f"[seed] 个股判断已提取: {len(all_j)} 条 -> {JUDG_FILE.name}")
    return all_j


def load_judgments(as_of=None):
    """优先读库，库不可用则读本地 JSON"""
    cfg = _load_db_cfg()
    db = _db_available(cfg)
    if db:
        try:
            rows = db.get_judgments(as_of)
            if rows:
                return rows
        except Exception:
            pass
    if JUDG_FILE.exists():
        rows = json.loads(JUDG_FILE.read_text(encoding="utf-8"))
        if as_of:
            rows = [r for r in rows if r["report_date"] == as_of]
        return rows
    return []


# ----------------- 行情获取（东方财富 qfq 日K） -----------------
_EM_CACHE = {}


def fetch_kline(code: str, beg="2026-06-01", end=None):
    """返回 [{date, open, close, high, low}]，按日期升序（新浪主源，东方财富兜底）

    end 默认为今天（实时截止），不再写死未来日期。
    """
    end = end or datetime.now().strftime("%Y-%m-%d")
    if code in _EM_CACHE:
        return _EM_CACHE[code]
    import time
    prefix = "sh" if code[0] == "6" else "sz"
    sina = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen=90")
    last_err = None
    # 1) 新浪
    for attempt in range(3):
        try:
            time.sleep(0.25 * attempt)
            req = urllib.request.Request(sina, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = json.loads(r.read().decode("utf-8"))
            out = [{"date": k["day"], "open": float(k["open"]), "close": float(k["close"]),
                    "high": float(k["high"]), "low": float(k["low"])} for k in raw]
            _EM_CACHE[code] = out
            return out
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    # 2) 东方财富兜底
    mkt = "1" if code[0] == "6" else "0"
    secid = f"{mkt}.{code}"
    em = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
          f"secid={secid}&fields1=f1&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1&beg={beg}&end={end}")
    for attempt in range(2):
        try:
            time.sleep(0.4 * attempt)
            req = urllib.request.Request(em, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://finance.eastmoney.com/"})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8"))
            raw = (d.get("data") or {}).get("klines") or []
            out = []
            for line in raw:
                p = line.split(",")
                out.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                            "high": float(p[3]), "low": float(p[4])})
            _EM_CACHE[code] = out
            return out
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    print(f"[行情] {code} 获取失败: {last_err}")
    return []


def idx_of_date(klines, target):
    """返回 >= target 的第一根 bar 的索引（交易日对齐）；找不到返回最后一根。target 可为 date 或 str。"""
    t = target.isoformat() if hasattr(target, "isoformat") else str(target)
    for i, k in enumerate(klines):
        if k["date"] >= t:
            return i
    return len(klines) - 1 if klines else -1


def ma20_at(klines, idx):
    if idx < 19 or len(klines) < 20:
        return None
    window = [k["close"] for k in klines[idx - 19:idx + 1]]
    return sum(window) / 20.0


def tech_signal_at(klines, idx):
    """独立技术信号：价格相对MA20 + MA20斜率。返回 bullish/bearish/neutral"""
    if idx < 22 or len(klines) < 23:
        return "neutral"
    ma_now = ma20_at(klines, idx)
    ma_prev = ma20_at(klines, idx - 3)
    if ma_now is None or ma_prev is None:
        return "neutral"
    close = klines[idx]["close"]
    slope = ma_now - ma_prev
    if close > ma_now and slope > 0:
        return "bullish"
    if close < ma_now and slope < 0:
        return "bearish"
    return "neutral"


# ----------------- 回测主逻辑 -----------------
def run_backtest(window_days=WINDOWS, as_of=None):
    judgments = load_judgments(as_of)
    if not judgments:
        print("[回测] 个股判断为空，请先 --seed")
        return None

    results = []        # 每行一个 (judgment, window)
    pending = 0
    for j in judgments:
        code = j["stock_code"]
        kl = fetch_kline(code)
        if not kl:
            continue
        idx = idx_of_date(kl, j["report_date"])
        if idx < 0:
            continue
        entry = kl[idx]["close"]
        tech_sig = tech_signal_at(kl, idx)
        for w in window_days:
            ex = idx + w
            if ex >= len(kl):
                pending += 1
                results.append({
                    "judgment_date": j["report_date"], "stock_code": code,
                    "stock_name": j["stock_name"], "action": j["action"],
                    "direction": j["direction"], "window_days": w,
                    "entry_close": entry, "exit_close": None,
                    "ret_pct": None, "direction_hit": None,
                    "tech_signal": tech_sig, "tech_agree": None,
                    "status": "pending",
                })
                continue
            exit_c = kl[ex]["close"]
            ret = (exit_c / entry - 1) * 100
            # 方向命中（仅看多/看空参与）
            if j["direction"] == "bullish":
                hit = ret > 0
            elif j["direction"] == "bearish":
                hit = ret < 0
            else:
                hit = None
            # 交叉验证：agent 方向 vs 独立技术信号
            if j["direction"] == "neutral" or tech_sig == "neutral":
                agree = None
            else:
                agree = (j["direction"] == tech_sig)
            results.append({
                "judgment_date": j["report_date"], "stock_code": code,
                "stock_name": j["stock_name"], "action": j["action"],
                "direction": j["direction"], "window_days": w,
                "entry_close": entry, "exit_close": exit_c,
                "ret_pct": round(ret, 2), "direction_hit": hit,
                "tech_signal": tech_sig, "tech_agree": agree,
                "status": "done",
            })
    db = _db_available(_load_db_cfg())
    if db:
        try:
            db.save_backtest_results(results)
        except Exception as e:
            print(f"[DB] 回测结果入库跳过: {e}")
    # 本地 JSON 存储（兜底/主存储）
    RES_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    agg = aggregate(results)
    return {"results": results, "agg": agg, "pending": pending}


def aggregate(results):
    done = [r for r in results if r["status"] == "done"]
    # 方向命中
    dir_calls = [r for r in done if r["direction"] in ("bullish", "bearish") and r["direction_hit"] is not None]
    dir_hits = sum(1 for r in dir_calls if r["direction_hit"])
    # 交叉验证（技术信号一致性）
    agree_calls = [r for r in done if r["tech_agree"] is not None]
    agree_hits = sum(1 for r in agree_calls if r["tech_agree"])
    # 按窗口
    by_window = {}
    for w in WINDOWS:
        ws = [r for r in dir_calls if r["window_days"] == w]
        h = sum(1 for r in ws if r["direction_hit"])
        by_window[w] = {"n": len(ws), "hits": h,
                        "rate": round(h / len(ws) * 100, 1) if ws else None}
    # 按个股
    by_stock = defaultdict(lambda: {"n": 0, "hits": 0, "rets": []})
    for r in dir_calls:
        s = by_stock[r["stock_code"]]
        s["n"] += 1
        s["hits"] += 1 if r["direction_hit"] else 0
        s["rets"].append(r["ret_pct"])
    by_stock_out = {k: {"n": v["n"], "hits": v["hits"],
                        "rate": round(v["hits"] / v["n"] * 100, 1),
                        "avg_ret": round(sum(v["rets"]) / len(v["rets"]), 2)}
                   for k, v in by_stock.items()}
    return {
        "total_done": len(done),
        "dir_calls": len(dir_calls), "dir_hits": dir_hits,
        "dir_rate": round(dir_hits / len(dir_calls) * 100, 1) if dir_calls else None,
        "agree_calls": len(agree_calls), "agree_hits": agree_hits,
        "agree_rate": round(agree_hits / len(agree_calls) * 100, 1) if agree_calls else None,
        "by_window": by_window,
        "by_stock": by_stock_out,
    }


# ----------------- 生成 HTML 报告 -----------------
def build_backtest_html(data):
    results = data["results"]
    agg = data["agg"]
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    pend = [r for r in results if r["status"] == "pending"]

    def cls_of(v):
        if v is None:
            return "muted"
        return "up" if v else "down"

    # 明细表
    rows = ""
    for r in sorted(results, key=lambda x: (x["judgment_date"], x["stock_code"], x["window_days"])):
        if r["status"] == "pending":
            ret_s = '<span class="muted">待回测</span>'
            hit_s = '<span class="muted">—</span>'
            agree_s = '<span class="muted">—</span>'
        else:
            ret = r["ret_pct"]
            ret_s = f'<span class="{cls_of(ret>0)}">{ret:+.2f}%</span>'
            if r["direction_hit"] is None:
                hit_s = '<span class="muted">中性不参与</span>'
            else:
                hit_s = f'<span class="{cls_of(r["direction_hit"])}">{"✓命中" if r["direction_hit"] else "✗未中"}</span>'
            if r["tech_agree"] is None:
                agree_s = '<span class="muted">—</span>'
            else:
                agree_s = f'<span class="{cls_of(r["tech_agree"])}">{"一致" if r["tech_agree"] else "背离"}</span>'
        rows += (f"<tr><td>{r['judgment_date']}</td><td>{r['stock_name']}<span class='muted'> {r['stock_code']}</span></td>"
                 f"<td>{r['action']}</td><td>{DIRECTION_CN[r['direction']]}</td><td>{r['window_days']}日</td>"
                 f"<td>{ret_s}</td><td>{hit_s}</td><td>{DIRECTION_CN[r['tech_signal']]}</td><td>{agree_s}</td></tr>")

    # 按窗口汇总
    wrows = ""
    for w in WINDOWS:
        b = agg["by_window"].get(w, {})
        if b.get("rate") is None:
            wrows += f"<tr><td>{w}日</td><td colspan='2' class='muted'>样本不足</td></tr>"
        else:
            rc = "#1f9e86" if b['rate'] >= 50 else "#e67e22"
            wrows += (f"<tr><td>{w}日</td><td>{b['hits']}/{b['n']}</td>"
                      f"<td><b style='color:{rc}'>{b['rate']}%</b></td></tr>")

    # 按个股
    srows = ""
    for code, s in sorted(agg["by_stock"].items()):
        rc = "#1f9e86" if s['rate'] >= 50 else "#e67e22"
        srows += (f"<tr><td>{code}</td><td>{s['hits']}/{s['n']}</td>"
                  f"<td><b style='color:{rc}'>{s['rate']}%</b></td>"
                  f"<td>{s['avg_ret']:+.2f}%</td></tr>")

    rate = agg["dir_rate"]
    arate = agg["agree_rate"]
    rate_s = f"{rate}%" if rate is not None else "样本不足"
    arate_s = f"{arate}%" if arate is not None else "样本不足"
    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股舆情Agent · 个股判断回测与交叉验证</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:#f4f5f7;color:#1d2129;line-height:1.8}}
.wrap{{max-width:980px;margin:0 auto;padding:24px 20px 60px}}
.header{{background:linear-gradient(135deg,#0f6b5e 0%,#1f9e86 100%);color:#fff;border-radius:14px;padding:26px 30px;margin-bottom:22px}}
.header h1{{font-size:22px;font-weight:700;margin-bottom:6px}}
.header .sub{{font-size:13px;opacity:.85}}
.header .meta{{display:flex;gap:12px;margin-top:12px;font-size:12px;flex-wrap:wrap}}
.header .meta span{{background:rgba(255,255,255,.16);padding:4px 14px;border-radius:20px}}
.card{{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.05)}}
.card h2{{font-size:16px;font-weight:700;color:#0f6b5e;margin-bottom:14px;padding-left:10px;border-left:4px solid #1f9e86}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0}}
th{{background:#f0f2f5;color:#555;font-weight:600;padding:8px 10px;text-align:left;border-bottom:2px solid #e0e3e8}}
td{{padding:7px 10px;border-bottom:1px solid #eef0f3}}
.up{{color:#d63031;font-weight:600}}
.down{{color:#00a865;font-weight:600}}
.muted{{color:#999}}
.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.metric{{background:#fafbfc;border-radius:10px;padding:16px;border:1px solid #eef0f3}}
.metric .label{{font-size:12px;color:#888;margin-bottom:6px}}
.metric .value{{font-size:26px;font-weight:700}}
.metric .value.good{{color:#1f9e86}}
.metric .value.warn{{color:#e67e22}}
.metric .desc{{font-size:12px;color:#666;margin-top:4px}}
.disclaimer{{background:#fff;border:1px solid #e8eaed;border-radius:10px;padding:16px;font-size:12px;color:#666}}
.disclaimer strong{{color:#d63031}}
</style></head><body><div class="wrap">
<div class="header">
<h1>A股舆情Agent · 个股判断回测与交叉验证</h1>
<div class="sub">每日报告个股操作指引 → 新浪日K实测收益 → 独立技术信号对照</div>
<div class="meta"><span>生成时间：{today}</span><span>样本(已兑现)：{agg['total_done']} 条</span>
<span>待回测：{len(pend)} 条</span><span>窗口：1/3/5 交易日</span></div>
</div>

<div class="card">
<h2>一、回测核心指标</h2>
<div class="metrics">
  <div class="metric"><div class="label">方向命中率（看多/看空判定）</div>
    <div class="value {'good' if (rate or 0)>=50 else 'warn'}">{rate_s}</div>
    <div class="desc">基于 {agg['dir_calls']} 次有明确方向的判断，未来窗口实际涨跌与判断方向一致的比例（即回测交叉验证结果）</div></div>
  <div class="metric"><div class="label">独立技术信号一致率</div>
    <div class="value {'good' if (arate or 0)>=50 else 'warn'}">{arate_s}</div>
    <div class="desc">agent 叙事判断 与 独立量价技术信号（MA20+斜率）方向一致的比例；样本不足时待更多方向性判断兑现</div></div>
</div>
</div>

<div class="card">
<h2>二、分窗口命中率</h2>
<table><thead><tr><th>持有窗口</th><th>命中/总数</th><th>命中率</th></tr></thead><tbody>{wrows}</tbody></table>
</div>

<div class="card">
<h2>三、分个股命中率（看多/看空判定）</h2>
<table><thead><tr><th>代码</th><th>命中/总数</th><th>命中率</th><th>平均收益</th></tr></thead><tbody>{srows}</tbody></table>
</div>

<div class="card">
<h2>四、逐笔回测明细</h2>
<table><thead><tr><th>判断日</th><th>个股</th><th>操作</th><th>方向</th><th>窗口</th><th>实际收益</th><th>方向命中</th><th>技术信号</th><th>交叉验证</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="muted" style="font-size:12px;margin-top:8px;">说明：中性（持有/观望/谨慎）不参与方向命中统计，仅列示实际收益；技术信号为判断日当天的独立量价趋势（价格相对MA20 + MA20斜率）。待回测=该股判断日之后的窗口尚未到交易日。</p>
</div>

<div class="disclaimer">
<strong>免责声明</strong>：本回测基于公开行情与历史报告自动生成，样本量有限，统计结论不构成投资建议。命中率受样本期市场风格影响，过去表现不预示未来。交叉验证仅用于评估两类方法的一致性，不保证收益。
</div>
</div></body></html>'''
    return html


# ----------------- 入口 -----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="解析报告HTML写入 stock_judgments")
    ap.add_argument("--run", action="store_true", help="运行回测并生成报告")
    ap.add_argument("--all", action="store_true", help="seed + run")
    ap.add_argument("--as-of", help="仅回测指定日期(YYYY-MM-DD)的判断")
    args = ap.parse_args()

    did = args.seed or args.all
    if did:
        print("[1/2] 解析报告判断...")
        seed_judgments()

    if args.run or args.all:
        print("[2/2] 运行回测...")
        data = run_backtest(as_of=args.as_of)
        if not data:
            return
        html = build_backtest_html(data)
        out_dir = BASE_DIR / "reports" / "回测"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"回测报告-{datetime.now().strftime('%Y%m%d')}.html"
        out.write_text(html, encoding="utf-8")
        print(f"回测报告已生成: {out} ({out.stat().st_size // 1024} KB)")
        agg = data["agg"]
        print(f"方向命中率: {agg['dir_rate']}% | 交叉验证一致率: {agg['agree_rate']}% | 待回测: {data['pending']}")


if __name__ == "__main__":
    main()
