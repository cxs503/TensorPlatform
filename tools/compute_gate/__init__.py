#!/usr/bin/env python3
"""__init__ —— TensorPlatform 计算递交门（compute_gate）。

入口：
  from tools.compute_gate import run_compute, guard, preflight
  python3 -m tools.compute_gate.cli submit --repo TensorBEM --cmd "pytest -q" --cores 8
  python3 -m tools.compute_gate.cli guard --once          # 扫一轮并按策略处置
  python3 -m tools.compute_gate.cli guard --loop          # 常驻（systemd 单元见 deploy/）
"""
from .compute import run_compute  # noqa: F401
from .policy import load_config  # noqa: F401

__all__ = ["run_compute", "load_config"]