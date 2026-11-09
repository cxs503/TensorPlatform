"""验收聚合（通用层，零领域语义）：把「留痕 + 预测盲 + 多臂对照」折成机器可裁 JSON。

判据（每条都带可复算数字与工件路径）
------------------------------------
A. 留痕完整性：五角色都有记录；七环全部以 pass 关闭；哈希链校验通过。
B. 预测盲：假设卡数 ≥ ``min_blind``；全部封存；盲性复算无违规；已评分。
C. 多臂对照：可比维 ≥ ``min_dims``，且每维两侧都有 source。
D. 三腿闭合（可选，需要调用方提供三腿数字）：theory/implementation/experiment
   三腿误差都在容差内——本层只做结构聚合，不解释物理。
E. 统计功效（可选，调用方传 ``power_gate=six_dim_power.six_dim_advantage(...)`` 的返回值）：
   「≥k 维优于对照」必须过配对置换检验 + Holm FWER 控制后才算成立——C 环只裁决
   「结构可比」，E 环才把「优于」做成可裁命题（每维带 p_perm / p_holm / d_z）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .blind import BlindRegistry, blind_integrity
from .dimensions import comparison_gate
from .gates import GATES, GateSequence
from .ledger import TraceLedger
from .roles import ROLES


def evaluate(
    *,
    ledger: TraceLedger,
    gate_sequence: GateSequence,
    blind: BlindRegistry,
    dim_table: Dict[str, Any],
    legs: Optional[Dict[str, Any]] = None,
    min_blind: int = 2,
    min_dims: int = 2,
    control_arm: str = "control",
    treatment_arm: str = "experiment",
    artifact_paths: Optional[Dict[str, str]] = None,
    power_gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """聚合验收。返回 {pass, criteria:[...], ...}；``criteria`` 每项含 requirement/observed/pass。"""
    artifacts = dict(artifact_paths or {})
    criteria: List[Dict[str, Any]] = []

    # ---- A 留痕 ----
    records = ledger.records
    roles_present = sorted({r["actor_role"] for r in records} & set(ROLES))
    criteria.append({
        "id": "A1_roles_covered",
        "requirement": f"五角色都有留痕（{len(ROLES)} 席）",
        "observed": {"roles_present": roles_present, "n": len(roles_present)},
        "pass": len(roles_present) == len(ROLES),
        "artifact": artifacts.get("trace_ledger"),
    })
    gate_status = gate_sequence.status()
    criteria.append({
        "id": "A2_gates_all_pass",
        "requirement": "七环按序全部 pass",
        "observed": gate_status,
        "pass": bool(gate_status["all_pass"]),
        "artifact": artifacts.get("trace_ledger"),
    })
    chain = ledger.verify()
    criteria.append({
        "id": "A3_chain_integrity",
        "requirement": "哈希链复算无断点",
        "observed": {"ok": chain["ok"], "n_records": chain["n"], "digest": chain["digest"],
                     "reason": chain["reason"]},
        "pass": bool(chain["ok"]) and chain["n"] > 0,
        "artifact": artifacts.get("trace_ledger"),
    })
    criteria.append({
        "id": "A4_trace_volume",
        "requirement": "留痕条数 ≥ 七环 × 3（每环至少问题/结果/裁决三条）",
        "observed": {"n_records": chain["n"], "threshold": 3 * len(GATES)},
        "pass": chain["n"] >= 3 * len(GATES),
        "artifact": artifacts.get("trace_ledger"),
    })

    # ---- B 预测盲 ----
    cards = blind.export()
    integrity = blind_integrity(ledger)
    criteria.append({
        "id": "B1_blind_cards",
        "requirement": f"假设卡 ≥{min_blind} 且全部封存",
        "observed": {"n_cards": cards["n_cards"], "n_sealed": cards["n_sealed"]},
        "pass": cards["n_cards"] >= min_blind and cards["n_sealed"] == cards["n_cards"],
        "artifact": artifacts.get("blind_registry"),
    })
    criteria.append({
        "id": "B2_blind_integrity",
        "requirement": "每个 observe 记录都晚于其 seal 记录（先承诺后观测）",
        "observed": integrity,
        "pass": bool(integrity["ok"]) and integrity["n_cards"] >= min_blind,
        "artifact": artifacts.get("blind_registry"),
    })
    criteria.append({
        "id": "B3_blind_scored",
        "requirement": "全部假设卡已逐项评分（命中/证伪都要有结论）",
        "observed": {"n_scored": cards["n_scored"], "n_cards": cards["n_cards"]},
        "pass": cards["n_cards"] >= min_blind and cards["n_scored"] == cards["n_cards"],
        "artifact": artifacts.get("blind_registry"),
    })

    # ---- C 多臂对照 ----
    dim_gate = comparison_gate(dim_table, arms=[treatment_arm, control_arm], min_dims=min_dims)
    criteria.append({
        "id": "C1_two_arm_dims",
        "requirement": f"实验组 vs 对照组 可比维 ≥{min_dims}（同单位且有 source）",
        "observed": {"n_comparable": dim_gate["n_comparable"],
                     "dims": [d["dimension"] for d in dim_gate["comparable"]],
                     "insufficient": dim_gate["insufficient"]},
        "pass": bool(dim_gate["pass"]),
        "artifact": artifacts.get("six_dim_table"),
    })

    # ---- D 三腿闭合（可选） ----
    if legs is not None:
        leg_items = []
        for name in ("theory", "implementation", "experiment"):
            leg = legs.get(name) or {}
            ok = bool(leg.get("pass")) and leg.get("value") is not None
            leg_items.append({"leg": name, "pass": ok, "value": leg.get("value"),
                              "tolerance": leg.get("tolerance"), "unit": leg.get("unit"),
                              "source": leg.get("source")})
        criteria.append({
            "id": "D1_three_leg_closure",
            "requirement": "理论/实现/实验三腿各自达标（值+容差+出处齐全）",
            "observed": {"legs": leg_items},
            "pass": all(i["pass"] for i in leg_items) and len(leg_items) == 3,
            "artifact": artifacts.get("three_leg"),
        })

    # ---- E 统计功效（可选） ----
    if power_gate is not None:
        dims_pg = power_gate.get("dimensions") or {}
        criteria.append({
            "id": "E1_six_dim_advantage_statistical",
            "requirement": str(power_gate.get("rule")
                               or "六维『优于对照』需过配对置换检验 + Holm 校正"),
            "observed": {
                "n_significant_better": power_gate.get("n_significant_better"),
                "min_dims": power_gate.get("min_dims"),
                "alpha": power_gate.get("alpha"),
                "alternative": power_gate.get("alternative"),
                "n_perm": power_gate.get("n_perm"),
                "n_perm_resolution_ok": power_gate.get("n_perm_resolution_ok"),
                "per_dim": {
                    d: {
                        "n_pairs": (v or {}).get("n_pairs"),
                        "cohen_dz": ((v or {}).get("effect") or {}).get("cohen_dz"),
                        "p_perm": (v or {}).get("p_perm"),
                        "p_holm": (v or {}).get("p_holm"),
                        "significant_better": (v or {}).get("significant_better"),
                    }
                    for d, v in dims_pg.items()
                },
            },
            "pass": bool(power_gate.get("decision_pass")),
            "artifact": artifacts.get("power_gate"),
        })

    failed = [c["id"] for c in criteria if not c["pass"]]
    return {
        "pass": not failed,
        "criteria": criteria,
        "failed_criteria": failed,
        "n_criteria": len(criteria),
        "n_pass": len(criteria) - len(failed),
        "gate_status": gate_status,
        "blind_summary": {"n_cards": cards["n_cards"], "n_sealed": cards["n_sealed"],
                          "n_scored": cards["n_scored"]},
        "dimension_gate": dim_gate,
        "ledger_digest": chain["digest"],
        "artifacts": artifacts,
    }