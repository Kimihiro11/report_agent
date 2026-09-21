#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 CLI 入口（薄壳）—— 实现见 ra/cli.py。

    python cli.py list                    # 列出全部子命令
    python cli.py collect                 # 采集 → 快照 → 入库
    python cli.py report --date 2026-09-21 --type 早报

## ⚠️ 注意：本文件由脚本生成，是**薄壳，不含任何逻辑**。
##    若锚点不符，请手工把 ra/cli.py 的 main() 直接接进来，不要改动本文件的
##    转调方式（runpy 保留「脚本式」行为，兼容只有 if __name__ 块的模块）。
"""
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_IMPL = _ROOT / "ra" / "cli.py"
sys.path.insert(0, str(_ROOT))

if __name__ == "__main__":
    runpy.run_path(str(_IMPL), run_name="__main__")
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("ra.cli")
