# A股舆情操作指引 Agent · 功能说明书

> 版本：2026-08-16 锁定版 ｜ 用途：功能总纲 + 防偏移基线
> 工作区：`C:\Users\cjass\WorkBuddy\report_agent\`

本文件是 Agent 的**权威功能说明书**。任何后续修改、扩展、重建报告，都必须以本文件定义的"核心不变式（Invariants）"为基线，防止功能漂移。

---

## 一、定位与目标

- **是什么**：一个本地 Python Agent，抓取微博大 V 舆情 + 全球人物动态 + 宏观/事件/资金/行情数据，生成结构化的《A股舆情操作指引》HTML 报告，并对报告中的个股操作判断做**回测与交叉验证**。
- **产出物**：每日 9 章节 HTML 报告 + 回测报告 + 本地 JSON/PostgreSQL 数据。
- **长期规则（不可违背）**：所有产出的报告（HTML 等）都必须上传到资料库「我的文档」。

---

## 二、核心功能清单

| # | 功能 | 模块/入口 | 说明 |
|---|------|-----------|------|
| 1 | 微博大 V 舆情抓取 | `a_stock_agent.py: fetch_weibo` | m.weibo.cn API，需 `config.json` 的 `weibo_cookie`；必带 `X-Requested-With` 头；`ok!=1` 提示 cookie 失效 |
| 2 | 全球人物动态抓取 | `a_stock_agent.py: fetch_global_source` | **外网搜索优先 Google News RSS，失败回退 Bing News RSS**（`search_news`）；网络通时进入「深度」模式补充「最新表态」类查询。Bing 通道查询仍禁带"最新"类词（空频道），Google 通道无此限制 |
| 3 | 宏观数据抓取 | `fetch_macro_data` | 中美 GDP/CPI/PMI/非农/利率/社融/M2 等 |
| 4 | 事件因子抓取 | `fetch_event_factors` | 地缘/原油/灾害 |
| 5 | 指数行情 | `fetch_index_quotes` | 新浪 `hq.sinajs.cn`，5 大指数 |
| 6 | 个股技术面 | `fetch_kline` + `fetch_technical_analysis` | 新浪日 K，MA5/10/20/60 + 多空排列判断 |
| 7 | 国家队/资金流 | `fetch_national_team` + `build_report` 的 ETF 章节 | 宽基 ETF 净流 + 半导体/科创 ETF 逆势吸金 |
| 8 | 9 章节完整报告生成 | `build_report.py`（实时模板） | 见第四节结构；**必须用 WebSearch 实时数据拼装** |
| 9 | 个股见顶诊断引擎 | `peak_detector.py` + `stock_diagnosis.py` | 五维评分（超买25/成交20/背离15/衰竭20/破位20，100 分制，见底缓冲已移除），输出评分/等级/趋势，嵌入自选股卡片 |
| 10 | 舆情数据 + 报告入库 | `db.py: StockAgentDB` | PostgreSQL（库 `a_stock_agent`），本地 JSON 兜底 |
| 11 | 个股判断回测与交叉验证 | `backtest.py` | 从报告提取判断 → 新浪日 K 回测 1/3/5 日 → 独立技术信号对照 |
| 12 | 报告上传资料库 | 资料库技能（connect_open_platform + import_html） | 上传「我的文档」，JSON 校验无外链 |
| 13 | 日本传导链采集 | `a_stock_agent.py: fetch_japan_carry` | **传导链主线=日元主导**：加息预期(50-75bp,非25bp)/抛美债(>1.1万亿)/FIMA押美债借美元/借美款干预汇率；Google 优先+Bing 兜底，深度模式追加套息平仓+美债持仓 |

**命令行入口**
```bash
python a_stock_agent.py              # 数据引擎：采集→JSON快照→可选入库（简版已取消，全功能报告走 build_report）
python a_stock_agent.py --no-fetch   # 仅用缓存/配置出报告
python a_stock_agent.py --backtest   # 跑回测（seed + run + 生成回测报告）
python generate_full_report.py       # ⚠️ 注意：内含硬编码历史数据，仅作样式参考，不可用于实时
python build_report.py      # ✅ 实时报告生成模板（WebSearch 数据），后续每日报告沿用此模式
python backtest.py --seed            # 解析工作区报告 HTML → judgments.json
python backtest.py --run             # 拉行情回测 → backtest_results.json + 回测报告 HTML
python backtest.py --all             # seed + run
python db.py                         # 初始化 PostgreSQL 库与全部表
python ingest_reports.py             # 把已生成的 9 章节 HTML 解析回填 DB（幂等）
```

---

## 三、数据流与运行流程

```
配置(config.json)
   ├─ 微博 cookie / 微博源 / 自选股 / 宏观·事件·技术·国家队开关
   │
   ├─[抓取层] fetch_weibo / fetch_global_source / fetch_macro / fetch_event
   │         / fetch_index_quotes / fetch_kline / fetch_national_team
   │         → 舆情 dict + 行情 dict
   │
   ├─[诊断层] stock_diagnosis.run_all(自选股代码, 动态取 config.watchlist_stocks) → 见顶诊断评分
   │
   ├─[生成层] build_report.py(实时模板)
   │         → A股操作指引-9章节-{YYYY-MM-DD}.html
   │         → 同时 save_report + save_sentiment_batch 入库
   │
   ├─[回测层] backtest.py --all
   │         → 解析报告个股判断 → 新浪日K回测 → 回测报告-{YYYY-MM-DD}.html
   │
   └─[归档层] 上传资料库「我的文档」(+ JSON 本地兜底)
