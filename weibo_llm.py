"""微博全源 LLM 解构：结构化提示、输入指纹、schema 归一化与安全缓存。"""
import datetime
import hashlib
import json
from pathlib import Path

from llm_client import atomic_write_json, call_json
from templates.prompts import WeiboPrompts

BASE_DIR = Path(__file__).parent
DEEP_DIR = BASE_DIR / "data" / "weibo_deep"
_DIRECTIONS = {"偏多": "b-red", "偏空": "b-green", "中性": "b-blue"}
_LEVELS = {"高", "中", "低"}
_HORIZONS = {"日内", "1-5日", "1-3月"}


def _date8(date_str):
    return date_str.replace("-", "")


def _clip(value, limit):
    return str(value or "").strip()[:limit]


def _confidence(value, default=0.5):
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return default


def _stance(value):
    value = str(value or "").strip()
    if "偏多" in value:
        return "偏多"
    if "偏空" in value:
        return "偏空"
    return "中性"


def _list_text(value, max_items=3, max_len=80):
    if not isinstance(value, list):
        return []
    return [_clip(v, max_len) for v in value[:max_items] if _clip(v, max_len)]


def _input_payload(weibo_data, watchlist, date_str):
    rows = {}
    for name, posts in sorted((weibo_data or {}).items()):
        vals = []
        for post in posts or []:
            text = _clip((post or {}).get("text"), 1000)
            if text:
                vals.append({"text": text, "time": _clip((post or {}).get("time"), 80)})
        if vals:
            rows[name] = vals
    return {"date": date_str, "watchlist": watchlist or {}, "weibo_data": rows}


