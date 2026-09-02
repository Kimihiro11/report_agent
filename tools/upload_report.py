# -*- coding: utf-8 -*-
"""报告上传工具：上传到资料库 report 空间，同名节点已存在则跳过（去重）。

用法:
  python tools/upload_report.py <html_path> --token-stdin

说明:
  - report 空间 ID: EVK0x45KBCZhRZQXZMwlYS（团队空间「report」）
  - 去重依据：report 空间内已存在同名节点（如「早报-2026-08-27」）则跳过上传
  - 依赖: space_api.py / import_html.py（workbuddy-builtin skill-library）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPORT_SPACE_ID = "EVK0x45KBCZhRZQXZMwlYS"
SKILL_DIR = Path(r"C:\Users\cjass\.workbuddy\plugins\cache\workbuddy-builtin\skill-library\0.5.9")


def get_token():
    return sys.stdin.read().strip()


def list_report_nodes(token):
    """返回 report 空间现有节点标题列表。"""
    api = SKILL_DIR / "space_api.py"
    r = subprocess.run(["python3", str(api), "space.workspace.list-node",
                        "--spaceId", REPORT_SPACE_ID, "--token-stdin"],
                       input=token, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"list-node 失败: {r.stderr[-500:]}")
    d = json.loads(r.stdout)
    nodes = d.get("data", {}).get("nodes", [])
    return nodes


def upload(token, html_path, title, node_block_id=None):
    script = SKILL_DIR / "page" / "import_html.py"
    args = ["python3", str(script), str(html_path), "--token-stdin",
            "--space-id", REPORT_SPACE_ID]
    if node_block_id:  # 重导入：覆盖更新已有节点内容（不新建）
        args += ["--node-block-id", node_block_id]
    r = subprocess.run(args, input=token, capture_output=True, text=True)
    out = r.stdout.strip()
    if "KS_IMPORT_OK" not in out:
        raise RuntimeError(f"import_html 失败: {out[-800:]} {r.stderr[-400:]}")
    d = json.loads(out.split(" ", 1)[1])
    return d.get("url", ""), d.get("node_block_id", "")


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/upload_report.py <html_path> --token-stdin [--update]")
        return 1
    html_path = Path(sys.argv[1]).resolve()
    title = html_path.stem  # 如「早报-2026-08-27」
    update_mode = "--update" in sys.argv
    token = get_token()
    if not token:
        print("[上传] 缺少 token（需 --token-stdin）")
        return 1

    # 去重检查
    try:
        nodes = list_report_nodes(token)
    except Exception as e:
        print(f"[上传] 空间查询失败，跳过上传: {e}")
        return 1
    dup = [n for n in nodes if n.get("title") == title]
    if dup:
        dup_node = dup[0]
        if not update_mode:
            print(f"[上传] SKIP 已存在同名「{title}」于 report 空间: {dup_node.get('url', '')}")
            return 0
        # --update：覆盖更新该节点内容（不新建，不产生重复）
        try:
            url, nid = upload(token, html_path, title, node_block_id=dup_node.get("id"))
            print(f"[上传] UPDATE {title} -> {url}（覆盖节点 {nid}）")
            return 0
        except Exception as e:
            print(f"[上传] 更新失败: {e}")
            return 1

    # 上传（新建）
    try:
        url, nid = upload(token, html_path, title)
        print(f"[上传] OK {title} -> {url}")
        return 0
    except Exception as e:
        print(f"[上传] 失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
