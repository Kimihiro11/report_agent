"""OpenAI 兼容 LLM 公共客户端：结构化调用、JSON 提取、重试与原子缓存。"""
import json
import os
import time
from pathlib import Path


def extract_json(text):
    """从模型文本中提取首个完整 JSON 对象；失败返回 None。"""
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() in ("```", "```json"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (TypeError, ValueError):
        pass
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start:idx + 1])
                    return obj if isinstance(obj, dict) else None
                except (TypeError, ValueError):
                    return None
    return None


def call_json(prompt, config, *, system_prompt="", temperature=0.1, timeout=120, retries=2):
    """调用 OpenAI 兼容接口并返回 JSON 对象；未配置或失败返回 (None, reason)。

    默认(未配外部API)走当前运行的模型：provider=builtin 或缺少 base_url/model/api_key 时
    返回 (None, "agent_inject")，由 Agent 注入结论，而非报错中断。
    """
    llm = (config or {}).get("llm") or {}
    provider = (llm.get("provider") or "").lower()
    # 默认路径：当前运行的模型(Agent 注入)
    if provider == "builtin":
        return None, "agent_inject"
    base_url = (llm.get("base_url") or "").rstrip("/")
    model = llm.get("model") or ""
    key_env = llm.get("api_key_env") or ""
    api_key = (os.environ.get(key_env) if key_env else "") or llm.get("api_key") or ""
    if not (base_url and model and api_key):
        return None, "agent_inject"
    try:
        import httpx
    except ImportError:
        return None, "httpx_not_installed"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    last_error = "unknown"
    for attempt in range(retries + 1):
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                return None, "empty_choices"
            content = ((choices[0].get("message") or {}).get("content") or "").strip()
            obj = extract_json(content)
            if obj is not None:
                return obj, "ok"
            last_error = "invalid_json"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None, last_error


def atomic_write_json(path, obj):
    """同目录临时文件写入后原子替换，避免中途终止留下损坏缓存。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
