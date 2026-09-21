# data/ 目录说明

运行时产物目录，**不入版本控制**（`.gitignore` 已排除）。
2026-09-21 按「快照 / 按日产物 / 按日派生 / 状态 / 备份」五类归置。

路径常量统一在 `ra/paths.py`（`DATA_SNAPSHOTS` / `DATA_DAILY` / `DATA_DERIVED` /
`DATA_STATE` / `DATA_BACKUP`），新代码请引用常量、勿写字面路径。

| 目录 | 内容 | 谁写 | 寿命 / 清理策略 |
|---|---|---|---|
| `snapshots/` | 采集快照 `fetched_<YYYYMMDD>_<HHMMSS>.json`，含 `meta{basis,data_date}` | `ra/stock_report_agent.py` | **唯一原始输入**。保留 2 天；重跑历史报告依赖它，删了就无法复现 |
| `daily/` | 按日产物，采集端**可自动再生** | | |
| `daily/momentum/` | 中美动量对照 `momentum_<DATE8>.json` | `ra/sources/momentum.py` | 按日；仅当期报告消费，可随报告归档一起留 |
| `daily/oil/` | 原油价格与舆情 `oil_<DATE8>.json` | `ra/sources/oil.py` | 同上 |
| `daily/news_intel/` | 外网资讯解析 `news_intel_<DATE8>.json`（`raw` 英文原文 + `summary_zh` 等） | `ra/sources/news_intel.py` + `tools/inject_news_intel.py` | ⚠️ **中文摘要由模型注入，重抓不会复原** → 视为半派生产物，历史日勿删 |
| `derived/` | 按日派生，**含模型/人工注入，不可自动再生** | | |
| `derived/diagnosis/` | 自选股见顶诊断 `diagnosis_<DATE8>.json` | `ra/analysis/stock_diagnosis.py`（经 `build_report`） | 可重算，但**重算耗时且依赖当日行情**；盘前报告会锚定 `target_date=上一交易日` |
| `derived/weibo_deep/` | 微博解构 `weibo_deconstruct_<DATE8>.json` + 唐史深读 `tangshi_<DATE8>.json` | `ra/analysis/weibo_llm.py` + `tools/inject_weibo_llm.py` | ⚠️ **模型注入，必留**。按 `input_hash(快照, 自选股, 日期)` 缓存，自选股或口径变化即失配 |
| `derived/focus/` | 限时关注（日银加息程度）`focus_state_*.json` | `ra/analysis/focus_monitor.py` | 可重算（需外网） |
| `derived/chan/` | 缠论推演 `chan_forecast_<DATE8>.json` | `ra/analysis/chan_analysis.py` | 可重算（需行情） |
| `state/` | 跨日状态，**单文件覆盖写，只保留最新** | | |
| `state/ai_capex_state.json` | AI 资本开支章节的指纹与上次呈现日期 | `ra/sources/ai_capex.py` | 删掉只会导致该章节多呈现一次，无副作用 |
| `state/westock_etf_override.json` | ETF 资金流人工覆盖（westock 口径） | agent 经 westock MCP 写 | `date` 须为当日，过期即失效 |
| `state/westock_fund_override.json` | 两融/北向人工覆盖 | 同上 | 同上 |
| `state/weibo_cookie_browser.txt` | 从浏览器提取的 cookie 临时落地 | `tools/read_edge_cookies.py` | 用完即删；**含敏感凭证** |
| `backup/` | 配置与数据备份 | `tools/add_watchlist.py` 等 | `config_backup_*.bak` 含**旧 cookie**，按需清理；`config_broken_*` 为故障取证样本 |

## 清理建议（保守）

- **可安全删除**：`state/weibo_cookie_browser.txt`、`backup/` 中超过 1 个月的 `config_backup_*`
- **删前想清楚**：`derived/weibo_deep/`、`daily/news_intel/`（模型注入内容，删了要重新生成）
- **不要删**：`snapshots/`（历史报告复现的唯一入口）

## 历史坑

- **重采集不能修历史日**：重采集生成的是**运行日**快照，`--date <历史日>` 仍指向旧快照。
  要补历史数据须直接改对应快照文件（如 ETF 假 0 用 DB 真值回填 `etf` 字段）。
- 快照保留期外的历史报告重跑会失败（找不到快照），属预期行为。
