"""提案→战役供给链成熟化的端到端管线（分阶段、可单独重跑、工件全落盘）。

阶段（``python3 -m tools.proposal_chain.pipeline --stage <name>``）：

============  ====================================================================
stage         产出
============  ====================================================================
``sources``    ``nodes_snapshot.json.gz`` + ``corpus_compact.json.gz`` + ``source_report.json``
``ledger``     ``gap_ledger_snapshot.json``（PANORAMA 缺口账本的结构化快照，带 sha256/行号）
``schema``     ``schema_validation.json``（957 节点全量 + 100 随机子样，漏报率机验）
``dedup``      ``dedup_proposals.json`` / ``dedup_nodes.json``（合并建议 + 相似对）
``coverage``   ``coverage_matrix.json`` / ``ledger_coverage.json``（覆盖矩阵 + 逐项比对）
``labels``     ``label_eval.json`` / ``threshold_sweep.json``（标注评估 + 阈值扫描）
``negative``   ``negative_control.json``（空池/坏数据不崩的负控记录）
``figures``    ``fig_*.png``（4 图）
``acceptance`` ``acceptance.json``（判据 a–f 逐条数字 + 工件路径）
============  ====================================================================

每个阶段只依赖前序工件（幂等、可复算），参数全部来自 ``config/proposal_chain.json``。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import node_schema, sources
from .config import ChainConfig, REPO_ROOT, load_config
from .coverage import coverage_matrix, ledger_coverage, topic_vocabulary
from .dedup import Doc, analyze
from .labels import (
    calibrate_review_threshold,
    candidate_hash,
    doc_text_for,
    evaluate_labels,
    read_label_file,
    score_matrix,
    split_labels,
)
from .ledger import parse_gap_ledger
from .semantic import SemanticSpace


# --------------------------------------------------------------------------- io
def artifacts_dir(cfg: ChainConfig) -> Path:
    d = cfg.path_of("artifacts_dir")
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(blob + "\n", encoding="utf-8")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- stages
def stage_sources(cfg: ChainConfig, *, live: bool = False) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    proposals, prep = sources.load_proposals(cfg)
    nodes_docs, node_rows, nrep = sources.load_nodes(cfg, allow_live=live)
    snapshot = cfg.path_of("nodes_snapshot")
    if node_rows:
        payload = {"schema_version": "proposal-chain-nodes/1.0", "items": node_rows}
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        gz_path = snapshot.with_suffix(".json.gz")
        with gzip.open(gz_path, "wb", compresslevel=9) as fh:
            fh.write(blob)
        if snapshot.exists():
            snapshot.unlink()  # 只保留 gz 版，避免 twin 工件
    corpus_sha = sources.write_compact_corpus(cfg.path_of("corpus_gz"), proposals, nodes_docs)
    report = {
        "proposals": prep.as_dict(),
        "nodes": nrep.as_dict(),
        "corpus_sha256": corpus_sha,
        "corpus_path": str(cfg.path_of("corpus_gz").relative_to(REPO_ROOT)),
        "campaigns": len({d.campaign for d in proposals}),
        "node_plans": len({d.campaign for d in nodes_docs}),
    }
    write_json(out / "source_report.json", report)
    return report


def load_nodes_for_schema(cfg: ChainConfig) -> list[dict[str, Any]]:
    gz_path = cfg.path_of("nodes_snapshot").with_suffix(".json.gz")
    if gz_path.exists():
        with gzip.open(gz_path, "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
    else:
        plain = cfg.path_of("nodes_snapshot")
        payload = read_json(plain, {"items": []})
    rows = payload.get("items") if isinstance(payload, Mapping) else payload
    return list(rows or [])


def stage_schema(cfg: ChainConfig) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    rows = load_nodes_for_schema(cfg)
    batch = node_schema.validate_batch(rows, cfg.raw)
    sample_n = int(cfg.get("node_schema.sample_size", 100))
    rng = random.Random(int(cfg.get("node_schema.sample_seed", 20261110)))
    sample = rng.sample(rows, min(sample_n, len(rows))) if rows else []
    sample_batch = node_schema.validate_batch(sample, cfg.raw)
    payload = {
        "full": batch.as_dict(include_records=False),
        "sample": sample_batch.as_dict(),
        "sample_size": len(sample),
        "required_fields": list(cfg.get("node_schema.required_fields", [])),
        "degrade_policy": cfg.get("node_schema.on_missing", "degrade"),
    }
    write_json(out / "schema_validation.json", payload)
    return payload


def stage_ledger(cfg: ChainConfig) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    src = Path(str(cfg.get("coverage.gap_ledger_source", "")))
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"source": str(src), "available": False}
    if src.exists():
        markdown = src.read_text(encoding="utf-8", errors="replace")
        parsed, meta2 = parse_gap_ledger(markdown, cfg.raw, source=str(src))
        items = [it.as_dict() for it in parsed]
        meta.update(meta2)
        meta["available"] = True
    payload = {
        "schema_version": "proposal-chain-gap-ledger/1.0",
        "meta": meta,
        "items": items,
    }
    write_json(out / "gap_ledger_snapshot.json", payload)
    return payload


def load_ledger_items(cfg: ChainConfig) -> list[Any]:
    ledger_path = REPO_ROOT / str(cfg.get("coverage.gap_ledger_path"))
    payload = read_json(ledger_path, {"items": []}) or {}
    from .ledger import LedgerItem

    out = []
    for row in payload.get("items", []):
        out.append(
            LedgerItem(
                item_id=row["item_id"],
                kind=row["kind"],
                section=row["section"],
                line_no=int(row["line_no"]),
                text=row["text"],
                declared_status=row["declared_status"],
                gap_ids=tuple(row.get("gap_ids", [])),
            )
        )
    return out


def _dedup_payload(res: Any, *, top_suggestions: int = 40) -> dict[str, Any]:
    payload = res.as_dict(emit_pairs=True)
    payload["suggestions"] = payload["suggestions"][:top_suggestions]
    payload["duplicate_pairs"] = payload["duplicate_pairs"][:200]
    payload["review_pairs"] = payload["review_pairs"][:200]
    return payload


def stage_dedup(cfg: ChainConfig) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    proposals, _ = sources.load_proposals(cfg)
    nodes_docs, _, _ = sources.load_nodes(cfg)
    t0 = time.time()
    res_p = analyze(proposals, cfg.raw)
    t1 = time.time()
    res_n = analyze(nodes_docs, cfg.raw)
    t2 = time.time()
    payload = {
        "proposals": _dedup_payload(res_p),
        "nodes": _dedup_payload(res_n),
        "timing_s": {"proposals": round(t1 - t0, 2), "nodes": round(t2 - t1, 2)},
    }
    write_json(out / "dedup_proposals.json", payload["proposals"])
    write_json(out / "dedup_nodes.json", payload["nodes"])
    write_json(
        out / "dedup_summary.json",
        {"proposals": res_p.stats, "nodes": res_n.stats, "timing_s": payload["timing_s"]},
    )
    return payload


def stage_coverage(cfg: ChainConfig) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    proposals, _ = sources.load_proposals(cfg)
    nodes_docs, _, _ = sources.load_nodes(cfg)
    matrix = coverage_matrix(proposals, nodes_docs, cfg.raw)
    items = load_ledger_items(cfg)
    lc = ledger_coverage(items, proposals, nodes_docs, cfg.raw)
    write_json(out / "coverage_matrix.json", matrix)
    write_json(out / "ledger_coverage.json", lc.as_dict())
    return {"matrix": matrix, "ledger": lc.as_dict()}


def stage_labels(cfg: ChainConfig) -> dict[str, Any]:
    out = artifacts_dir(cfg)
    proposals, _ = sources.load_proposals(cfg)
    labeled, meta = read_label_file(REPO_ROOT / str(cfg.get("labels.path")))
    tune, holdout = split_labels(labeled, holdout_mod=int(cfg.get("labels.holdout_mod", 2)))
    dup_threshold = float(cfg.get("dedup.dup_threshold", 0.85))
    grid = [float(x) for x in (cfg.get("labels.sweep_grid") or
                               [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90])]
    # 三次空间构建（每条表示一次），复用给扫描与评估
    study: dict[str, Any] = {"annotations": meta, "labeled_pairs": len(labeled),
                             "positives": sum(1 for r in labeled if r.get("label") == "overlap"),
                             "negatives": sum(1 for r in labeled if r.get("label") == "distinct"),
                             "label_sha256": candidate_hash(labeled)}
    sweeps: dict[str, Any] = {}
    evals: dict[str, Any] = {}
    for rep in ("claim", "title", "full"):
        sem, index = score_matrix(proposals, cfg.raw, representation=rep)
        sweep_rows = []
        for t in grid:
            tp = sum(1 for r in tune if r["label"] == "overlap" and sem.sim(index[r["a"]], index[r["b"]]) >= t)
            pos = sum(1 for r in tune if r["label"] == "overlap")
            fp = sum(1 for r in tune if r["label"] == "distinct" and sem.sim(index[r["a"]], index[r["b"]]) >= t)
            neg = sum(1 for r in tune if r["label"] == "distinct")
            sweep_rows.append({"threshold": t, "recall": round(tp / pos, 6) if pos else 0.0,
                               "fp_rate": round(fp / neg, 6) if neg else 0.0,
                               "caught": tp, "positives": pos,
                               "false_positives": fp, "negatives": neg})
        sweeps[rep] = sweep_rows
        cal = calibrate_review_threshold(
            sweep_rows,
            target_recall=float(cfg.get("labels.recall_target", 0.90)),
            max_fp_rate=float(cfg.get("labels.fp_rate_target", 0.10)),
        )
        rt = float(cal.get("threshold") or cfg.get("dedup.review_threshold", 0.55))
        for name, rows in (("tune", tune), ("holdout", holdout), ("all", labeled)):
            evals.setdefault(rep, {})[name] = evaluate_labels(
                proposals, rows, cfg.raw, representation=rep,
                dup_threshold=dup_threshold, review_threshold=rt, space=sem,
            ).as_dict()
            evals[rep][name]["calibration"] = cal
        evals[rep]["backend"] = sem.backend
        evals[rep]["dim"] = sem.dim
        evals[rep]["labeled_scores"] = [
            {
                "pair_id": r.get("pair_id"),
                "label": r.get("label"),
                "score": round(sem.sim(index[r["a"]], index[r["b"]]), 6),
                "rule": r.get("rule"),
            }
            for r in labeled
            if r.get("label") in ("overlap", "distinct")
            and r["a"] in index
            and r["b"] in index
        ]
    # 基线：裸 TF-IDF（无 LSA）在同一标注集上
    baseline_space, bidx = score_matrix(proposals, cfg.raw, representation="claim", use_lsa=False)
    baseline = {
        name: evaluate_labels(
            proposals, rows, cfg.raw, representation="claim", use_lsa=False,
            dup_threshold=dup_threshold,
            review_threshold=float(cfg.get("dedup.review_threshold", 0.55)),
            space=baseline_space,
        ).as_dict()
        for name, rows in (("tune", tune), ("holdout", holdout), ("all", labeled))
    }
    payload = {
        "study": study,
        "sweeps": sweeps,
        "evaluations": evals,
        "tfidf_baseline": baseline,
        "configured": {
            "representation": cfg.get("labels.representation", "claim"),
            "dup_threshold": dup_threshold,
            "review_threshold": float(cfg.get("dedup.review_threshold", 0.55)),
        },
    }
    write_json(out / "label_eval.json", payload)
    write_json(out / "threshold_sweep.json", sweeps)
    return payload


def stage_negative(cfg: ChainConfig) -> dict[str, Any]:
    """负控：空池 / 单文档 / 空文本 / 坏 metadata 全部恒等不崩。"""
    from . import node_schema as ns

    out = artifacts_dir(cfg)
    checks: dict[str, Any] = {}
    empty = analyze([], cfg.raw)
    checks["empty_pool"] = {
        "ok": empty.stats["doc_count"] == 0 and not empty.pairs and not empty.suggestions,
        "pairs": len(empty.pairs),
        "suggestions": len(empty.suggestions),
        "stats_doc_count": empty.stats["doc_count"],
    }
    single = analyze([Doc(id="a", text="single doc", campaign="c1")], cfg.raw)
    checks["single_doc"] = {"ok": not single.pairs, "pairs": len(single.pairs)}
    blanks = analyze(
        [Doc(id="a", text="", campaign="c1"), Doc(id="b", text="   ", campaign="c2")], cfg.raw
    )
    checks["blank_texts"] = {"ok": not blanks.pairs, "pairs": len(blanks.pairs)}
    bad = ns.validate_node({"id": 1, "metadata": "{not json"}, cfg.raw)
    checks["bad_metadata_degrade"] = {
        "ok": bad.action == "degrade" and len(bad.missing) == len(cfg.get("node_schema.required_fields", [])),
        "action": bad.action,
        "missing": list(bad.missing),
    }
    none_meta = ns.validate_node({"id": 2, "metadata": None}, cfg.raw)
    checks["none_metadata_degrade"] = {"ok": none_meta.action == "degrade", "action": none_meta.action}
    ok_node = ns.validate_node(
        {
            "id": 3,
            "metadata": {
                "ext_id": "x1",
                "repo": "TensorPlatform",
                "goal": "do thing",
                "acceptance_criteria": ["metric ≥ 3"],
            },
        },
        cfg.raw,
    )
    checks["complete_node_accept"] = {"ok": ok_node.action == "accept" and ok_node.ok, "action": ok_node.action}
    payload = {
        "checks": checks,
        "all_pass": all(bool(c["ok"]) for c in checks.values()),
    }
    write_json(out / "negative_control.json", payload)
    return payload


def stage_acceptance(cfg: ChainConfig) -> dict[str, Any]:
    """汇总判据 a–f（数字全部从工件读，零手抄）。"""
    out = artifacts_dir(cfg)
    rel = lambda p: str(Path(p).relative_to(REPO_ROOT)) if Path(p).is_relative_to(REPO_ROOT) else str(p)
    dedup_sum = read_json(out / "dedup_summary.json", {}) or {}
    labels_eval = read_json(out / "label_eval.json", {}) or {}
    schema = read_json(out / "schema_validation.json", {}) or {}
    cov = read_json(out / "coverage_matrix.json", {}) or {}
    lc = read_json(out / "ledger_coverage.json", {}) or {}
    neg = read_json(out / "negative_control.json", {}) or {}
    src = read_json(out / "source_report.json", {}) or {}
    ledger_snap = read_json(out / "gap_ledger_snapshot.json", {}) or {}

    rep = str(labels_eval.get("configured", {}).get("representation", "claim"))
    ev = labels_eval.get("evaluations", {}).get(rep, {})
    hold = ev.get("holdout", {})
    allset = ev.get("all", {})
    baseline = labels_eval.get("tfidf_baseline", {}).get("all", {})
    recall_target = float(cfg.get("labels.recall_target", 0.90))
    fp_target = float(cfg.get("labels.fp_rate_target", 0.10))

    crit: dict[str, Any] = {}
    crit["a_dedup_recall"] = {
        "target": f">= {recall_target}",
        "value_all": allset.get("catch_recall"),
        "value_holdout": hold.get("catch_recall"),
        "metric": "机器门档案召回（auto ≥0.85 ∪ 复核带 ≥review_threshold）/ 标注正对",
        "auto_tier_only_recall_all": allset.get("recall"),
        "tfidf_baseline_recall_all": baseline.get("recall"),
        "positives": allset.get("positives"),
        "pass": bool((allset.get("catch_recall") or 0) >= recall_target),
        "evidence": ["artifacts/proposal_chain/label_eval.json",
                     "artifacts/proposal_chain/labels/labeled_pairs.json"],
    }
    crit["b_dedup_fp"] = {
        "target": f"<= {fp_target}",
        "auto_fp_rate_all": allset.get("fp_rate"),
        "auto_precision_all": allset.get("precision"),
        "review_band_fp_rate_all": allset.get("review_fp_rate"),
        "holdout_auto_fp_rate": hold.get("fp_rate"),
        "negatives": allset.get("negatives"),
        "pass": bool(
            (allset.get("fp_rate") is not None)
            and (allset.get("fp_rate") <= fp_target)
            and ((allset.get("review_fp_rate") or 0) <= fp_target)
        ),
        "evidence": ["artifacts/proposal_chain/label_eval.json"],
    }
    sample = schema.get("sample", {})
    crit["c_schema_miss"] = {
        "target": "miss_rate == 0 over 100-node sample",
        "sample_size": schema.get("sample_size"),
        "missed_count": sample.get("missed_count"),
        "miss_rate": sample.get("miss_rate"),
        "degraded_ratio_full": schema.get("full", {}).get("degraded_ratio"),
        "missing_field_counts_full": schema.get("full", {}).get("missing_field_counts"),
        "pass": bool(sample.get("missed_count") == 0 and (schema.get("sample_size") or 0) >= 100),
        "evidence": ["artifacts/proposal_chain/schema_validation.json"],
    }
    stats = lc.get("stats", {})
    crit["d_coverage_ledger"] = {
        "target": "逐项比对输出：条目级一致性 + 差异清单",
        "ledger_items": stats.get("items"),
        "judged_items": stats.get("judged_items"),
        "agreement_rate": stats.get("agreement_rate"),
        "disagreements": stats.get("disagreement_items"),
        "state_counts": stats.get("state_counts"),
        "domain_matrix_rows": len(cov.get("domain_matrix", [])),
        "domains_covered": cov.get("totals", {}).get("domains_covered"),
        "topics": cov.get("totals", {}).get("topics"),
        "topics_with_node_hits": cov.get("totals", {}).get("topics_with_node_hits"),
        "pass": bool((stats.get("items") or 0) > 0 and len(lc.get("rows", [])) > 0),
        "evidence": ["artifacts/proposal_chain/ledger_coverage.json",
                     "artifacts/proposal_chain/coverage_matrix.json",
                     "artifacts/proposal_chain/gap_ledger_snapshot.json"],
    }
    crit["e_negative_control"] = {
        "target": "空提案池 → 去重器输出空集不崩",
        "checks": neg.get("checks"),
        "pass": bool(neg.get("all_pass")),
        "evidence": ["artifacts/proposal_chain/negative_control.json"],
    }
    tests = read_json(out / "pytest_result.json", {}) or {}
    crit["f_pytest"] = {
        "target": ">= 15 passed",
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "skipped": tests.get("skipped"),
        "pass": bool((tests.get("passed") or 0) >= 15 and (tests.get("failed") or 0) == 0),
        "evidence": [tests.get("command", "pytest tests/test_proposal_chain_*.py")],
    }
    payload = {
        "schema_version": "proposal-chain-acceptance/1.0",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config_sha256": hashlib.sha256(cfg.path.read_bytes()).hexdigest(),
        "sources": src,
        "gap_ledger_meta": ledger_snap.get("meta", {}),
        "criteria": crit,
        "all_pass": all(v.get("pass") for v in crit.values()),
        "artifacts": {
            "dedup_proposals": "artifacts/proposal_chain/dedup_proposals.json",
            "dedup_nodes": "artifacts/proposal_chain/dedup_nodes.json",
            "schema_validation": "artifacts/proposal_chain/schema_validation.json",
            "coverage_matrix": "artifacts/proposal_chain/coverage_matrix.json",
            "ledger_coverage": "artifacts/proposal_chain/ledger_coverage.json",
            "label_eval": "artifacts/proposal_chain/label_eval.json",
            "negative_control": "artifacts/proposal_chain/negative_control.json",
        },
        "dedup_stats": dedup_sum,
    }
    write_json(out / "acceptance.json", payload)
    return payload


def stage_figures(cfg: ChainConfig) -> dict[str, Any]:
    from . import figures  # 局部导入：matplotlib 只在出图时需要

    paths = figures.render_all(cfg)
    write_json(artifacts_dir(cfg) / "figures.json", paths)
    return paths


def stage_tests(cfg: ChainConfig) -> dict[str, Any]:
    """跑本模块 pytest 并把计数落盘（判据 f 的机器证据）。"""
    import re
    import subprocess

    out = artifacts_dir(cfg)
    cmd = [
        sys.executable, "-m", "pytest", "tests/test_proposal_chain_text_semantic.py",
        "tests/test_proposal_chain_dedup.py", "tests/test_proposal_chain_node_schema.py",
        "tests/test_proposal_chain_coverage_labels.py", "tests/test_proposal_chain_pipeline.py",
        "-q", "--no-header", "-p", "no:cacheprovider",
    ]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    tail = (proc.stdout or "")[-4000:]
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for key in ("passed", "failed", "skipped", "errors"):
        m = re.findall(rf"(\d+) {key}", tail)
        if m:
            counts[key] = int(m[-1])
    payload = {
        "command": " ".join(cmd[1:]),
        "returncode": proc.returncode,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "errors": counts["errors"],
        "tail": tail[-1500:],
    }
    write_json(out / "pytest_result.json", payload)
    return payload


STAGES = {
    "sources": stage_sources,
    "ledger": stage_ledger,
    "schema": stage_schema,
    "dedup": stage_dedup,
    "coverage": stage_coverage,
    "labels": stage_labels,
    "negative": stage_negative,
    "figures": stage_figures,
    "tests": stage_tests,
    "acceptance": stage_acceptance,
}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提案→战役供给链成熟化管线")
    ap.add_argument("--stage", required=True, choices=sorted(STAGES) + ["all"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--live", action="store_true", help="联网采集节点（默认用快照）")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    stages = list(STAGES) if args.stage == "all" else [args.stage]
    for name in stages:
        fn = STAGES[name]
        t0 = time.time()
        if name == "sources":
            result = fn(cfg, live=args.live)  # type: ignore[call-arg]
        else:
            result = fn(cfg)  # type: ignore[operator]
        print(f"[stage:{name}] {time.time() - t0:.1f}s ok", flush=True)
        if name == "acceptance":
            print(json.dumps(result["criteria"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())