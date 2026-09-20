# -*- coding: utf-8 -*-
"""报告上传工具：上传到资料库 report 空间，**同名则更新原节点（不新建）**，并剥离回写属性。

用法:
  printf '%s' "<token>" | python tools/upload_report.py <html_path> [--space-id <id>]
                                                          [--skip-if-exists] [--keep-attrs]

说明:
  - report 空间 ID: EVK0x45KBCZhRZQXZMwlYS（团队空间「report」）
  - 去重依据：空间顶层已存在同名节点（title == 文件名去扩展名，如「早报-2026-08-27」）
      · 默认      -> `import_html.py --node-block-id <id>` **覆盖更新**（节点数与 URL 不变）
      · --skip-if-exists -> 同名则跳过（保留旧行为，适用于只补历史、不更新内容的场景）
  - 为什么必须走这个工具：`import_html.py` 每次调用都**新建**节点。直接调用它上传迭代版本，
    会在空间里堆出多个同名节点；而技能侧**没有节点删除 API**
    （SKILL.md mutation.md：「文件/文件夹节点删除没有正式 Agent API」），堆出来只能手工清理。
    ⚠️ 2026-09-20 实测：`周报-2026-09-18` 因反复重传累积了 4 份，已归档处理。
  - 上传后 `import_html` 会在本地 HTML 回写 `data-page-node-id`（服务端延迟、可能多次落地），
    本工具上传后自动剥离，避免误提交进 git。

⚠️ 历史坑（2026-09-20 修复）：本工具原先硬编码 SKILL_DIR 为
   `~/.workbuddy/plugins/cache/workbuddy-builtin/skill-library/0.5.9`，插件升级后该路径失效，
   工具静默失败 → 上传流程绕过它、改用 import_html 直传 → 产生重复。现改为多路径自动探测。
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

REPORT_SPACE_ID = "EVK0x45KBCZhRZQXZMwlYS"

# 技能目录候选（插件升级会换路径，按序探测第一个含 space_api.py 的）
SKILL_CANDIDATES = [
    pathlib.Path(r"C:\Users\cjass\AppData\Local\Programs\WorkBuddy\resources"
                 r"\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\library"),
    pathlib.Path(r"C:\Users\cjass\.workbuddy\plugins\cache\workbuddy-builtin"
                 r"\skill-library\0.5.9"),
]

# 托管 Python（隔离环境）；不可用时回退当前解释器
PY_CANDIDATES = [
    pathlib.Path(r"C:\Users\cjass\.workbuddy\binaries\python\versions\3.13.12\python.exe"),
]

ATTR_RE = re.compile(r' data-page-node-id="[^"]*"')


def find_skill():
    for p in SKILL_CANDIDATES:
        if (p / "space_api.py").exists():
            return p
    return None


def find_python():
    for p in PY_CANDIDATES:
        if p.exists():
            return str(p)
    return sys.executable


def list_nodes(space_id, token, python, skill):
    """返回空间顶层节点列表；失败抛异常。"""
    r = subprocess.run(
        [python, str(skill / "space_api.py"), "space.workspace.list-node",
         "--token-stdin", "--stdin"],
        input=token + "\n" + json.dumps({"spaceId": space_id}),
        capture_output=True, text=True, cwd=str(skill),
    )
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError(f"list-node 无有效返回: {out[-300:]}")
    return json.loads(m.group(0))["data"]["nodes"]


def import_html(python, skill, space_id, token, html_path, node_block_id=None):
    args = [python, str(skill / "page" / "import_html.py"),
            "--token-stdin", "--space-id", space_id]
    if node_block_id:
        args += ["--node-block-id", node_block_id]
    args.append(str(html_path))
    r = subprocess.run(args, input=token, capture_output=True, text=True, cwd=str(skill))
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if "KS_IMPORT_OK" not in out:
        raise RuntimeError(f"import_html 失败: {out[-800:]}")
    return json.loads(out.split(" ", 1)[1])


def strip_attrs(path: pathlib.Path) -> int:
    """剥离上传工具回写的 data-page-node-id；返回剥离个数。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    cleaned, cnt = ATTR_RE.subn("", raw)
    if cnt:
        path.write_text(cleaned, encoding="utf-8")
    return cnt


def main():
    ap = argparse.ArgumentParser(description="上传报告到资料库（同名更新，不新建）")
    ap.add_argument("path", help="本地 HTML 绝对路径")
    ap.add_argument("--space-id", default=REPORT_SPACE_ID)
    ap.add_argument("--skip-if-exists", action="store_true",
                    help="同名则跳过（默认是同名则更新）")
    ap.add_argument("--keep-attrs", action="store_true",
                    help="不剥离回写的 data-page-node-id（默认剥离）")
    args = ap.parse_args()

    token = sys.stdin.readline().strip()
    if not token:
        print("[上传] 缺少 token（需从 stdin 首行传入）")
        return 1

    skill, python = find_skill(), find_python()
    if skill is None:
        print(f"[上传] 未找到 library 技能目录，候选均不存在: {[str(p) for p in SKILL_CANDIDATES]}")
        return 1

    html_path = pathlib.Path(args.path)
    if not html_path.is_absolute():
        html_path = html_path.resolve()
    if not html_path.exists():
        print(f"[上传] 文件不存在: {html_path}")
        return 1
    title = html_path.stem

    try:
        nodes = list_nodes(args.space_id, token, python, skill)
    except Exception as e:
        print(f"[上传] 空间查询失败，中止（不做新建以免产生重复）: {e}")
        return 1

    dup = [n for n in nodes if n.get("title") == title and n.get("type") == "node"]

    if dup:
        node = dup[0]
        if args.skip_if_exists:
            print(f"[上传] SKIP 同名已存在: {title} -> {node.get('url', '')}")
            return 0
        try:
            d = import_html(python, skill, args.space_id, token, html_path,
                            node_block_id=node.get("id"))
            print(f"[上传] UPDATE {title} -> {d.get('url', '')}（覆盖节点 {node.get('id')}）")
        except Exception as e:
            print(f"[上传] 更新失败: {e}")
            return 1
    else:
        try:
            d = import_html(python, skill, args.space_id, token, html_path)
            print(f"[上传] OK {title} -> {d.get('url', '')}（新建节点 {d.get('node_block_id', '')}）")
        except Exception as e:
            print(f"[上传] 失败: {e}")
            return 1

    if not args.keep_attrs:
        n = strip_attrs(html_path)
        if n:
            print(f"[属性] 已剥离 {n} 处 data-page-node-id")

    return 0


if __name__ == "__main__":
    sys.exit(main())
