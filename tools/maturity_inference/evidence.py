"""证据节点：五级阶梯各环的绑定要求 + fail-closed 校验。

一条证据节点（``EvidenceNode``）绑定 **等级环位（rung）+ 证据类型 + 来源 +
可执行性**。``RUNG_REQUIREMENTS`` 就是"这一环要凭什么才算数"的机器可判清单：
每条要求是一个点号路径（``dotted path``），路径存在且非空即满足，否则该环
**不可用**——调用方据此截断阶梯（fail-closed）。

约定（与 ``interface_contract`` 一致，上游漂移时只改本模块 + ``ledger_adapter``）::

    {
      "node_id": "ev:3f2a1c9d",                       # 稳定 id（缺省按内容算）
      "capability_key": "alpha.beta.gamma",           # 由命名空间门校验
      "rung": "tested",                               # LADDER 内
      "source": {"kind": "pytest", "locator": "...", "scope": "repo",
                 "artifact_sha256": "<64 hex>"},
      "rerun": {"cmd": "...", "status": "passed", "executed_at": "...",
                "log_sha256": "<64 hex>"},
      "criterion": {"name": "...", "value": 0.0, "tolerance": 0.0,
                    "baseline": "...", "satisfied": true, "unit": "-"},
      "coupling": {"components": ["a","b"], "interface_digest": "<64 hex>",
                   "end_to_end_rerun": "..."},
      "qualification": {"reviewer": "...", "repeat_runs": [{"sha256": "...",
                         "verdict": "pass"}, ...], "envelope": "...",
                         "signoff_sha256": "<64 hex>"},
      "provenance": {"producer": "...", "recorded_at": "ISO-8601",
                     "sha256": "<64 hex>"}
    }

校验是**结构性**的（字段齐备 + sha 形状 + 数值自洽），不代表物理正确；
物理正确性由 ``criterion``（数值 vs 基线 vs 容差）与矛盾证据封顶共同约束。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .ladder import LADDER, rank_of

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

#: 各环的必交字段（点号路径）。rank 0 无要求。
RUNG_REQUIREMENTS: Dict[str, Tuple[str, ...]] = {
    "implemented": (
        "source.kind",
        "source.locator",
        "source.artifact_sha256",
        "provenance.producer",
        "provenance.recorded_at",
        "provenance.sha256",
    ),
    "tested": (
        "rerun.cmd",
        "rerun.status",
        "rerun.log_sha256",
        "rerun.executed_at",
    ),
    "benchmark_verified": (
        "criterion.name",
        "criterion.value",
        "criterion.tolerance",
        "criterion.baseline",
        "criterion.satisfied",
    ),
    "coupled_prototype": (
        "coupling.components",
        "coupling.interface_digest",
        "coupling.end_to_end_rerun",
    ),
    "engineering_qualified": (
        "qualification.reviewer",
        "qualification.repeat_runs",
        "qualification.envelope",
        "qualification.signoff_sha256",
    ),
}

#: 需要 sha256 形状校验的字段
SHA_FIELDS: Tuple[str, ...] = (
    "source.artifact_sha256",
    "rerun.log_sha256",
    "coupling.interface_digest",
    "qualification.signoff_sha256",
    "provenance.sha256",
)

#: 证据作用域：``fixture`` = 仅测试夹具/合成，最多支撑到 tested（L2）
SCOPE_CAP: Dict[str, int] = {"fixture": rank_of("tested"), "repo": len(LADDER)}


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def canonical_digest(payload: Any) -> str:
    """规范化 JSON 摘要（键序稳定），用于节点 id 与工件 sha。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class EvidenceNode:
    """一条证据节点（原始 mapping + 派生校验结果）。"""

    payload: Dict[str, Any]
    node_id: str = ""
    missing: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "EvidenceNode":
        data = dict(payload)
        node = cls(payload=data)
        node.node_id = str(data.get("node_id") or ("ev:" + canonical_digest(data)[:16]))
        node.missing, node.problems = validate_node(data)
        return node

    # -- 基本属性 ---------------------------------------------------------
    @property
    def capability_key(self) -> str:
        return str(self.payload.get("capability_key", ""))

    @property
    def rung(self) -> str:
        return str(self.payload.get("rung", ""))

    @property
    def rank(self) -> int:
        return rank_of(self.rung)

    @property
    def scope(self) -> str:
        return str(_dig(self.payload, "source.scope") or "repo")

    @property
    def valid(self) -> bool:
        """结构校验全过（缺件为空且无问题）。"""
        return not self.missing and not self.problems

    @property
    def supports(self) -> Optional[int]:
        """本节点实际支撑到的最高 rank（fail-closed：无效即降一环；夹具再封顶）。"""
        if not self.valid:
            return max(self.rank - 1, 0)
        return min(self.rank, SCOPE_CAP.get(self.scope, len(LADDER)))

    def digest(self) -> str:
        return canonical_digest(self.payload)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "capability_key": self.capability_key,
            "rung": self.rung,
            "rank": self.rank,
            "scope": self.scope,
            "valid": self.valid,
            "missing": list(self.missing),
            "problems": list(self.problems),
            "supports_rank": self.supports,
            "digest": self.digest(),
        }


