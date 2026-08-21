# -*- coding: utf-8 -*-
"""报告生成中的中文研判文本模板。

集中管理 build_report.py 中的判断语句，便于统一调优与多场景复用。
所有模板均为纯文本，接受上下文变量后由调用方格式化。
"""


class ConclusionPrompts:
    """核心结论（第一节）研判模板。"""

    # 当日无大V更新时的 stance 模板
    NO_UPDATE_BULLISH = "指数偏多，大V当日未更新，情绪面无新增扰动"
    NO_UPDATE_BEARISH = "指数偏弱，大V当日未更新，情绪面无新增支撑"
    NO_UPDATE_NEUTRAL = "指数震荡，大V当日未更新，以结构与技术面为准"

    # 有大V更新时的 stance 模板
    BULL_WITH_INDEX = "情绪与指数共振偏多，可积极但不追高"
    BEAR_VS_BULL_INDEX = "指数走强但情绪偏空，注意背离与回调"
    BEAR_WITH_INDEX = "情绪与指数双弱，以防御为主"
    MIXED = "多空交织，震荡格局下重结构轻指数"

    @staticmethod
    def stance(updated_any, consensus_label, idx_label):
        if not updated_any:
            if idx_label == "偏多":
                return ConclusionPrompts.NO_UPDATE_BULLISH
            if idx_label == "偏空":
                return ConclusionPrompts.NO_UPDATE_BEARISH
            return ConclusionPrompts.NO_UPDATE_NEUTRAL
        if consensus_label == "偏多" and idx_label in ("偏多", "震荡"):
            return ConclusionPrompts.BULL_WITH_INDEX
        if consensus_label == "偏空" and idx_label == "偏多":
            return ConclusionPrompts.BEAR_VS_BULL_INDEX
        if consensus_label == "偏空":
            return ConclusionPrompts.BEAR_WITH_INDEX
        return ConclusionPrompts.MIXED

    @staticmethod
    def verdict_no_update(idx_label, idx_n, up_n, main_line, risk_label):
        return (
            f"综合实时指数：大盘 <b>{idx_label}</b>（{idx_n} 个主要指数中 {up_n} 个上涨）。"
            f"大V当日未更新微博，情绪面暂无新增信号。"
            f"主线聚焦「{main_line}」；{risk_label}。"
        )

    @staticmethod
    def verdict_with_consensus(idx_label, idx_n, up_n, consensus_label, consensus_cls,
                               stance, main_line, risk_label):
        return (
            f"综合实时指数与微博舆情解构：大盘 <b>{idx_label}</b>（{idx_n} 个主要指数中 {up_n} 个上涨），"
            f"大V意见领袖共识 <b class='{consensus_cls}'>{consensus_label}</b>。{stance}。"
            f"主线聚焦「{main_line}」；{risk_label}。"
        )


class StrategyPrompts:
    """今日操作策略（第九节）模板。"""

    STATE_LABEL = {"bullish": "偏多积极", "bearish": "防御为主", "neutral": "中性偏谨慎"}

    @staticmethod
    def risk_line(has_japan_items):
        return (
            "日本传导链有实时预警信号，关注日元/套息平仓。"
            if has_japan_items else "暂无日本传导链实时预警。"
        )

    @staticmethod
    def avoid_line(avoid_names):
        if avoid_names:
            return "、".join(avoid_names)
        return "当前自选股诊断无预警及以上风险项。"


