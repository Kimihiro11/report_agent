# A股舆情操作指引 Agent

基于实时数据生成的 A 股舆情 / 宏观传导链 / 操作指引报告系统。每日输出 **9 章节**报告
（早报 / 晚报 / 周报），并内置「限时关注」——专攻日本央行（BOJ）加息程度研判
（抓取各大所英文研报与观点、解析正文、合成一致预期）。

- **目录与分层、依赖图、命令清单 → [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**
- **data/ 产物目录的寿命与清理策略 → [`data/README.md`](data/README.md)**
- **功能说明书 → [`docs/A股舆情操作指引Agent-功能说明书.md`](docs/A股舆情操作指引Agent-功能说明书.md)**
- **运行规范 → [`docs/报告类型与手动运行规范.md`](docs/报告类型与手动运行规范.md)**

## 目录结构（2026-09-21 包化后）

```
report_agent/
├── ra/                       # 全部实现
│   ├── cli.py                # 统一 CLI 分发器（子命令表 / 历史名别名 / runpy 转调）
│   ├── __main__.py           # 支持 python -m ra <子命令>
│   ├── paths.py              # 项目根与 data 分层常量（唯一来源）
│   ├── stock_report_agent.py # 采集主编排（数据引擎）
│   ├── infra/                # db / view / charts / llm_client
│   ├── sources/              # momentum / oil / news_intel / ai_capex
│   ├── analysis/             # peak_detector / stock_diagnosis / aggressive_analysis
│   │                         # chan_analysis / focus_monitor / weibo_llm / backtest
│   └── report/               # build_report / chan_report / arr_report
│
├── cli.py                    # **全项目唯一命令入口**：python cli.py <子命令> [参数]
│                             #   （薄壳，实现在 ra/cli.py；python cli.py list 看全部子命令）
│
├── config.json               # 全部配置（含 weibo_cookie / DB 凭证），**gitignore 排除**
├── requirements.txt
├── docs/                     # ARCHITECTURE / 功能说明书 / 运行规范 / 代码审查报告
├── sql/                      # 数据库 schema
├── seeds/                    # 人工维护种子：judgments / backtest_results / ai_capex / arr_cn_us
├── templates/                # 提示词与报告静态资源（prompts.py / style.css / *.html）
├── tools/                    # 运维工具（自选股同步、上传去重、注入、cookie 提取…）
├── tests/                    # test_health.py：50 项契约断言
├── data/                     # 运行时产物（gitignore，仅 README.md 入库）
│   ├── snapshots/            #   ← 采集快照（唯一原始输入，保留 2 天）
│   ├── daily/                #   ← 按日产物：momentum / oil / news_intel
│   ├── derived/              #   ← 按日派生（含模型注入，勿删）：diagnosis / weibo_deep / focus / chan
│   ├── state/                #   ← 跨日状态（覆盖写）：ai_capex_state / westock_*_override
│   └── backup/               #   ← 配置与数据备份
└── reports/                  # 产出报告（入库）：早报|晚报|周报|回测|专题|早期版本
```

## 核心模块速查

| 子命令 | 模块（`ra/` 下） | 职责 |
|---|---|---|
| `collect` | `stock_report_agent.py` | 数据引擎：微博舆情 / A股行情 / 美股 / ETF 资金流 / 宏观 / 原油 / 中美动量 / 日本传导链采集 → 写 `data/snapshots/` → 全量入库 PostgreSQL。含 180s 时间预算、空骨架保护、多源兜底。 |
| `report` | `report/build_report.py` | 9 章节报告渲染，读快照 + 诊断 + 动量 + 原油 + 资讯 + 微博解构。`--date` / `--type 早报\|晚报\|周报`。 |
| `backtest` | `analysis/backtest.py` | 回测与交叉验证（1/3/5 交易日窗口）。`--seed` 解析报告写 `seeds/`，`--run` 全量回测出报告。**不自动跑，仅用户明确要求时执行。** |
| `news` | `sources/news_intel.py` | 外网资讯解析：英文 query 抓取 + publisher 正文解析 → `data/daily/news_intel/`；中文摘要在 `summary_mode=agent_inject` 下由模型注入。 |
| `oil` | `sources/oil.py` | 原油 WTI/Brent 双口径（新浪外盘期货）+ RSS 关键词情绪打分。 |
| `momentum` | `sources/momentum.py` | 中美动量对照：美国 MTUM vs 中国科技指数（**中国侧必须用指数**，ETF 拆分致未复权失真）。 |
| `focus` | `analysis/focus_monitor.py` | BOJ 加息程度研判：抓 10 家投行英文研报并解析正文 → 合成一致预期与分歧。 |
| `chan` / `chan-report` | `analysis/chan_analysis.py` / `report/chan_report.py` | 缠论三级别推演与独立专题报告。 |
| `arr` | `report/arr_report.py` | 中美 AI 资本开支与 ARR 对比专题。 |
| — | `infra/view.py` | **口径层**：`basis`(pre_market/intraday/close) / `data_date` / `as_of` 的唯一来源。渲染层禁止自算交易日。 |
| `db` | `infra/db.py` | PostgreSQL 封装：建表自愈、upsert、探活。 |
| `charts` | `infra/charts.py` | 通用 SVG 图表（`line_chart` / `bar_chart`），新章节优先复用。 |
| `peak` | `analysis/peak_detector.py` | 见顶 / 技术诊断引擎，被 `stock_diagnosis` 引用。 |
| `watchlist` `upload` `inject-weibo` `inject-news` … | `tools/*.py` | 运维工具，完整清单见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 第五节。 |

> 完整子命令列表：`python cli.py list`。历史脚本名（如 `build_report`）仍可作为别名使用。

## 运行流程（手动）

```bash
# 1) 采集 + 强制入库
python cli.py collect

# 2) 外网资讯（英文源 + 正文解析）
python cli.py news --date YYYY-MM-DD
#    → 写入 data/daily/news_intel/news_intel_YYYYMMDD.json
#    → Agent 读 content_en 总结中文，经 tools/inject_news_intel.py 回填

# 3) 报告
python cli.py report --date YYYY-MM-DD --type 早报|晚报|周报

# 4) 回测（仅用户明确要求时）
python cli.py backtest --seed && python cli.py backtest --run

# 5) 上传资料库（必须走去重工具）
printf '%s' "<token>" | python cli.py upload <html 绝对路径>
```

## 配置与依赖

- **配置**：`config.json`（gitignore 排除，含敏感凭证）。自选股 / 微博 cookie / 数据库 / 时段参数均在此。
- **依赖**：`.venv`（numpy / pandas / psycopg2-binary / requests / czsc / cryptography 等）。
  ⚠️ `psycopg2` 仅在 `.venv`，涉入库的命令请用 `.venv/Scripts/python.exe`。
- **数据库**：PostgreSQL，跑在 Docker 容器 `my-postgres`（5432）。连接失败会打印醒目 ⚠️ 告警，**不静默跳过**。

## 不变式（重要约定）

1. 涨跌颜色（中国习惯）：涨 = 红 `#d63031`、跌 = 绿 `#00a865`，不可颠倒。
2. **9 章节结构固定**，新章节只能插在核心结论之后；缠论已独立成报。增删章节须同步 5 处。
3. 自选股 = `config.json` 的 `watchlist_stocks`（当前 **10 只**），**数量禁写死**。增删用
   `python cli.py watchlist`，并同步微博解构重注入。
4. 报告必须用实时数据，源码无硬编码数值；数据缺口渲染「实时数据缺失」，绝不造假。
5. `config.json` 与 `data/` 不入库（`data/README.md` 除外）；`seeds/` `reports/` `docs/` `sql/` `tools/` `tests/` `templates/` 与 `ra/` 入库。
6. **报告 HTML 上传/预览会被回写 `data-page-node-id` 属性**，且可延迟落地 → 提交前必须直接统计属性数，与 `git add` 放同一条命令。
