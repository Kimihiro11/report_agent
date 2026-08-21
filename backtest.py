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
_DEFAULT_WATCHLIST_NAME = {
    "688668": "鼎通科技", "688409": "富创精密", "600641": "先导基电",
    "000725": "京东方A", "301392": "汇成真空", "688530": "欧莱新材", "600580": "卧龙电驱",
}


def _load_watchlist_names():
    """从 config.json 的 watchlist_names 读取；缺失时回退内置默认。"""
    try:
        import a_stock_agent as agent
        cfg = agent.load_config()
        names = cfg.get("watchlist_names")
        if names:
            return dict(names)
    except Exception:
        pass
    return dict(_DEFAULT_WATCHLIST_NAME)


# 模块级加载
WATCHLIST_NAME = _load_watchlist_names()


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
    # 去重：同 (报告日, 代码) 仅保留一条。来源优先级 早报(盘前预测,3) > 晚报(盘后,2) > 9章节/盘中(1)
    # 优先选用更高优先级的来源；同级时偏向有方向（非中性）的判断；再同级则保留先到者。
    def _src_priority(name: str) -> int:
        if "早报" in name:
            return 3
        if "晚报" in name:
            return 2
        if any(k in name for k in ("9章节", "9维度", "盘中")):
            return 1
        return 0

    best = {}
    for f in sorted(set(files)):
        pri = _src_priority(f.name)
        for j in extract_judgments_from_html(f):
            key = (j["report_date"], j["stock_code"])
            is_dir = j["direction"] != "neutral"
            cur = best.get(key)
            if cur is None:
                best[key] = (pri, is_dir, j)
            else:
                cpri, cdi, _ = cur
                if (pri, is_dir) > (cpri, cdi):
                    best[key] = (pri, is_dir, j)
    all_j = [v[2] for v in best.values()]
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


def _fetch_kline_live(code: str, beg="2026-06-01", end=None):
    """实时抓取日K线（新浪主源，东方财富兜底），返回 [{date, open, close, high, low}] 升序。"""
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


def fetch_kline(code: str, beg="2026-06-01", end=None, refresh_days: int = 5):
    """DB 优先返回日K线（历史数据）：本地 daily_klines 缺失或尾端早于 今天-refresh_days 时，
    才拉实时行情并回写数据库。使回测可完全基于已入库的历史数据复现，不依赖每次联网抓取。

    end 默认为今天（实时截止），不再写死未来日期。
    """
    cfg = _load_db_cfg()
    db = _db_available(cfg)
    if db:
        kl = db.get_klines(code)
        latest = kl[-1]["date"] if kl else None
        from datetime import timedelta
        stale = (latest is None) or (
            latest < (datetime.now().date() - timedelta(days=refresh_days)).isoformat())
        if stale:
            live = _fetch_kline_live(code, beg, end)
            if live:
                try:
                    db.save_klines(code, live)
                except Exception as e:
                    print(f"[DB] {code} kline 回写跳过: {e}")
                kl = db.get_klines(code) or live
        return kl or []
    return _fetch_kline_live(code, beg, end)


def idx_of_date(klines, target):
    """返回 >= target 的第一根 bar 的索引（交易日对齐）；target 晚于行情末端返回 -1（由调用方按 pending 处理，避免入场价静默错位）。target 可为 date 或 str。"""
    t = target.isoformat() if hasattr(target, "isoformat") else str(target)
    for i, k in enumerate(klines):
        if k["date"] >= t:
            return i
    return -1


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

    db = _db_available(_load_db_cfg())
    psrc = "DB" if db else "LIVE"  # 价格来源：DB=已入库历史日K；LIVE=实时抓取

    results = []        # 每行一个 (judgment, window)
    pending = 0
    for j in judgments:
        code = j["stock_code"]
        kl = fetch_kline(code)
        if not kl:
            continue
        idx = idx_of_date(kl, j["report_date"])
        if idx < 0:
            # 判断日晚于行情数据末端（如盘后/周末产生的判断）：入场价尚未出现，显式记为待观察，不再静默错位到最后一根 bar
            print(f"[回测] {code} 判断日 {j['report_date']} 晚于行情末端，记为待观察")
            for w in window_days:
                pending += 1
                results.append({
                    "judgment_date": j["report_date"], "stock_code": code,
                    "stock_name": j["stock_name"], "action": j["action"],
                    "direction": j["direction"], "window_days": w,
                    "entry_close": None, "exit_close": None,
                    "ret_pct": None, "direction_hit": None,
                    "tech_signal": "neutral", "tech_agree": None,
                    "status": "pending", "price_source": psrc, "note": "",
                })
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
                    "status": "pending", "price_source": psrc, "note": "",
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
                "status": "done", "price_source": psrc, "note": "",
            })
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
def _load_backtest_template():
    """加载外部回测报告模板；缺失时回退内置极简模板。"""
    try:
        p = BASE_DIR / "templates" / "backtest_report.html"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ("<!DOCTYPE html><html><head><meta charset='UTF-8'><title>回测报告</title></head>"
            "<body><h1>回测报告</h1>{rows}</body></html>")


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
        src = r.get("price_source", "")
        src_badge = ('<span class="badge-db">DB</span>' if src == "DB"
                     else '<span class="badge-live">实时</span>' if src == "LIVE" else "")
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
                 f"<td>{ret_s}</td><td>{hit_s}</td><td>{DIRECTION_CN[r['tech_signal']]}</td><td>{agree_s}</td>"
                 f"<td>{src_badge}</td></tr>")

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
    rate_class = "good" if (rate or 0) >= 50 else "warn"
    arate_class = "good" if (arate or 0) >= 50 else "warn"

    # 价格来源统计（DB历史 vs 实时）
    src_db = sum(1 for r in results if r.get("price_source") == "DB")
    src_live = sum(1 for r in results if r.get("price_source") == "LIVE")
    src_note = (f"DB历史 {src_db} / 实时 {src_live}") if (src_db or src_live) else "实时行情"

    # 安全替换（避免模板 CSS 花括号触发 str.format 报错）
    tmpl = _load_backtest_template()
    repl = {
        "{today}": today,
        "{total_done}": str(agg["total_done"]),
        "{pending_count}": str(len(pend)),
        "{rate_s}": rate_s,
        "{rate_class}": rate_class,
        "{dir_calls}": str(agg["dir_calls"]),
        "{arate_s}": arate_s,
        "{arate_class}": arate_class,
        "{wrows}": wrows,
        "{srows}": srows,
        "{rows}": rows,
        "{src_note}": src_note,
    }
    out = tmpl
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


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
