# -*- coding: utf-8 -*-
"""最小回归测试集 —— 挡住「同一类问题反复发作」的高频回归。

设计原则：
  1) 只测「结构性契约」，不测具体数值（数值每天都在变）
  2) 不依赖网络（外部源本来就不稳定，测它等于自找失败）
  3) DB 不可用时自动 skip，保证任何环境都能跑

运行：
  .venv\\Scripts\\python.exe -m unittest discover -s tests -v

覆盖的历史事故（每条测试都对应一次真实返工）：
  - 快照字段缺失 / meta 口径不一致 → 2026-09 反复出现
  - index_quotes 盘前被标成当日（整体错位一天）→ 9/9、9/10、9/11 连修 3 次
  - 报告缺章节（沙箱抓取失败导致）→ 8 月修过
  - requirements 漏登记依赖 → 9/12 发现缺 cryptography
"""
import copy
import json
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

SNAP_DIR = BASE / "data" / "snapshots"
REPORT_DIRS = [BASE / "reports" / t for t in ("早报", "晚报", "周报")]

# 报告必须存在的章节锚点（h2 标题关键字）
SECTION_ANCHORS = ["核心结论", "隔夜美股", "CPI", "传导链", "地缘", "ETF", "舆情解构",
                   "共振信号", "自选股", "限时关注", "缠论", "免责"]

REQUIRED_SNAPSHOT_KEYS = ["date", "quotes", "weibo_data", "market_state"]


def latest_snapshot():
    if not SNAP_DIR.exists():
        return None
    files = sorted(SNAP_DIR.glob("fetched_*.json"))
    return files[-1] if files else None


def latest_report():
    best = None
    for d in REPORT_DIRS:
        if not d.exists():
            continue
        for f in d.glob("*.html"):
            if best is None or f.stat().st_mtime > best.stat().st_mtime:
                best = f
    return best


class TestSnapshotContract(unittest.TestCase):
    """快照是整条管线的输入契约，字段缺失会让下游静默降级成占位。"""

    def setUp(self):
        self.path = latest_snapshot()
        if self.path is None:
            self.skipTest("尚无快照文件（先跑 stock_report_agent.py）")
        self.snap = json.loads(self.path.read_text(encoding="utf-8"))

    def test_required_keys(self):
        missing = [k for k in REQUIRED_SNAPSHOT_KEYS if k not in self.snap]
        self.assertEqual([], missing, f"{self.path.name} 缺少必需字段: {missing}")

    def test_quotes_not_empty(self):
        self.assertTrue(self.snap.get("quotes"), "指数行情为空，报告会退化为占位")

    def test_meta_schema_when_present(self):
        """meta 若存在则必须自洽：basis 合法、data_date 可解析且不晚于采集日。"""
        meta = self.snap.get("meta")
        if not meta:
            self.skipTest("该快照为旧格式（无 meta），口径层会走推断回退")
        self.assertIn(meta.get("basis"), ("pre_market", "intraday", "close"))
        dd = meta.get("data_date")
        self.assertTrue(dd, "meta.data_date 不可为空")
        parsed = datetime.strptime(dd, "%Y-%m-%d").date()
        snap_day = datetime.strptime(self.snap["date"], "%Y%m%d").date()
        self.assertLessEqual(parsed, snap_day,
                             f"data_date({dd}) 晚于采集日({snap_day})，口径错位")
        if meta["basis"] == "pre_market":
            self.assertLess(parsed, snap_day,
                            "盘前口径的 data_date 必须早于采集日（行情来自上一交易日）")

    def test_as_of_parseable(self):
        meta = self.snap.get("meta")
        if not meta or not meta.get("as_of"):
            self.skipTest("无 meta.as_of")
        datetime.strptime(meta["as_of"], "%Y-%m-%d %H:%M:%S")