def validate_node(payload: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    """对一条证据节点做结构性校验，返回 ``(缺失字段, 其他问题)``。

    校验规则（全部显式，无默认放行）：

    1. ``rung`` 必须在 ``LADDER`` 内且不是 rank 0（rank 0 无证据要求，也不该成节点）。
    2. 该环 ``RUNG_REQUIREMENTS`` 的每个点号路径必须存在且非空。
    3. sha256 字段必须是 64 位小写十六进制。
    4. ``tested`` 及以上的 ``rerun.status`` 必须是 ``passed``；``failed``/``not_run`` 判不通过。
    5. ``benchmark_verified`` 的 ``criterion.satisfied`` 必须为真，且
       ``|value - baseline_ref|`` 不得超过 ``tolerance``（若给出 ``baseline_ref`` 数值）。
    6. ``coupled_prototype`` 的组件数必须 ≥2。
    7. ``engineering_qualified`` 的 ``repeat_runs`` 必须 ≥2 次且 sha256 互不相同（复跑独立）。
    """
    missing: List[str] = []
    problems: List[str] = []
    rung = str(payload.get("rung", ""))
    if rung not in LADDER:
        problems.append(f"rung 未登记: {rung!r}")
        return missing, problems
    rank = rank_of(rung)
    if rank == 0:
        problems.append("rank 0 不应作为证据节点")
        return missing, problems

    # 阶梯是**逐环累积**的：一环的证据必须同时满足更低下限（implemented 基础件）
    needed: List[str] = list(RUNG_REQUIREMENTS["implemented"])
    for name in LADDER[1: rank + 1]:
        for req in RUNG_REQUIREMENTS[name]:
            if req not in needed:
                needed.append(req)

    for path in needed:
        if _empty(_dig(payload, path)):
            missing.append(path)

    for path in SHA_FIELDS:
        value = _dig(payload, path)
        if isinstance(value, str) and value.strip() and not _SHA_RE.match(value.strip()):
            problems.append(f"{path} 非 64 位小写 hex: {value[:16]}…")

    status = _dig(payload, "rerun.status")
    if rank >= rank_of("tested") and not _empty(status) and str(status) != "passed":
        problems.append(f"rerun.status={status!r} 不是 passed（不可支撑 tested 及以上）")

    if rank >= rank_of("benchmark_verified"):
        satisfied = _dig(payload, "criterion.satisfied")
        if satisfied is not True:
            problems.append("criterion.satisfied 不为 true")
        value = _dig(payload, "criterion.value")
        tol = _dig(payload, "criterion.tolerance")
        ref = _dig(payload, "criterion.baseline_ref")
        if isinstance(value, (int, float)) and isinstance(ref, (int, float)) and isinstance(tol, (int, float)):
            if abs(float(value) - float(ref)) > float(tol):
                problems.append(
                    f"criterion 越界: |{value} - {ref}| = {abs(float(value) - float(ref)):.6g} > tol {tol}")

    if rank >= rank_of("coupled_prototype"):
        comps = _dig(payload, "coupling.components")
        if isinstance(comps, (list, tuple)) and len(comps) < 2:
            problems.append(f"coupling.components 少于 2 个: {comps!r}")

    if rank >= rank_of("engineering_qualified"):
        runs = _dig(payload, "qualification.repeat_runs")
        if isinstance(runs, (list, tuple)):
            if len(runs) < 2:
                problems.append(f"qualification.repeat_runs 少于 2 次: {len(runs)}")
            else:
                shas = [str((r or {}).get("sha256", "")) for r in runs]
                if len(set(shas)) < len(shas):
                    problems.append("qualification.repeat_runs 的 sha256 未互异（复跑不独立）")
                bad = [r for r in runs if str((r or {}).get("verdict", "")) != "pass"]
                if bad:
                    problems.append(f"qualification.repeat_runs 有非 pass 判: {len(bad)} 次")

    return missing, problems


def build_node(
    capability_key: str,
    rung: str,
    *,
    kind: str,
    locator: str,
    artifact_sha256: str,
    producer: str,
    recorded_at: str,
    scope: str = "repo",
    rerun: Optional[Mapping[str, Any]] = None,
    criterion: Optional[Mapping[str, Any]] = None,
    coupling: Optional[Mapping[str, Any]] = None,
    qualification: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """便捷构造器：补齐 ``provenance.sha256``（对全部内容取摘要）与 ``node_id``。"""
    payload: Dict[str, Any] = {
        "capability_key": capability_key,
        "rung": rung,
        "source": {
            "kind": kind,
            "locator": locator,
            "scope": scope,
            "artifact_sha256": artifact_sha256,
        },
    }
    if rerun is not None:
        payload["rerun"] = dict(rerun)
    if criterion is not None:
        payload["criterion"] = dict(criterion)
    if coupling is not None:
        payload["coupling"] = dict(coupling)
    if qualification is not None:
        payload["qualification"] = dict(qualification)
    body = canonical_digest(payload)
    payload["provenance"] = {
        "producer": producer,
        "recorded_at": recorded_at,
        "sha256": body,
    }
    payload["node_id"] = "ev:" + body[:16]
    return payload


def evidence_chain(nodes: Sequence[EvidenceNode], upto_rank: int) -> List[Dict[str, Any]]:
    """挑出支撑阶梯到 ``upto_rank`` 的证据链：每环取一条最强（有效优先、id 最小）。"""
    chain: List[Dict[str, Any]] = []
    for rank in range(1, upto_rank + 1):
        level = LADDER[rank]
        candidates = [n for n in nodes if n.rank == rank]
        valid = [n for n in candidates if n.valid]
        pick = sorted(valid or candidates, key=lambda n: n.node_id)[0] if candidates else None
        if pick is None:
            break
        entry = pick.as_dict()
        entry["locator"] = _dig(pick.payload, "source.locator")
        entry["rerun_cmd"] = _dig(pick.payload, "rerun.cmd")
        entry["criterion"] = _dig(pick.payload, "criterion")
        chain.append(entry)
    return chain