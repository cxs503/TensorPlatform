#!/usr/bin/env python
"""AIL 基准入口（Issue #74）：真实执行四臂对比并写机器判据工件。

复现命令：
    PYTHONPATH=/srv/tensor/shared/tensor_eco/agent_work/m204/.pydeps:. \
      /srv/tensor/shared/venvs/tensor_eco/bin/python scripts/run_ail_benchmark.py \
      --out artifacts/ail_benchmark.json
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ail.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())