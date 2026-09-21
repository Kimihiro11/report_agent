# 架构与目录导览

> 2026-09-21 包化重构后定稿。改动代码前先读本文 + `.workbuddy/memory/MEMORY.md`（不变式与历史坑）。

## 一、总览

```
report_agent/
├── cli.py                  ← **全项目唯一命令入口**（见第三节）
├── ra/                     ← 全部实现（Python 包）
│   ├── cli.py               CLI 分发器（子命令表 / 别名 / runpy 转调）
│   ├── __main__.py          支持 `python -m ra <子命令>`
│   ├── paths.py             项目根与 data/ 分层常量（唯一来源）
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
├── templates/              提示词与报告静态资源（prompts.py / style.css / *.html / sentiment_words.json）
├── seeds/                  人工维护的种子数据（judgments / backtest_results / ai_capex / arr_cn_us）
├── data/                   运行时产物（不入库，仅 README.md 入库）→ 见 data/README.md
├── reports/                报告输出（入库）：早报|晚报|周报|回测|专题|早期版本
├── docs/                   文档
├── tools/                  运维工具（全部经 cli.py 调用，见第五节）
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

## 三、统一 CLI（唯一命令入口）

历史上根目录平铺 19 个模块，包化后一度保留 19 个同名兼容壳 —— 但壳本身就是新的累赘。
现改为**单一入口 `cli.py`**（薄壳 20 行，实现在 `ra/cli.py`）：

```bash
python cli.py list                # 列出全部子命令
python cli.py <子命令> [参数...]   # 参数原样透传给该模块的 argparse
python -m ra <子命令> [参数...]    # 等价写法
```

分发机制：`runpy.run_path(实现文件, run_name="__main__")` + 重设 `sys.argv`。
用 runpy 而非 `import + main()`，是因为部分模块只有 `if __name__ == "__main__"` 块、没有 `main()`。

**历史脚本名仍可用作别名**（平滑迁移），例如：

```
python cli.py build_report --date 2026-09-21 --type 早报    # = python cli.py report ...
python cli.py stock_report_agent                            # = python cli.py collect
```

添加新能力：在 `ra/cli.py` 的 `COMMANDS` 表登记一行（名字 → 实现文件 + 说明），
`list` 会自动列出；入口文件无需改动。

## 四、常用命令（均在项目根执行）

```bash
# 主流程
python cli.py collect                                     # 采集 → 快照 → 入库（行情优先于舆情，180s 预算）
python cli.py report --date 2026-09-21 --type 早报          # 早报|晚报|周报
python cli.py backtest --seed && python cli.py backtest --run   # 回测（仅用户明确要求时）

# 数据源
python cli.py news --date 2026-09-21
python cli.py oil --date 2026-09-21 --show
python cli.py momentum --date 2026-09-21 --show

# 分析 / 专题
python cli.py focus --no-fetch
python cli.py chan 000001 1000 600 --from auto
python cli.py chan-report --date 2026-09-21
python cli.py arr --date 2026-09-15

# 运维
python cli.py watchlist --check                    # 自选股一致性体检（改动后必跑）
printf '%s' "<token>" | python cli.py upload <html 绝对路径>
python cli.py inject-news --date 2026-09-21 --content <summaries.json>
python cli.py archive-dups --dry-run

# 契约测试（50 项，改动后必跑）
.venv/Scripts/python.exe tests/test_health.py
```

## 五、tools/ 清单（经 `cli.py` 对应子命令调用）

| 子命令 | 实现 | 用途 |
|---|---|---|
| `watchlist` | `add_watchlist.py` | 自选股新增/体检：同步 config 三处 + 两张兜底表 + 日K回填 |
| `upload` | `upload_report.py` | 报告上传资料库（**同名 UPDATE 覆盖，禁 `import_html.py` 直传**）+ 自动剥离回写属性 |
| `archive-dups` | `archive_duplicates.py` | 资料库同名重复批量归档（`--dry-run`） |
| `inject-weibo` | `inject_weibo_llm.py` | 按目标快照重算 `input_hash` 注入微博解构 |
| `inject-news` | `inject_news_intel.py` | 资讯中文摘要注入（`summary_mode=agent_inject` 规范化） |
| `close-snapshot` | `build_close_snapshot.py` | 收盘口径快照补建 |
| `cookies` | `read_edge_cookies.py` | 从 Edge/Chrome 提取微博 cookie（**须先退出浏览器**） |
| `ingest-reports` / `verify-ingest` | `ingest_reports.py` / `verify_ingest.py` | 历史报告入库与校验（归档用途） |

## 六、路径约定（易错点）

- **项目根只有一个来源**：`ra/paths.py` 的 `ROOT`（= `ra/` 的上一级）。
  各模块用 `from ra.paths import ROOT as BASE_DIR`，**不要再用 `Path(__file__).parent`**
  —— 模块在 `ra/<层>/` 下，该表达式会指向包内目录（曾把产物写到 `ra/sources/data/`）。
- `data/` 二级分层见 `data/README.md`；常量在 `ra/paths.py`（`DATA_SNAPSHOTS` 等）。
- `seeds/`、`templates/`、`reports/` 固定在项目根，属数据/模板资产而非代码。
- 模块间以**子进程**调用时，不要指向已不存在的根级脚本名；走
  `[sys.executable, str(BASE_DIR / "cli.py"), "<子命令>", ...]`（`chan_report.py` 即此写法）。

## 七、报告结构（9 编号章节，AI 资本开支按需 +1）

```
核心结论 → 一、中美动量对照 → [海外AI巨头资本开支·按需] → 二、隔夜美股
→ 三、CPI与宏观 → 四、宏观传导链 → 五、地缘政治与原油(实时价量+外网解析)
→ 六、ETF资金流向 → 七、微博舆情解构 → 八、共振信号 → 九、自选股操作指引
→ 限时关注 → 免责
```

缠论已于 2026-09-18 拆出为独立专题报告。**增删章节须同步 5 处**（目录项 / h2 中文编号 /
`n_sec` / `db.save_report` 类型串 / `tests` 的 `SECTION_ANCHORS` + 章节数断言）。
