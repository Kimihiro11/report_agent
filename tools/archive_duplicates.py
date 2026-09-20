#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资料库重复节点归档工具（重命名标注 + 移入归档文件夹）。

为什么需要
----------
技能侧**没有节点删除 API**（SKILL.md mutation.md：
「文件/文件夹节点删除没有正式 Agent API；Doc block、Database 字段/记录或 Page DOM 删除按「修改」执行」）。
一旦空间里堆出同名重复节点，就只能靠网页端手工删除。本工具提供可逆的替代处理：
把旧副本**重命名**（加「（旧 MM-DD HHMM）」后缀）并**移入**「_归档-重复副本」文件夹，
使空间顶层不再出现同名重复，同时保留原件可追溯、可移回。

预防胜于清理：日常上传务必走 `tools/upload_report.py`（同名自动更新，不新建）。

用法
----
    printf '%s' "<token>" | python tools/archive_duplicates.py [--space-id <id>] [--dry-run]

凭证从 stdin 首行读取，只经管道传子进程 stdin，不落盘、不进命令行参数。
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

REPORT_SPACE_ID = "EVK0x45KBCZhRZQXZMwlYS"
ARCHIVE_TITLE = "_归档-重复副本"

SKILL_CANDIDATES = [
    pathlib.Path(r"C:\Users\cjass\AppData\Local\Programs\WorkBuddy\resources"
                 r"\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\library"),
    pathlib.Path(r"C:\Users\cjass\.workbuddy\plugins\cache\workbuddy-builtin"
                 r"\skill-library\0.5.9"),
]
PY_CANDIDATES = [
    pathlib.Path(r"C:\Users\cjass\.workbuddy\binaries\python\versions\3.13.12\python.exe"),
]


def _skill():
    for p in SKILL_CANDIDATES:
        if (p / "space_api.py").exists():
            return p
    raise SystemExit("[错误] 未找到 library 技能目录")


def _python():
    for p in PY_CANDIDATES:
        if p.exists():
            return str(p)
    return sys.executable


def _call(skill, python, api, args, token, payload=None):
    cmd = [python, str(skill / "space_api.py"), api, "--token-stdin"]
    if payload is not None:
        cmd.append("--stdin")
    stdin = token if payload is None else token + "\n" + json.dumps(payload)
    r = subprocess.run(cmd + args, input=stdin, text=True, capture_output=True, cwd=str(skill))
    return ((r.stdout or "") + (r.stderr or "")).strip()


def main():
    ap = argparse.ArgumentParser(description="归档资料库重复节点")
    ap.add_argument("--space-id", default=REPORT_SPACE_ID)
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = ap.parse_args()

    token = sys.stdin.readline().strip()
    if not token:
        print("[错误] 未从 stdin 读到凭证")
        return 2
    skill, python = _skill(), _python()

    out = _call(skill, python, "space.workspace.list-node", [], token, {"spaceId": args.space_id})
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        print(f"[错误] list-node 无有效返回: {out[-300:]}")
        return 1
    nodes = json.loads(m.group(0))["data"]["nodes"]

    groups = {}
    for n in nodes:
        groups.setdefault(n["title"], []).append(n)
    dups = {k: v for k, v in groups.items() if len(v) > 1}

    if not dups:
        print(f"[归档] 空间 {len(nodes)} 个顶层节点，无重复 ✅")
        return 0

    ts = lambda t: datetime.datetime.fromtimestamp(t / 1000).strftime("%m-%d %H%M")
    total = sum(len(v) - 1 for v in dups.values())
    print(f"[归档] {len(dups)} 组重复 / 待归档 {total} 个（空间顶层 {len(nodes)} 个节点）\n")

    if args.dry_run:
        for k, v in sorted(dups.items()):
            v2 = sorted(v, key=lambda x: x["createdAt"])
            print(f"  {k} x{len(v)}")
            for n in v2[:-1]:
                print(f"      待归档 {n['id']} ({ts(n['createdAt'])})")
            print(f"      保留   {v2[-1]['id']} ({ts(v2[-1]['createdAt'])})  <- 最新")
        return 0

    # 查找/创建归档文件夹
    folder = next((n for n in nodes if n.get("title") == ARCHIVE_TITLE), None)
    if folder:
        fid = folder["id"]
    else:
        r = subprocess.run(
            [python, str(skill / "manage" / "create_folder.py"),
             "--title", ARCHIVE_TITLE, "--space-id", args.space_id, "--token-stdin"],
            input=token, text=True, capture_output=True, cwd=str(skill))
        mo = re.search(r"KS_FOLDER_CREATE\s+(\S+)", (r.stdout or "") + (r.stderr or ""))
        if not mo:
            print(f"[错误] 创建归档文件夹失败: {((r.stdout or '') + (r.stderr or ''))[-300:]}")
            return 1
        fid = mo.group(1)
        print(f"[归档] 已创建文件夹「{ARCHIVE_TITLE}」 {fid}\n")

    ok = fail = 0
    for title, v in sorted(dups.items()):
        for n in sorted(v, key=lambda x: x["createdAt"])[:-1]:
            new_title = f"{title}（旧 {ts(n['createdAt'])}）"
            _call(skill, python, "space.workspace.rename-node",
                  ["--node-id", n["id"], "--title", new_title], token)
            o2 = _call(skill, python, "space.workspace.move-node",
                       ["--node-id", n["id"], "--target-parent-id", fid], token)
            if '"error"' in o2:
                print(f"  [ERR] {new_title}: {o2[:120]}")
                fail += 1
            else:
                print(f"  [OK ] {new_title}")
                ok += 1

    print(f"\n[完成] 成功 {ok} / 失败 {fail}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
