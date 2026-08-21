#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库模块 - A股舆情Agent数据入库
用法:
    python db.py          # 初始化数据库和表
    在 a_stock_agent.py 中导入使用
"""
import psycopg2
from contextlib import contextmanager
from psycopg2 import sql as _sql


class StockAgentDB:
    def __init__(self, host="localhost", port=5432, user="postgres", password="", dbname="a_stock_agent"):
        self.conn_params = dict(host=host, port=port, user=user, password=password)
        self.dbname = dbname

    def _ensure_db_exists(self):
        """连 postgres 数据库，确认目标库存在；不存在则创建。"""
        conn = psycopg2.connect(**self.conn_params, dbname="postgres")
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.dbname,))
            if not cur.fetchone():
                cur.execute(_sql.SQL('CREATE DATABASE {}').format(_sql.Identifier(self.dbname)))
                print(f"[DB] 数据库 {self.dbname} 创建成功")
            else:
                print(f"[DB] 数据库 {self.dbname} 已存在")
            cur.close()
        finally:
            conn.close()

    def _ensure_tables(self):
        """在目标库中创建所有必要的表。"""
        tables = [
            """CREATE TABLE IF NOT EXISTS daily_reports (
                id SERIAL PRIMARY KEY,
                report_date DATE NOT NULL,
                market_state VARCHAR(50),
                summary TEXT,
                html_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS index_quotes (
                id SERIAL PRIMARY KEY,
                quote_date DATE NOT NULL,
                index_name VARCHAR(50),
                index_code VARCHAR(20),
                price NUMERIC(12,2),
                change_pct NUMERIC(8,2),
                volume VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS sentiment_data (
                id SERIAL PRIMARY KEY,
                record_date DATE NOT NULL,
                source_name VARCHAR(100),
                source_type VARCHAR(50),
                tier INT,
                content TEXT,
                post_time VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS stock_analysis (
                id SERIAL PRIMARY KEY,
                analysis_date DATE NOT NULL,
                stock_code VARCHAR(20),
                stock_name VARCHAR(50),
                price NUMERIC(12,2),
                change_pct NUMERIC(8,2),
                action VARCHAR(20),
                reasoning TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS technical_indicators (
                id SERIAL PRIMARY KEY,
                analysis_date DATE NOT NULL,
                symbol VARCHAR(20),
                close_price NUMERIC(12,2),
                ma5 NUMERIC(12,2),
                ma10 NUMERIC(12,2),
                ma20 NUMERIC(12,2),
                ma60 NUMERIC(12,2),
                trend VARCHAR(50),
                high5 NUMERIC(12,2),
                low5 NUMERIC(12,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS resonance_signals (
                id SERIAL PRIMARY KEY,
                signal_date DATE NOT NULL,
                signal_name VARCHAR(200),
                resonance_level INT,
                sources TEXT,
                confidence VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS stock_judgments (
                id SERIAL PRIMARY KEY,
                report_date DATE NOT NULL,
                stock_code VARCHAR(20),
                stock_name VARCHAR(50),
                action VARCHAR(30),
                direction VARCHAR(10),
                rationale TEXT,
                source_file VARCHAR(200),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(report_date, stock_code)
            )""",
            """CREATE TABLE IF NOT EXISTS backtest_results (
                id SERIAL PRIMARY KEY,
                judgment_date DATE NOT NULL,
                stock_code VARCHAR(20),
                stock_name VARCHAR(50),
                action VARCHAR(30),
                direction VARCHAR(10),
                window_days INT,
                entry_close NUMERIC(12,2),
                exit_close NUMERIC(12,2),
                ret_pct NUMERIC(8,2),
                direction_hit BOOLEAN,
                tech_signal VARCHAR(10),
                tech_agree BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(judgment_date, stock_code, window_days)
            )""",
            """CREATE TABLE IF NOT EXISTS raw_snapshots (
                id SERIAL PRIMARY KEY,
                snapshot_date DATE NOT NULL,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS us_market_quotes (
                id SERIAL PRIMARY KEY,
                quote_date DATE NOT NULL,
                name VARCHAR(50),
                change_pct NUMERIC(8,2),
                price VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS etf_flows (
                id SERIAL PRIMARY KEY,
                flow_date DATE NOT NULL,
                name VARCHAR(50),
                code VARCHAR(20),
                direction VARCHAR(20),
                amount NUMERIC(12,2),
                signal TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS daily_klines (
                id SERIAL PRIMARY KEY,
                stock_code VARCHAR(20) NOT NULL,
                trade_date DATE NOT NULL,
                open NUMERIC(12,2),
                high NUMERIC(12,2),
                low NUMERIC(12,2),
                close NUMERIC(12,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, trade_date)
            )""",
            # 回测结果扩展列（幂等，兼容旧表）
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS status VARCHAR(10)",
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS price_source VARCHAR(10)",
            "ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS note TEXT",
        ]
        with self._cursor() as cur:
            for sql in tables:
                cur.execute(sql)
        print(f"[DB] 创建/检查 {len(tables)} 张表完成")

    def init_database(self):
        """创建数据库和所有表"""
        self._ensure_db_exists()
        self._ensure_tables()

    def _conn(self):
        return psycopg2.connect(**self.conn_params, dbname=self.dbname, connect_timeout=8)

    @contextmanager
    def _cursor(self):
        """统一连接/游标管理：正常结束 commit，异常 rollback 并上抛，最终保证关闭。"""
        conn = self._conn()
        try:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def test_connection(self, timeout=5):
        """快速探活：成功返回 True，失败抛异常（由调用方捕获并提醒）。"""
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            return True
        finally:
            conn.close()

    def save_report(self, report_date, market_state, summary, html_content):
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO daily_reports (report_date, market_state, summary, html_content) VALUES (%s,%s,%s,%s)",
                (report_date, market_state, summary, html_content),
            )
        print(f"[DB] 报告入库: {report_date}")

    def save_index_quotes(self, quote_date, quotes):
        with self._cursor() as cur:
            for name, q in quotes.items():
                cur.execute(
                    "INSERT INTO index_quotes (quote_date, index_name, price, change_pct, volume) VALUES (%s,%s,%s,%s,%s)",
                    (quote_date, name, q.get("price"), q.get("chg_pct"), str(q.get("volume", ""))),
                )
        print(f"[DB] 指数行情入库: {len(quotes)} 条")

    def save_sentiment_batch(self, record_date, weibo_data, source_patterns=None):
        """批量写入舆情数据。

        source_patterns: 可选配置列表，每项 {"prefix": "[全球]", "type": "global", "tier": 2}。
        未提供时使用内置默认规则解析 source_type/tier。
        """
        count = 0
        default_patterns = [
            {"prefix": "[全球]", "type": "global", "tier": 2},
            {"prefix": "[宏观]", "type": "macro", "tier": 0},
            {"prefix": "[事件]", "type": "event", "tier": 0},
            {"prefix": "[技术]", "type": "technical", "tier": 0},
        ]
        patterns = source_patterns or default_patterns

        def classify(name):
            for p in patterns:
                prefix = p.get("prefix", "")
                if name.startswith(prefix):
                    clean = name.replace(prefix, "").strip()
                    return p.get("type", "weibo"), p.get("tier", 0), clean
            return "weibo", 0, name

        with self._cursor() as cur:
            for source_name, posts in weibo_data.items():
                stype, tier, name = classify(source_name)
                for post in posts:
                    cur.execute(
                        "INSERT INTO sentiment_data (record_date, source_name, source_type, tier, content, post_time) VALUES (%s,%s,%s,%s,%s,%s)",
                        (record_date, name, stype, tier, post.get("text", ""), post.get("time", "")),
                    )
                    count += 1
        print(f"[DB] 舆情数据入库: {count} 条")

    def save_technical(self, analysis_date, ta_data):
        with self._cursor() as cur:
            for symbol, kline in ta_data.items():
                cur.execute(
                    """INSERT INTO technical_indicators
                    (analysis_date, symbol, close_price, ma5, ma10, ma20, ma60, trend, high5, low5)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (analysis_date, symbol, kline.get("close"), kline.get("ma5"), kline.get("ma10"),
                     kline.get("ma20"), kline.get("ma60"), kline.get("trend"), kline.get("high5"), kline.get("low5")),
                )
        print(f"[DB] 技术指标入库: {len(ta_data)} 条")

    def save_judgments(self, judgments):
        """批量写入每日个股判断（按 report_date+stock_code upsert）"""
        n = 0
        with self._cursor() as cur:
            for j in judgments:
                cur.execute(
                    """INSERT INTO stock_judgments
                    (report_date, stock_code, stock_name, action, direction, rationale, source_file)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (report_date, stock_code) DO UPDATE SET
                        stock_name=EXCLUDED.stock_name, action=EXCLUDED.action,
                        direction=EXCLUDED.direction, rationale=EXCLUDED.rationale,
                        source_file=EXCLUDED.source_file""",
                    (j["report_date"], j["stock_code"], j["stock_name"], j["action"],
                     j["direction"], j.get("rationale", ""), j.get("source_file", "")),
                )
                n += 1
        print(f"[DB] 个股判断入库: {n} 条")

    def get_judgments(self, as_of=None):
        """读取个股判断；as_of 为日期字符串(YYYY-MM-DD)时只取当日"""
        with self._cursor() as cur:
            if as_of:
                cur.execute(
                    "SELECT report_date,stock_code,stock_name,action,direction,rationale,source_file "
                    "FROM stock_judgments WHERE report_date=%s ORDER BY stock_code", (as_of,))
            else:
                cur.execute(
                    "SELECT report_date,stock_code,stock_name,action,direction,rationale,source_file "
                    "FROM stock_judgments ORDER BY report_date, stock_code")
            rows = cur.fetchall()
        return [
            dict(report_date=r[0], stock_code=r[1], stock_name=r[2], action=r[3],
                 direction=r[4], rationale=r[5], source_file=r[6])
            for r in rows
        ]

    def ensure_extras(self):
        """运行时自愈：确保 daily_klines 表与 backtest_results 扩展列存在（幂等）。"""
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS daily_klines (
                    id SERIAL PRIMARY KEY,
                    stock_code VARCHAR(20) NOT NULL,
                    trade_date DATE NOT NULL,
                    open NUMERIC(12,2),
                    high NUMERIC(12,2),
                    low NUMERIC(12,2),
                    close NUMERIC(12,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(stock_code, trade_date)
                )""")
            for col in ["status VARCHAR(10)", "price_source VARCHAR(10)", "note TEXT"]:
                cur.execute(f"ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS {col}")

    def save_backtest_results(self, results):
        """批量写入回测结果（按 judgment_date+stock_code+window_days upsert）"""
        self.ensure_extras()
        n = 0
        with self._cursor() as cur:
            for r in results:
                cur.execute(
                    """INSERT INTO backtest_results
                    (judgment_date, stock_code, stock_name, action, direction, window_days,
                     entry_close, exit_close, ret_pct, direction_hit, tech_signal, tech_agree,
                     status, price_source, note)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (judgment_date, stock_code, window_days) DO UPDATE SET
                        stock_name=EXCLUDED.stock_name, action=EXCLUDED.action, direction=EXCLUDED.direction,
                        entry_close=EXCLUDED.entry_close, exit_close=EXCLUDED.exit_close,
                        ret_pct=EXCLUDED.ret_pct, direction_hit=EXCLUDED.direction_hit,
                        tech_signal=EXCLUDED.tech_signal, tech_agree=EXCLUDED.tech_agree,
                        status=EXCLUDED.status, price_source=EXCLUDED.price_source, note=EXCLUDED.note""",
                    (r["judgment_date"], r["stock_code"], r["stock_name"], r["action"], r["direction"],
                     r["window_days"], r["entry_close"], r["exit_close"], r["ret_pct"],
                     r["direction_hit"], r["tech_signal"], r["tech_agree"],
                     r.get("status"), r.get("price_source"), r.get("note")),
                )
                n += 1
        print(f"[DB] 回测结果入库: {n} 条")

    def save_klines(self, stock_code, klines):
        """批量写入/更新个股日K线（按 stock_code+trade_date upsert）"""
        self.ensure_extras()
        n = 0
        with self._cursor() as cur:
            for k in klines:
                cur.execute(
                    """INSERT INTO daily_klines (stock_code, trade_date, open, high, low, close)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                        open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close""",
                    (stock_code, k["date"], k.get("open"), k.get("high"), k.get("low"), k.get("close")),
                )
                n += 1
        print(f"[DB] 日K线入库: {stock_code} {n} 根")

    def get_klines(self, stock_code):
        """读取个股全部日K线，按日期升序返回 [{date,open,close,high,low}]"""
        self.ensure_extras()
        with self._cursor() as cur:
            cur.execute(
                "SELECT trade_date, open, close, high, low FROM daily_klines "
                "WHERE stock_code=%s ORDER BY trade_date", (stock_code,))
            rows = cur.fetchall()
        return [
            {"date": r[0].isoformat(), "open": float(r[1]), "close": float(r[2]),
             "high": float(r[3]), "low": float(r[4])}
            for r in rows
        ]

    def save_snapshot(self, snapshot_date, payload):
        """保存完整原始快照（所有采集数据）为 JSON，确保『全部数据入库』。"""
        import json as _json
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO raw_snapshots (snapshot_date, payload) VALUES (%s,%s)",
                (snapshot_date, _json.dumps(payload, ensure_ascii=False)),
            )
        print(f"[DB] 原始快照入库: {snapshot_date}")

    def save_us_market(self, quote_date, us_market):
        """隔夜美股行情入库。us_market: [(name, pct, price, signal), ...]"""
        n = 0
        with self._cursor() as cur:
            for name, pct, price, signal in us_market:
                cur.execute(
                    "INSERT INTO us_market_quotes (quote_date, name, change_pct, price) VALUES (%s,%s,%s,%s)",
                    (quote_date, name, pct, str(price)),
                )
                n += 1
        print(f"[DB] 美股行情入库: {n} 条")

    def save_etf_flows(self, flow_date, etf):
        """ETF 资金流入库。etf: [(name, code, direction, cls, signal), ...]"""
        import re as _re
        n = 0
        with self._cursor() as cur:
            for name, code, direction, cls, signal in etf:
                m = _re.search(r"(净流入|净流出)\s*([\d.]+)\s*亿元", signal or "")
                amount = float(m.group(2)) * (1 if m.group(1) == "净流入" else -1) if m else None
                cur.execute(
                    "INSERT INTO etf_flows (flow_date, name, code, direction, amount, signal) VALUES (%s,%s,%s,%s,%s,%s)",
                    (flow_date, name, code, direction, amount, signal),
                )
                n += 1
        print(f"[DB] ETF资金流入库: {n} 条")


if __name__ == "__main__":
    db = StockAgentDB(password="1q2w3e4r")
    db.init_database()
    print("数据库初始化完成，可运行 a_stock_agent.py 开始入库")
