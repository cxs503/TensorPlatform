"""七条机器判据 + 命令行入口（一次跑完 build → train → figures → accept）。

判据（任务书原文口径，数字全部落 ``acceptance.json``）：

1. walk-forward pooled AUC ≥ 0.65（且须**可识别**：训练折里两类都出现过）
2. pooled Brier ≤ 0.25
3. 相对简单规则基线（S1/S2=成功、E≤1=失败）AUC 提升 ≥ 0.05
4. Shapley 特征重要性排序输出，且效率性 ``|sum(phi)-(f(x)-E[f])| < 1e-6``
5. 时序交叉验证无泄露：每折 ``train_max < test_min``，且每样本 ``t_features < t_label``
6. 队列样本量 ≥ 200（已判级且下游结果可观测的 finished mission）
7. pytest 用例 ≥ 10 条且全绿

判据之外另记 ``diagnostics``（不参与裁决，但报告必须引用）：可识别性证书、
仪器正/负控、批次混淆诊断、评价渠道覆盖率。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from typing import Any

from . import analysis, dataset as ds_mod

SCHEMA_VERSION = "extval.v1"
ACCEPTANCE_THRESHOLDS = {
    "auc_min": 0.65,
    "brier_max": 0.25,
    "auc_gain_vs_rule_min": 0.05,
    "efficiency_tol": 1e-6,
    "min_samples": 200,
    "min_pytest_cases": 10,
}


def _crit(name: str, ok: bool | None, value: Any, threshold: Any, note: str = "") -> dict[str, Any]:
    return {
        "criterion": name,
        "pass": bool(ok) if ok is not None else False,
        "status": "PASS" if ok else ("WITHHELD" if ok is None else "FAIL"),
        "value": value,
        "threshold": threshold,
        "note": note,
    }


def evaluate_criteria(
    dataset: dict[str, Any],
    result: dict[str, Any],
    pytest_cases: int = 0,
    pytest_passed: int = 0,
) -> dict[str, Any]:
    th = ACCEPTANCE_THRESHOLDS
    wf = result["walk_forward"]["primary"]
    auc = wf["pooled_auc"]
    brier = wf["pooled_brier"]
    rule_auc = result["metrics"]["simple_rule"]["auc"]
    gain = None if (auc is None or rule_auc is None) else auc - rule_auc
    identifiable = bool(wf.get("identifiable"))

    crits = [
        _crit(
            "walk_forward_auc>=0.65",
            None if auc is None else (auc >= th["auc_min"] and identifiable),
            auc,
            th["auc_min"],
            (
                f"primary={result['primary_model']}；pooled OOF AUC 覆盖 {wf['n_scored']}/{result['n']}"
                f"（coverage {wf['coverage']:.3f}）；95% bootstrap CI "
                f"{wf['auc_ci']['lo']}..{wf['auc_ci']['hi']}；"
                f"可识别性={identifiable}（单类训练折 {wf['folds_with_single_class_train']}/{wf['n_folds']}，"
                f"单类测试折 {wf.get('folds_with_single_class_test')}/{wf['n_folds']}，"
                f"有定义的折 AUC {wf['fold_auc_defined']}/{wf['n_folds']}）"
            ),
        ),
        _crit(
            "brier<=0.25",
            None if (brier is None or len(wf.get("scored_classes") or []) < 2)
            else brier <= th["brier_max"],
            brier,
            th["brier_max"],
            f"pooled OOF Brier（判级口径 ECE={wf['pooled_ece']}）；"
            f"折外打分子集类别={wf.get('scored_classes')}（单类则 Brier 无意义 -> WITHHELD）；"
            f"常数基线 Brier(base rate)={result['metrics']['random_baseline']['brier_base_rate']}",
        ),
        _crit(
            "auc_gain_vs_simple_rule>=0.05",
            None if gain is None else gain >= th["auc_gain_vs_rule_min"],
            gain,
            th["auc_gain_vs_rule_min"],
            f"简单规则 AUC={rule_auc}（S1/S2=成功、E≤1=失败，命中 {result['metrics']['simple_rule']['n_pos_pred']} 条）",
        ),
        _crit(
            "shap_importance_emitted",
            bool(result["shap"]["importance"])
            and abs(result["shap"]["max_abs_efficiency_gap"] or 0.0) < th["efficiency_tol"],
            {
                "top5": [r["feature"] for r in result["shap"]["importance"][:5]],
                "max_abs_efficiency_gap": result["shap"]["max_abs_efficiency_gap"],
            },
            f"|sum(phi)-(f(x)-E[f])| < {th['efficiency_tol']}",
            "置换采样 Shapley，背景=单点参考；红标=批次代理特征",
        ),
        _crit(
            "no_temporal_leakage",
            _leakage_ok(dataset, result),
            {
                "folds": [
                    {
                        "fold": f["fold"],
                        "t_train_max": f["t_train_max"],
                        "t_test_min": f["t_test_min"],
                        "ordered": f["t_train_max"] < f["t_test_min"],
                    }
                    for f in result["walk_forward"]["logreg"]["folds"]
                ],
                "samples_feature_before_label": _samples_ordered(dataset),
            },
            "train_max < test_min（每折）且 t_features < t_label（每样本）",
            "标签时刻取下游事件（PR merged/closed、返工/治理、终止），恒晚于特征时刻",
        ),
        _crit(
            "n_samples>=200",
            dataset["n"] >= th["min_samples"],
            dataset["n"],
            th["min_samples"],
            f"已判级且下游可观测；阳性 {dataset['n_positive']}；剔除 {sum(dataset['excluded_counts'].values())}"
            f"（{dataset['excluded_counts']}）",
        ),
        _crit(
            "pytest>=10_cases",
            pytest_cases >= th["min_pytest_cases"] and pytest_passed == pytest_cases and pytest_cases > 0,
            {"cases": pytest_cases, "passed": pytest_passed},
            f"≥{th['min_pytest_cases']} cases 且全绿",
            "tests/test_extval_calibration.py",
        ),
    ]
    n_pass = sum(1 for c in crits if c["pass"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "thresholds": th,
        "criteria": crits,
        "n_pass": n_pass,
        "n_total": len(crits),
        "verdict": "PASS" if n_pass == len(crits) else "FAIL",
        "diagnostics": _diagnostics(dataset, result),
        "artifacts": {},
    }


def _diagnostics(dataset: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "identifiability_certificate": {
            "walk_forward_identifiable": result["structural"]["walk_forward_identifiable"],
            "folds_with_single_class_train": result["structural"]["folds_with_single_class_train"],
            "n_folds": result["structural"]["n_folds"],
            "fold_auc_defined": result["walk_forward"]["logreg"]["fold_auc_defined"],
            "oof_coverage": result["structural"]["oof_coverage"],
            "negative_class_distinct_finish_days": result["structural"]["negative_class_distinct_finish_days"],
            "folds_with_single_class_test": result["structural"]["folds_with_single_class_test"],
            "scored_classes": result["structural"]["scored_classes"],
            "negative_max_t_features": result["structural"]["negative_max_t_features"],
            "positive_after_last_negative": result["structural"]["positive_after_last_negative"],
            "negative_by_finish_day": result["structural"]["negative_by_finish_day"],
        },
        "instrument_controls": result["controls"],
        "association_batch_controlled": {
            "full": result["association"]["full"],
            "grade_only": result["association"]["grade_only"],
            "batch_proxy_only": result["association"]["batch_only"],
            "full_minus_batch_proxy": result["association"]["no_batch"],
            "permutation_null_full": result["association"]["permutation_null_full"],
            "permutation_null_grade_only": result["association"]["permutation_null_grade_only"],
            "within_delivery_wave": result["association"]["within_delivery_wave"],
            "batch_proxy_features": result["batch_proxy_features"],
        },
        "grade_provenance": result["structural"].get("grade_counts", {}),
        "grade_decided_after_label": result["structural"].get("grade_decided_after_label", {}),
        "excluded_counts": dataset.get("excluded_counts", {}),
    }


def _leakage_ok(dataset: dict[str, Any], result: dict[str, Any]) -> bool:
    for f in result["walk_forward"]["logreg"]["folds"]:
        if not f["t_train_max"] < f["t_test_min"]:
            return False
    return _samples_ordered(dataset)["all_ordered"]


def _samples_ordered(dataset: dict[str, Any]) -> dict[str, Any]:
    bad = [s["mission_id"] for s in dataset["samples"] if not s["t_features"] < s["t_label"]]
    return {"n": dataset["n"], "violations": len(bad), "all_ordered": not bad}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_PYTEST_RE = re.compile(r"(\d+) passed")


def _run_pytest(test_path: str) -> tuple[int, int]:
    if not os.path.exists(test_path):
        return 0, 0
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-q", "--no-header"],
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    m = _PYTEST_RE.search(text)
    passed = int(m.group(1)) if m else 0
    return passed, passed if proc.returncode == 0 else 0


def run_all(
    dsn: str,
    outdir: str,
    as_of: str,
    horizon_hours: float = 48.0,
    n_folds: int = 5,
    test_path: str = "tests/test_extval_calibration.py",
    skip_pytest: bool = False,
    n_permutations: int = 40,
) -> dict[str, Any]:
    os.makedirs(outdir, exist_ok=True)
    dataset = ds_mod.build_dataset_from_db(dsn, as_of, horizon_hours=horizon_hours)
    ds_mod.write_dataset(dataset, os.path.join(outdir, "dataset.json"))
    ds_json = dataset.to_json()

    result = analysis.analyze(ds_json, n_folds=n_folds, null_permutations=n_permutations)
    figs = analysis.make_figures(ds_json, result, os.path.join(outdir, "figures"))

    cases, passed = (0, 0) if skip_pytest else _run_pytest(test_path)
    acc = evaluate_criteria(ds_json, result, pytest_cases=cases, pytest_passed=passed)
    acc["artifacts"] = {
        "dataset": os.path.join(outdir, "dataset.json"),
        "metrics": os.path.join(outdir, "metrics.json"),
        "shap": os.path.join(outdir, "shap_importance.json"),
        "acceptance": os.path.join(outdir, "acceptance.json"),
        "figures": figs,
    }

    _dump(os.path.join(outdir, "metrics.json"), analysis._strip_deep(result))
    _dump(
        os.path.join(outdir, "shap_importance.json"),
        {
            "schema_version": SCHEMA_VERSION,
            "n_explain": result["shap"]["n_explain"],
            "n_permutations": result["shap"]["n_permutations"],
            "max_abs_efficiency_gap": result["shap"]["max_abs_efficiency_gap"],
            "importance": result["shap"]["importance"],
            "permutation_importance": result["permutation_importance"],
        },
    )
    _dump(os.path.join(outdir, "acceptance.json"), acc)
    return {"dataset": ds_json, "result": result, "acceptance": acc, "figures": figs}


def _dump(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="extval", description="S/E grading external-validity calibration")
    ap.add_argument("--dsn", default=os.environ.get("TP_DSN", "postgresql:///tensorplatform?host=/var/run/postgresql&user=postgres"))
    ap.add_argument("--as-of", default=_dt.datetime.now(_dt.timezone.utc).isoformat())
    ap.add_argument("--horizon-hours", type=float, default=48.0)
    ap.add_argument("--n-folds", type=int, default=5)
    ap.add_argument("--n-permutations", type=int, default=40)
    ap.add_argument("--out", default="artifacts/IN56-N07")
    ap.add_argument("--test-path", default="tests/test_extval_calibration.py")
    ap.add_argument("--skip-pytest", action="store_true")
    args = ap.parse_args(argv)

    out = run_all(
        dsn=args.dsn,
        outdir=args.out,
        as_of=args.as_of,
        horizon_hours=args.horizon_hours,
        n_folds=args.n_folds,
        test_path=args.test_path,
        skip_pytest=args.skip_pytest,
        n_permutations=args.n_permutations,
    )
    acc = out["acceptance"]
    print(json.dumps({k: v for k, v in acc.items() if k not in ("criteria", "diagnostics")}, indent=2))
    for c in acc["criteria"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['criterion']}: {c['value']}")
    print("figures:", out["figures"])
    return 0 if acc["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())