class TestViewState(unittest.TestCase):
    """口径层自身的行为契约。"""

    def setUp(self):
        import view
        self.view = view

    def test_pre_market_maps_to_prev_trading_day(self):
        meta = self.view.build_snapshot_meta(datetime(2026, 9, 14, 8, 45))  # 周一盘前
        self.assertEqual("pre_market", meta["basis"])
        self.assertEqual("2026-09-11", meta["data_date"])  # 上一交易日=周五

    def test_close_after_market(self):
        meta = self.view.build_snapshot_meta(datetime(2026, 9, 14, 15, 30))
        self.assertEqual("close", meta["basis"])
        self.assertEqual("2026-09-14", meta["data_date"])

    def test_weekend_maps_to_friday(self):
        meta = self.view.build_snapshot_meta(datetime(2026, 9, 12, 19, 30))  # 周六
        self.assertEqual("close", meta["basis"])
        self.assertEqual("2026-09-11", meta["data_date"])

    def test_meta_takes_priority_over_inference(self):
        v = self.view.build_view("早报", {"basis": "pre_market", "data_date": "2026-09-10",
                                          "as_of": "2026-09-11 08:47:00"}, {})
        self.assertTrue(v.is_pre_market)
        self.assertEqual("2026-09-10", v.data_date)
        self.assertEqual("meta", v.source)

    def test_legacy_snapshot_fallback(self):
        """旧快照（无 meta）必须仍能出报，不能被改造打挂。"""
        v = self.view.build_view("早报", None, {"上证指数": {"chg_pct": 0.5, "volume": 12345}},
                                 datetime(2026, 9, 14, 8, 45))
        self.assertTrue(v.is_pre_market)
        self.assertEqual("inferred", v.source)

    def test_trading_day_helpers_skip_weekend(self):
        self.assertEqual(date(2026, 9, 11), self.view.prev_trading_day(date(2026, 9, 14)))
        self.assertEqual(date(2026, 9, 11), self.view.recent_trading_day(date(2026, 9, 13)))


class TestEtfGuard(unittest.TestCase):
    """盘前 ETF 假 0 值不得入库（9/11 产生过脏行）。"""

    def test_zero_rows_rejected(self):
        import stock_report_agent as sra
        rows = [["沪深300ETF", "510300", "净申购", "b-red", "近一日净流入 0.00亿元"]]
        self.assertFalse(sra._etf_amount_valid(rows))

    def test_real_rows_accepted(self):
        import stock_report_agent as sra
        rows = [["沪深300ETF", "510300", "净流出", "b-green", "净流出 11.08亿元"]]
        self.assertTrue(sra._etf_amount_valid(rows))


class TestAiCapexGating(unittest.TestCase):
    """AI 资本开支章节按「数据指纹变化」呈现。

    该章节是季度频率的静态内容，每天重复占版面没有信息增量；
    无更新时不应出现，有更新时必须出现并带「本期更新」标注。
    """

    def setUp(self):
        import ai_capex
        self.ac = ai_capex
        seed = BASE / "seeds" / "ai_capex.json"
        if not seed.exists():
            self.skipTest("缺少 seeds/ai_capex.json")
        self.base = json.loads(seed.read_text(encoding="utf-8"))
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _with_baseline(self, d):
        return mock.patch.object(self.ac, "load_baseline", lambda: d)

    def test_fingerprint_stable_but_sensitive(self):
        fp = self.ac.data_fingerprint(self.base)
        self.assertEqual(fp, self.ac.data_fingerprint(self.base), "同数据指纹必须稳定")
        cosmetic = copy.deepcopy(self.base)
        cosmetic["as_of"] = "2099-01-01"
        cosmetic["note"] = "版面文案调整"
        self.assertEqual(fp, self.ac.data_fingerprint(cosmetic),
                         "装饰性字段变化不应触发章节重现")
        changed = copy.deepcopy(self.base)
        changed["hyperscalers"][0]["guidance_2026"] = "9999"
        self.assertNotEqual(fp, self.ac.data_fingerprint(changed),
                            "指引变化必须改变指纹")

    def test_presents_once_then_stays_silent(self):
        with self._with_baseline(self.base):
            first = self.ac.render_if_updated("auto", state_path=self.state)
            self.assertIn("sec-aicapex", first, "首次（无状态）应呈现")
            self.assertTrue(self.ac.commit_state("2026-09-14"))
            self.assertEqual("", self.ac.render_if_updated("auto", state_path=self.state),
                             "数据未更新时不应再次呈现")
            self.assertFalse(self.ac.commit_state("2026-09-15"), "未呈现时 commit 应为空操作")

    def test_presents_again_on_update_with_badge(self):
        changed = copy.deepcopy(self.base)
        h0 = changed["hyperscalers"][0]
        h0["guidance_path"] = (h0.get("guidance_path") or []) + [["9月", "新增指引"]]
        with self._with_baseline(self.base):
            self.ac.render_if_updated("auto", state_path=self.state)
            self.ac.commit_state("2026-09-14")
        with self._with_baseline(changed):
            html = self.ac.render_if_updated("auto", state_path=self.state)
        self.assertIn("sec-aicapex", html, "数据更新后必须重新呈现")
        self.assertIn("本期更新", html, "重新呈现时须标注本期更新")

    def test_never_and_always_modes(self):
        with self._with_baseline(self.base):
            self.assertEqual("", self.ac.render_if_updated("never", state_path=self.state))
            self.assertIn("sec-aicapex", self.ac.render_if_updated("always", state_path=self.state))


