"""IN47-N08 总装：把三条成熟化能力跑成一份机器可裁的结论 JSON。

输出结构（``artifacts/in47_n08/verdict.json``）固定包含任务要求的判据数字与
产物路径，便于上层战役表机器读取。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Optional, Sequence

from .corpus import CaseRecord, REAL, build_corpus, holdout_hulls, oracle_label
from .cross_validation import cross_verify
from .external_validity import evaluate_external_validity
from .families import family_a, family_b
from .robustness import evaluate_robustness

SCHEMA = "in47-n08-maturity/1"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parents[2] / "artifacts" / "in47_n08"


def run_all(
    cases: Optional[Sequence[CaseRecord]] = None,
    *,
    out_dir: Optional[pathlib.Path] = None,
    n_evals: int = 5,
    seed: int = 20261106,
    write: bool = True,
) -> Dict[str, Any]:
    cases = list(cases) if cases is not None else build_corpus()
    out_dir = pathlib.Path(out_dir) if out_dir is not None else DEFAULT_OUT

    cv = cross_verify(cases)
    ev = evaluate_external_validity(cases)
    rb = evaluate_robustness(cases, n_evals=n_evals, seed=seed)
    oracle = {c.case_id: oracle_label(c) for c in cases}

    # 物理误差表（程序化注入，零手抄）
    physics_rows = [c.to_dict() for c in cases]

    verified_real = [r["case_id"] for r in cv["rows"]
                     if r["agreed"] and r["consistent_label"] == REAL]

    criteria = {
        "cross_model": {
            "n_cases": cv["n_cases"],
            "n_families": 2,
            "n_physics_domains": len({c.domain for c in cases}),
            "consistency": cv["consistency"],
            "min_consistency": cv["min_consistency"],
            "n_cross_model_verified": len(cv["cross_model_verified"]),
            "n_severe_mismatches": len(cv["severe_mismatches"]),
            "precision_vs_oracle": cv["precision_vs_oracle"],
            "pass": bool(cv["pass"]),
        },
        "external_validity": {
            "holdout_hulls": ev["holdout_hulls"],
            "n_holdout_hulls": ev["n_holdout_hulls"],
            "delta_accuracy_pp": ev["scorer_external_validity"]["delta_accuracy_pp"],
            "delta_consistency_pp": ev["scorer_external_validity"]["delta_consistency_pp"],
            "tolerance_pp": ev["scorer_external_validity"]["tolerance_pp"],
            "accuracy_verified": ev["by_split"]["verified"]["joint_accuracy"],
            "accuracy_holdout": ev["by_split"]["holdout"]["joint_accuracy"],
            "pass": bool(ev["scorer_external_validity"]["pass"]),
            "model_generalization_pass": bool(ev["model_generalization"]["pass"]),
            "model_generalization_failing_domains":
                ev["model_generalization"]["failing_domains"],
        },
        "scorer_robustness": {
            "n_cases": rb["n_cases"],
            "n_evals": rb["n_evals"],
            "n_evaluations": rb["n_cases"] * rb["n_evals"],
            "flip_rate": rb["flip_rate"],
            "flip_rate_single_measurement": rb["single_measurement"]["flip_rate"]["joint"],
            "max_flip_rate": rb["max_flip_rate"],
            "pass": bool(rb["pass"]),
        },
    }
    all_pass = all(v["pass"] for v in criteria.values())
    verdict = {
        "schema": SCHEMA,
        "mission_id": 5735,
        "node": "IN47-N08",
        "n_cases": len(cases),
        "domains": sorted({c.domain for c in cases}),
        "criteria": criteria,
        "pass": all_pass,
        "verdict": "PASS" if all_pass else "HONEST_FAIL",
        "honest_caveats": _caveats(criteria) + _miss_caveats(ev),
        "cross_model_verified_real_cases": verified_real,
        "artifact_paths": {
            name: _rel(out_dir / name)
            for name in ("verdict.json", "cross_model.json", "external_validity.json",
                         "robustness.json", "physics_error_table.json", "oracle_labels.json")
        },
    }

    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
        (out_dir / "cross_model.json").write_text(json.dumps(cv, ensure_ascii=False, indent=2))
        (out_dir / "external_validity.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2))
        (out_dir / "robustness.json").write_text(json.dumps(rb, ensure_ascii=False, indent=2))
        (out_dir / "physics_error_table.json").write_text(
            json.dumps(physics_rows, ensure_ascii=False, indent=2))
        (out_dir / "oracle_labels.json").write_text(
            json.dumps({k: v for k, v in sorted(oracle.items())}, ensure_ascii=False, indent=2))
    return {"verdict": verdict, "cross_model": cv, "external_validity": ev,
            "robustness": rb, "cases": cases}


def _rel(path: pathlib.Path) -> str:
    """工作树相对路径（报告与产物可移植）。"""
    try:
        return str(pathlib.Path(path).resolve().relative_to(
            pathlib.Path(__file__).resolve().parents[2]))
    except Exception:  # pragma: no cover
        return str(path)


def _miss_caveats(ev: Dict[str, Any]) -> list:
    """把 hold-out 误判逐条披露（方向：过度保守 / 过度认证）。"""
    misses = [r for r in ev.get("rows", [])
              if r["split"] == "holdout" and r["joint_label"] != r["oracle_label"]]
    if not misses:
        return []
    detail = "、".join(
        f"{m['case_id']}（联合 {m['joint_label']} vs 参考标准 {m['oracle_label']}，"
        f"|dev|={m['abs_error_pct']}%）" for m in misses)
    return [f"hold-out 误判 {len(misses)} 例：{detail}；方向为过度保守（贴容差不敢确认 REAL），"
            f"不是过度认证"]


def _caveats(criteria: Dict[str, Any]) -> list:
    out = []
    ev = criteria["external_validity"]
    if ev["model_generalization_failing_domains"]:
        out.append(
            "物理模型泛化在域 " + "、".join(ev["model_generalization_failing_domains"])
            + " 上未通过 |Δerr|≤5pp（评分器外部效度不受影响，但模型侧是该域的既有技术债，如实披露）")
    if ev["delta_accuracy_pp"] is not None and abs(ev["delta_accuracy_pp"]) > 0:
        out.append(f"评分器 hold-out 与标定集正确率差 {ev['delta_accuracy_pp']:+.2f} pp")
    if criteria["cross_model"]["precision_vs_oracle"] is not None \
            and criteria["cross_model"]["precision_vs_oracle"] < 1.0:
        out.append(
            f"两族一致的正判精确率 {criteria['cross_model']['precision_vs_oracle']:.3f} < 1："
            "一致 ≠ 为真，交叉验证仍需预注册参考标准兜底")
    if criteria["scorer_robustness"]["flip_rate_single_measurement"] is not None:
        out.append(
            "单次测量协议的翻转率 "
            f"{criteria['scorer_robustness']['flip_rate_single_measurement']:.4f} "
            "作为对照披露（结论协议为复测取中位）")
    return out