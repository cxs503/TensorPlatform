#!/usr/bin/env python3
"""Benchmark runner entry point (repo convention: benchmarks/verified/<case>/run_benchmark.py).

The implementation lives in :mod:`tools.data_assimilation.run_benchmark`; this file
only exists so the benchmark can be replayed from its own directory::

    python benchmarks/verified/data_assimilation/run_benchmark.py

It writes ``results.json`` next to this file plus figures under ``figures/``.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.data_assimilation.run_benchmark import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())