def input_hash(weibo_data, watchlist, date_str):
    raw = json.dumps(_input_payload(weibo_data, watchlist, date_str), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_llm(obj, watchlist=None, date8=""):
    """把远程/人工注入结果归一化为稳定 schema；严重非法返回 None。"""
    if not isinstance(obj, dict):
        return None
    watchlist = watchlist or {}
    out = {
        "date": date8 or _clip(obj.get("date"), 8),
        "as_of": _clip(obj.get("as_of") or obj.get("date"), 10),
        "method": _clip(obj.get("method"), 40),
        "generated_at": _clip(obj.get("generated_at"), 30),
        "input_hash": _clip(obj.get("input_hash"), 64),
    }

    td = obj.get("tangshi_deep") or {}
    if isinstance(td, dict) and td:
        direction = _stance(td.get("direction"))
        out["tangshi_deep"] = {
            "core_logic": _clip(td.get("core_logic"), 160),
            "direction": direction,
            "direction_cls": _DIRECTIONS[direction],
            "mainline": _list_text(td.get("mainline"), 3, 50),
            "avoid": _list_text(td.get("avoid"), 3, 50),
            "action": _clip(td.get("action"), 100),
            "risks": _list_text(td.get("risks"), 3, 70),
            "summary": _clip(td.get("summary"), 180),
            "confidence": _confidence(td.get("confidence"), 0.6),
        }
    else:
        out["tangshi_deep"] = {}

    consensus = obj.get("consensus") or {}
    if not isinstance(consensus, dict):
        consensus = {}
    direction = _stance(consensus.get("direction") or consensus.get("label"))
    label = _clip(consensus.get("label"), 8)
    if label not in {"偏多", "偏空", "中性", "分歧"}:
        label = direction
    out["consensus"] = {
        "direction": direction,
        "direction_cls": _DIRECTIONS[direction],
        "label": label,
        "text": _clip(consensus.get("text"), 180),
        "confidence": _confidence(consensus.get("confidence"), 0.5),
    }

    mentions = []
    seen_codes = set()
    raw_mentions = obj.get("stock_mentions") if isinstance(obj.get("stock_mentions"), list) else []
    for item in raw_mentions:
        if not isinstance(item, dict):
            continue
        code = _clip(item.get("code"), 10)
        if not code or code in seen_codes or (watchlist and code not in watchlist):
            continue
        stance = _stance(item.get("stance"))
        mentions.append({
            "code": code,
            "name": _clip(watchlist.get(code) or item.get("name") or code, 30),
            "stance": stance,
            "cls": _DIRECTIONS[stance],
            "reason": _clip(item.get("reason"), 100),
            "confidence": _confidence(item.get("confidence"), 0.5),
        })
        seen_codes.add(code)
    out["stock_mentions"] = mentions

    points = []
    raw_points = obj.get("key_points") if isinstance(obj.get("key_points"), list) else []
    for item in raw_points[:5]:
        if not isinstance(item, dict):
            continue
        fact = _clip(item.get("fact") or item.get("text"), 140)
        inference = _clip(item.get("inference"), 140)
        if not fact:
            continue
        points.append({
            "source": _clip(item.get("source"), 40),
            "stance": _stance(item.get("stance")),
            "fact": fact,
            "inference": inference,
            "text": fact + (f"；{inference}" if inference else ""),
            "horizon": item.get("horizon") if item.get("horizon") in _HORIZONS else "1-5日",
            "confidence": _confidence(item.get("confidence"), 0.5),
        })
    out["key_points"] = points

    risks = []
    raw_risks = obj.get("risks") if isinstance(obj.get("risks"), list) else []
    for item in raw_risks[:4]:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = _clip(item.get("text"), 140)
        if not text:
            continue
        risks.append({
            "text": text,
            "level": item.get("level") if item.get("level") in _LEVELS else "中",
            "horizon": item.get("horizon") if item.get("horizon") in _HORIZONS else "1-5日",
            "confidence": _confidence(item.get("confidence"), 0.5),
        })
    out["risks"] = risks
    return out


def load_llm(date8, watchlist=None, expected_hash=None):
    """读取并归一化当日解构；可按输入指纹拒绝陈旧缓存。"""
    path = DEEP_DIR / f"weibo_deconstruct_{date8}.json"
    if not path.exists():
        return None
    try:
        normalized = normalize_llm(json.loads(path.read_text(encoding="utf-8")), watchlist, date8)
        if expected_hash and normalized and normalized.get("input_hash") != expected_hash:
            return None
        return normalized
    except (OSError, ValueError) as exc:
        print(f"[微博LLM] 缓存不可用 {path.name}: {exc}")
        return None


def save_llm(date8, obj, watchlist=None):
    """校验、归一化并原子写入当日解构。"""
    normalized = normalize_llm(obj, watchlist, date8)
    if normalized is None:
        raise ValueError("微博LLM结果不是合法JSON对象")
    path = DEEP_DIR / f"weibo_deconstruct_{date8}.json"
    return atomic_write_json(path, normalized)


def build_prompt(weibo_data, watchlist, date_str):
    by_source = {}
    for name, posts in (weibo_data or {}).items():
        texts = [_clip((p or {}).get("text"), 1000) for p in (posts or [])]
        texts = [t for t in texts if t]
        if texts:
            by_source[name] = texts
    return WeiboPrompts.deconstruct_all(by_source, watchlist, date_str)


def run_for_date(date_str, weibo_data, config, watchlist, force=False):
    """按输入指纹缓存；原文或自选股变化后自动重新解构。"""
    date8 = _date8(date_str)
    digest = input_hash(weibo_data, watchlist, date_str)
    cached = load_llm(date8, watchlist)
    if cached and not force and cached.get("input_hash") == digest:
        return cached, "cached"

    prompt = build_prompt(weibo_data, watchlist, date_str)
    system = "你只做金融文本结构化抽取。禁止补充输入外事实；只输出严格JSON。"
    obj, reason = call_json(prompt, config, system_prompt=system, temperature=0.1)
    if obj is not None:
        obj.update({
            "date": date8,
            "as_of": date_str,
            "method": "remote LLM",
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "input_hash": digest,
        })
        normalized = normalize_llm(obj, watchlist, date8)
        save_llm(date8, normalized, watchlist)
        return normalized, "remote"
    if reason not in {"agent_inject", "httpx_not_installed"}:
        print(f"[微博LLM] 远程解构未完成: {reason}")
    # 输入已变化时绝不返回旧解构，避免早报/旧自选股污染晚报。
    return None, "stale_cache_ignored" if cached else "agent_inject"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="微博 LLM 解构器")
    parser.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    snaps = sorted((BASE_DIR / "data" / "snapshots").glob(f"fetched_{_date8(args.date)}_*.json"))
    weibo_data = json.loads(snaps[-1].read_text(encoding="utf-8")).get("weibo_data", {}) if snaps else {}
    watchlist = {c: cfg.get("watchlist_names", {}).get(c, c) for c in cfg.get("watchlist_stocks", [])}
    result, mode = run_for_date(args.date, weibo_data, cfg, watchlist, force=args.force)
    print(f"[微博LLM] mode={mode}; stocks={len((result or {}).get('stock_mentions', []))}")
