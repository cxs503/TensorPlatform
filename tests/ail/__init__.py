"""AIL 原型测试（Issue #74 阶段 1-2）。

纪律：这些测试只读真实工件，不写任何来源文件；不依赖 GPU、不联网。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))