class WeiboPrompts:
    """微博舆情解构（第六节）模板。"""

    NO_UPDATE_CONSENSUS = (
        '<b class="b-orange">当日大V均未更新微博，无新增舆情可解构</b>；'
        '以下基于历史快照中的最新条目仅作参考，不构成当日研判。'
    )
    CONSENSUS_PREFIX = "大V整体共识（仅当日更新，T1权重1.5）"
    NO_STOCK_MENTION = '<p class="muted">当日自选股均无大V点名、主题共振或唐史主线关联，暂无相关舆情。</p>'
    NO_UPDATE_STOCK = '<p class="muted">当日无大V更新，自选股无新增舆情信号（唐史主线/板块共振仍可参考）。</p>'
    NO_UPDATE_KEY = '<p class="muted">当日无大V更新，无关键论点可解构。</p>'
    NO_KEY_SIGNAL = '<p class="muted">当日大V观点未提取到强多空信号。</p>'
    NO_RISK_SIGNAL = '<p class="muted">实时风险因子（日本传导链 / 事件）未提取到明确利空信号。</p>'

    # 全源 LLM 解构的统一输出 schema 示例（供 deconstruct_all 拼入 prompt）
    DECONSTRUCT_SCHEMA = '''{
  "as_of": "YYYY-MM-DD",
  "tangshi_deep": {
    "core_logic": "不超过80字，只提炼输入中可验证的连续逻辑",
    "direction": "偏多/偏空/中性",
    "direction_cls": "b-red/b-green/b-blue",
    "mainline": ["最多3项"],
    "avoid": ["最多3项"],
    "action": "不超过50字的条件化应对，不写确定性买卖指令",
    "risks": ["最多3项证伪条件"],
    "summary": "结论→驱动→证伪，不超过100字",
    "confidence": 0.0
  },
  "consensus": {
    "direction": "偏多/偏空/中性",
    "direction_cls": "b-red/b-green/b-blue",
    "label": "偏多/偏空/中性/分歧",
    "text": "共识→分歧→A股含义，不超过100字",
    "confidence": 0.0
  },
  "stock_mentions": [{"code": "仅限自选股", "name": "名称", "stance": "偏多/偏空/中性", "cls": "b-red/b-green/b-blue", "reason": "事实依据+传导，不超过50字", "confidence": 0.0}],
  "key_points": [{"source": "输入中的来源名", "stance": "偏多/偏空/中性", "fact": "明确事实或观点", "inference": "对A股的推断", "horizon": "日内/1-5日/1-3月", "confidence": 0.0}],
  "risks": [{"text": "风险及触发条件", "level": "高/中/低", "horizon": "日内/1-5日/1-3月", "confidence": 0.0}]
}'''

    @staticmethod
    def deconstruct_all(by_source, watchlist, date_str):
        """生成要求 LLM 把多源微博原文结构化解构为统一 JSON 的提示词。

        by_source: {源名: [当日原文文本, ...]}（已按当日过滤）
        watchlist: {code: name} 自选股映射
        返回的 prompt 要求 LLM 直接输出 DECONSTRUCT_SCHEMA 结构的 JSON。
        """
        src_block = "\n\n".join(
            f"【{name}】\n" + "\n".join(f"- {t}" for t in texts)
            for name, texts in by_source.items() if texts
        ) or "（当日无大V/信号源更新）"
        wl = "\n".join(f"- {c} {n}" for c, n in watchlist.items())
        return f"""你是A股日度舆情解构器。报告基准日：{date_str}。只能使用下面输入；不得调用记忆补充事实，不得把主题相关性写成个股被点名。

任务目标：把噪声文本压缩成“事实/观点 → 推断 → A股映射 → 证伪条件”，输出可校验JSON。偏多=红b-red，偏空=绿b-green，中性=蓝b-blue。

自选股白名单（stock_mentions只能出现这些代码）：
{wl or '（空）'}

===== 输入原文（按来源分组）=====
{src_block}
===== 输入结束 =====

硬性规则：
1. 事实与推断分离：key_points.fact只能复述输入中的事实/观点；inference才写A股含义。禁止把推断伪装成事实。
2. 时点优先：过期、无明确时点或只重复旧观点的内容降低confidence；不得称为“新增催化”。
3. 冲突不平均：先写共识，再写分歧及分歧来自哪条假设；证据不足时direction=中性或label=分歧。
4. 唐史仅在输入含其内容时输出深度解构；没有则tangshi_deep设为空对象，不得补写历史观点。
5. 自选股必须满足“原文明示点名”或“输入中主题与配置行业存在直接映射”；只因同属科技不得覆盖。最多{len(watchlist)}只。
6. key_points保留3-5条高信息密度论点；每条必须含source/fact/inference/horizon/confidence。
7. risks只保留可触发、可证伪的风险，最多4条；没有明确风险返回空数组。
8. confidence范围0-1：直接点名且时点清晰≥0.8；主题映射0.5-0.7；弱关联≤0.4。
9. 禁止原文长段复制、禁止来源链接、禁止“必涨/必跌”、禁止输出JSON之外的文字。

输出必须严格符合：
{WeiboPrompts.DECONSTRUCT_SCHEMA}
"""


