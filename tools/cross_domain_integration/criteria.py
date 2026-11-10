"""验收判据 I1..I5 自检 (数字 + 布尔 + 理由，机器可裁)。

I1 工件完整性   —— 全部节点 ``required`` 工件齐 + 无 ``expected`` 缺口；
I2 契约一致性   —— 契约检查通过率 100% 且无违约条目 (检查数为 0 记 FAIL)；
I3 回归可比     —— 至少一条量的"基线值 + 当前值"同时存在 (可量化)；
I4 报告六要素   —— 报告文档含 manifest 声明的全部小节标题；
I5 产物与图     —— 报告 JSON + 图 PNG 落盘且带 sha256。

每条判据都给 ``required``/``actual``/``pass``/``detail``，报告消费方可直接
按字段裁决，不需要读散文。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def i1_completeness(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(nodes)
    complete = [n for n in nodes if n.get("complete")]
    missing = [{"node": n["node_ext_id"], "required_missing": n.get("required_missing", []),
                "expected_missing": n.get("expected_missing", [])}
               for n in nodes if not n.get("complete")]
    ratio = len(complete) / total if total else 0.0
    return {"required": "all nodes complete (ratio == 1.0)",
            "actual": round(ratio, 6), "pass": ratio == 1.0,
            "detail": f"{len(complete)}/{total} nodes complete",
            "missing": missing}


def i2_contract(contract_summary: dict[str, Any]) -> dict[str, Any]:
    rate = contract_summary.get("pass_rate")
    counts = contract_summary.get("counts", {})
    checks = counts.get("PASS", 0) + counts.get("FAIL", 0)
    ok = checks > 0 and rate == 1.0 and not contract_summary.get("violations")
    return {"required": "contract pass rate == 1.0 with >=1 applicable check",
            "actual": rate, "checks_applicable": checks, "pass": ok,
            "detail": (f"{counts.get('PASS', 0)} PASS / {counts.get('FAIL', 0)} FAIL / "
                       f"{counts.get('NA', 0)} NA")}


def i3_baseline(quantities: list[dict[str, Any]]) -> dict[str, Any]:
    declared = [q for q in quantities if q.get("baseline", {}).get("declared")]
    comparable = [q for q in declared if q.get("comparison", {}).get("verdict")
                  in ("PASS", "FAIL", "NA")]
    ok = len(comparable) >= 1
    return {"required": ">=1 quantity with both baseline and current value",
            "actual": len(comparable), "declared_baselines": len(declared),
            "pass": ok,
            "detail": (f"{len(comparable)}/{len(declared)} declared baselines are "
                       f"numerically comparable; "
                       f"{len(declared) - len(comparable)} WITHHELD")}


def i4_report_doc(doc_path: str | Path | None,
                  sections: dict[str, Any]) -> dict[str, Any]:
    if not doc_path or not Path(doc_path).is_file():
        return {"required": "report document with all declared sections",
                "actual": None, "pass": False,
                "detail": f"document not found: {doc_path}"}
    text = Path(doc_path).read_text(encoding="utf-8")
    headings = [h.strip() for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)]
    joined = "\n".join(headings)
    found, absent = {}, []
    for key, needles in (sections or {}).items():
        patterns = [needles] if isinstance(needles, str) else list(needles)
        hit = next((p for p in patterns if p in joined), None)
        found[key] = hit
        if hit is None:
            absent.append(key)
    ok = bool(found) and not absent
    return {"required": "all declared sections present as headings",
            "actual": f"{len(found) - len(absent)}/{len(found)}", "pass": ok,
            "detail": (f"headings={len(headings)}; absent={absent}" if absent
                       else f"all {len(found)} sections found; headings={len(headings)}"),
            "sections": found}


def i5_outputs(report_path: str | Path | None, report_sha256: str | None,
               figures: list[dict[str, Any]]) -> dict[str, Any]:
    figs_ok = [f for f in figures if f.get("sha256")]
    ok = bool(report_path) and len(figs_ok) >= 1
    return {"required": "report json path plus >=1 figure PNG with sha256",
            "actual": {"report": (str(report_path) if report_path else None),
                       "report_sha256": report_sha256,
                       "figures": len(figs_ok)},
            "pass": ok, "detail": f"{len(figs_ok)} figure(s) hashed"}


def finalize(body: dict[str, Any], report_path: str | Path | None,
             figures: list[dict[str, Any]]) -> dict[str, Any]:
    """用已落盘的图与报告路径补齐 I5，并重算总判定 (不改动其他判据)。"""
    block = body["criteria"]
    block["criteria"]["I5"] = i5_outputs(report_path, None, figures)
    block["criteria"]["I5"]["applicable"] = "I5" in block.get("applicable", ["I5"])
    if not block["criteria"]["I5"]["applicable"]:
        block["criteria"]["I5"]["pass"] = None
    passed = [k for k, v in block["criteria"].items()
              if v.get("applicable", True) and v["pass"]]
    failed = [k for k, v in block["criteria"].items()
              if v.get("applicable", True) and not v["pass"]]
    block["passed"] = passed
    block["failed"] = failed
    block["all_pass"] = not failed
    block["verdict"] = "PASS" if not failed else "FAIL"
    gaps = [g for g in body.get("gaps", []) if not g["id"].startswith("gap:criteria:")]
    for key in failed:
        gaps.append({"id": f"gap:criteria:{key}", "kind": "criterion_fail",
                     "node_ext_id": None, "detail": block["criteria"][key],
                     "action": "see criterion detail; do not overwrite an honest FAIL"})
    body["gaps"] = gaps
    return body


def evaluate(*, nodes: list[dict[str, Any]], contract_summary: dict[str, Any],
             quantities: list[dict[str, Any]], doc_path: str | Path | None,
             doc_sections: dict[str, Any], report_path: str | Path | None,
             report_sha256: str | None, figures: list[dict[str, Any]],
             applicable: list[str] | None = None) -> dict[str, Any]:
    criteria = {
        "I1": i1_completeness(nodes),
        "I2": i2_contract(contract_summary),
        "I3": i3_baseline(quantities),
        "I4": i4_report_doc(doc_path, doc_sections),
        "I5": i5_outputs(report_path, report_sha256, figures),
    }
    applicable = list(applicable or criteria)
    for key, crit in criteria.items():
        crit["applicable"] = key in applicable
        if not crit["applicable"]:
            crit["pass"] = None
            crit["detail"] = "declared not applicable by manifest.criteria_applicable"
    passed = [k for k, v in criteria.items() if v["applicable"] and v["pass"]]
    failed = [k for k, v in criteria.items() if v["applicable"] and not v["pass"]]
    return {"criteria": criteria, "passed": passed, "failed": failed,
            "applicable": applicable,
            "all_pass": not failed,
            "verdict": "PASS" if not failed else "FAIL"}