class TestArrTopicData(unittest.TestCase):
    """中美 ARR 专题：数据契约 + 可独立渲染（不依赖快照/DB）。"""

    def test_seed_contract(self):
        p = BASE / "seeds" / "arr_cn_us.json"
        if not p.exists():
            self.skipTest("缺少 seeds/arr_cn_us.json")
        d = json.loads(p.read_text(encoding="utf-8"))
        for k in ("as_of", "us_capex_2026", "cn_capex_2026", "arr_us", "arr_cn"):
            self.assertIn(k, d, f"数据文件缺少 {k}")
        self.assertTrue(d["us_capex_2026"].get("total"), "美国 capex 合计不可为空")
        self.assertTrue(d["arr_us"].get("rows"), "美国 ARR 行不可为空")
        self.assertTrue(d["arr_cn"].get("rows"), "中国 ARR 行不可为空")

    def test_renders_standalone_html(self):
        import arr_report
        d = arr_report.load_data()
        if not d:
            self.skipTest("无专题数据")
        html = arr_report.render(d, "2026-09-15")
        for anchor in ('id="s1"', 'id="s9"', "中美对比"):
            self.assertIn(anchor, html, f"专题报告缺少 {anchor}")
        self.assertNotIn("**", html, "Markdown 标记外泄")


class TestReportAnchors(unittest.TestCase):
    """报告章节缺失是最容易静默发生的回归（抓取失败→整章消失）。"""

    def setUp(self):
        self.path = latest_report()
        if self.path is None:
            self.skipTest("尚无报告 HTML")
        self.html = self.path.read_text(encoding="utf-8")

    def test_section_anchors_present(self):
        missing = [a for a in SECTION_ANCHORS if a not in self.html]
        self.assertEqual([], missing, f"{self.path.name} 缺少章节: {missing}")

    def test_section_count_matches_ai_capex_presence(self):
        """章节数须与 AI 资本开支章节是否出现一致（该章节按数据更新动态出现）。"""
        has = "sec-aicapex" in self.html
        self.assertIn(f"{10 if has else 9}章节", self.html,
                      f"章节数与 AI 资本开支章节存在性（{has}）不一致")

    def test_no_unrendered_placeholder_leak(self):
        """模板占位符外泄说明渲染分支出错。"""
        for bad in ("{{", "}}"):  # CSS 双大括号转义事故（8 月修过）
            self.assertNotIn(bad, self.html, f"报告残留未渲染标记 {bad}")


class TestEnvironmentContract(unittest.TestCase):
    """环境可重现性：requirements 必须覆盖代码真实用到的第三方依赖。"""

    def test_requirements_covers_key_deps(self):
        req = (BASE / "requirements.txt").read_text(encoding="utf-8")
        for pkg in ("psycopg2-binary", "czsc", "cryptography", "pandas", "numpy"):
            self.assertIn(pkg, req, f"requirements.txt 缺少 {pkg}")

    def test_modules_importable(self):
        for mod in ("view", "db", "backtest", "chan_analysis", "weibo_llm", "news_intel"):
            with self.subTest(module=mod):
                __import__(mod)

    def test_build_report_symbols(self):
        import build_report as br
        for fn in ("load_context", "etf_section", "market_width_section", "chan_section",
                   "core_conclusion", "_index_snapshot"):
            self.assertTrue(hasattr(br, fn), f"build_report 缺少 {fn}")

    def test_collector_has_budget_and_empty_snapshot_guard(self):
        """采集端必须保留「整体时间预算」与「空骨架快照保护」两道守卫。

        背景：2026-09-13 周报卡 1h45m、09-14 采集卡 4m54s 且产出空骨架快照，
        两者都会让主流程产出废报告。
        """
        src = (BASE / "stock_report_agent.py").read_text(encoding="utf-8")
        self.assertIn("_budget_ok(", src, "采集端缺少整体时间预算守卫")
        self.assertIn("BUDGET_SEC", src)
        self.assertIn("核心数据为空", src, "采集端缺少空骨架快照保护")
        # 行情必须先于舆情采集（否则舆情源会耗尽预算、行情全被跳过）
        # 注意：用带 _budget_ok( 前缀的精确锚点，避免匹配到同名的日志文案
        self.assertLess(src.index('_budget_ok("指数行情"'), src.index('_budget_ok(f"微博源'),
                        "行情采集必须排在舆情之前，避免预算被舆情耗尽")

    def test_rendering_layer_has_no_own_date_math(self):
        """口径必须来自 VIEW / DB，渲染层不得自行推算交易日。"""
        src = (BASE / "build_report.py").read_text(encoding="utf-8")
        self.assertNotIn("PRE_MARKET", src, "渲染层仍存在旧的散落盘前判断")
        for bad in ("timedelta(", "weekday()"):
            self.assertNotIn(bad, src, f"渲染层出现自行推算日期的代码: {bad}")


