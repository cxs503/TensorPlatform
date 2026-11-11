"""集成层：蒸馏种子 → 派单候选；泄漏审计；判据汇总。

三件事
------
1. :func:`seed_candidates` —— 把 ``tools.knowledge_distill`` 的 ``seeds.json`` + ``run.json``
   映到与历史任务**同一设计矩阵**（特征名逐字相同），并给每个候选算 P(pass)、预测成本、EVOI
   与排序名次。种子的 repo 归属由**来源节点的任务记录**多数票决定（数据驱动，零硬编码）。
   溯源链保留：来源 node_key / mission_id / 报告路径 / 报告 sha256 / 原文引用 / 引用 sha256。
2. :func:`audit_leakage` —— 事后字段扰动替换审计（双侧）：
   (i) 特征矩阵不变性（证明特征不读 ``result.*`` / ``finished_at``）；
   (ii) 指标稳定性（|Δprecision@20%| ≤ ``leakage_tol``）；
   (iii) 负对照灵敏度（把结局本身当特征注入 → 指标必须跳 ≥ ``leakage_sensitivity``，
        否则审计无鉴别力，判负）。
3. :func:`assemble_gates` —— 把 (a)~(g) 的判据数字合成 dict，**全部数字来自实算**，
   缺证据的判据记 ``ok=False`` 并写明 ``reason``（不许静默通过）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .log import Record, build_records, summarise
from .policy import (
    FEATURES,
    evoi,
    fit_cost_model,
    fit_logistic,
    matrix,
    predict,
    walk_forward,
)

__all__ = [
    "seed_candidates",
    "rank_seeds",
    "audit_leakage",
    "assemble_gates",
    "load_distill_bundle",
    "corpus_summary",
    "sha256_text",
]

#: 事后字段中**必须与排序无关**的那一批（替换成扰动后特征矩阵与指标都不许动）
_POSTHOC_MUTABLE = (
    "headline",
    "decision_note",
    "fail_stage",
    "checks",
    "evidence",
    "changed_files",
    "compactions",
)

#: 成本通道：``tool_calls`` 天然是事后量，只作为成本模型的**拟合标签**，不进特征。
#: 它被单独审计（``cost_channel_delta``），不与"必须无关"的那批混为一谈。
_COST_FIELD = "tool_calls"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _majority(values: Sequence[str], default: str = "") -> str:
    if not values:
        return default
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def load_distill_bundle(distill_dir: str | Path) -> dict[str, Any]:
    """读蒸馏产物（seeds.json / run.json）；缺文件直接报错。"""
    base = Path(distill_dir)
    seeds_path = base / "seeds.json"
    if not seeds_path.is_file():
        raise FileNotFoundError(f"missing {seeds_path}")
    bundle: dict[str, Any] = {
        "dir": str(base),
        "seeds_sha256": hashlib.sha256(seeds_path.read_bytes()).hexdigest(),
        "seeds": json.loads(seeds_path.read_text(encoding="utf-8")),
    }
    run_path = base / "run.json"
    bundle["run"] = json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
    bundle["run_sha256"] = (
        hashlib.sha256(run_path.read_bytes()).hexdigest() if run_path.is_file() else None
    )
    return bundle


def _accepted_seeds(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    seeds = bundle["seeds"]
    if isinstance(seeds, Mapping):
        if isinstance(seeds.get("accepted"), list):
            return list(seeds["accepted"])
        return [s for s in seeds.get("seeds") or [] if (s.get("gate") or {}).get("passed")]
    return [s for s in seeds if (s.get("gate") or {}).get("passed")]


def seed_candidates(
    bundle: Mapping[str, Any],
    records: Sequence[Record],
    *,
    by_mission: Mapping[int, Record] | None = None,
) -> list[dict[str, Any]]:
    """种子 → 候选行（特征名与历史任务完全一致）。"""
    index = by_mission or {r.mission_id: r for r in records}
    repo_hist: dict[str, list[int]] = {}
    repo_budget: dict[str, list[float]] = {}
    for rec in records:
        repo_hist.setdefault(rec.repo, [])
        if rec.label is not None:
            repo_hist[rec.repo].append(rec.label)
        repo_budget.setdefault(rec.repo, []).append(rec.features["f_log_budget"])
    global_rate = _pass_rate([x for v in repo_hist.values() for x in v])
    out: list[dict[str, Any]] = []
    for seed in _accepted_seeds(bundle):
        sources = seed.get("sources") or []
        repos = [index[int(s["mission_id"])].repo for s in sources
                 if s.get("mission_id") is not None and int(s["mission_id"]) in index]
        repo = _majority(repos, default=_majority(list(repo_hist), default=""))
        labels = repo_hist.get(repo) or []
        recent = labels[-40:]
        gate = seed.get("gate") or {}
        novelty = float(gate.get("novelty") or 0.0)
        budgets = repo_budget.get(repo) or [0.0]
        recent_all = [r.label for r in records[-100:] if r.label is not None]
        features = {
            "f_repo_pass": round(_pass_rate(labels, global_rate), 6),
            "f_repo_recent_fail": round(1.0 - _pass_rate(recent, global_rate), 6),
            "f_global_recent_fail": round(1.0 - _pass_rate(recent_all, global_rate), 6),
            "f_log_len": round(math.log1p(len(str(seed.get("title") or ""))
                                          + len(str(seed.get("gap_description") or ""))), 6),
            "f_log_budget": round(float(np.median(budgets)), 6),
            "f_novelty": round(float(novelty), 6),
            "f_is_child": 0.0,
            "f_co_repos": 0.0,
        }
        text = f"{seed.get('title', '')} :: {seed.get('gap_description', '')}"
        out.append(
            {
                "seed_id": seed.get("seed_id"),
                "cluster_id": seed.get("cluster_id"),
                "title": seed.get("title"),
                "repo": repo,
                "breadth": int(seed.get("size") or len(sources)),
                "kind_counts": seed.get("kind_counts") or {},
                "features": features,
                "provenance": {
                    "sources": [
                        {
                            "node_key": s.get("node_key"),
                            "mission_id": s.get("mission_id"),
                            "kind": s.get("kind"),
                            "source_path": s.get("source_path"),
                            "source_sha256": s.get("source_sha256"),
                            "quote": s.get("quote"),
                            "quote_sha256": s.get("quote_sha256"),
                            "pointer": s.get("pointer"),
                        }
                        for s in sources
                    ],
                    "seed_text_sha256": sha256_text(text),
                    "gate": {
                        "novelty": gate.get("novelty"),
                        "max_sim": gate.get("max_sim"),
                        "mean_sim": gate.get("mean_sim"),
                        "match_ref_id": gate.get("match_ref_id"),
                        "match_sha256": gate.get("match_sha256"),
                    },
                    "distill_artifacts": {
                        "dir": bundle.get("dir"),
                        "seeds_sha256": bundle.get("seeds_sha256"),
                        "run_sha256": bundle.get("run_sha256"),
                    },
                },
            }
        )
    return out


def _pass_rate(labels: Sequence[int], default: float = 0.5) -> float:
    return (sum(labels) / len(labels)) if labels else default


def rank_seeds(
    candidates: Sequence[Mapping[str, Any]],
    records: Sequence[Record],
    *,
    ridge: float = 1.0,
    epochs: int = 400,
    lr: float = 0.35,
    gain_floor: float = 0.05,
) -> dict[str, Any]:
    """用全历史训练模型给种子打 EVOI 分并排序（含成本预测与溯源链）。"""
    train = [r for r in records if r.label is not None]
    if not train:
        raise ValueError("no labelled history to fit seed ranker")
    X_train = matrix(train)
    y_train = np.asarray([int(r.label) for r in train], dtype=float)
    model = fit_logistic(X_train, y_train, ridge=ridge, epochs=epochs, lr=lr)
    cost_model = fit_cost_model(train)
    base = {"model": model.to_dict(), "cost_model": cost_model.to_dict(),
            "train_n": int(X_train.shape[0])}
    if not candidates:
        return {**base, "ranked": []}
    X = matrix(candidates)
    p = predict(model, X)
    ib = FEATURES.index("f_log_budget")
    il = FEATURES.index("f_log_len")
    ino = FEATURES.index("f_novelty")
    cost = cost_model.predict(X[:, [ib, il]])
    gain = np.maximum(X[:, ino], gain_floor)
    scores = evoi(p, gain, cost)
    order = sorted(range(len(candidates)),
                   key=lambda i: (-float(scores[i]), str(candidates[i]["seed_id"])))
    ranked: list[dict[str, Any]] = []
    for rank, i in enumerate(order, start=1):
        cand = dict(candidates[i])
        cand.update(
            {
                "rank": rank,
                "p_pass": round(float(p[i]), 6),
                "predicted_cost": round(float(cost[i]), 3),
                "gain": round(float(gain[i]), 6),
                "evoi": round(float(scores[i]), 8),
                "evoi_per_breadth": round(float(scores[i]) / max(int(cand["breadth"]), 1), 8),
            }
        )
        ranked.append(cand)
    return {
        **base,
        "ranked": ranked,
        "score_sha256": sha256_text("\n".join(f"{r['seed_id']}\t{r['evoi']}" for r in ranked)),
    }


# --- 泄漏审计 ---------------------------------------------------------------

def _perturb_raw(
    raw: Sequence[Mapping[str, Any]],
    *,
    seed: int = 12345,
    perturb_fields: Sequence[str] = _POSTHOC_MUTABLE,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for rec in raw:
        clone = copy.deepcopy(dict(rec))
        result = clone.get("result")
        if isinstance(result, Mapping):
            result = dict(result)
            for name in perturb_fields:
                if name in result:
                    result[name] = rng.choice(["perturb", 0, 1, None, "MOCK", 999])
            clone["result"] = result
        if "finished_at" in clone and rng.random() < 0.5:
            clone["finished_at"] = None
        out.append(clone)
    return out


def _feature_hash(records: Sequence[Record]) -> str:
    payload = "\n".join(
        f"{r.mission_id}\t" + ",".join(f"{k}={r.features[k]}" for k in FEATURES)
        for r in records
    )
    return sha256_text(payload)


def _metrics(records: Sequence[Record], params: Mapping[str, Any]) -> dict[str, Any]:
    wf = walk_forward(
        records,
        windows=int(params.get("windows", 5)),
        top_frac=float(params.get("top_frac", 0.2)),
        min_train=int(params.get("min_train", 300)),
        ridge=float(params.get("ridge", 1.0)),
        epochs=int(params.get("epochs", 400)),
        lr=float(params.get("lr", 0.35)),
        random_repeats=int(params.get("random_repeats", 25)),
        gain_floor=float(params.get("gain_floor", 0.05)),
        seed=int(params.get("seed", 7)),
    )
    return {
        "precision_evoi": wf["precision_weighted"]["evoi"],
        "precision_fifo": wf["precision_weighted"]["fifo"],
        "auc": wf["pooled_auc"],
        "gain_pp": wf["gain_vs_fifo_pp"],
    }


def _leaky_records(records: Sequence[Record]) -> list[Record]:
    """负对照：把结局本身当特征注入（真实泄漏）。"""
    out: list[Record] = []
    for rec in records:
        features = dict(rec.features)
        features["f_novelty"] = float(rec.label or 0)
        out.append(
            Record(
                mission_id=rec.mission_id,
                repo=rec.repo,
                created_at=rec.created_at,
                ts=rec.ts,
                features=features,
                label=rec.label,
                cost_tool_calls=rec.cost_tool_calls,
                wall_sec=rec.wall_sec,
                status=rec.status,
                posthoc_sha256=rec.posthoc_sha256,
            )
        )
    return out


def audit_leakage(
    raw: Sequence[Mapping[str, Any]],
    records: Sequence[Record],
    *,
    params: Mapping[str, Any],
    build_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """事后字段扰动替换审计（双侧）+ 负对照灵敏度。"""
    kwargs = dict(build_kwargs or {})
    perturbed_records = build_records(_perturb_raw(raw), **kwargs)
    base_hash = _feature_hash(records)
    perturbed_hash = _feature_hash(perturbed_records)
    base = _metrics(records, params)
    perturbed = _metrics(perturbed_records, params)
    delta = {k: round(abs(base[k] - perturbed[k]), 6) for k in ("precision_evoi", "auc", "gain_pp")}
    tol = float(params.get("leakage_tol", 0.02))
    sens_floor = float(params.get("leakage_sensitivity", 0.03))
    leaky = _metrics(_leaky_records(records), params)
    jump = round(leaky["precision_evoi"] - base["precision_evoi"], 6)
    # 成本通道单独量化：tool_calls 换成扰动 → 成本模型失配，排序必动；该量如实报出供判读
    cost_perturbed = _metrics(
        build_records(_perturb_raw(raw, seed=999, perturb_fields=(_COST_FIELD,)), **kwargs), params
    )
    cost_delta = round(abs(base["precision_evoi"] - cost_perturbed["precision_evoi"]), 6)
    return {
        "features_identical": bool(base_hash == perturbed_hash),
        "feature_hash": base_hash,
        "metrics_clean": {k: round(float(v), 6) for k, v in base.items()},
        "metrics_perturbd": {k: round(float(v), 6) for k, v in perturbed.items()},
        "delta": delta,
        "tolerance": tol,
        "tolerance_ok": bool(max(delta.values()) <= tol),
        "injected_leaky_precision": round(float(leaky["precision_evoi"]), 6),
        "injection_jump": jump,
        "sensitivity_floor": sens_floor,
        "sensitivity_ok": bool(jump >= sens_floor),
        "cost_channel_delta": cost_delta,
        "cost_channel_note": "tool_calls 是事后成本标签（只进成本模型拟合，不进特征）；"
                             "替换为扰动后排序变化量如实报出，不参与 (e) 的通过判定",
    }


# --- 判据汇总 ---------------------------------------------------------------

def assemble_gates(
    *,
    distill_run: Mapping[str, Any],
    walk: Mapping[str, Any],
    leakage: Mapping[str, Any],
    pytest_check: Mapping[str, Any] | None,
    coverage: Mapping[str, Any] | None,
    domain_scan: Mapping[str, Any] | None,
    cluster_consistency: Mapping[str, Any] | None,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """把 (a)~(g) 的实算数字与通过与否汇成一个 dict。"""
    cov = distill_run.get("coverage") or {}
    seed_gate = distill_run.get("gate") or {}
    gate_a = {
        "value": round(float(cov.get("parse_coverage") or 0.0), 6),
        "threshold": float(params.get("min_parse_coverage", 0.9)),
        "reports": int(cov.get("reports_total") or 0),
        "consistent": int(cov.get("reports_consistent") or 0),
        "items": int(cov.get("items") or 0),
        "items_by_kind": cov.get("items_by_kind") or {},
        "formula": "consistent_reports / reports_total（蒸馏层解析覆盖率）",
    }
    gate_a["ok"] = bool(gate_a["value"] >= gate_a["threshold"])
    gate_b = dict(cluster_consistency or {"value": 0.0, "judged": 0, "ok": False,
                                          "reason": "no truth-labelled fixture run"})
    gate_c = {
        "value": round(float(seed_gate.get("mean_max_sim_of_passed") or 1.0), 6),
        "mean_sim_of_passed": round(float(seed_gate.get("mean_sim_of_passed") or 1.0), 6),
        "threshold": float(params.get("max_mean_sim", 0.4)),
        "novelty_floor": float(params.get("novelty_floor", 0.55)),
        "min_novelty_observed": round(float(seed_gate.get("novelty_min", 0.0) or 0.0), 6),
        "candidates": int(seed_gate.get("candidates") or 0),
        "passed": int(seed_gate.get("passed") or 0),
        "formula": "mean over 输出种子 of max_sim(种子, 既有提案池) —— 必须 < 0.40",
    }
    gate_c["ok"] = bool(gate_c["value"] < gate_c["threshold"])
    gate_d = {
        "value": round(float(walk.get("gain_vs_fifo_pp") or 0.0), 3),
        "threshold_pp": float(params.get("min_precision_gain_pp", 10.0)),
        "precision_evoi": walk.get("precision_weighted", {}).get("evoi"),
        "precision_fifo": walk.get("precision_weighted", {}).get("fifo"),
        "precision_cheapest": walk.get("precision_weighted", {}).get("cheapest"),
        "precision_random": walk.get("precision_weighted", {}).get("random"),
        "baseline_best": walk.get("baseline_best"),
        "gain_vs_best_baseline_pp": walk.get("gain_vs_best_baseline_pp"),
        "evaluated_records": walk.get("evaluated_records"),
        "pooled_auc": walk.get("pooled_auc"),
        "formula": "precision@20%(EVOI) - precision@20%(FIFO 基线)，单位 pp",
    }
    gate_d["ok"] = bool(gate_d["value"] >= gate_d["threshold_pp"])
    gate_e = dict(leakage or {})
    gate_e["ok"] = bool(
        gate_e.get("features_identical") and gate_e.get("tolerance_ok")
        and gate_e.get("sensitivity_ok")
    )
    gate_f = {
        "pytest": pytest_check or {"pass": False, "reason": "not run"},
        "coverage": coverage or {"value": 0.0, "ok": False, "reason": "not run"},
        "formula": "pytest 全绿 且 行覆盖率 ≥ 0.85",
    }
    gate_f["ok"] = bool((pytest_check or {}).get("pass") and (coverage or {}).get("ok"))
    gate_g = dict(domain_scan or {"ok": False, "reason": "domain scan not run"})
    gate_g["formula"] = "tools/audit/domain_leak_scan.py 扫描新增文件：命中 0 且文件全部实扫"
    gates: dict[str, Any] = {
        "a_parse_coverage": gate_a,
        "b_cluster_consistency": gate_b,
        "c_novelty_gate": gate_c,
        "d_ranking_utility": gate_d,
        "e_leakage_audit": gate_e,
        "f_tests_coverage": gate_f,
        "g_zero_domain_semantics": gate_g,
    }
    gates["all_pass"] = bool(all(g.get("ok") for g in gates.values() if isinstance(g, Mapping)))
    return gates


def corpus_summary(records: Sequence[Record]) -> dict[str, Any]:
    """日志画像（报告「案例介绍」节的数字来源）。"""
    s = summarise(records)
    s["feature_names"] = list(FEATURES)
    s["label_rule"] = "result.accepted 优先；否则 status∈{succeeded,failed}；其余删失"
    return s