#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容壳（自动生成，勿手改）—— 真实实现：ra/sources/oil.py

保留本文件是为了让既有用法完全不变：
    python oil.py [参数]     # 等同运行包内实现
    import oil               # 转出包内模块对象（含私有名）

入口清单见 docs/ARCHITECTURE.md。改代码请改 ra/ 下的实现。
"""
import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_IMPL = _ROOT / "ra" / "sources/oil.py"
sys.path.insert(0, str(_ROOT))

if __name__ == "__main__":
    runpy.run_path(str(_IMPL), run_name="__main__")
else:
    import importlib
    sys.modules[__name__] = importlib.import_module("ra.sources.oil")
