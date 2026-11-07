"""七环有序过门状态机（通用层，零领域语义）。

环序固定：问题树 → 基线 → 多假设+预测盲 → 实验 → 挑战 → 六维验收 → 沉淀。
硬规则：**前一环未 pass，后一环不得关闭**；任一环 fail/blocked 后整条链停在此处，
下游环一律不可闭（防止「跳过基线上来就给结论」）。状态可序列化，便于落库审计。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

GATES = (
    "problem_tree",
    "baseline",
    "hypotheses_blind",
    "experiment",
    "challenge",
    "acceptance_six_dim",
    "sediment",
)

GATE_INDEX = {name: i for i, name in enumerate(GATES)}

VERDICTS = ("pass", "fail", "blocked")


class GateSequence:
    """七环状态机。``close`` 前置：所有前序环已 pass；环名必须合法。"""

    def __init__(self, required=GATES):
        self.required = tuple(required)
        self._index = {name: i for i, name in enumerate(self.required)}
        self._state: Dict[str, Optional[Dict[str, Any]]] = {g: None for g in self.required}

    # ------------------------------------------------------------------ query
    def index_of(self, gate: str) -> int:
        if gate not in self._index:
            raise ValueError(f"未知环节: {gate}")
        return self._index[gate]

    def blockers(self, gate: str) -> List[str]:
        """关闭该环前仍缺的前序环（已 pass 的不计）。"""
        i = self.index_of(gate)
        missing = []
        for g in self.required[:i]:
            st = self._state[g]
            if not st or st.get("verdict") != "pass":
                missing.append(g)
        return missing

    def can_close(self, gate: str) -> bool:
        return not self.blockers(gate)

    def status(self) -> Dict[str, Any]:
        passed = [g for g in self.required if (self._state[g] or {}).get("verdict") == "pass"]
        verdicts = {g: (self._state[g] or {}).get("verdict") for g in self.required}
        closed_all = all(v == "pass" for v in verdicts.values())
        first_open = next((g for g in self.required if verdicts.get(g) != "pass"), None)
        return {
            "required": list(self.required),
            "verdicts": verdicts,
            "n_pass": len(passed),
            "first_open": first_open,
            "all_pass": closed_all,
            "closure_ratio": round(len(passed) / len(self.required), 6),
        }

    # ------------------------------------------------------------------ close
    def close(
        self,
        gate: str,
        verdict: str,
        *,
        actor_role: str,
        evidence_refs=("",),
        note: str = "",
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"非法 verdict: {verdict}")
        missing = self.blockers(gate)
        if missing:
            raise ValueError(f"环节 {gate} 不可关闭：前序未过 {missing}")
        self._state[gate] = {
            "gate": gate,
            "verdict": verdict,
            "actor_role": actor_role,
            "evidence_refs": sorted({str(x) for x in evidence_refs if str(x)}),
            "note": note,
            "metrics": metrics or {},
        }
        return dict(self._state[gate])