class TestDatabaseConsistency(unittest.TestCase):
    """DB 侧契约：不可用时整体 skip。"""

    @classmethod
    def setUpClass(cls):
        try:
            import json as _json
            from db import StockAgentDB
            cfg = _json.loads((BASE / "config.json").read_text(encoding="utf-8"))["database"]
            cls.db = StockAgentDB(host=cfg["host"], port=cfg.get("port", 5432),
                                  user=cfg["user"], password=cfg["password"],
                                  dbname=cfg["dbname"])
            with cls.db._cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as e:
            raise unittest.SkipTest(f"数据库不可用: {type(e).__name__}")

    def _rows(self, sql):
        with self.db._cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def test_ai_capex_tables_populated(self):
        """AI 资本开支三张表须存在且有数据（章节数据必须落库，不能只留 JSON 文件）。"""
        for t in ("ai_capex_quarters", "ai_capex_guidance_path", "ai_lab_commitments"):
            rows = self._rows(f"SELECT COUNT(*) FROM {t}")
            self.assertGreater(rows[0][0], 0, f"{t} 无数据（seeds/ai_capex.json 未落库？）")

    def test_index_quotes_has_unique_constraint(self):
        """index_quotes 必须有 (quote_date, index_name) 唯一约束。

        该表此前是裸表（无约束 + 纯 INSERT），每次采集追加一份，
        2026-09-14 一次清理出 131 行重复。
        """
        rows = self._rows("""SELECT conname FROM pg_constraint
                             WHERE conrelid='index_quotes'::regclass AND contype='u'""")
        self.assertTrue(rows, "index_quotes 缺少唯一约束（重复行会持续累积）")

    def test_index_quotes_no_weekend_rows(self):
        """指数行情不应落在周末（盘前错位的典型征兆）。"""
        rows = self._rows("SELECT DISTINCT quote_date FROM index_quotes "
                          "ORDER BY quote_date DESC LIMIT 30")
        bad = [r[0] for r in rows if r[0] and r[0].weekday() >= 5]
        self.assertEqual([], bad, f"index_quotes 存在周末日期（错位征兆）: {bad}")

    def test_index_quotes_complete_per_day(self):
        """每个交易日的指数条数应一致且不少于 5（缺行说明采集不完整）。"""
        rows = self._rows("SELECT quote_date, COUNT(*) FROM index_quotes "
                          "GROUP BY quote_date ORDER BY quote_date DESC LIMIT 5")
        for d, n in rows:
            self.assertGreaterEqual(n, 5, f"{d} 仅 {n} 条指数行情，可能不完整")

    def test_market_width_dates_are_trading_days(self):
        rows = self._rows("SELECT width_date FROM market_width ORDER BY width_date DESC LIMIT 10")
        bad = [r[0] for r in rows if r[0] and r[0].weekday() >= 5]
        self.assertEqual([], bad, f"market_width 存在周末日期: {bad}")

    def test_etf_no_all_zero_rows(self):
        """ETF 历史不应残留全 0 的假数据行。"""
        rows = self._rows("SELECT flow_date, COUNT(*) FROM etf_flows GROUP BY flow_date "
                          "HAVING SUM(ABS(amount)) = 0")
        self.assertEqual([], rows, f"存在全 0 的 ETF 假数据日: {rows}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