class NewsIntelPrompts:
    """外网英文资讯中文总结提示词模板。"""

    SUMMARY_SCHEMA = '''{
  "direction": "偏多/偏空/中性",
  "confidence": 0.0,
  "as_of": "YYYY-MM-DD",
  "facts": ["最多3条输入中可核验事实"],
  "core_conclusion": "结论先行，不超过80字",
  "transmission": ["宏观变量→中间变量→A股风格/板块，最多3条"],
  "priced_in": "已定价/部分定价/未充分定价/无法判断",
  "watch": ["未来验证指标或证伪条件，最多3条"],
  "summary_zh": "用于报告展示的120-180字中文摘要"
}'''

    @staticmethod
    def summarize_zh(topic_label, articles_en, date_str=""):
        """生成英文资讯→A股结构化中文摘要提示词。"""
        joined = "\n\n---\n\n".join(
            f"[{i+1}] source={a.get('source', '')}; title={a.get('title_en', '')}; "
            f"published={a.get('published', '')}\n{(a.get('content_en', '') or '')[:6000]}"
            for i, a in enumerate(articles_en)
        )
        return f"""你是A股跨市场资讯压缩器。报告基准日：{date_str or '未提供'}；主题：{topic_label}。只能依据输入英文资讯，不得用记忆补数字或事件。

目标：不是逐条翻译，而是找出真正改变A股定价的新增信息，并压缩为“事实→传导→是否已定价→验证指标”。

规则：
1. facts只写输入中明确出现、带时点或数值的事实；冲突数据并列说明，不自行选边。
2. core_conclusion必须结论先行；信息不足时写“信息不足，无法形成明确方向”，direction=中性，confidence≤0.3。
3. transmission每条必须经过中间变量，格式为“A→B→C”，禁止从新闻直接跳到个股涨跌。
4. 区分新增信息与市场已知背景；旧闻、重复报道、无时点材料降低confidence，不作为新催化。
5. 只保留会改变风险偏好、流动性、估值锚或明确行业盈利预期的信息；删除人物修辞、过程描述和同义重复。
6. 具体数字必须与输入一致；禁止制造目标价、概率、涨跌幅。
7. summary_zh控制120-180字：第一句结论，第二句驱动与传导，第三句验证/风险；不要来源前缀，不要markdown。
8. 只输出JSON，不要解释。

原始英文资讯：
{joined or '（无有效正文）'}

输出严格符合：
{NewsIntelPrompts.SUMMARY_SCHEMA}"""


class FocusMonitorPrompts:
    """日本加息程度研判（focus_monitor）中文文本模板。"""

    CHAIN = (
        "原油(上游触发) → 日本输入型通胀 → <b>央行加息</b> ←(本次研判焦点：幅度/节奏/终点) → "
        "抛美债压力 → FIMA工具(缓冲) → 日元/套息平仓 → A股。"
        "本模块专攻「央行加息」这一节点的<b>程度</b>：加息越激进，套息平仓与流动性收紧压力越大。"
    )

    IMPACT_HAWKISH = (
        "机构判断日银加息偏激进（单次或达 50bp+、终点利率上修），将显著强化套息交易平仓逻辑，"
        "借入日元套利的国际资金回流，全球风险资产（含 A 股北向资金）面临波动与流出压力；"
        "美债收益率上行亦压制成长股估值。属「预警/关注」级别，需提高风险意识。"
    )

    IMPACT_DOVISH = (
        "机构判断日银加息偏温和（渐进 25bp、终点利率有限），套息平仓压力可控，"
        "对 A 股更多是情绪与北向资金扰动，而非系统性冲击；但仍需盯防超预期鹰派信号。"
    )

    IMPACT_NEUTRAL = (
        "机构对日银加息程度分歧明显，方向未明。分歧本身意味着一旦某一方预期兑现（尤其偏鹰），"
        "市场波动会放大。对 A 股属「观察/待确认」级别，建议跟踪一致预期的收敛方向。"
    )

    WATCHPOINTS = [
        "各大所是否将日银单次加息预期上调至 50bp 及以上（激进信号）",
        "日银终点利率预期是否上修至 1.25% 以上",
        "美元/日元汇率是否跌破关键位触发程序化套息平仓",
        "日本实际减持美债 / FIMA 工具是否被启用（传导链末端确认）",
    ]

    @staticmethod
    def impact(degree_label):
        if "激进" in degree_label:
            return FocusMonitorPrompts.IMPACT_HAWKISH
        if "温和" in degree_label:
            return FocusMonitorPrompts.IMPACT_DOVISH
        return FocusMonitorPrompts.IMPACT_NEUTRAL
