#!/usr/bin/env python3
"""去重证据复算门验收（p337w1-n03）：C1–C6 机器判据 + 图表 + 判据数字落盘。

用法::

    python3 tools/acceptance/dedup_evidence_gate.py \
        --snapshot artifacts/m9953/snapshot/snapshot.json \
        --out artifacts/m9953

判据（全部机器可算，数字写入 ``<out>/dedup_evidence_gate_acceptance.json``）：
* C1 复算一致率：5 条真提案重放，命中计数与 top-k 集合一致率 100%（同一快照指纹）
* C2 篡改检出：≥10 个合成篡改用例全部被拒且原因分类正确
* C3 零领域语义：门禁代码/夹具域词扫描 0 命中
* C4 回放判别力：≥50 条真实记录回放，输出 precision/recall/混淆矩阵（recall<0.8 → 登记不可单独否决）
* C5 成本/可用性：单次复算 ≤2s；源不可达 → fail-closed（REJECT）
* C6 无副作用：只读语句 + 快照/文件哈希前后对照
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.proposal_gate.dedup_evidence import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    R_COUNT,
    R_MALFORMED,
    R_MISSING,
    R_SCORE,
    R_SNAPSHOT_STALE,
    R_SOURCE,
    R_SPEC_COVERAGE,
    R_SPEC_TAMPERED,
    R_TAMPERED,
    R_TOPK,
    VERDICT_DEEPEN,
    VERDICT_REJECT,
    DbapiSource,
    DedupEvidence,
    EvidenceConfig,
    JsonSnapshotSource,
    MemorySource,
    QueryOutcome,
    QuerySpec,
    SnapshotFingerprint,
    build_evidence,
    confusion,
    nearest_signal,
    enforce_adoption,
    sha256_hex,
    snapshot_fingerprint,
    specs_for_proposal,
    verify_evidence,
)
from tools.proposal_gate.config import load_config as load_gate_config  # noqa: E402
from tools.proposal_gate.textsim import shingles, sim_from_sets  # noqa: E402

SNAPSHOT_TABLES: tuple[str, ...] = ("campaign_nodes", "campaign_plans", "proposals")


def file_sha256(path: Path) -> str:
    """文件内容 sha256（只读，无副作用）。"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────── 工具 ───────────────────────────


def _sampled(rows: Sequence[Mapping[str, Any]], k: int) -> list[Mapping[str, Any]]:
    """确定性等距抽样（不依赖随机数，可复跑）。"""
    if k >= len(rows):
        return list(rows)
    step = len(rows) / float(k)
    return [rows[min(len(rows) - 1, int(i * step))] for i in range(k)]


