# 架构与目录导览

> 2026-09-21 包化重构后定稿。改动代码前先读本文 + `.workbuddy/memory/MEMORY.md`（不变式与历史坑）。

## 一、总览

```
report_agent/
├── ra/                     ← 全部实现（Python 包）
│   ├── paths.py            项目根与 data/ 分层常量（唯一来源）
│   ├── stock_report_agent.py   采集主编排（数据引擎）
│   ├── infra/              基础设施（无业务语义）
│   │   ├── db.py           PostgreSQL 封装
│   │   ├── view.py         口径层：basis / data_date / as_of（唯一来源）
│   │   ├── charts.py       通用 SVG 图表（line_chart / bar_chart）
│   │   └── llm_client.py   LLM 调用封装
│   ├── sources/            数据源采集（对外抓取 → data/）
│   │   ├── momentum.py     中美动量对照（MTUM × 中国科技指数）
│   │   ├── oil.py          原油价格与舆情（WTI / Brent）
│   │   ├── news_intel.py   外网资讯抓取与解析
│   │   └── ai_capex.py     AI 资本开支（指纹门控）
│   ├── analysis/           分析引擎（算指标 / 出判断）
│   │   ├── peak_detector.py       见顶诊断核心
│   │   ├── stock_diagnosis.py     自选股诊断编排
│   │   ├── aggressive_analysis.py 激进视角（进攻单元）
│   │   ├── chan_analysis.py       缠论推演引擎（czsc）
│   │   ├── focus_monitor.py       限时关注（日银加息程度）
│   │   ├── weibo_llm.py           微博结构化解构
│   │   └── backtest.py            回测与判断沉淀
│   └── report/             报告渲染
│       ├── build_report.py   日报（早报 / 晚报 / 周报）
│       ├── chan_report.py    缠论推演专题
│       └── arr_report.py     中美 AI 资本开支与 ARR 专题
│
├── <19 个同名 .py>          ← **兼容壳**（自动生成，勿手改），见第三节
├── templates/              提示词与报告静态资源（prompts.py / style.css / *.html / sentiment_words.json）
├── seeds/                  人工维护的种子数据（judgments / backtest_results / ai_capex / arr_cn_us）
├── data/                   运行时产物（不入库，仅 README.md 入库）→ 见 data/README.md
├── reports/                报告输出（入库）：早报|晚报|周报|回测|专题|早期版本
├── docs/                   文档
├── tools/                  运维工具（见第五节）
├── tests/                  test_health.py（50 项契约断言）
├── sql/                    建表语句
├── miaoxiang/              妙想（东财）查询输出（不入库）
└── config.json             运行配置（不入库，含 cookie/DB 口令）
```

## 二、分层与依赖方向

依赖是单向的，**不得反向**：

```
        report/  （报告渲染：可依赖下面所有层）
           ↓
      analysis/  （分析引擎：可依赖 sources + infra）
           ↓
       sources/  （数据源：可依赖 infra）
           ↓
        infra/   （基础设施：不依赖任何业务层）
```

实测依赖图（脚本扫描）：

| 模块 | 依赖 |
|---|---|
| `report/build_report` | ai_capex, backtest, charts, db, focus_monitor, momentum, news_intel, oil, stock_diagnosis, stock_report_agent, view, weibo_llm |
| `sources/news_intel` | llm_client, stock_report_agent |
| `analysis/backtest` | db, stock_report_agent |
| `analysis/stock_diagnosis` | aggressive_analysis, peak_detector |
| `stock_report_agent` | backtest, db, momentum, oil, view |
| `analysis/weibo_llm` | llm_client |
| `report/chan_report` | chan_analysis |
| `infra/*` | 无项目内依赖 |

⚠️ `backtest ↔ stock_report_agent` 看似循环，实为**函数内惰性导入**打破，别改成顶层导入。

