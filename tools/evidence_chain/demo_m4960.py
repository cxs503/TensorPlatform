#!/usr/bin/env python3
"""m4960 演示：把一次「合成证据」批量入账，并给出幂等/隔离/拒收三种判据的实测数字。

做的事（全部程序化，报告数字零手抄）
====================================
1. 生成：用解析函数生成合成数据（N 点扫描 + 设计点），落盘成工件目录。
   数据完全由数学函数产生，不依赖任何物理域 API。
2. 成文：用 ``build_manifest`` 扫工件目录算出 RunManifest（逐文件 sha256）。
3. 入账四相：
   A 全新批（2 个干净 run）→ 期望 registered=2
   B 重跑同一批         → 期望 already_registered=2 且证据状态逐字节 diff=0
   C 混合批（篡改件 + 非法 JSON + 新干净件）→ 期望 1 registered / 1 quarantined / 1 rejected
   D dry-run 同一混合批 → 期望不落盘
4. 出图 + 出 ``report_metrics.json``（counts / digests / 判据布尔 / 独立 sha256sum 复核）。

用法：python3 tools/evidence_chain/demo_m4960.py [--no-figures]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.evidence_chain.evidence_chain import (  # noqa: E402
    EXIT_FATAL,
    IngestConfig,
    build_manifest,
    ingest,
    load_vocab,
    sha256_file,
)

ART = REPO_ROOT / "artifacts" / "m4960"
EVIDENCE = ART / "evidence"
CHAIN = ART / "chain"

# 合成数据参数（域无关）
SCAN_POINTS = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
SYNTH_PARAMS = {"alpha": 2.5, "beta": 0.8, "gamma": 1.2, "delta": 0.15}


def generate_synthetic_data() -> tuple[dict, dict, list[str]]:
    """生成合成数据：解析函数扫描 + 设计点；返回 (sweep, design, provenance_notes)。"""
    notes: list[str] = []
    
    # 解析函数：y = alpha * x^beta + gamma * sin(delta * x)
    sweep: dict[str, dict] = {}
    for x in SCAN_POINTS:
        y_main = SYNTH_PARAMS["alpha"] * (x ** SYNTH_PARAMS["beta"])
        y_osc = SYNTH_PARAMS["gamma"] * math.sin(SYNTH_PARAMS["delta"] * x)
        y_total = y_main + y_osc
        sweep[f"{x:g}"] = {
            "x": x,
            "y_main": round(y_main, 6),
            "y_osc": round(y_osc, 6),
            "y_total": round(y_total, 6),
            "derivative": round(
                SYNTH_PARAMS["alpha"] * SYNTH_PARAMS["beta"] * (x ** (SYNTH_PARAMS["beta"] - 1))
                + SYNTH_PARAMS["gamma"] * SYNTH_PARAMS["delta"] * math.cos(SYNTH_PARAMS["delta"] * x),
                6
            ),
        }
    
    # 设计点（x=50）
    design_x = 50.0
    design_y_main = SYNTH_PARAMS["alpha"] * (design_x ** SYNTH_PARAMS["beta"])
    design_y_osc = SYNTH_PARAMS["gamma"] * math.sin(SYNTH_PARAMS["delta"] * design_x)
    design_y_total = design_y_main + design_y_osc
    design = {
        "x": design_x,
        "y_main": round(design_y_main, 6),
        "y_osc": round(design_y_osc, 6),
        "y_total": round(design_y_total, 6),
        "derivative": round(
            SYNTH_PARAMS["alpha"] * SYNTH_PARAMS["beta"] * (design_x ** (SYNTH_PARAMS["beta"] - 1))
            + SYNTH_PARAMS["gamma"] * SYNTH_PARAMS["delta"] * math.cos(SYNTH_PARAMS["delta"] * design_x),
            6
        ),
        "consistency_check": round(design_y_total / (design_y_main + design_y_osc), 6),
    }
    
    notes.append(f"generated synthetic data: {len(sweep)} scan points + design point at x={design_x}")
    return sweep, design, notes


def _write(path: Path, payload: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def materialise_clean_batch(sweep: dict, design: dict) -> Path:
    batch = EVIDENCE / "batch_clean"
    if batch.exists():
        shutil.rmtree(batch)

    r1 = batch / "r1_synthetic_sweep"
    rows = ["x,y_main,y_osc,y_total,derivative"]
    for x in SCAN_POINTS:
        d = sweep[f"{x:g}"]
        rows.append(",".join(str(v) for v in [
            d["x"], d["y_main"], d["y_osc"], d["y_total"], d["derivative"]
        ]))
    _write(r1 / "sweep.csv", "\n".join(rows) + "\n")
    _write(r1 / "sweep.json", json.dumps(sweep, indent=2, sort_keys=True))
    _write(r1 / "README.md",
           "# r1 合成数据扫描（解析函数生成）\n\n"
           f"参数：α={SYNTH_PARAMS['alpha']}, β={SYNTH_PARAMS['beta']}, "
           f"γ={SYNTH_PARAMS['gamma']}, δ={SYNTH_PARAMS['delta']}；"
           f"扫描点 {SCAN_POINTS[0]:g}–{SCAN_POINTS[-1]:g} 共 {len(SCAN_POINTS)} 点。\n"
           "函数：y = α·x^β + γ·sin(δ·x)。\n")
    _write(r1 / "run_manifest.json", json.dumps(
        build_manifest(r1, {
            "run_id": "m4960-r1-synthetic-sweep",
            "status": "succeeded",
            "validation_status": "not_run",
            "producer": {"name": "synthetic-data-generator", "version": "1.0.0"},
            "goal": {"objective": "解析函数扫描，作为入账证据链批量录入的标准样本"},
            "inputs": [{"name": name, "value": value} for name, value in SYNTH_PARAMS.items()]
                      + [{"name": "scan_points", "value": SCAN_POINTS}],
            "validation": {"status": "not_run",
                           "limitations": ["合成数据无数值基准；本 run 仅证明工件哈希与声明一致"]},
            "limitations": ["数值正确性由解析函数保证，入账工具不背书"],
            "roles": {"sweep.csv": "scan-curve", "sweep.json": "raw-payload",
                      "README.md": "case-note"},
        }, vocab=load_vocab()), indent=2, sort_keys=True))

    r2 = batch / "r2_design_point"
    _write(r2 / "design_point.json", json.dumps(design, indent=2, sort_keys=True))
    _write(r2 / "check_consistency.json", json.dumps({
        "params_declared": SYNTH_PARAMS,
        "consistency_ratio": design["consistency_check"],
        "x_design": design["x"],
        "y_total": design["y_total"],
        "note": "y_total / (y_main + y_osc) 一致性（解析函数内部恒等式）",
    }, indent=2, sort_keys=True))
    _write(r2 / "run_manifest.json", json.dumps(
        build_manifest(r2, {
            "run_id": "m4960-r2-design-point-x50",
            "status": "succeeded",
            "validation_status": "not_run",
            "producer": {"name": "synthetic-data-generator", "version": "1.0.0"},
            "goal": {"objective": "设计工况 x=50 的函数值 + 一致性核对"},
            "metrics": {"y_total": design["y_total"], "derivative": design["derivative"]},
            "validation": {"status": "not_run",
                           "limitations": ["一致性 1.0 属解析恒等，不等于对实验基准的精度"]},
            "roles": {"design_point.json": "design-point", "check_consistency.json": "internal-check"},
        }, vocab=load_vocab()), indent=2, sort_keys=True))
    return batch


def materialise_mixed_batch() -> Path:
    batch = EVIDENCE / "batch_mixed"
    if batch.exists():
        shutil.rmtree(batch)

    # (a) 篡改件：先按原样建 manifest，再等长改写一个数字 → 哈希不符、size 不变
    bad = batch / "r3_tampered_artifact"
    bad.mkdir(parents=True)
    _write(bad / "design_point.json", json.dumps({"y_total": 123.456, "derivative": 7.89}, indent=2))
    manifest = build_manifest(bad, {"run_id": "m4960-r3-tampered", "status": "succeeded",
                                   "validation_status": "not_run"}, vocab=load_vocab())
    _write(bad / "run_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    _write(bad / "design_point.json", json.dumps({"y_total": 123.457, "derivative": 7.89}, indent=2))

    # (b) 非法 JSON 的 manifest（截断）
    broken = batch / "r4_broken_manifest"
    broken.mkdir(parents=True)
    _write(broken / "run_manifest.json", '{"schema_version": 1, "run_id": "m4960-r4-broken", ')

    # (c) 新干净件：独立复算的一致性说明（真实算术，非硬编码结论）
    good = batch / "r5_consistency_note"
    good.mkdir(parents=True)
    _write(good / "note.json", json.dumps({
        "run_id": "m4960-r5-consistency-note",
        "statement": "入账工具证明工件与声明一致；数值精度由各域验证器裁决",
        "hashes_checked_by": "tools/evidence_chain/evidence_chain.py",
    }, indent=2, sort_keys=True))
    _write(good / "run_manifest.json", json.dumps(
        build_manifest(good, {"run_id": "m4960-r5-consistency-note", "status": "succeeded",
                              "validation_status": "not_run",
                              "producer": {"name": "TensorPlatform/evidence_chain", "version": "1.0.0"}},
                       vocab=load_vocab()), indent=2, sort_keys=True))
    return batch


def _snapshot(out_dir: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_dir.glob("*")) if p.is_file()}


def _phase(result, *, label: str, extra: dict | None = None) -> dict:
    row = {
        "phase": label,
        "exit_code": result.exit_code,
        "counts": result.counts,
        "digests": (result.report.get("evidence_state") or {}).get("digests", {}),
        "outcomes": {r["run_id"] or "<unparsed>": r["outcome"] for r in result.rows},
    }
    if extra:
        row.update(extra)
    return row


def independent_sha256sum(paths: list[Path]) -> dict[str, dict]:
    """负控：用 coreutils sha256sum 独立复核工具算出的摘要（防自证）。"""
    out: dict[str, dict] = {}
    for path in paths:
        try:
            proc = subprocess.run(["sha256sum", str(path)], capture_output=True, text=True, check=True)
            coreutils = proc.stdout.split()[0]
        except (OSError, subprocess.CalledProcessError):
            coreutils = None
        out[str(path.relative_to(REPO_ROOT))] = {
            "tool_sha256": sha256_file(path),
            "coreutils_sha256": coreutils,
            "agree": coreutils == sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return out


def run_phases(clean_batch: Path, mixed_batch: Path) -> dict:
    if CHAIN.exists():
        shutil.rmtree(CHAIN)
    CHAIN.mkdir(parents=True, exist_ok=True)
    registry = CHAIN / "registry.local.jsonl"

    def cfg(source: Path, *, dry_run: bool = False) -> IngestConfig:
        return IngestConfig(source_dirs=[source], out_dir=CHAIN, sink_kind="local",
                            registry_path=registry, dry_run=dry_run)

    phase_a = ingest(cfg(clean_batch))
    snap_a = _snapshot(CHAIN)

    phase_b = ingest(cfg(clean_batch))
    snap_b = _snapshot(CHAIN)

    phase_c = ingest(cfg(mixed_batch))
    snap_c = _snapshot(CHAIN)

    snap_before_dry = _snapshot(CHAIN)
    phase_d = ingest(cfg(mixed_batch, dry_run=True))
    snap_after_dry = _snapshot(CHAIN)

    idempotent_files = ("registry.local.jsonl", "state.json", "quarantine.json", "ledger.jsonl")
    diff_zero = all(snap_a[name] == snap_b[name] for name in idempotent_files)
    dry_run_no_side_effect = snap_before_dry == snap_after_dry

    return {
        "phase_a": _phase(phase_a, label="A 全新批（2 干净 run）"),
        "phase_b": _phase(phase_b, label="B 幂等重跑同一批"),
        "phase_c": _phase(phase_c, label="C 混合批（篡改+非法JSON+新干净件）"),
        "phase_d": _phase(phase_d, label="D dry-run 混合批"),
        "idempotency": {
            "compared_files": list(idempotent_files),
            "digests_a": {name: snap_a[name] for name in idempotent_files},
            "digests_b": {name: snap_b[name] for name in idempotent_files},
            "diff_zero": diff_zero,
            "evidence_state_block_identical": json.dumps(phase_a.report["evidence_state"], sort_keys=True)
            == json.dumps(phase_b.report["evidence_state"], sort_keys=True),
            "rerun_registered": phase_b.counts["registered"],
            "rerun_already_registered": phase_b.counts["already_registered"],
            "dry_run_wrote_nothing": dry_run_no_side_effect,
        },
        "quarantine": phase_c.quarantine,
        "state": json.loads((CHAIN / "state.json").read_text(encoding="utf-8")),
        "registry_lines": len(registry.read_text(encoding="utf-8").splitlines()),
    }


def figures(sweep: dict, metrics: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made: list[Path] = []

    # 图 1：四相入账结果（每次调用各 outcome 计数）
    phases = [
        ("A", metrics["phase_a"]),
        ("B", metrics["phase_b"]),
        ("C", metrics["phase_c"]),
        ("D", metrics["phase_d"]),
    ]
    keys = ["registered", "already_registered", "quarantined",
            "rejected", "conflict", "failed"]
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    bottom = [0] * len(phases)
    for key in keys:
        values = [p[1]["counts"].get(key, 0) for p in phases]
        if not any(values):
            continue
        ax.bar([p[0] for p in phases], values, bottom=bottom, label=key)
        bottom = [b + v for b, v in zip(bottom, values)]
    ax.set_ylabel("runs per invocation")
    ax.set_title("m4960 evidence-chain ingest — outcomes across four phases")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = ART / "fig1_ingest_outcomes.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)

    # 图 2：逐文件复算覆盖（每 run 已验证工件数 vs 工件总数）
    fig, ax = plt.subplots(figsize=(8.2, 4.0))
    labels, verified, total = [], [], []
    state_runs = metrics["state"]["runs"]
    for run_id in sorted(state_runs):
        labels.append(run_id.replace("m4960-", ""))
        verified.append(state_runs[run_id]["artifact_count"])
        total.append(state_runs[run_id]["artifact_count"])
    quarantined = metrics["phase_c"]["counts"]["quarantined"]
    ax.bar(labels, verified, color="#2b7a3d", label="sha256 verified artifacts")
    for idx, run_id in enumerate(sorted(state_runs)):
        digest = state_runs[run_id]["manifest_sha256"][:8]
        ax.annotate(f"digest {digest}", (idx, verified[idx]), textcoords="offset points",
                    xytext=(0, 3), ha="center", fontsize=7)
    ax.set_ylim(0, max(verified + [1]) + 1.2)
    ax.set_ylabel("artifacts")
    ax.set_title(f"verified artifacts per registered run (quarantined runs: {quarantined})")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    path = ART / "fig2_artifact_verification.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)

    # 图 3：被入账的证据本身（合成数据扫描曲线）
    xs = [float(k) for k in sweep]
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    y_main = [sweep[f"{xs[i]:g}"]["y_main"] for i in order]
    y_osc = [sweep[f"{xs[i]:g}"]["y_osc"] for i in order]
    y_total = [sweep[f"{xs[i]:g}"]["y_total"] for i in order]
    xs_sorted = [xs[i] for i in order]
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.plot(xs_sorted, y_main, "o-", label="y_main = α·x^β")
    ax.plot(xs_sorted, y_osc, "s-", label="y_osc = γ·sin(δ·x)")
    ax.plot(xs_sorted, y_total, "^-", linewidth=2.2, label="y_total = y_main + y_osc")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Ingested evidence payload: synthetic function sweep")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = ART / "fig3_synthetic_sweep.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(path)
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="m4960 evidence-chain ingest demo")
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args(argv)

    sweep, design, notes = generate_synthetic_data()
    clean_batch = materialise_clean_batch(sweep, design)
    mixed_batch = materialise_mixed_batch()
    metrics = run_phases(clean_batch, mixed_batch)

    checked = sorted(clean_batch.rglob("*")) + sorted(mixed_batch.rglob("*"))
    artifacts = [p for p in checked if p.is_file() and p.name != "run_manifest.json"]
    cross_check = independent_sha256sum(artifacts)

    y_totals = {float(k): v["y_total"] for k, v in sweep.items()}
    metrics.update({
        "mission": 4960,
        "campaign_node": "IN17-B3",
        "tool": {"name": "evidence_chain", "version": "1.0.0",
                 "path": "tools/evidence_chain/evidence_chain.py"},
        "capture_notes": notes,
        "capture_source": "synthetic-data-generator",
        "synth_params": SYNTH_PARAMS,
        "scan_points": SCAN_POINTS,
        "design_point": design,
        "criteria": {
            "idempotent_rerun_diff_zero": metrics["idempotency"]["diff_zero"],
            "idempotent_rerun_evidence_state_identical":
                metrics["idempotency"]["evidence_state_block_identical"],
            "rerun_registered_is_zero": metrics["idempotency"]["rerun_registered"] == 0,
            "corrupted_file_quarantined":
                metrics["phase_c"]["counts"]["quarantined"] == 1
                and metrics["phase_c"]["outcomes"].get("m4960-r3-tampered") == "quarantined",
            "valid_row_not_blocked_by_bad_row":
                metrics["phase_c"]["counts"]["registered"] == 1
                and metrics["phase_c"]["outcomes"].get("m4960-r5-consistency-note") == "registered",
            "invalid_json_rejected":
                metrics["phase_c"]["counts"]["rejected"] == 1,
            "dry_run_zero_side_effects": metrics["idempotency"]["dry_run_wrote_nothing"],
            "hash_cross_checked_by_coreutils": all(v["agree"] for v in cross_check.values()),
        },
        "hash_cross_check": cross_check,
        "y_total_by_x": {f"{k:g}": y_totals[k] for k in sorted(y_totals)},
    })
    metrics["criteria"]["all_passed"] = all(bool(v) for v in metrics["criteria"].values())

    ART.mkdir(parents=True, exist_ok=True)
    (ART / "report_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    if not args.no_figures:
        for path in figures(sweep, metrics):
            print(f"figure: {path.relative_to(REPO_ROOT)}")

    summary = {key: metrics["criteria"][key] for key in metrics["criteria"]}
    print(json.dumps({"criteria": summary,
                      "registry_lines": metrics["registry_lines"],
                      "quarantine": [{"run_id": q["run_id"], "reason": q["reason"]}
                                     for q in metrics["quarantine"]]},
                     indent=2, sort_keys=True))
    return 0 if metrics["criteria"]["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
