"""派生生成器节点：``artifacts/planner/golden_annotation_set.json`` → ``artifacts/repro_audit/generated/planner_golden_annotation_set.json``（数据驱动、零领域语义）。

由 ``artifacts/repro_audit/channels.json`` 中登记在该工件上的 2 条通道的派生式
重算头部值；重算逻辑在 ``tools/repro_audit/generators/recompute.py``，
本文件只钉常量（输入工件 / 另路输出 / 通道清单）。

纪律：
  * **不写回被审计工件**——输出永远另路（``artifacts/repro_audit/generated/``）；
  * ``REPRO_INPUT`` / ``REPRO_OUTPUT`` 只供契约取证（头部篡改 / 明细篡改重放）
    改道，正常运行不受影响；
  * 无随机、无时间、无环境依赖 → 两次运行逐字节相同。
"""
from __future__ import annotations

import os

from tools.repro_audit.generators.recompute import run_node

INPUT_PATH = "artifacts/planner/golden_annotation_set.json"
OUTPUT_PATH = "artifacts/repro_audit/generated/planner_golden_annotation_set.json"
CHANNELS_PATH = "artifacts/repro_audit/channels.json"


def main() -> int:
    run_node(os.environ.get("REPRO_INPUT", INPUT_PATH),
             os.environ.get("REPRO_OUTPUT", OUTPUT_PATH),
             CHANNELS_PATH, repo_root=os.getcwd(), artifact_rel=INPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
