"""判据度量学流水线：采集 → 回放重判 → 负控 → 不变量 → 能力三元 → 检索凭证 → 出图。

一条命令产出全部可发表工件：

    python3 -m tools.criteria_metrology --workdir . --out artifacts/criteria_metrology

产物：
* ``corpus.json``        判据语料快照（含逐行溯源与指纹）
* ``replay.json``        回放重判结果（退化清单 + 替代判据数值 + 翻转标记）
* ``controls.json``      负控（12 退化 + 12 良性）命中/误报
* ``invariants.json``    不变量属性测试（≥1000 随机样本）
* ``triad.json``         能力三元台账校验
* ``retrieval.json``     检索凭证重放门 + 负空间图谱
* ``agreement.json``     退化分类 vs 人工标注（一致率 + κ）
* ``cases.json``         四个历史案例的替代判据数值与方向一致性
* ``summary.json``       全部机器判据数字（报告 JSON 的机器可读面）
* ``fig_*.png``          图

纪律：本模块只读被审计文件，只写 ``--out`` 目录与 ``--data-out``（快照）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from .adaptive import evaluate_family, family_table
from .degeneracy import DegeneracyClass, DegeneracyPolicy, classify_degeneracy, degeneracy_report
from .harvest import (
    PlatformClient,
    finalize_record,
    harvest_docs,
    harvest_reports,
    load_snapshot,
    merge_snapshots,
    record_is_usable,
    sample_mission_ids,
    snapshot_digest,
)
from .model import Criterion, Metric, Verdict, normalize_criterion, relative_error
from .rejudge import agreement_stats, rejudge_corpus, rejudge_one, replay_summary
from .retrieval import (
    FileTermIndex,
    SnapshotIndex,
    build_receipt,
    claim_gate,
    negative_space_map,
    receipts_from_records,
)
from .triad import ledger_from_json, triad_validate

ROOT = Path(".")


# --------------------------------------------------------------------------- #
# 0. 配置装载
# --------------------------------------------------------------------------- #


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def policy_from_file(path: Path | None) -> DegeneracyPolicy:
    if path and Path(path).exists():
        return DegeneracyPolicy.from_dict(load_json(Path(path)))
    return DegeneracyPolicy()


# --------------------------------------------------------------------------- #
# 1. 语料
# --------------------------------------------------------------------------- #


def build_corpus(
    root: Path,
    *,
    doc_globs: Sequence[str] = ("docs/**/*.md",),
    data_out: Path | None = None,
    use_reports: bool = False,
    n_missions: int = 0,
    token: str | None = None,
    api_base: str | None = None,
    seed: int = 7810,
) -> dict[str, Any]:
    files: list[Path] = []
    for g in doc_globs:
        files.extend(sorted(root.glob(g)))
    docs = harvest_docs(files, root=root, require_tolerance_role=False)
    snaps = [docs]
    if use_reports and n_missions > 0:
        base = api_base or os.environ.get("TENSOR_API_BASE")
        tk = token or os.environ.get("TENSOR_API_TOKEN") or ""
        if base and tk:
            client = PlatformClient(base.rstrip("/"), tk)
            ids: list[int] = []
            for off in range(0, max(100, n_missions * 10), 100):
                ids.extend(client.mission_ids(limit=100, offset=off))
            sample = sample_mission_ids(ids, n_missions, seed=seed)
            snaps.append(harvest_reports(client, mission_ids=sample))
    snap = merge_snapshots(*snaps)
    # 自洽门：报告误差必须能被 (y, y_ref) 复现；不自洽的记录说明抽取把列搞错了，剔除。
    kept, dropped = [], []
    for r in snap["records"]:
        tier = (r.get("source") or {}).get("faithfulness")
        (dropped if tier == "inconsistent" else kept).append(r)
    snap["records"] = kept
    snap["n_records"] = len(kept)
    snap["n_dropped_inconsistent"] = len(dropped)
    snap["tiers"] = {
        "faithful": sum(1 for r in kept if (r.get("source") or {}).get("faithfulness") == "faithful"),
        "derived": sum(1 for r in kept if (r.get("source") or {}).get("faithfulness") == "derived"),
        "inconsistent_dropped": len(dropped),
    }
    snap["generated_on"] = str(__import__("datetime").date.today())
    snap["digest"] = snapshot_digest(snap)
    snap["policy"] = DegeneracyPolicy().as_dict()
    if data_out is not None:
        data_out.parent.mkdir(parents=True, exist_ok=True)
        data_out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


# --------------------------------------------------------------------------- #
# 2. 回放重判
# --------------------------------------------------------------------------- #


def run_replay(snapshot: dict[str, Any], policy: DegeneracyPolicy) -> dict[str, Any]:
    report = rejudge_corpus(snapshot["records"], policy)
    summary = replay_summary(report)
    summary["corpus_digest"] = snapshot.get("digest")
    summary["n_corpus_records"] = snapshot.get("n_records")
    summary["sources"] = snapshot.get("sources")
    return {"summary": summary, "rows": report["rows"]}


# --------------------------------------------------------------------------- #
# 3. 负控 + 不变量
# --------------------------------------------------------------------------- #


def load_controls(path: Path) -> dict[str, Any]:
    return load_json(path)


def run_controls(controls: dict[str, Any], policy: DegeneracyPolicy) -> dict[str, Any]:
    degenerate = controls.get("degenerate", [])
    benign = controls.get("benign", [])
    hits, misses = [], []
    for item in degenerate:
        c = normalize_criterion(Criterion.from_dict(item["criterion"]))
        f = classify_degeneracy(c, policy)
        expect = item.get("expected_class")
        ok = f.degeneracy.value == expect
        row = {
            "control_id": item["control_id"],
            "expected_class": expect,
            "got_class": f.degeneracy.value,
            "hit": ok,
            "reasons": list(f.reasons),
        }
        (hits if ok else misses).append(row)
    false_pos, true_neg = [], []
    for item in benign:
        c = normalize_criterion(Criterion.from_dict(item["criterion"]))
        f = classify_degeneracy(c, policy)
        ok = f.degeneracy is DegeneracyClass.NONE
        row = {
            "control_id": item["control_id"],
            "got_class": f.degeneracy.value,
            "false_positive": not ok,
            "reasons": list(f.reasons),
        }
        (false_pos if not ok else true_neg).append(row)
    return {
        "n_degenerate": len(degenerate),
        "n_degenerate_hit": len(hits),
        "degenerate_hit_rate": (len(hits) / len(degenerate)) if degenerate else None,
        "n_benign": len(benign),
        "n_benign_clean": len(true_neg),
        "benign_false_positive_rate": (len(false_pos) / len(benign)) if benign else None,
        "degenerate_rows": hits + misses,
        "benign_rows": true_neg + false_pos,
        "misses": misses,
        "false_positives": false_pos,
    }


def _make_criterion(**kw) -> Criterion:
    cid = kw.pop("criterion_id")
    return normalize_criterion(Criterion(criterion_id=cid, **kw))


def run_invariants(n_samples: int = 1200, seed: int = 20261111) -> dict[str, Any]:
    """属性测试：``y_ref → 0`` 时判据不得返回无界相对误差。

    样本构造刻意覆盖危险区：``y_ref`` 取 {0, ±1e-300, ±1e-12, ±1e-6, ±1, ±1e6} 与随机值，
    ``y`` 随机并在其中混入"与 y_ref 同量级/差 1e12 倍/异号"三类。判定：
    1. 朴素相对误差要么 ``None``，要么有限（绝不 inf/nan）
    2. 自适应族主判据数值要么 ``None``，要么有限
    3. 判据在 ``y_ref == 0`` 时不得给出"PASS"式放行而不登记退化
    """
    rnd = random.Random(seed)
    magnitudes = [0.0, 1e-300, 1e-12, 1e-6, 1e-3, 1.0, 1e3, 1e6]
    bad: list[dict[str, Any]] = []
    n_naive_none = 0
    n_degenerate = 0
    values: list[float] = []
    for i in range(n_samples):
        yr_scale = magnitudes[i % len(magnitudes)]
        y_ref = yr_scale * rnd.choice([1.0, -1.0, 0.5, 7.3])
        mode = i % 3
        if mode == 0:
            y = y_ref * rnd.uniform(-3.0, 3.0)
        elif mode == 1:
            y = y_ref * 1e12 + rnd.uniform(-1e-9, 1e-9)
        else:
            y = rnd.uniform(-1e6, 1e6)
        tau = rnd.choice([0.03, 0.05, 0.10, 0.15, 1e-10])
        c = normalize_criterion(
            Criterion(
                criterion_id=f"inv_{i}",
                y=y,
                y_ref=y_ref,
                tau=tau,
                metric=Metric.RELATIVE,
                scale=(y_ref if False else None),
            )
        )
        e = relative_error(c.y, c.y_ref)
        if e is None:
            n_naive_none += 1
        elif not math.isfinite(e):
            bad.append({"i": i, "kind": "naive_unbounded", "y": y, "y_ref": y_ref, "e": e})
        res = evaluate_family(c)
        if res.degeneracy is not DegeneracyClass.NONE:
            n_degenerate += 1
        if res.value is not None and not math.isfinite(res.value):
            bad.append({"i": i, "kind": "adaptive_unbounded", "y": y, "y_ref": y_ref, "v": res.value})
        if res.verdict is Verdict.PASS and c.y_ref == 0.0 and res.degeneracy is DegeneracyClass.NONE:
            bad.append({"i": i, "kind": "silent_pass_on_zero_reference", "y": y})
        if res.value is not None:
            values.append(abs(res.value))
    # 边界：y_ref 恰好取 0 / 极小 / 极大，逐档给表
    ladder = []
    for yr in (0.0, 1e-300, 1e-12, 1e-6, 1e-3, 1.0, 1e6):
        c = normalize_criterion(Criterion(criterion_id=f"ladder_{yr}", y=1.0, y_ref=yr, tau=0.03))
        res = evaluate_family(c)
        ladder.append(
            {
                "y_ref": yr,
                "naive_relative": relative_error(1.0, yr),
                "naive_verdict": build_naive_verdict(1.0, yr, 0.03),
                "adaptive_member": res.family_member,
                "adaptive_value": res.value,
                "adaptive_bound": res.bound,
                "adaptive_verdict": res.verdict.value,
                "degeneracy": res.degeneracy.value,
            }
        )
    return {
        "n_samples": n_samples,
        "seed": seed,
        "n_violations": len(bad),
        "violations": bad[:50],
        "n_naive_none": n_naive_none,
        "n_degenerate": n_degenerate,
        "max_adaptive_value": max(values) if values else None,
        "ladder": ladder,
        "pass": not bad,
    }


def build_naive_verdict(y: float, y_ref: float, tau: float) -> str | None:
    e = relative_error(y, y_ref)
    if e is None:
        return None
    return Verdict.PASS.value if e <= tau else Verdict.FAIL.value


# --------------------------------------------------------------------------- #
# 4. 四案例
# --------------------------------------------------------------------------- #


def run_cases(path: Path, policy: DegeneracyPolicy) -> dict[str, Any]:
    data = load_json(path)
    rows = []
    agree = 0
    for case in data["cases"]:
        c = normalize_criterion(Criterion.from_dict(case["criterion"]))
        r = rejudge_one(c, policy)
        expected = case.get("expected_adaptive_verdict")
        direction_ok = expected is None or r["adaptive_verdict"] == expected
        agree += int(direction_ok)
        rows.append(
            {
                "case_id": case["case_id"],
                "mission_id": case.get("mission_id"),
                "report_id": case.get("report_id"),
                "statement": case.get("statement"),
                "provenance": case.get("provenance"),
                "declared_verdict": r["declared_verdict"],
                "degeneracy": r["degeneracy"],
                "degeneracy_reasons": r["degeneracy_reasons"],
                "naive_relative": r["naive_relative"],
                "naive_verdict": r["naive_verdict"],
                "adaptive_member": r["adaptive_member"],
                "adaptive_value": r["adaptive_value"],
                "adaptive_bound": r["adaptive_bound"],
                "adaptive_verdict": r["adaptive_verdict"],
                "expected_adaptive_verdict": expected,
                "direction_ok": direction_ok,
                "recompute": r["recompute"],
            }
        )
    return {
        "n_cases": len(rows),
        "n_direction_ok": agree,
        "direction_consistency": (agree / len(rows)) if rows else None,
        "cases": rows,
    }


# --------------------------------------------------------------------------- #
# 5. 检索凭证 + 负空间
# --------------------------------------------------------------------------- #


def run_retrieval(root: Path, receipts_path: Path, claims_path: Path, regions_path: Path) -> dict[str, Any]:
    receipts = receipts_from_records(load_json(receipts_path)["receipts"])
    claims = load_json(claims_path)["claims"]
    regions = load_json(regions_path)["regions"]
    snapshot_dir = receipts_path.parent
    node_snap = load_json(snapshot_dir / "node_pool_snapshot.json") if (snapshot_dir / "node_pool_snapshot.json").exists() else {"entries": {}}
    prop_snap = load_json(snapshot_dir / "proposal_pool_snapshot.json") if (snapshot_dir / "proposal_pool_snapshot.json").exists() else {"entries": {}}
    resolvers = {
        "code": FileTermIndex(root),
        "pool": SnapshotIndex(prop_snap, kind="pool"),
        "node": SnapshotIndex(node_snap, kind="node"),
    }
    gates = []
    for claim in claims:
        rs = [r for r in receipts if r.claim_id == claim["claim_id"]]
        gates.append(claim_gate(claim, rs, resolvers))
    replay_rows = [dict(r) for g in gates for r in g["results"]]
    n_supported = sum(1 for g in gates if g["verdict"] == "SUPPORTED")
    neg = negative_space_map(regions, receipts, resolvers=resolvers)
    expected = {c["claim_id"]: c.get("expected_verdict") for c in claims}
    for g in gates:
        g["expected_verdict"] = expected.get(g["claim_id"])
        g["expected_match"] = (g["expected_verdict"] is None) or (g["verdict"] == g["expected_verdict"])
    return {
        "n_receipts": len(receipts),
        "n_claims": len(gates),
        "n_claims_supported": n_supported,
        "n_claims_expected_match": sum(1 for g in gates if g.get("expected_match")),
        "claims": gates,
        "receipt_replay": replay_rows,
        "n_replay_match": sum(1 for r in replay_rows if r["status"] == "MATCH"),
        "n_replay_drift": sum(1 for r in replay_rows if r["status"] == "DRIFT"),
        "n_replay_unreplayable": sum(1 for r in replay_rows if r["status"] == "UNREPLAYABLE"),
        "negative_space": neg,
    }


# --------------------------------------------------------------------------- #
# 6. 能力三元台账
# --------------------------------------------------------------------------- #


def run_triad(root: Path, ledger_path: Path) -> dict[str, Any]:
    entries = ledger_from_json(ledger_path)
    return triad_validate(entries, root=root)


# --------------------------------------------------------------------------- #
# 7. 一致率
# --------------------------------------------------------------------------- #


def run_agreement(labels_path: Path, policy: DegeneracyPolicy) -> dict[str, Any]:
    data = load_json(labels_path)
    pred, truth, rows = [], [], []
    for item in data["labels"]:
        c = normalize_criterion(Criterion.from_dict(item["criterion"]))
        f = classify_degeneracy(c, policy)
        pred.append(f.degeneracy.value)
        truth.append(item["human_label"])
        rows.append(
            {
                "label_id": item["label_id"],
                "criterion_id": c.criterion_id,
                "predicted": f.degeneracy.value,
                "human_label": item["human_label"],
                "agree": f.degeneracy.value == item["human_label"],
                "rationale": item.get("rationale"),
                "source": c.source,
            }
        )
    stats = agreement_stats(pred, truth)
    stats["n_disagree"] = sum(1 for r in rows if not r["agree"])
    stats["rows"] = rows
    stats["label_provenance"] = data.get("provenance")
    return stats


# --------------------------------------------------------------------------- #
# 8. 主流水线
# --------------------------------------------------------------------------- #


def run_all(
    *,
    root: Path,
    out: Path,
    data_dir: Path,
    policy_path: Path | None = None,
    corpus_path: Path | None = None,
    make_figures: bool = True,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    policy = policy_from_file(policy_path)
    snap = load_snapshot(corpus_path) if corpus_path else build_corpus(root, data_out=None)
    replay = run_replay(snap, policy)
    controls = run_controls(load_controls(data_dir / "controls.json"), policy)
    invariants = run_invariants()
    cases = run_cases(data_dir / "cases_m7241_m7248.json", policy)
    triad = run_triad(root, data_dir / "capability_triad.json")
    retrieval = run_retrieval(
        root,
        data_dir / "retrieval_receipts.json",
        data_dir / "retrieval_claims.json",
        data_dir / "retrieval_regions.json",
    )
    agreement = run_agreement(data_dir / "labels_40.json", policy)

    summary = {
        "corpus": {
            "n_records": snap.get("n_records"),
            "digest": snap.get("digest"),
            "generated_on": snap.get("generated_on"),
            "sources": snap.get("sources"),
        },
        "replay": {k: v for k, v in replay["summary"].items() if k not in ("degenerate_rows", "flipped_rows", "family")},
        "controls": {k: v for k, v in controls.items() if not k.endswith("_rows") and k not in ("misses", "false_positives")},
        "invariants": {k: v for k, v in invariants.items() if k not in ("violations", "ladder")},
        "cases": {k: v for k, v in cases.items() if k != "cases"},
        "agreement": {k: v for k, v in agreement.items() if k not in ("rows", "confusion")},
        "retrieval": {
            "n_receipts": retrieval["n_receipts"],
            "n_claims": retrieval["n_claims"],
            "n_claims_supported": retrieval["n_claims_supported"],
            "n_claims_expected_match": retrieval["n_claims_expected_match"],
            "n_replay_match": retrieval["n_replay_match"],
            "n_replay_drift": retrieval["n_replay_drift"],
            "n_replay_unreplayable": retrieval["n_replay_unreplayable"],
            "negative_space_probe_ratio": retrieval["negative_space"]["probe_ratio"],
            "negative_space_coverage_ratio": retrieval["negative_space"]["coverage_ratio"],
        },
        "triad": {
            "n_entries": triad["n_entries"],
            "grade_counts": triad["grade_counts"],
            "n_violations": triad["n_violations"],
            "not_supported_registered": triad["not_supported_registered"],
            "layering_completeness": triad["layering_completeness"],
        },
    }
    # 机器判据（验收线）逐条判定
    checks = {
        "degeneracy_agreement_ge_0_90": (agreement.get("agreement") or 0.0) >= 0.90,
        "replay_n_records_ge_200": (replay["summary"].get("n_records") or 0) >= 200,
        "replay_degenerate_rate_ge_0_05": (replay["summary"].get("degenerate_rate") or 0.0) >= 0.05,
        "replay_rows_have_alternative_values": all(
            r["adaptive_member"] for r in replay["summary"].get("degenerate_rows", [])
        ),
        "cases_direction_4_of_4": (cases.get("n_direction_ok") or 0) == 4,
        "m7241_fail_under_adaptive": next(
            (c["adaptive_verdict"] == "FAIL" for c in cases["cases"] if c["case_id"] == "m7241"), None
        ),
        "controls_degenerate_12_of_12": (controls.get("n_degenerate_hit") or 0) == 12,
        "controls_benign_fp_0": (controls.get("n_benign_clean") or 0) == (controls.get("n_benign") or 0) == 12,
        "invariants_no_unbounded": invariants["pass"] and invariants["n_samples"] >= 1000,
        "triad_zero_violations": triad["n_violations"] == 0,
        "retrieval_claims_match_expected": retrieval["n_claims_expected_match"] == retrieval["n_claims"],
    }
    summary["checks"] = checks
    summary["all_checks_pass"] = all(v is True for v in checks.values())

    (out / "corpus.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "replay.json").write_text(json.dumps(replay, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "invariants.json").write_text(json.dumps(invariants, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "agreement.json").write_text(json.dumps(agreement, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "retrieval.json").write_text(json.dumps(retrieval, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "triad.json").write_text(json.dumps(triad, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if make_figures:
        from . import figures

        figures.make_all(out, summary=summary, replay=replay, agreement=agreement, invariants=invariants, controls=controls)
    return summary