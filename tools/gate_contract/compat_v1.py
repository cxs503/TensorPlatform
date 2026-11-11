"""v1→v2 兼容层：将 v1 gate.json 无损映射到 v2 schema。

映射规则：
- v1 的 ``select`` 字段映射为 v2 的 ``aggregation.op``
- v1 的标量判据映射为 v2 的 ``type: scalar``
- v1 的 ``measured`` 字段保留
- schema_version 升级为 gate-contract/2.0
- env_fingerprint 保留（但不参与判定摘要）

反向兼容：v2 引擎可以处理 v1 gate.json（自动升级后求值）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .contract import SCHEMA_VERSION, canonical_sha256
from .contract_v2 import SCHEMA_VERSION_V2

# v1 select → v2 aggregation.op 映射
SELECT_TO_AGG = {
    "absmax": "absmax",
    "max": "max",
    "min": "min",
    "mean": "mean",
}


def upgrade_v1_to_v2(gate_v1: dict) -> dict:
    """将 v1 gate.json 升级为 v2 schema。

    保持语义等价：v1 的 select=absmax 语义无损映射到 v2 的 aggregation.op=absmax。
    """
    if gate_v1.get("schema_version") == SCHEMA_VERSION_V2:
        return gate_v1  # 已经是 v2

    gate_v2 = copy.deepcopy(gate_v1)
    gate_v2["schema_version"] = SCHEMA_VERSION_V2

    # 升级每个判据
    criteria = gate_v2.get("criteria", [])
    for entry in criteria:
        if not isinstance(entry, dict):
            continue
        # 设置 type（默认 scalar）
        if "type" not in entry:
            entry["type"] = "scalar"
        # 映射 select → aggregation（保留 select 字段供 v2 引擎使用）
        select = entry.get("select")
        if select and "aggregation" not in entry:
            agg_op = SELECT_TO_AGG.get(select, select)
            entry["aggregation"] = {"op": agg_op, "detail": f"upgraded_from_select={select}"}
        elif "aggregation" not in entry and entry.get("type") == "scalar":
            # 默认 aggregation
            entry["aggregation"] = {"op": "absmax", "detail": "default_for_scalar"}

    return gate_v2


def is_v1_compatible(gate: dict) -> bool:
    """判断 gate 是否为 v1 格式（可无损升级到 v2）。"""
    sv = gate.get("schema_version")
    if sv == SCHEMA_VERSION:
        return True
    if sv == SCHEMA_VERSION_V2:
        criteria = gate.get("criteria", [])
        return all(e.get("type", "scalar") == "scalar" for e in criteria if isinstance(e, dict))
    return False


def verify_v1_with_v2_engine(gate_path: str | Path, registry: dict | None = None,
                             evidence_root: str | Path | None = None) -> dict:
    """用 v2 引擎复算 v1 gate.json（自动升级）。

    这是 C1 验收判据的核心：历史 v1 语料在 v2 引擎下判定与 v1 逐位一致。
    """
    from .contract_v2 import verify_gate_v2
    from .verify import verify_gate

    # v1 引擎结果
    v1_result = verify_gate(gate_path, registry=registry, evidence_root=evidence_root)

    # 升级到 v2 并用 v2 引擎
    gate_doc = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    gate_v2 = upgrade_v1_to_v2(gate_doc)
    v2_result = verify_gate_v2(gate_doc=gate_v2, registry=registry,
                               evidence_root=evidence_root)

    return {
        "v1_result": v1_result,
        "v2_result": v2_result,
        "grade_match": v1_result["grade"] == v2_result["grade"],
        "criteria_match": _compare_criteria(v1_result, v2_result),
    }


def _compare_criteria(v1: dict, v2: dict) -> bool:
    """比较 v1 和 v2 的逐判据结果是否一致。"""
    v1_criteria = v1.get("criteria", [])
    v2_criteria = v2.get("criteria", [])
    if len(v1_criteria) != len(v2_criteria):
        return False
    for c1, c2 in zip(v1_criteria, v2_criteria):
        if c1.get("status") != c2.get("status"):
            return False
        if c1.get("code") != c2.get("code"):
            return False
        # measured 值比较（容差）
        m1, m2 = c1.get("measured"), c2.get("measured")
        if isinstance(m1, (int, float)) and isinstance(m2, (int, float)):
            if abs(float(m1) - float(m2)) > 1e-9 * max(abs(float(m1)), 1.0):
                return False
        elif m1 is None and m2 is None:
            pass  # both None is OK
        elif m1 is None or m2 is None:
            return False
    return True
