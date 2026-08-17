#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库模块 - A股舆情Agent数据入库
用法:
    python db.py          # 初始化数据库和表
    在 a_stock_agent.py 中导入使用
"""
import psycopg2
from datetime import date


class StockAgentDB:
    def __init__(self, host="localhost", port=5432, user="postgres", password="", dbname="a_stock_agent"):
        self.conn_params = dict(host=host, port=port, user=user, password=password)
        self.dbname = dbname

    def init_database(self):
        """创建数据库和所有表"""
        conn = psycopg2.connect(**self.conn_params, dbname="postgres")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.dbname,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE {self.dbname}')
            print(f"[DB] 数据库 {self.dbname} 创建成功")
        else:
            print(f"[DB] 数据库 {self.dbname} 已存在")
        cur.close()
        conn.close()

        conn = psycopg2.connect(**self.conn_params, dbname=self.dbname)
        conn.autocommit = True
        cur = conn.cursor()
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
        ]
        for sql in tables:
            cur.execute(sql)
        print(f"[DB] 创建/检查 {len(tables)} 张表完成")
        cur.close()
        conn.close()

    def _conn(self):
        return psycopg2.connect(**self.conn_params, dbname=self.dbname)

    def save_report(self, report_date, market_state, summary, html_content):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO daily_reports (report_date, market_state, summary, html_content) VALUES (%s,%s,%s,%s)",
            (report_date, market_state, summary, html_content),
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] 报告入库: {report_date}")

    def save_index_quotes(self, quote_date, quotes):
        conn = self._conn()
        cur = conn.cursor()
        for name, q in quotes.items():
            cur.execute(
                "INSERT INTO index_quotes (quote_date, index_name, price, change_pct, volume) VALUES (%s,%s,%s,%s,%s)",
                (quote_date, name, q.get("price"), q.get("chg_pct"), str(q.get("volume", ""))),
            )
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] 指数行情入库: {len(quotes)} 条")

    def save_sentiment_batch(self, record_date, weibo_data):
        conn = self._conn()
        cur = conn.cursor()
        count = 0
        for source_name, posts in weibo_data.items():
            if source_name.startswith("[全球]"):
                stype, tier, name = "global", 2, source_name.replace("[全球] ", "")
            elif source_name.startswith("[宏观]"):
                stype, tier, name = "macro", 0, source_name.replace("[宏观] ", "")
            elif source_name.startswith("[事件]"):
                stype, tier, name = "event", 0, source_name.replace("[事件] ", "")
            elif source_name.startswith("[技术]"):
                stype, tier, name = "technical", 0, source_name.replace("[技术] ", "")
            else:
                stype, tier, name = "weibo", 0, source_name
            for post in posts:
                cur.execute(
                    "INSERT INTO sentiment_data (record_date, source_name, source_type, tier, content, post_time) VALUES (%s,%s,%s,%s,%s,%s)",
                    (record_date, name, stype, tier, post.get("text", ""), post.get("time", "")),
                )
                count += 1
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] 舆情数据入库: {count} 条")

    def save_technical(self, analysis_date, ta_data):
        conn = self._conn()
        cur = conn.cursor()
        for symbol, kline in ta_data.items():
            cur.execute(
                """INSERT INTO technical_indicators
                (analysis_date, symbol, close_price, ma5, ma10, ma20, ma60, trend, high5, low5)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (analysis_date, symbol, kline.get("close"), kline.get("ma5"), kline.get("ma10"),
                 kline.get("ma20"), kline.get("ma60"), kline.get("trend"), kline.get("high5"), kline.get("low5")),
            )
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] 技术指标入库: {len(ta_data)} 条")


if __name__ == "__main__":
    db = StockAgentDB(password="1q2w3e4r")
    db.init_database()
    print("数据库初始化完成，可运行 a_stock_agent.py 开始入库")
