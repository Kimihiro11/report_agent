# A股舆情操作指引 Agent

基于实时数据生成的 A 股舆情 / 宏观传导链 / 操作指引报告系统。每日输出 9 章节分析报告（早报 / 晚报 / 周报），并内置「限时关注的重点数据解析」宏观引爆信号监测模块。

## 目录结构

```
report_agent/
├── a_stock_agent.py          # 数据引擎：实时采集 → data/snapshots 快照 → 强制入库 PostgreSQL
├── build_report_20260816.py  # 报告生成器：消费快照渲染 9 章节 HTML（含焦点模块嵌入）
├── backtest.py               # 回测与交叉验证（1/3/5 交易日窗口）
├── db.py                     # PostgreSQL 封装（11 张表 + upsert + 探活 + 自愈合建表）
├── focus_monitor.py          # 限时关注：日元主导传导链末端引爆信号监测（抛美债/FIMA/大机构加息）
├── stock_diagnosis.py        # 自选股实时诊断（调用 peak_detector）
├── peak_detector.py          # 见顶 / 技术诊断引擎（被 stock_diagnosis 引用，须与之上同目录）
│
├── config.json               # 全部配置（含 weibo_cookie / 数据库凭证，**已被 gitignore 排除**）
├── requirements.txt          # Python 依赖
├── README.md                 # 本文件
│
├── docs/                     # 项目文档
│   ├── A股舆情操作指引Agent-功能说明书.md
│   └── 报告类型与手动运行规范.md
│
├── sql/                      # 数据库 schema
│   └── init_db.sql
│
├── seeds/                    # 回测种子（版本控制）
│   ├── judgments.json        # 个股判断本地存储（DB 不可用时的兜底）
│   └── backtest_results.json # 回测结果本地存储
│
├── tools/                    # 一次性 / 工具脚本（含硬编码历史路径，非日常运行）
│   ├── ingest_reports.py     # 将历史报告 HTML 入库（一次性）
│   └── verify_ingest.py      # 入库校验（一次性）
│
├── data/                     # 运行时产物（data/ 已被 gitignore 排除）
│   ├── snapshots/            # 数据引擎每次采集的 JSON 快照（fetched_YYYYMMDD_HHMMSS.json，**不入库**，自动清理≤2天）
│   ├── diagnosis/            # 个股诊断缓存（diagnosis_YYYYMMDD.json，按需版本化）
│   └── focus/                # 焦点监控状态（focus_state_YYYYMMDD.json，按需版本化）
│
├── reports/                  # 产出报告（版本控制）
│   ├── 早报/  晚报/  周报/  回测/  早期版本/  限时关注/
│
└── archive/                  # 历史版本归档（按日期）
```

## 各文件职责（速查）

| 文件 | 职责 |
|------|------|
| `a_stock_agent.py` | 数据引擎：微博舆情 / A股行情 / 美股 / ETF 资金流 / 宏观 / 原油 / 日本传导链实时采集 → 写 `data/snapshots/` 快照 → 强制全量入库 PostgreSQL（连接不通打印醒目 ⚠️ 告警）。含多源兜底框架与东方财富妙想技能包装。 |
| `build_report_20260816.py` | 实时 9 章节报告模板。读取当日 `data/snapshots/` 最新快照 + 诊断 + 焦点模块，渲染 HTML。支持 `--date` / `--type 早报\|晚报\|周报`。 |
| `backtest.py` | 回测与交叉验证（方向命中 + 量价技术信号交叉验证）。`--all` 生成回测报告，`--seed` 解析报告 HTML 写入 `seeds/`。 |
| `db.py` | PostgreSQL 封装：11 张表、`upsert`、探活 `test_connection`、自愈合 `init_database`。 |
| `focus_monitor.py` | 监测三类日元主导传导链末端引爆信号（抛美债 / FIMA 工具 / 大机构日元加息），代理感知的 Google News + Bing 抓取，mention+action 排除 negation 判定；输出专业研判结论。CLI：`--no-fetch` / `--days N`。 |
| `stock_diagnosis.py` | 对 `config.json` 自选股做个股层面风险诊断，调用 `peak_detector`。 |
| `peak_detector.py` | 见顶 / 技术诊断引擎，被 `stock_diagnosis` 以 `from peak_detector import ...` 引用，**须与 `stock_diagnosis.py` 同目录（根目录）**。 |

## 运行流程（手动）

```
1) 数据引擎（采集 + 强制入库）
   python a_stock_agent.py

2) 生成报告（消费快照）
   python build_report_20260816.py --date YYYY-MM-DD --type 早报|晚报|周报
   （内置焦点模块会在当日无缓存时自动实时抓取，外网不可达则显示「实时数据缺失」占位）

3) 回测（晚报/周报之后）
   python backtest.py --all

4) 限时关注监测（独立 / 已嵌入报告）
   python focus_monitor.py            # 实时爬取 + 检测
   python focus_monitor.py --no-fetch # 渲染上次缓存
```

## 配置与依赖

- **配置**：`config.json`（被 gitignore 排除，含敏感凭证）。修改自选股 / 微博 cookie / 数据库 / 宏观参数均在此。
- **依赖**：见 `requirements.txt`。项目虚拟环境为 `.venv`（已装 numpy / pandas / psycopg2-binary / requests / httpx / openpyxl）。**注意**：`psycopg2` 仅存在于 `.venv`，用管理版 Python 运行时跑入库会报 `No module named 'psycopg2'`，请用 `.venv/Scripts/python.exe` 运行涉及入库的命令。
- **数据库**：PostgreSQL。连接不通时程序打印醒目 ⚠️ 告警并提示检查服务/配置，**不会静默跳过**；本地 `data/snapshots/*.json` 仅作缓存兜底，自动清理最多保留 2 天。

## 不变式（重要约定）

1. 涨跌颜色（中国习惯）：涨=红 `#d63031`、跌=绿 `#00a865`，不可颠倒。
2. 9 章节报告结构固定；新增章节只能插在「今日操作策略」之前。
3. 自选股固定 7 只（见 `config.json` 与 `backtest.py` 的 `WATCHLIST_NAME`）。
4. 报告必须用实时数据，源码中无硬编码数值；数据缺口渲染为「实时数据缺失」占位，绝不出现假数。
5. `config.json` 与 `data/` 不纳入版本控制（密钥 / 运行时产物）；`seeds/`、`reports/`、`docs/`、`sql/`、`tools/` 与核心脚本纳入版本控制。
