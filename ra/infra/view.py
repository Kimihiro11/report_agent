# -*- coding: utf-8 -*-
"""视图状态（ViewState）——把「盘前 / 盘中 / 收盘」口径从散落各处的判断收敛成一个对象。

背景（2026-09-13 重构）：
    改造前 build_report.py 里有 16 处独立的 PRE_MARKET 判断，每个章节各自决定
    「当前是不是盘前、该显示待开盘还是实时」。每新增一个数据源或调整一次口径，
    就要在多个章节手写一遍兜底分支 —— 9/07 至 9/11 连续四个版本都在修盘前语义。

现在统一为：
    VIEW = build_view(report_type, snapshot.get("meta"), quotes)
    if VIEW.is_pre_market: ...

口径来源优先级：
    1) 快照 meta（采集时由 stock_report_agent.build_snapshot_meta 写入，最权威）
    2) 运行时刻 + 报告类型推断（回退，兼容历史快照）

同时回答「报告展示的行情属于哪个交易日」（data_date）——这是消除
index_quotes 错位、诊断缓存污染、ETF 口径不一致的共用基础。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

BASIS_PRE = "pre_market"
BASIS_INTRADAY = "intraday"
BASIS_CLOSE = "close"

BASIS_LABEL = {BASIS_PRE: "盘前", BASIS_INTRADAY: "盘中", BASIS_CLOSE: "收盘"}

# 盘前采集时行情仍为上一交易日收盘；此阈值与 A 股开盘时间一致
MARKET_OPEN_HHMM = 930
# 收盘（含尾盘）之后采集到的行情即为当日收盘价
MARKET_CLOSE_HHMM = 1500


def recent_trading_day(d: datetime.date) -> datetime.date:
    """最近交易日（含当日；跳过周末，不含节假日日历）。"""
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def prev_trading_day(d: datetime.date) -> datetime.date:
    """上一交易日（严格早于 d；跳过周末，不含节假日日历）。"""
    return recent_trading_day(d - datetime.timedelta(days=1))


def infer_basis_by_time(now: datetime.datetime) -> str:
    """按采集时刻判定口径（采集端使用，不依赖报告类型）。

    交易日：09:30 前 = 盘前；09:30 ~ 15:00 = 盘中；15:00 后 = 收盘。
    非交易日（周末）：收盘（行情为最近交易日收盘）。
    """
    if now.weekday() >= 5:
        return BASIS_CLOSE
    hhmm = int(now.strftime("%H%M"))
    if hhmm < MARKET_OPEN_HHMM:
        return BASIS_PRE
    if hhmm <= MARKET_CLOSE_HHMM:
        return BASIS_INTRADAY
    return BASIS_CLOSE


def infer_basis(report_type: str, now: datetime.datetime, quotes=None) -> str:
    """按报告类型与运行时刻推断口径（快照 meta 缺失时的回退路径）。

    早报：09:30 前 = 盘前；09:30 后（盘前错过、盘中补跑）= 盘中
    晚报 / 周报：收盘口径
    另保留历史行为：指数涨幅与成交量全为 0（未开盘特征）时判为盘前。
    """
    if report_type in ("晚报", "周报"):
        return BASIS_CLOSE
    if report_type != "早报":
        return BASIS_CLOSE
    if quotes:
        zero_chg = all(abs((q.get("chg_pct") or 0)) < 0.005 for q in quotes.values())
        zero_vol = all((q.get("volume") in (None, "", "0", 0)) for q in quotes.values())
        if zero_chg or zero_vol:
            return BASIS_PRE
    try:
        hhmm = int(now.strftime("%H%M"))
    except Exception:
        hhmm = 2359
    return BASIS_PRE if hhmm < MARKET_OPEN_HHMM else BASIS_INTRADAY


@dataclass
class ViewState:
    """一份报告的「口径视图」：basis（盘前/盘中/收盘）+ data_date（行情所属交易日）。"""

    report_type: str = "早报"
    basis: str = BASIS_CLOSE
    data_date: Optional[str] = None      # 行情所属交易日 YYYY-MM-DD
    as_of: str = ""                      # 采集时刻 YYYY-MM-DD HH:MM:SS
    source: str = "inferred"             # meta（快照携带） | inferred（推断）

    @property
    def is_pre_market(self) -> bool:
        return self.basis == BASIS_PRE

    @property
    def is_intraday(self) -> bool:
        return self.basis == BASIS_INTRADAY

    @property
    def is_close(self) -> bool:
        return self.basis == BASIS_CLOSE

    @property
    def basis_label(self) -> str:
        return BASIS_LABEL.get(self.basis, "收盘")

    @property
    def quote_caption(self) -> str:
        """指数栏目用语：盘前=以上一交易日收盘为基准，盘中/收盘=实时。"""
        if self.is_pre_market:
            return f"{self.data_date} 收盘基准" if self.data_date else "上一交易日收盘基准"
        return "实时"

    def describe(self) -> str:
        return (f"basis={self.basis}({self.basis_label}) "
                f"data_date={self.data_date} source={self.source}")


def build_view(report_type: str, snapshot_meta: Optional[dict] = None,
               quotes=None, now: Optional[datetime.datetime] = None) -> ViewState:
    """构造 ViewState。优先采用快照 meta，缺失则按运行时刻推断。"""
    now = now or datetime.datetime.now()
    meta = snapshot_meta if isinstance(snapshot_meta, dict) else None

    if meta and meta.get("basis") in (BASIS_PRE, BASIS_INTRADAY, BASIS_CLOSE):
        basis = meta["basis"]
        data_date = meta.get("data_date") or None
        as_of = meta.get("as_of") or ""
        source = "meta"
    else:
        basis = infer_basis(report_type, now, quotes)
        if basis == BASIS_PRE:
            data_date = prev_trading_day(now.date()).isoformat()
        else:
            data_date = recent_trading_day(now.date()).isoformat()
        as_of = now.strftime("%Y-%m-%d %H:%M:%S")
        source = "inferred"

    return ViewState(report_type=report_type, basis=basis, data_date=data_date,
                     as_of=as_of, source=source)


def build_snapshot_meta(now: Optional[datetime.datetime] = None) -> dict:
    """采集端调用：生成写进快照的 meta（data_date / as_of / basis）。

    采集脚本本身不区分报告类型，一律按当日行情时刻判定：
      盘前采集 → data_date 落到上一交易日（根治「盘前快照的指数被标成当日」的历史错位）；
      盘中 / 收盘 / 周末采集 → data_date 为当日或最近交易日。
    """
    now = now or datetime.datetime.now()
    basis = infer_basis_by_time(now)
    if basis == BASIS_PRE:
        data_date = prev_trading_day(now.date())
    else:
        data_date = recent_trading_day(now.date())
    return {
        "data_date": data_date.isoformat(),
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "basis": basis,
        "schema_version": 2,
    }
