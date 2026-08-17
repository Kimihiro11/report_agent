# A股舆情操作指引 Agent — 归档版本 2026-08-13

本目录为「A股舆情操作指引 Agent」的干净可用快照，已从上一工作区恢复并补齐缺失模块、修复抓取问题。最后更新：2026-08-16（新增个股判断回测与交叉验证功能）。

## 目录结构
```
archive/2026-08-13/
├── README.md                         本说明
├── src/                              全部源码（可直接运行）
│   ├── a_stock_agent.py             主抓取脚本（微博/指数/全球新闻/宏观）
│   ├── db.py                        PostgreSQL 入库模块
│   ├── config.json                  完整配置（3微博源+全球+宏观+7自选股）
│   ├── init_db.sql                  建库建表脚本
│   ├── peak_detector.py            个股见顶/拐点诊断
│   ├── stock_diagnosis.py          7只自选股诊断卡片
│   ├── generate_full_report.py     9维度完整报告生成器
│   └── backtest.py                 个股判断回测与交叉验证模块（新增）
├── reports/
│   ├── A股舆情操作指引-9维度-20260813.html   9维度完整报告（最终）
│   ├── A股舆情操作指引-20260813.html         a_stock_agent.py 简版报告
│   ├── 回测报告-20260816.html              个股判断回测与交叉验证报告（新增）
│   └── 20260813-daliy-morning.pdf            用户提供的参考样例报告
└── data/
    ├── weibo_posts.json            已抓取的微博数据缓存
    ├── judgments.json              从各报告提取的个股判断（回测输入）
    └── backtest_results.json       回测结果明细（回测输出）
```

## 本版本相对初版的关键修复
1. **全球数据（马斯克/特朗普）修复**：原查询含「最新发言」导致必应新闻 RSS 返回空频道；改为 `{name} 中国 股市` 后正常（马斯克~8条、特朗普~12条）。
2. **微博 cookie 失效检测**：给 m.weibo.cn 请求加 `X-Requested-With` 头以返回正确 JSON，并检测 `ok != 1` 时提示 cookie 失效。本版本 config.json 中的 weibo_cookie 已更新为有效值，实测可抓取（唐史主任司马迁 10 条、投星资产 10 条、投星大爷 1 条）。
3. **补齐缺失模块**：从上一工作区复制 peak_detector.py / stock_diagnosis.py，使 7 只自选股诊断可用。
4. **新增 9 维度报告生成器**：综合 WebSearch 抓取（隔夜美股/CPI/宏观传导链/地缘原油/ETF 资金流/A股收盘）与微博、个股诊断，生成结构与参考 PDF 一致的完整报告。
5. **9 维度报告也落库**：`generate_full_report.py` 已接入 `db.py`，运行后自动把微博舆情写入 `sentiment_data`、完整报告写入 `daily_reports`（与 `a_stock_agent.py` 路径一致）。实测：微博舆情入库 21 条、报告入库 1 条。

## 运行方式
- 轻量抓取+简版报告：`python src/a_stock_agent.py`（可加 `--no-fetch` 仅用缓存）
- 9维度完整报告：`python src/generate_full_report.py`
- **个股判断回测与交叉验证**：`python src/backtest.py --all`（解析所有报告→提取判断→新浪日K回测→生成回测报告）；或 `python src/a_stock_agent.py --backtest`
- 建库（首次）：`psql -f src/init_db.sql`
- 入库依赖本地 PostgreSQL 运行；未启动会自动跳过入库，脚本不报错。

## 个股判断回测与交叉验证（2026-08-16 新增）
**目的**：把每日报告「7只自选股操作指引」里的判断，用真实行情回测，验证 agent 的判断是否站得住脚，并用独立量价技术信号交叉对照。

**工作流**：
1. `seed`：解析工作区所有 9章节/9维度报告 HTML，提取每只自选股的操作（加仓/持有/观望/回避等）与方向（看多/看空/中性），存入 `judgments.json`（并同步写库 `stock_judgments`）。
2. `run`：用新浪日K线（`money.finance.sina.com.cn`）拉取每只股票行情，按判断日对齐，计算未来 1/3/5 个交易日的实际涨跌幅与方向命中；同时用独立技术信号（价格相对 MA20 + MA20 斜率）对照 agent 判断方向，做交叉验证。结果写入 `backtest_results.json`（并同步写库 `backtest_results`）。
3. 生成 `回测报告-{YYYYMMDD}.html`（含方向命中率、独立技术信号一致率、分窗口/分个股命中、逐笔明细）。

**数据源容错**：主用新浪日K，东方财富兜底；判断/结果以本地 JSON 为主存储，PostgreSQL 可用时同步入库（DB 不稳定也不影响回测）。

**自动化**：`automation-1786523682789` 已追加第 13 步，每个交易日 21:00 生成报告并上传后，自动运行 `python backtest.py --all` 生成回测报告并上传资料库。

**首跑结果（2026-08-16）**：8/13 三条方向性判断在次日（8/14）兑现——鼎通科技(加仓首选)+5.78%✓、富创精密(持有/回调加仓)+0.06%✓、欧莱新材(回避)+0.43%✗，方向命中率 66.7%；8/14–8/16 的窗口尚在未来（待回测）。独立技术信号在判断日多为中性，故一致率样本不足。

## 自动化
自动化任务 `automation-1786634913226`「A股舆情操作指引·晚报」已指向本工作区，每日 17:00 自动运行：
- WebSearch 采集实时市场数据 → 写入 `src/data/evening_data.json`
- 运行 `generate_evening_report.py` 生成动态 HTML 晚报
- 自动呈现 `reports/A股舆情操作指引-晚报-{日期}.html`

## 优化记录（2026-08-13）
1. **新增 `generate_evening_report.py`（动态数据驱动版）**：移除原 `generate_full_report.py` 中所有硬编码市场数据，改为从 JSON 数据文件读取。数据采集（WebSearch）与报告渲染（Python）完全解耦。
2. **数据文件 `src/data/evening_data.json`**：结构化存储全部市场数据（美股/宏观/A股/ETF/地缘/板块/自选股/共振/策略），可由自动化任务动态更新。
3. **恢复自动化任务**：原任务 `automation-1786523682789` 已失效，新建 `automation-1786634913226`，每日 17:00 运行。