```

---

## 四、报告结构标准（9 章节）—— 防偏移核心

每日报告的**权威结构**（顺序与标题不可随意删改，新增章节只能追加在"今日操作策略"之前）：

1. **核心结论**（头部 header + conclusion-grid：最强主线 / 次强主线 / 操作基调 / 风险）
2. **一、隔夜美股**（含全球人物马斯克/特朗普信号，条形图）
3. **二、CPI 与宏观**（中美指标表 + 风险解除结论）
4. **三、宏观传导链监控**（日元主导：原油触发→日本输入型通胀→日本加息→抛美债压力→FIMA 回购→日元干预→套息平仓→A股；附 SVG 横向流程图及美国财政部 10Y/2Y 最新日度收益率面板）
5. **四、地缘政治与原油**（事件因子）
6. **五、ETF 资金流向**（国家队/资金切换主线确认）
7. **六、唐史主任长文分析 + 投星观点提炼**
8. **七、共振信号**（多源交叉验证，N 重共振表）
9. **八、7 只自选股操作指引**（每张卡片含：名称/代码/板块 + 操作 badge + 逻辑 + 见顶诊断评分）
10. **九、今日操作策略**（大盘/仓位/主线/回避）
11. **附：主要指数（A股收盘）**
12. **免责声明**（固定文案）

**自选股由 `config.json` 的 `watchlist_stocks` 动态决定（数量不写死；改代码须同步 `config.json`、`backtest.py` 的 `WATCHLIST_NAME`、各报告生成器，报告标题用 len(WATCHLIST) 渲染）：**
（具体标的与数量见 `config.json` 的 `watchlist_stocks`，当前含 600498 烽火通信共 8 只）

---

## 五、关键约定与防偏移规则（Invariants）

> 这些是被历史坑验证过的硬规则，**任何重构都必须遵守**，否则即视为功能偏移。

1. **涨跌颜色约定（中国习惯，不可颠倒）**：
   - 涨（正收益）= **红色** `.up{color:#d63031}`
   - 跌（负收益）= **绿色** `.down{color:#00a865}`
   - 中性/持平 = 灰色 `.muted`
   - 回测报告、共振、操作 badge 全部沿用此语义。

2. **实时报告必须用实时数据**：`generate_full_report.py` 内含 8/13 硬编码数据，**禁止**直接用于每日生产；每日报告应沿用 `build_report.py` 的"WebSearch 拉取 → 拼装"模式。

3. **报告文件名带横线**：`A股操作指引-9章节-2026-08-16.html`、`回测报告-2026-08-16.html`。因中文 + 横线在 shell 易出错，**上传必须走 Python 子进程传绝对路径**。

4. **行情源优先级**：新浪日 K（`money.finance.sina.com.cn`）为主源；东方财富 `push2his` 仅作兜底（高频被限流/阻塞）。回测同理。

5. **存储策略**：本地 JSON（`judgments.json` / `backtest_results.json`）为**主存储**，PostgreSQL 为**可选同步**——本地 PG 不稳定，JSON 兜底保证功能随时可用。

6. **报告上传资料库是长期规则**：每份产出报告都要上传「我的文档」；上传前确认无第三方 `<img>` 外链（当前报告均为纯 CSS/HTML 文字页）。

7. **回测窗口固定 1/3/5 交易日**；方向命中仅统计 bullish/bearish；交叉验证 = agent 叙事方向 vs 独立量价技术信号（价格 vs MA20 + MA20 斜率），任一方中性则不计一致率。

8. **微博抓取三要素**：`config.json` 填有效 `weibo_cookie` + 请求带 `X-Requested-With` 头 + `ok==1` 才成功；否则打印失效提示并跳过。

---

## 六、文件结构与职责

```
a_stock_agent.py          # 数据引擎：采集→JSON快照→入库 + --backtest 入口；被 generate/backtest/build 复用（简版模式已取消）
config.json               # 全部配置：微博源/自选股/全球源/宏观/事件/技术/国家队/数据库/cookie
db.py                     # PostgreSQL 封装 StockAgentDB（8 张表 + upsert 方法）
generate_full_report.py   # ⚠️ 含硬编码历史数据，仅供样式参考
build_report.py  # ✅ 实时 9 章节报告生成模板（后续每日沿用）
backtest.py               # 个股判断回测与交叉验证模块
peak_detector.py          # 见顶诊断引擎（5 维评分）
stock_diagnosis.py        # 自选股诊断集成（调用 peak_detector）
ingest_reports.py         # 把已生成 9 章节 HTML 解析回填 DB（幂等）
verify_ingest.py          # DB 入库校验
init_db.sql               # 建库建表 SQL（与 db.py 对应）
judgments.json            # 个股判断本地主存储（回测用）
backtest_results.json     # 回测结果本地主存储
weibo_posts.json          # 微博抓取缓存（实时报告数据源之一）
diagnosis_YYYYMMDD.json   # 个股诊断缓存
archive/                  # 历史快照（按日期归档 src/reports/data/README）
reports/                  # 产出物按类型分目录：早报/晚报/周报/回测（见手动运行规范）
报告类型与手动运行规范.md # 早报/晚报/周报 触发时机、章节结构、命名、目录与手动流程
```

---

## 七、config.json 关键配置项

| 字段 | 作用 | 注意 |
|------|------|------|
| `weibo_sources` | 微博大 V 列表（user_id/name/tier） | 加用户即扩展舆情源 |
| `watchlist_stocks` | 自选股代码数组（数量动态，由 config 决定） | 改代码须同步全局 |
| `global_sources` | 马斯克/特朗普等全球人物 | 信号类型配置 |
| `macro_indicators` + `_guide` | 宏观指标与解读 | 指导报告第二章 |
| `event_factors` | 地缘/原油/灾害关键词 | 第四章 |
| `technical_analysis` | 指数/个股技术标的 | 第六章/技术面 |
| `national_team` | ETF 跟踪 + 机构 + 信号逻辑 | 第五章 |
| `macro_chain` | 宏观传导链节点与信号逻辑 | 第三章 + SVG |
| `database` | PG 连接（localhost:5432/a_stock_agent） | 不稳定，仅可选同步 |
| `weibo_cookie` | 微博登录 cookie | 失效需更新，否则抓不到微博 |

---

## 八、数据来源与兜底策略

| 数据 | 主源 | 兜底 | 备注 |
|------|------|------|------|
| 微博大 V | m.weibo.cn API | WebSearch（自动化任务用） | 需 cookie |
| 全球人物 | **Google News RSS**（`news.google.com/rss/search`，`search_news` 优先） | Bing News RSS | 网络探测 `check_network()` 连通→「深度」模式（扩充关键词+条目，补充「最新表态」类查询）；不通→「浅度」仅必要查询。Bing 通道查询仍禁带"最新" |
| 美股/宏观 | WebSearch | — | 实时拼装 |
| 日本传导链 | **Google News RSS**（`search_news`） | Bing News RSS | `fetch_japan_carry`：加息预期/抛美债/FIMA/借美款干预四要素；深度追加套息平仓+美债持仓 |
| A股指数 | 新浪 `hq.sinajs.cn` | — | — |
| 个股日 K | 新浪 `money.finance.sina` | 东方财富 `push2his` | 新浪优先 |
| 见顶诊断 | peak_detector 本地计算 | — | numpy/pandas |

---

## 九、数据库 Schema（PostgreSQL `a_stock_agent`）

- `daily_reports`（report_date, market_state, summary, html_content）
- `index_quotes`（quote_date, index_name, price, change_pct, volume）
- `sentiment_data`（record_date, source_name, source_type, tier, content, post_time）
- `stock_analysis`（analysis_date, stock_code, stock_name, price, change_pct, action, reasoning）
- `technical_indicators`（analysis_date, symbol, ma5/10/20/60, trend, high5, low5）
- `resonance_signals`（signal_date, signal_name, resonance_level, sources, confidence）
- `stock_judgments`（report_date, stock_code, stock_name, action, direction, rationale, source_file）— UNIQUE(report_date, stock_code)
- `backtest_results`（judgment_date, stock_code, direction, window_days, entry/exit_close, ret_pct, direction_hit, tech_signal, tech_agree）— UNIQUE(judgment_date, stock_code, window_days)

> 主存储为本地 JSON，DB 仅可选同步；DB 掉线不影响功能。

---

## 十、回测与交叉验证功能详解（`backtest.py`）

**目的**：用未来实际行情检验每日报告中的个股操作判断，并两类方法交叉验证，防止"叙事自洽但方向错误"。

**流程**
1. `--seed`：递归扫描 `reports/`（含 `早报-*.html` / `晚报-*.html` / `周报-*.html` 及历史 `A股操作指引*9章节*.html`），**排除 `archive/` 历史副本**；从 `stock-card` 提取判断；兼容 `badge` 与 `op-row` 两种卡片格式；同日期同代码冲突时优先保留非中性判断；写入 `judgments.json`（并可选同步 DB）。
2. `--run`：读判断 → 新浪日 K（东方财富兜底）→ 算判断日后 1/3/5 交易日实际涨跌 → 方向命中（bullish 看涨、bearish 看跌）→ 独立技术信号（价格 vs MA20 + MA20 斜率）→ 一致率；写入 `backtest_results.json` + 生成 `回测报告-{YYYY-MM-DD}.html`。
3. 未到交易日的窗口标记为 `pending`（待回测）。

**指标**
- 方向命中率 = 有明确方向的判断中，未来窗口涨跌与判断方向一致的比例。
- 独立技术信号一致率 = agent 叙事方向 与 量价技术信号方向一致的比例（样本不足时标注）。

---

## 十一、报告上传资料库流程

1. 加载资料库技能（client 模式）。
2. `connect_open_platform` 换取 token（约 30 分钟有效）。
3. 用 `import_html.py --token-stdin` 上传 HTML 到「我的文档」（仅上传、不建表）。
4. 上传前用 Python 子进程传**绝对路径**（中文 + 横线文件名在 shell 易出错）。

---

## 十二、历史故障与修复记录（避免重蹈）

- **全球抓不到**：必应对"最新发言/最新动态"返回空频道 → 查询改为 `{name} 中国 股市`；后升级为 **Google News RSS 优先 + Bing 兜底**（`search_news`），Google 通道支持"最新"类词，并新增 `check_network()` 网络探测：通→深度采集（更多关键词/条目），不通→浅度仅必要查询。
- **微博抓不到**：cookie 失效（`ok=-100`）+ 缺 `X-Requested-With` 头 → 加头 + 检测 `ok==1` 并打印失效提示。
- **报告差距大**：原 `a_stock_agent.py` 仅轻量 → 引入 `peak_detector`/`stock_diagnosis` + 新建 9 章节生成器。
- **东方财富限流**：`push2his` 高频阻塞超时 → 改新浪为主源。
- **PostgreSQL 掉线**：psycopg2 连接异常 → 本地 JSON 主存储 + DB 可选。
- **中文文件名上传失败**：shell 传参错误 → Python 子进程绝对路径。

---

## 十三、自动化任务（已取消，改为手动）

- ⚠️ 原 `automation-1786523682789`（工作日 21:00）**已于 2026-08-16 删除**，改为用户每日手动触发。
- 手动运行标准见 **`报告类型与手动运行规范.md`**：分「早报 / 晚报 / 周报」三类 + 回测报告，产出物按类型放入 `reports/早报 | 晚报 | 周报 | 回测/`。
- 手动流程要点：WebSearch 实时拼装 → 生成对应目录 HTML → 上传资料库「我的文档」→（晚报/周报后）`python backtest.py --all` 并上传回测报告。

---

## 十四、功能边界（不做的事）

- 不提供真实交易下单、不接券商。
- 不做个股的硬预测承诺；报告与回测均为"参考/交叉验证"，含固定免责声明。
- 不在无有效 cookie 时伪造微博数据；缺 cookie 则跳过并提示。
- 不把 `generate_full_report.py` 的硬编码数据当作实时输出。
