# -*- coding: utf-8 -*-
"""项目根与关键目录常量 —— 全项目唯一来源。

背景：包化前各模块用 `Path(__file__).parent` 作为项目根，模块一旦移入
`ra/<layer>/`，该表达式会指向包内目录而非项目根，所有数据/产物路径随之失准。
统一改为 `from ra.paths import ROOT as BASE_DIR`（沿用本地名，调用点零改动）。

约定：本模块**不得**导入任何项目内其他模块，避免循环依赖。
"""
from pathlib import Path

#: 项目根目录（ra/ 的上一级）
ROOT = Path(__file__).resolve().parents[1]

#: 数据根（运行时产物，不入库）
DATA = ROOT / "data"

# ---- data/ 二级分层（2026-09-21 归置，详见 data/README.md）----
#: 采集快照：唯一「原始输入」，报告读取的起点（保留 2 天）
DATA_SNAPSHOTS = DATA / "snapshots"
#: 按日产物：采集端可再生产，报告直接消费（momentum / oil / news_intel）
DATA_DAILY = DATA / "daily"
#: 按日派生：含模型/人工注入，**不可自动再生**（diagnosis / weibo_deep / focus / chan）
DATA_DERIVED = DATA / "derived"
#: 跨日状态：单文件覆盖写（ai_capex_state / westock_*_override / cookie 提取）
DATA_STATE = DATA / "state"
#: 配置与数据备份（config_backup / config_broken / db_cleanup_backup）
DATA_BACKUP = DATA / "backup"

#: 人工维护的种子数据（入库）
SEEDS = ROOT / "seeds"

#: 报告模板与静态资源（入库）
TEMPLATES = ROOT / "templates"

#: 报告输出根（入库）
REPORTS = ROOT / "reports"
