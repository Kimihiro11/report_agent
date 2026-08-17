-- A股舆情Agent 数据库表结构
-- 数据库名: a_stock_agent
-- 用法: python db.py 自动初始化，或用psql手动执行本文件

CREATE DATABASE a_stock_agent;
\c a_stock_agent

-- 1. 每日报告（HTML全文入库）
CREATE TABLE IF NOT EXISTS daily_reports (
    id SERIAL PRIMARY KEY,
    report_date DATE NOT NULL,
    market_state VARCHAR(50),      -- 偏多/偏空/震荡
    summary TEXT,                  -- 核心结论摘要
    html_content TEXT,             -- 完整HTML报告
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 指数行情
CREATE TABLE IF NOT EXISTS index_quotes (
    id SERIAL PRIMARY KEY,
    quote_date DATE NOT NULL,
    index_name VARCHAR(50),        -- 上证指数/深证成指/创业板指/科创50/沪深300
    index_code VARCHAR(20),
    price NUMERIC(12,2),
    change_pct NUMERIC(8,2),
    volume VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 舆情数据（微博/全球人物/宏观/事件/技术 全部统一入库）
CREATE TABLE IF NOT EXISTS sentiment_data (
    id SERIAL PRIMARY KEY,
    record_date DATE NOT NULL,
    source_name VARCHAR(100),      -- 唐史主任司马迁/Elon Musk/中国CPI/中东局势...
    source_type VARCHAR(50),       -- weibo/global/macro/event/technical
    tier INT,                      -- 0=普通, 1=tier1, 2=tier2
    content TEXT,                  -- 内容摘要
    post_time VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. 自选股分析
CREATE TABLE IF NOT EXISTS stock_analysis (
    id SERIAL PRIMARY KEY,
    analysis_date DATE NOT NULL,
    stock_code VARCHAR(20),
    stock_name VARCHAR(50),
    price NUMERIC(12,2),
    change_pct NUMERIC(8,2),
    action VARCHAR(20),            -- 持有/加仓/观望/谨慎
    reasoning TEXT,                -- 操作理由
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. 技术指标
CREATE TABLE IF NOT EXISTS technical_indicators (
    id SERIAL PRIMARY KEY,
    analysis_date DATE NOT NULL,
    symbol VARCHAR(20),
    close_price NUMERIC(12,2),
    ma5 NUMERIC(12,2),
    ma10 NUMERIC(12,2),
    ma20 NUMERIC(12,2),
    ma60 NUMERIC(12,2),
    trend VARCHAR(50),             -- 多头排列(偏多)/空头排列(偏空)/震荡
    high5 NUMERIC(12,2),           -- 近5日最高
    low5 NUMERIC(12,2),            -- 近5日最低
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. 共振信号
CREATE TABLE IF NOT EXISTS resonance_signals (
    id SERIAL PRIMARY KEY,
    signal_date DATE NOT NULL,
    signal_name VARCHAR(200),      -- 如"AI算力硬件5重共振"
    resonance_level INT,           -- 共振重数(3/4/5)
    sources TEXT,                  -- 共振来源(唐史+投星+马斯克+资金+宏观)
    confidence VARCHAR(20),        -- 高/中/低
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引（加速按日期查询）
CREATE INDEX IF NOT EXISTS idx_reports_date ON daily_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_quotes_date ON index_quotes(quote_date);
CREATE INDEX IF NOT EXISTS idx_sentiment_date ON sentiment_data(record_date);
CREATE INDEX IF NOT EXISTS idx_stock_analysis_date ON stock_analysis(analysis_date);
CREATE INDEX IF NOT EXISTS idx_technical_date ON technical_indicators(analysis_date);
CREATE INDEX IF NOT EXISTS idx_resonance_date ON resonance_signals(signal_date);