**新增模块时**：按职责放对应目录；跨层引用一律 `from ra.<层>.<模块> import ...`。

## 三、根目录兼容壳（命令不变的关键）

根目录保留了 19 个与模块同名的 `.py`，每个约 20 行，**自动生成、不含任何实现**：

- `python build_report.py --date ... --type 早报` → `runpy.run_path("ra/report/build_report.py", run_name="__main__")`
- `import db` → 转出 `ra.infra.db` 的真实模块对象（含私有名，如 `backtest._DEFAULT_WATCHLIST_NAME`）

因此 **所有既有命令、文档示例、定时自动化均无需改动**。

改代码请改 `ra/` 下的实现；**不要编辑根级壳**（下次生成会被覆盖，且壳里没有逻辑）。

## 四、常用命令（均在项目根执行）

```bash
# 采集 → 快照 → 入库（行情优先于舆情，180s 预算）
python stock_report_agent.py

# 报告（早报|晚报|周报）
python build_report.py --date 2026-09-21 --type 早报

# 专项
python chan_analysis.py 000001 1000 600 --from auto && python chan_report.py --date 2026-09-21
python arr_report.py --date 2026-09-15
python oil.py --date 2026-09-21 --show
python momentum.py --date 2026-09-21 --show
python news_intel.py --date 2026-09-21

# 回测（仅用户明确要求时执行）
python backtest.py --seed && python backtest.py --run

# 自选股：一致性体检（新增/删改后必跑）
python tools/add_watchlist.py --check

# 契约测试（50 项，改动后必跑）
.venv/Scripts/python.exe tests/test_health.py
```

## 五、tools/ 清单

| 工具 | 用途 |
|---|---|
| `add_watchlist.py` | 自选股新增/体检：同步 config 三处 + 两张兜底表 + 日K回填 |
| `upload_report.py` | 报告上传资料库（**同名 UPDATE 覆盖，禁 `import_html.py` 直传**）+ 自动剥离回写属性 |
| `archive_duplicates.py` | 资料库同名重复批量归档（`--dry-run`） |
| `inject_weibo_llm.py` | 按目标快照重算 `input_hash` 注入微博解构 |
| `inject_news_intel.py` | 资讯中文摘要注入（`summary_mode=agent_inject` 规范化） |
| `build_close_snapshot.py` | 收盘口径快照补建 |
| `read_edge_cookies.py` | 从 Edge/Chrome 提取微博 cookie（**须先退出浏览器**） |
| `ingest_reports.py` / `verify_ingest.py` | 历史报告入库与校验（归档用途） |

## 六、路径约定（易错点）

- **项目根只有一个来源**：`ra/paths.py` 的 `ROOT`（= `ra/` 的上一级）。
  各模块用 `from ra.paths import ROOT as BASE_DIR`，**不要再用 `Path(__file__).parent`**
  —— 模块在 `ra/<层>/` 下，该表达式会指向包内目录（曾把产物写到 `ra/sources/data/`）。
- `data/` 二级分层见 `data/README.md`；常量在 `ra/paths.py`（`DATA_SNAPSHOTS` 等）。
- `seeds/`、`templates/`、`reports/` 固定在项目根，属数据/模板资产而非代码。

## 七、报告结构（9 编号章节，AI 资本开支按需 +1）

```
核心结论 → 一、中美动量对照 → [海外AI巨头资本开支·按需] → 二、隔夜美股
→ 三、CPI与宏观 → 四、宏观传导链 → 五、地缘政治与原油(实时价量+外网解析)
→ 六、ETF资金流向 → 七、微博舆情解构 → 八、共振信号 → 九、自选股操作指引
→ 限时关注 → 免责
```

缠论已于 2026-09-18 拆出为独立专题报告。**增删章节须同步 5 处**（目录项 / h2 中文编号 /
`n_sec` / `db.save_report` 类型串 / `tests` 的 `SECTION_ANCHORS` + 章节数断言）。
