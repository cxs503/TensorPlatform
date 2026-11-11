#!/usr/bin/env python3
"""KPI-REPRO-95 计量入口脚本。

用法:
  python entry_kpi_repro.py [--synthetic] [--output-dir DIR]

本脚本是 KPI-REPRO-95 复现官记录通过率计量的统一入口。
生产模式连接真实数据库，合成模式用于测试/CI。
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from kpi_repro.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
