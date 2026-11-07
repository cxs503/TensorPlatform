#!/usr/bin/env python3
"""IN28-G25 继承门的对照实验（阳性对照 + 阴性对照），证明门既不恒过也不恒败。

背景（为什么必须有对照）：一个"任何输入都判 not_ready"的门等于没有门。真实快照此刻
上游 9 个 mission 全 running、0 报告，裁决自然是 not_ready —— 需要**阳性对照**证明门在
证据齐备时会放行，再用**阴性对照**逐项证明门对四类缺陷各有一条独立拦截路径。

做法（全部在内存里构造，标注 synthetic，不改库、不改真实快照）：
  1. 读真实快照 `artifacts/in28_g25/snapshot.json`；
  2. 对每个节点按其 metadata.acceptance_criteria 生成"恰好达标"的测量值
     （<,<= → 0.9×阈值；>,>= → 1.1×阈值；= → 阈值本体；百分点判据统一 unit=fraction），
     并把节点置 done / verdict=pass / mission=done，附共享互裁量（spread≈2% < 容差 10%）；
  3. 阳性对照：裁决应为 ready（score ≥ 0.7 且四条硬门全过）；
  4. 阴性对照（各只注入一个缺陷，其余保持阳性状态）：
       T1 单条判据越限        → 期望 criterion_failed 且节点不被验收
       T2 百分点判据缺 unit   → 期望 unproven（不猜量纲）
       T3 一致性越容差        → 期望 consistency_violation 硬门失败
       T4 治理节点缺证据      → 期望 governance 硬门失败
     每个变体都断言 verdict=not_ready 且阻塞项含指定子串（不只看分数）。

产物：artifacts/in28_g25/gate_controls.json（裁决摘要 + 断言结论 + digest）
用法：python3 tools/in28_g25_gate_controls.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ops.taskplane.inheritance_gate import (  # noqa: E402
    evaluate_inheritance_gate,
    parse_criterion,
)

ART = REPO / "artifacts" / "in28_g25"


def _load(name: str) -> dict:
    return json.loads((ART / name).read_text(encoding="utf-8"))


def _passing_value(crit: dict, synthetic_ref: str = "artifacts/synthetic/attest.json") -> dict:
    """生成恰好达标的测量条目（数值判据）；布尔/质性判据凭「通过声明 + 工件指针」。"""
    if not crit.get("parsed"):
        return {"pass": True, "evidence": synthetic_ref}
    t = float(crit["threshold"])
    op = crit["op"]
    # 方向先行：上界类取 0.9×，下界类取 1.1×，然后再做百分点归一
    v = t * 0.9 if op in ("lt", "le") else (t * 1.1 if op in ("gt", "ge") else t)
    if crit.get("percent"):
        return {"value": v / 100.0, "unit": "fraction"}
    return {"value": v}


def _golden_snapshot(real: dict, shared_values: dict[int, float] | None = None) -> dict:
    """真实快照 → 全部节点"证据齐备"的合成阳性快照（synthetic，标注在 snapshot_meta）。"""
    snap = json.loads(json.dumps(real))          # 深拷贝
    snap["snapshot_meta"] = dict(snap.get("snapshot_meta") or {})
    snap["snapshot_meta"]["synthetic"] = ("control experiment: evidence injected to "
                                         "exactly meet declared criteria")
    for node in snap["nodes"]:
        meta = node.get("metadata") or {}
        criteria = meta.get("acceptance_criteria") or []
        node["status"] = "done"
        node["reproduction_verdict"] = "pass"
        mid = node.get("mission_id")
        if mid is not None:
            snap["missions"][str(mid)] = {"id": mid, "status": "done"}
        acceptance = {}
        for raw in criteria:
            crit = parse_criterion(raw)
            acceptance[crit["key"]] = _passing_value(
                crit, synthetic_ref=f"artifacts/synthetic/node_{node['id']}_attest.json")
        nid = int(node["id"])
        q = (shared_values or {}).get(nid, 1.0 + 0.002 * (nid - 531))
        evidence = {
            "acceptance": acceptance,
            "quantities": {"q_shared": {"value": q, "tol": 0.10},
                           "q_ref": {"value": 4.0 * q, "reference": 4.0, "rtol": 0.10}},
            "artifacts": [f"artifacts/synthetic/node_{nid}.json"],
            "synthetic": True,
        }
        if mid is not None:
            snap["reports"][str(mid)] = [{
                "id": f"synthetic-{mid}", "mission_id": str(mid), "status": "published",
                "created_at": "2026-01-01T00:00:00+00:00",
                "inheritance_evidence": evidence,
            }]
    return snap


def _mutate(snap: dict, variant: str) -> dict:
    s = json.loads(json.dumps(snap))
    nodes = s["nodes"]
    if variant == "T1_measurement_out_of_limit":
        node = nodes[0]
        ev = s["reports"][str(node["mission_id"])][0]["inheritance_evidence"]
        key = next(iter(ev["acceptance"]))
        ev["acceptance"][key] = {"value": 1.5, "unit": "fraction"}   # 150% 远超任何限
    elif variant == "T2_pct_without_unit":
        node = next(n for n in nodes
                    if any(parse_criterion(c).get("percent")
                           for c in (n["metadata"].get("acceptance_criteria") or [])))
        ev = s["reports"][str(node["mission_id"])][0]["inheritance_evidence"]
        key = next(k for k, v in ev["acceptance"].items() if "unit" in v)
        ev["acceptance"][key] = {"value": 6.1}                        # 有值但无 unit
    elif variant == "T3_consistency_violation":
        node = nodes[1]
        ev = s["reports"][str(node["mission_id"])][0]["inheritance_evidence"]
        ev["quantities"]["q_shared"] = {"value": 1.6, "tol": 0.10}
    elif variant == "T4_governance_evidence_gap":
        cfg = _load("gate_config.json")
        gov_ids = [int(x) for x in cfg["governance"]["node_ids"]]
        node = next(n for n in nodes if int(n["id"]) in gov_ids)
        s["reports"].pop(str(node["mission_id"]), None)
    else:  # pragma: no cover
        raise ValueError(variant)
    return s


def main() -> int:
    real = _load("snapshot.json")
    cfg = _load("gate_config.json")

    real_report = evaluate_inheritance_gate(real, cfg)
    golden = _golden_snapshot(real)
    golden_report = evaluate_inheritance_gate(golden, cfg)

    checks = [{
        "case": "REAL",
        "expect_verdict": "not_ready",
        "verdict": real_report["verdict"],
        "score": real_report["score"],
        "expected_blocker_substring": "node_not_done",
        "assertion": any("node_not_done" in b for b in real_report["blockers"]),
    }, {
        "case": "POSITIVE_CONTROL_all_evidence",
        "expect_verdict": "ready",
        "verdict": golden_report["verdict"],
        "score": golden_report["score"],
        "expected_blocker_substring": None,
        "assertion": golden_report["verdict"] == "ready" and not golden_report["blockers"],
    }]

    variants = {
        "T1_measurement_out_of_limit": "criterion_failed",
        "T2_pct_without_unit": "pct_criterion_requires_unit",
        "T3_consistency_violation": "consistency_violation:q_shared",
        "T4_governance_evidence_gap": "governance_node_not_accepted",
    }
    for variant, needle in variants.items():
        rep = evaluate_inheritance_gate(_mutate(golden, variant), cfg)
        checks.append({
            "case": variant,
            "expect_verdict": "not_ready",
            "verdict": rep["verdict"],
            "score": rep["score"],
            "expected_blocker_substring": needle,
            "assertion": rep["verdict"] == "not_ready"
                         and any(needle in b for b in rep["blockers"]),
            "blocker_sample": next((b for b in rep["blockers"] if needle in b), None),
        })

    ok = all(c["assertion"] for c in checks)
    payload = {
        "schema": "inheritance_gate_controls/v1",
        "config_digest_input": cfg,
        "gate_input_digest_real": real_report["input_digest"],
        "gate_input_digest_golden": golden_report["input_digest"],
        "golden_components": golden_report["components"],
        "golden_hard_gates": golden_report["hard_gates"],
        "golden_milestones": [(m["milestone_id"], m["n_accepted"], m["n_nodes"])
                              for m in golden_report["milestones"]["milestones"]],
        "checks": checks,
        "all_assertions_passed": ok,
        "note": ("POSITIVE_CONTROL 与 T1–T4 的快照均为内存合成（synthetic=true），"
                 "不写入任何库；真实快照裁决见 gate_input_digest_real 对应报告。"),
    }
    (ART / "gate_controls.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for c in checks:
        print(f"{c['case']:28s} verdict={c['verdict']:10s} score={c['score']:.4f} "
              f"assert={c['assertion']}")
    print("all_assertions_passed =", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())