def ensure_snapshot_sqlite(snapshot_path: Path, sqlite_path: Path) -> Path:
    """确保只读证明用的 SQLite 快照存在：缺失时由 snapshot.json 确定性重建（不入库，可复跑）。"""
    if sqlite_path.exists():
        return sqlite_path
    doc = json.loads(snapshot_path.read_text(encoding="utf-8"))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        for name, rows in doc["tables"].items():
            conn.execute(f"CREATE TABLE {name} (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
            conn.executemany(
                f"INSERT INTO {name} (id, text) VALUES (?, ?)",
                [(str(r["id"]), str(r["text"])) for r in rows],
            )
        conn.commit()
    finally:
        conn.close()
    return sqlite_path


def _sql_queries(tables: Sequence[str]) -> dict[str, str]:
    return {name: f"SELECT id, text FROM {name} ORDER BY id" for name in tables}


def _pool_excluding(tables: Mapping[str, Sequence[Mapping[str, Any]]], table: str, row_id: Any) -> dict[str, list[dict[str, Any]]]:
    """构造「不含候选自身」的取证池（真实收养时提案尚未入池，自匹配必须排除）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for name, rows in tables.items():
        out[name] = [
            {"id": r["id"], "text": str(r["text"])}
            for r in rows
            if not (name == table and str(r["id"]) == str(row_id))
        ]
    return out


class BrokenSource:
    """不可达源（模拟库连接失败）。"""

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:
        raise RuntimeError("database unreachable (simulated)")

    def statements(self) -> Sequence[str]:
        return ()


class RecordingSource:
    """代理源：包一层内存源，记录被读的表（只读证据）。"""

    def __init__(self, inner: MemorySource) -> None:
        self.inner = inner
        self.reads: list[str] = []

    def rows(self, table: str) -> Sequence[Mapping[str, Any]]:
        self.reads.append(table)
        return self.inner.rows(table)

    def statements(self) -> Sequence[str]:
        return tuple(f"READ {t}" for t in self.reads)


# ─────────────────────────── C1 ───────────────────────────


def c1_replay_real_proposals(snapshot_path: Path, cfg: EvidenceConfig, out_dir: Path, n: int = 5) -> dict[str, Any]:
    src_json = JsonSnapshotSource(snapshot_path)
    all_tables = {t: list(src_json.rows(t)) for t in SNAPSHOT_TABLES}
    proposals = all_tables["proposals"]
    picks = _sampled(proposals, n)
    ev_dir = out_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for row in picks:
        text = str(row["text"])
        pool = MemorySource(_pool_excluding(all_tables, "proposals", row["id"]))
        specs = specs_for_proposal(text, cfg, query_prefix=f"proposal:{row['id']}", source=pool)
        evidence = build_evidence(pool, specs, cfg)
        ev_path = ev_dir / f"evidence_proposal_{row['id']}.json"
        ev_path.write_text(evidence.to_json(), encoding="utf-8")
        result = verify_evidence(json.loads(ev_path.read_text(encoding="utf-8")), pool, cfg)
        counts_equal = all(c["count_ok"] for c in result.checks)
        topk_equal = all(c["topk_jaccard"] >= 1.0 for c in result.checks)
        records.append(
            {
                "proposal_id": row["id"],
                "text_sha256": sha256_hex(text),
                "evidence_path": str(ev_path.relative_to(ROOT)),
                "evidence_sha256": evidence.sha256(),
                "snapshot_sha256": evidence.snapshot.sha256,
                "verdict": result.verdict,
                "reason_codes": list(result.reason_codes),
                "counts_equal": counts_equal,
                "topk_equal": topk_equal,
                "elapsed_ms": round(result.elapsed_ms, 3),
                "checks": list(result.checks),
            }
        )
    consistent = sum(1 for r in records if r["counts_equal"] and r["topk_equal"])
    rate = consistent / len(records) if records else 0.0
    return {
        "criterion": "C1",
        "n": len(records),
        "consistent": consistent,
        "consistency_rate": rate,
        "pass": rate >= 1.0,
        "records": records,
    }


# ─────────────────────────── C2 ───────────────────────────


def _rehash(evidence: Mapping[str, Any], cfg: EvidenceConfig) -> dict[str, Any]:
    """按内容重算 evidence_sha256（模拟『精明的』篡改者：哈希自洽但内容不符）。"""
    specs = tuple(QuerySpec.from_dict(sp, cfg=cfg) for sp in evidence["specs"])
    outcomes = tuple(QueryOutcome.from_dict(o) for o in evidence["outcomes"])
    snap = SnapshotFingerprint.from_dict(evidence["snapshot"])
    return DedupEvidence(snapshot=snap, specs=specs, outcomes=outcomes).to_dict()


def c2_tamper_cases(snapshot_path: Path, cfg: EvidenceConfig, out_dir: Path) -> dict[str, Any]:
    src = JsonSnapshotSource(snapshot_path)
    base_proposal = list(src.rows("proposals"))[len(src.rows("proposals")) // 2]
    base_text = str(base_proposal["text"])
    base_specs = specs_for_proposal(base_text, cfg, query_prefix="tamper", source=src)
    base = build_evidence(src, base_specs, cfg)
    base_doc = base.to_dict()

    # 旧快照冒充用的另一份快照：真库子集（去掉最后 10% 行）
    smaller = {
        table: list(src.rows(table))[: max(1, int(len(src.rows(table)) * 0.9))] for table in SNAPSHOT_TABLES
    }
    small_src = MemorySource(smaller)
    old_evidence = build_evidence(small_src, specs_for_proposal(base_text, cfg, query_prefix="old", source=small_src), cfg)
    old_doc = old_evidence.to_dict()

    cases: list[tuple[str, Any, Any, str]] = []
    hit_idx = max(range(len(base_doc["outcomes"])), key=lambda i: len(base_doc["outcomes"][i]["topk"]))
    if not base_doc["outcomes"][hit_idx]["topk"]:
        raise RuntimeError("夹具前提失败：基准证据没有任何近邻，无法构造 top-k 篡改用例")
    other_idx = next((i for i in range(len(base_doc["outcomes"])) if i != hit_idx), hit_idx)

    # 1) 改命中计数但不重算哈希
    d = json.loads(json.dumps(base_doc))
    d["outcomes"][hit_idx]["hit_count"] = int(d["outcomes"][hit_idx]["hit_count"]) + 7
    cases.append(("count_edit_without_rehash", d, src, R_TAMPERED))

    # 2) 改命中计数并重算哈希（内容自洽但与现网复算不符）
    d = json.loads(json.dumps(base_doc))
    d["outcomes"][hit_idx]["hit_count"] = int(d["outcomes"][hit_idx]["hit_count"]) + 7
    cases.append(("count_edit_rehashed", _rehash(d, cfg), src, R_COUNT))

    # 3) 删近邻但不重算哈希
    d = json.loads(json.dumps(base_doc))
    d["outcomes"][hit_idx]["topk"] = d["outcomes"][hit_idx]["topk"][:-1]
    cases.append(("neighbor_delete_without_rehash", d, src, R_TAMPERED))

    # 4) 置换一个近邻（计数不变）并重算哈希 → top-k 集合失配
    d = json.loads(json.dumps(base_doc))
    live_outcome = QueryOutcome.from_dict(base_doc["outcomes"][hit_idx])
    live_keys = {h.key for h in live_outcome.topk}
    pool = list(src.rows(base_specs[hit_idx].table))
    replacement = None
    for cand in pool:
        key = f"{base_specs[hit_idx].table}:{cand['id']}"
        if key not in live_keys:
            replacement = {"key": key, "table": base_specs[hit_idx].table, "row_id": str(cand["id"]), "score": 0.5}
            break
    if replacement:
        d["outcomes"][hit_idx]["topk"][-1] = replacement
    cases.append(("neighbor_swap_rehashed", _rehash(d, cfg), src, R_TOPK))

    # 5) 伪造快照指纹（改行数/max_id 并重算哈希）
    d = json.loads(json.dumps(base_doc))
    d["snapshot"]["tables"][0]["row_count"] = int(d["snapshot"]["tables"][0]["row_count"]) + 12345
    cases.append(("snapshot_fingerprint_forged", _rehash(d, cfg), src, R_SNAPSHOT_STALE))

    # 6) 用旧快照冒充（子集快照取证，拿去对现网复算）
    cases.append(("old_snapshot_masquerade", old_doc, src, R_SNAPSHOT_STALE))

    # 7) 自由文本「我查过了」
    cases.append(("free_text_evidence", "我查过了，没有重复（三重检索已完成）", src, R_MALFORMED))

    # 8) 空规格（没有检索规格 = 没有证据）
    d = json.loads(json.dumps(base_doc))
    d["specs"] = []
    d["outcomes"] = []
    cases.append(("empty_specs", _rehash(d, cfg), src, R_MISSING))

    # 9) 三重检索覆盖不全（去掉一张表的规格）
    d = json.loads(json.dumps(base_doc))
    d["specs"] = d["specs"][:2]
    d["outcomes"] = d["outcomes"][:2]
    cases.append(("coverage_incomplete", _rehash(d, cfg), src, R_SPEC_COVERAGE))

    # 10) 改规格词项但保留旧 outcome 的 spec_sha256
    d = json.loads(json.dumps(base_doc))
    d["specs"][other_idx]["terms"] = list(d["specs"][other_idx]["terms"])[:-1] or ["x"]
    cases.append(("spec_terms_tampered_rehashed", _rehash(d, cfg), src, R_SPEC_TAMPERED))

    # 11) 改 top-k 分数并重算哈希 → 分数失配
    d = json.loads(json.dumps(base_doc))
    d["outcomes"][hit_idx]["topk"][0]["score"] = float(d["outcomes"][hit_idx]["topk"][0]["score"]) + 0.5
    cases.append(("score_edit_rehashed", _rehash(d, cfg), src, R_SCORE))

    # 12) 数据源不可达（证据本身合法）→ fail-closed
    cases.append(("source_unavailable", base_doc, BrokenSource(), R_SOURCE))

    results: list[dict[str, Any]] = []
    for name, evidence, source, expected in cases:
        verdict = verify_evidence(evidence, source, cfg)
        ok = verdict.verdict == VERDICT_REJECT and expected in verdict.reason_codes
        results.append(
            {
                "case": name,
                "expected_reason": expected,
                "verdict": verdict.verdict,
                "reason_codes": list(verdict.reason_codes),
                "details": list(verdict.details),
                "correct": ok,
            }
        )
    # 13) 缺字段（提案根本不带证据）
    verdict = enforce_adoption({"title": "no-evidence"}, src, cfg)
    results.append(
        {
            "case": "missing_evidence_field",
            "expected_reason": R_MISSING,
            "verdict": verdict.verdict,
            "reason_codes": list(verdict.reason_codes),
            "details": list(verdict.details),
            "correct": verdict.verdict == VERDICT_REJECT and R_MISSING in verdict.reason_codes,
        }
    )

    tamper_dir = out_dir / "tamper"
    tamper_dir.mkdir(parents=True, exist_ok=True)
    (tamper_dir / "cases.json").write_text(
        json.dumps({"base_evidence_sha256": base_doc["evidence_sha256"], "results": results},
                   ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    caught = sum(1 for r in results if r["correct"])
    return {
        "criterion": "C2",
        "n_cases": len(results),
        "n_caught": caught,
        "detection_rate": caught / len(results) if results else 0.0,
        "pass": caught >= 10 and caught == len(results),
        "results": results,
    }


# ─────────────────────────── C3 ───────────────────────────


def c3_domain_scan(out_dir: Path) -> dict[str, Any]:
    paths = [
        "tools/proposal_gate/dedup_evidence.py",
        "tools/proposal_gate/dedup_evidence_cli.py",
        "tools/proposal_gate/dedup_evidence_collect.py",
        "tools/acceptance/dedup_evidence_gate.py",
        "tools/acceptance/dedup_evidence_report.py",
        "tools/acceptance/post_report_m9953.py",
        "tests/test_dedup_evidence_gate.py",
        "config/dedup_evidence.json",
    ]
    existing = [p for p in paths if (ROOT / p).exists()]
    out_json = out_dir / "domain_leak_scan.json"
    existing.append(str(out_json.relative_to(ROOT)) if out_json.exists() else "")
    existing = [p for p in existing if p]
    proc = subprocess.run(
        [sys.executable, "tools/audit/domain_leak_scan.py", "--paths", *existing, "--json", str(out_json)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    doc: dict[str, Any] = {}
    if out_json.exists():
        doc = json.loads(out_json.read_text(encoding="utf-8"))
    hits = doc.get("hits") or doc.get("total_hits") or 0
    if isinstance(hits, list):
        n_hits = len(hits)
    elif isinstance(hits, int):
        n_hits = hits
    else:
        n_hits = int(doc.get("summary", {}).get("hits", 0) or 0)
    return {
        "criterion": "C3",
        "paths": existing,
        "scan_exit_code": proc.returncode,
        "n_hits": n_hits,
        "pass": n_hits == 0 and proc.returncode == 0,
        "scanner_json": str(out_json.relative_to(ROOT)) if out_json.exists() else None,
        "stdout": proc.stdout.strip()[:2000],
        "stderr": proc.stderr.strip()[:1000],
    }


# ─────────────────────────── C4 ───────────────────────────


def _rule_decision(modes: Mapping[str, Mapping[str, float]], tau: float, min_votes: int) -> bool:
    """判定规则：任一张表里 ≥ min_votes 个模式的 top-1 ≥ tau ⇒ 判「池中有近邻」。"""
    for _table, per_mode in modes.items():
        votes = sum(1 for score in per_mode.values() if score >= tau)
        if votes >= min_votes:
            return True
    return False


def c4_replay_power(snapshot_path: Path, cfg: EvidenceConfig, out_dir: Path,
                    n_candidates: int = 60, pool_size: int = 900) -> dict[str, Any]:
    """回放判别力：真库三表等距抽样候选，参照标签 = 既有文本相似门（proxy oracle）。

    设计纪律（predeclared）：
    * 候选按**奇偶序**固定切 dev/holdout 两半（无随机、可复跑）；
    * 近邻判定规则（τ, min_votes）**只在 dev 半上网格选点**，选点目标 = 在 recall ≥ 0.8
      约束下最大化 F1，平局先取高 precision，再取更保守（更大 τ / 更多 votes）的规则；
    * holdout 半只报数不改规则；最终判定口径与 ``config/dedup_evidence.json`` 保持一致
      （若不一致，报告里显式列出差额并说明以 holdout 复算为准）。
    """
    src = JsonSnapshotSource(snapshot_path)
    rows_by_table = {t: list(src.rows(t)) for t in SNAPSHOT_TABLES}
    pool: list[dict[str, Any]] = []
    for table, rows in rows_by_table.items():
        for row in rows:
            pool.append({"table": table, "id": row["id"], "text": str(row["text"])})
    pool = _sampled(pool, pool_size)
    for row in pool:
        row["shingle"] = shingles(row["text"], 3)

    gate_cfg = load_gate_config()
    dup_threshold = float(gate_cfg.need("dedup.dup_threshold"))
    per_table = max(1, n_candidates // len(SNAPSHOT_TABLES))
    candidates = [
        {"table": table, "id": row["id"], "text": str(row["text"])}
        for table in SNAPSHOT_TABLES
        for row in _sampled(rows_by_table[table], per_table)
    ]
    base_tables = {t: [{"id": r["id"], "text": r["text"]} for r in rows_by_table[t]] for t in SNAPSHOT_TABLES}

    records: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates):
        text = cand["text"]
        cand_shingles = shingles(text, 3)
        best_score = 0.0
        best_key = ""
        for row in pool:
            if row["table"] == cand["table"] and str(row["id"]) == str(cand["id"]):
                continue
            sim = sim_from_sets(cand_shingles, row["shingle"], gate_cfg)
            if float(sim.score) > best_score:
                best_score = float(sim.score)
                best_key = f"{row['table']}:{row['id']}"
        label_dup = best_score >= dup_threshold

        replay_src = MemorySource(_pool_excluding(base_tables, cand["table"], cand["id"]))
        specs = specs_for_proposal(text, cfg, query_prefix=f"replay:{cand['table']}:{cand['id']}", source=replay_src)
        evidence = build_evidence(replay_src, specs, cfg)
        result = verify_evidence(json.loads(evidence.to_json()), replay_src, cfg)
        signal = nearest_signal(evidence.outcomes, specs, cfg)
        modes = {t: {m: float(v["top1_score"]) for m, v in slot["modes"].items()} for t, slot in signal["per_table"].items()}
        records.append(
            {
                "record_id": f"{cand['table']}:{cand['id']}",
                "split": "dev" if idx % 2 == 0 else "holdout",
                "text_sha256": sha256_hex(text),
                "oracle_score": round(best_score, 6),
                "oracle_against": best_key,
                "label_dup": label_dup,
                "shipped_verdict": result.verdict,
                "shipped_dup": result.verdict == VERDICT_DEEPEN,
                "mode_top1": {t: {m: round(v, 6) for m, v in per_mode.items()} for t, per_mode in modes.items()},
                "max_top1": round(max((max(v.values()) for v in modes.values()), default=0.0), 6),
                "elapsed_ms": round(result.elapsed_ms, 3),
            }
        )

    def evaluate(subset: Sequence[Mapping[str, Any]], tau: float, min_votes: int) -> dict[str, Any]:
        tp = fp = fn = tn = 0
        for r in subset:
            decision = _rule_decision(r["mode_top1"], tau, min_votes)
            if r["label_dup"] and decision:
                tp += 1
            elif r["label_dup"]:
                fn += 1
            elif decision:
                fp += 1
            else:
                tn += 1
        return confusion(tp, fp, fn, tn)

    dev = [r for r in records if r["split"] == "dev"]
    holdout = [r for r in records if r["split"] == "holdout"]
    grid: list[dict[str, Any]] = []
    for min_votes in (1, 2, 3):
        for tau in (0.6, 0.7, 0.8, 0.9):
            cm = evaluate(dev, tau, min_votes)
            grid.append({"min_votes": min_votes, "tau": tau, "dev": cm})

    def select_key(g: Mapping[str, Any]):
        cm = g["dev"]
        recall = cm["recall"] if cm["recall"] is not None else 0.0
        f1 = cm["f1"] if cm["f1"] is not None else 0.0
        feasible = recall >= 0.8
        return (1 if feasible else 0, round(f1, 12), round(cm["precision"] or 0.0, 12), g["min_votes"], g["tau"])

    chosen = max(grid, key=select_key)
    dev_cm = chosen["dev"]
    holdout_cm = evaluate(holdout, chosen["tau"], chosen["min_votes"])
    shipped_cm_dev = evaluate(dev, cfg.mode_threshold(cfg.required_modes[0]), cfg.neighbor_min_votes)
    shipped_cm_holdout = evaluate(holdout, cfg.mode_threshold(cfg.required_modes[0]), cfg.neighbor_min_votes)
    recall_ok = (holdout_cm["recall"] or 0.0) >= 0.8

    (out_dir / "replay_records.json").write_text(
        json.dumps(
            {
                "oracle": {
                    "type": "incumbent text-similarity dedup gate (proxy oracle, not human labels)",
                    "config": "config/proposal_gate.json",
                    "metric": "char 3-gram jaccard/containment blend",
                    "dup_threshold": dup_threshold,
                },
                "design": {
                    "split": "candidate index parity (deterministic)",
                    "rule_selection": "grid on dev only: min_votes in {1,2,3}, tau in {0.6,0.7,0.8,0.9}; maximize F1 subject to recall>=0.8",
                    "chosen_rule": {"min_votes": chosen["min_votes"], "tau": chosen["tau"]},
                },
                "pool_size": len(pool),
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "criterion": "C4",
        "n_records": len(records),
        "n_dev": len(dev),
        "n_holdout": len(holdout),
        "positives": sum(1 for r in records if r["label_dup"]),
        "confusion": holdout_cm,
        "confusion_dev": dev_cm,
        "confusion_all_chosen_rule": evaluate(records, chosen["tau"], chosen["min_votes"]),
        "shipped_rule": {
            "min_votes": cfg.neighbor_min_votes,
            "tau": cfg.mode_threshold(cfg.required_modes[0]),
            "confusion_dev": shipped_cm_dev,
            "confusion_holdout": shipped_cm_holdout,
        },
        "rule_grid": grid,
        "chosen_rule": {"min_votes": chosen["min_votes"], "tau": chosen["tau"]},
        "dup_threshold_oracle": dup_threshold,
        "pass": len(records) >= 50,
        "recall_ok": recall_ok,
        "sole_veto_allowed": bool(recall_ok),
        "registry_note": None
        if recall_ok
        else "RECALL_BELOW_0.8: 本门不得单独作否决依据，需与文本相似门并联；已如实登记",
        "records_path": str((out_dir / "replay_records.json").relative_to(ROOT)),
    }


# ─────────────────────────── C5 / C6 ───────────────────────────


def c5_cost_availability(snapshot_path: Path, cfg: EvidenceConfig, c1: Mapping[str, Any], c4: Mapping[str, Any]) -> dict[str, Any]:
    times: list[float] = [r["elapsed_ms"] for r in c1["records"]]
    records_path = ROOT / c4["records_path"]
    replays = json.loads(records_path.read_text(encoding="utf-8"))["records"]
    times += [float(r["elapsed_ms"]) for r in replays]
    times_sorted = sorted(times)
    def pct(p: float) -> float:
        if not times_sorted:
            return 0.0
        idx = min(len(times_sorted) - 1, int(math.ceil(p * len(times_sorted))) - 1)
        return times_sorted[max(0, idx)]
    worst = max(times) if times else float("inf")
    src = JsonSnapshotSource(snapshot_path)
    base = build_evidence(src, specs_for_proposal(str(list(src.rows("proposals"))[0]["text"]), cfg, query_prefix="c5", source=src), cfg)
    down = verify_evidence(base.to_dict(), BrokenSource(), cfg)
    fail_closed_ok = down.verdict == VERDICT_REJECT and R_SOURCE in down.reason_codes
    # 规模外推：真快照行数 ×2/×4（合成复制，仅用于测成本曲线，不参与判据）
    scaling: list[dict[str, Any]] = []
    base_tables = {t: [{"id": r["id"], "text": str(r["text"])} for r in src.rows(t)] for t in SNAPSHOT_TABLES}
    sample_text = str(list(src.rows("proposals"))[0]["text"])
    for factor in (1, 2, 4, 8):
        tables = {
            name: [{"id": f"{r['id']}_{k}", "text": r["text"]} for k in range(factor) for r in rows]
            for name, rows in base_tables.items()
        }
        scaled = MemorySource(tables)
        ev = build_evidence(scaled, specs_for_proposal(sample_text, cfg, query_prefix=f"scale{factor}", source=scaled), cfg)
        res = verify_evidence(ev.to_dict(), scaled, cfg)
        scaling.append(
            {
                "rows": sum(len(v) for v in tables.values()),
                "factor": factor,
                "elapsed_ms": round(res.elapsed_ms, 3),
                "within_budget": res.elapsed_ms <= cfg.max_recompute_seconds * 1000.0,
            }
        )
    breach = next((s for s in scaling if not s["within_budget"]), None)
    # 线性外推（最小二乘）给出「预算破线行数」的能力边界
    xs = [s["rows"] for s in scaling]
    ys = [s["elapsed_ms"] for s in scaling]
    extrapolated_rows = None
    if len(xs) >= 2 and max(xs) != min(xs):
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        slope = num / den if den else 0.0
        intercept = mean_y - slope * mean_x
        if slope > 0:
            extrapolated_rows = int((cfg.max_recompute_seconds * 1000.0 - intercept) / slope)

    return {
        "criterion": "C5",
        "n_runs": len(times),
        "mean_ms": round(sum(times) / len(times), 3) if times else None,
        "p95_ms": round(pct(0.95), 3),
        "max_ms": round(worst, 3),
        "budget_ms": cfg.max_recompute_seconds * 1000.0,
        "pass": bool(times) and worst <= cfg.max_recompute_seconds * 1000.0 and fail_closed_ok,
        "source_down_verdict": down.verdict,
        "source_down_reasons": list(down.reason_codes),
        "fail_closed": fail_closed_ok,
        "time_samples_ms": [round(t, 3) for t in times],
        "scaling_synthetic": scaling,
        "first_budget_breach_rows": breach["rows"] if breach else None,
        "extrapolated_budget_breach_rows": extrapolated_rows,
        "scaling_note": "规模外推用真快照行复制（合成，非真数据），只用于给出成本曲线的能力边界",
    }


def c6_no_side_effects(snapshot_path: Path, cfg: EvidenceConfig, sqlite_path: Path) -> dict[str, Any]:
    before_file = file_sha256(snapshot_path)
    before_db = file_sha256(sqlite_path)
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        src = DbapiSource(conn, _sql_queries(SNAPSHOT_TABLES))
        snap_before = snapshot_fingerprint(src, cfg)
        rows_sha_before = {t: sha256_hex([[str(r["id"]), str(r["text"])] for r in src.rows(t)]) for t in SNAPSHOT_TABLES}
        proposals = list(src.rows("proposals"))
        evidence = build_evidence(src, specs_for_proposal(str(proposals[0]["text"]), cfg, query_prefix="c6", source=src), cfg)
        result = verify_evidence(evidence.to_dict(), src, cfg)
        snap_after = snapshot_fingerprint(src, cfg)
        rows_sha_after = {t: sha256_hex([[str(r["id"]), str(r["text"])] for r in src.rows(t)]) for t in SNAPSHOT_TABLES}
        statements = list(src.statements())
    finally:
        conn.close()
    after_file = file_sha256(snapshot_path)
    after_db = file_sha256(sqlite_path)
    all_select = all(s.strip().upper().startswith(("SELECT", "WITH")) for s in statements)
    same_fp = snap_before.sha256 == snap_after.sha256
    same_rows = rows_sha_before == rows_sha_after
    files_same = before_file == after_file and before_db == after_db
    return {
        "criterion": "C6",
        "verdict": result.verdict,
        "snapshot_sha256_before": snap_before.sha256,
        "snapshot_sha256_after": snap_after.sha256,
        "snapshot_unchanged": same_fp,
        "row_hashes_unchanged": same_rows,
        "files_unchanged": files_same,
        "statements_executed": statements,
        "all_statements_select": all_select,
        "pass": same_fp and same_rows and files_same and all_select,
    }


# ─────────────────────────── 图表 ───────────────────────────


def make_figures(report: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, str]] = []

    # 图 1：C4 混淆矩阵
    cm = report["C4"]["confusion"]
    fig, ax = plt.subplots(figsize=(4.6, 4.0))
    grid = [[cm["tp"], cm["fn"]], [cm["fp"], cm["tn"]]]
    im = ax.imshow(grid, cmap="Blues")
    labels = [["TP", "FN"], ["FP", "TN"]]
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{labels[i][j]}={grid[i][j]}", ha="center", va="center",
                    color="white" if grid[i][j] > max(cm["tp"], cm["tn"]) / 2 else "black", fontsize=12)
    ax.set_xticks([0, 1], ["gate: dup", "gate: novel"])
    ax.set_yticks([0, 1], ["oracle: dup", "oracle: novel"])
    ax.set_title(f"Replay confusion (n={cm['n']}, P={cm['precision']:.2f}, R={cm['recall']:.2f})")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    p1 = fig_dir / "fig_confusion_matrix.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    figures.append({"name": p1.name, "caption": "C4 回放判别力：复算门 vs 参照文本相似门的混淆矩阵", "path": str(p1)})

    # 图 2：C5 复算耗时分布（对数横轴 + 预算线）
    times = report["C5"]["time_samples_ms"]
    fig, ax = plt.subplots(figsize=(5.4, 3.6))
    ax.hist(times, bins=min(30, max(5, len(times) // 2)), color="#3b6ea5", edgecolor="black", alpha=0.85)
    ax.axvline(report["C5"]["budget_ms"], color="red", linestyle="--", label=f"budget {report['C5']['budget_ms']:.0f} ms")
    ax.axvline(report["C5"]["max_ms"], color="black", linestyle=":", label=f"max {report['C5']['max_ms']:.1f} ms")
    ax.set_xlabel("single recompute wall time [ms]")
    ax.set_ylabel("count")
    ax.set_title(f"Recompute cost (n={report['C5']['n_runs']})")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p2 = fig_dir / "fig_recompute_cost.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    figures.append({"name": p2.name, "caption": "C5 单次复算耗时分布 vs 2s 预算", "path": str(p2)})

    # 图 3：C2 篡改用例检出与原因分类
    cases = report["C2"]["results"]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    names = [c["case"] for c in cases]
    colors = ["#2e7d32" if c["correct"] else "#c62828" for c in cases]
    ax.barh(range(len(cases)), [1] * len(cases), color=colors)
    ax.set_yticks(range(len(cases)), [f"{c['case']} → {c['reason_codes'][0] if c['reason_codes'] else '-'}" for c in cases], fontsize=7)
    ax.set_xlim(0, 1.05)
    ax.set_xticks([])
    ax.set_title(f"Tamper detection: {report['C2']['n_caught']}/{report['C2']['n_cases']} rejected with correct reason")
    fig.tight_layout()
    p3 = fig_dir / "fig_tamper_cases.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    figures.append({"name": p3.name, "caption": "C2 篡改用例逐条检出与原因码分类", "path": str(p3)})

    # 图 5：C5 规模外推成本曲线
    scaling = report["C5"].get("scaling_synthetic") or []
    if scaling:
        fig, ax = plt.subplots(figsize=(5.2, 3.6))
        xs = [s["rows"] for s in scaling]
        ys = [s["elapsed_ms"] for s in scaling]
        ax.plot(xs, ys, marker="o", color="#33587a", label="measured (synthetic scaling)")
        ax.axhline(report["C5"]["budget_ms"], color="red", linestyle="--", label="2 s budget")
        if report["C5"].get("extrapolated_budget_breach_rows"):
            ax.axvline(report["C5"]["extrapolated_budget_breach_rows"], color="gray", linestyle=":",
                       label=f"extrapolated breach @ {report['C5']['extrapolated_budget_breach_rows']} rows")
        ax.set_xlabel("snapshot rows")
        ax.set_ylabel("single recompute [ms]")
        ax.set_title("Recompute cost vs snapshot size")
        ax.legend(fontsize=7)
        fig.tight_layout()
        p5 = fig_dir / "fig_scaling_cost.png"
        fig.savefig(p5, dpi=140)
        plt.close(fig)
        figures.append({"name": p5.name, "caption": "C5 复算成本随快照规模的增长与 2s 预算破线外推", "path": str(p5)})

    # 图 4：C1 逐提案一致率 + top-k Jaccard
    recs = report["C1"]["records"]
    fig, ax = plt.subplots(figsize=(5.4, 3.4))
    idx = list(range(len(recs)))
    jac = [min((c["topk_jaccard"] for c in r["checks"]), default=0.0) for r in recs]
    ax.bar(idx, jac, color="#4a7c59")
    ax.axhline(1.0, color="black", linestyle="--", linewidth=1)
    ax.set_xticks(idx, [f"p{r['proposal_id']}" for r in recs], fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("min top-k Jaccard over specs")
    ax.set_title(f"C1 replay consistency = {report['C1']['consistency_rate']*100:.0f}% (n={report['C1']['n']})")
    fig.tight_layout()
    p4 = fig_dir / "fig_replay_consistency.png"
    fig.savefig(p4, dpi=140)
    plt.close(fig)
    figures.append({"name": p4.name, "caption": "C1 五条真提案复算一致率（逐规格最小 top-k Jaccard）", "path": str(p4)})
    return figures


# ─────────────────────────── main ───────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default="artifacts/m9953/snapshot/snapshot.json")
    ap.add_argument("--sqlite", default="artifacts/m9953/snapshot/snapshot.sqlite")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--out", default="artifacts/m9953")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = EvidenceConfig.load(args.config)
    snapshot_path = Path(args.snapshot).resolve()
    started = time.perf_counter()

    report: dict[str, Any] = {
        "schema_version": "dedup-evidence-acceptance/1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": str(args.config),
        "config_sha256": sha256_hex(json.loads(Path(args.config).read_text(encoding="utf-8"))),
        "snapshot_path": str(args.snapshot),
        "snapshot_sqlite": str(sqlite_path) if "sqlite_path" in dir() else None,
        "snapshot_sha256_file": file_sha256(snapshot_path),
    }
    report["C1"] = c1_replay_real_proposals(snapshot_path, cfg, out_dir)
    report["C2"] = c2_tamper_cases(snapshot_path, cfg, out_dir)
    report["C3"] = c3_domain_scan(out_dir)
    report["C4"] = c4_replay_power(snapshot_path, cfg, out_dir)
    report["C5"] = c5_cost_availability(snapshot_path, cfg, report["C1"], report["C4"])
    sqlite_path = ensure_snapshot_sqlite(snapshot_path, Path(args.sqlite).resolve())
    report["C6"] = (
        c6_no_side_effects(snapshot_path, cfg, sqlite_path)
        if sqlite_path.exists()
        else {"criterion": "C6", "pass": False, "error": f"缺少 sqlite 快照: {sqlite_path}"}
    )
    criteria = {k: bool(report[k].get("pass")) for k in ("C1", "C2", "C3", "C4", "C5", "C6")}
    report["criteria"] = criteria
    report["all_pass"] = all(criteria.values())
    figures = make_figures(report, out_dir)
    report["figures"] = [{"name": f["name"], "caption": f["caption"], "path": str(Path(f["path"]).relative_to(ROOT))} for f in figures]
    report["wall_seconds"] = round(time.perf_counter() - started, 2)

    out_json = out_dir / "dedup_evidence_gate_acceptance.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8")
    print(json.dumps({"criteria": criteria, "all_pass": report["all_pass"],
                      "C1_rate": report["C1"]["consistency_rate"],
                      "C2": f"{report['C2']['n_caught']}/{report['C2']['n_cases']}",
                      "C4": report["C4"]["confusion"], "C5_max_ms": report["C5"]["max_ms"],
                      "report": str(out_json)}, ensure_ascii=False, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())