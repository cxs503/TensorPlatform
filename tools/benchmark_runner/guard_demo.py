"""成熟化增量演示器：把"护栏确实拦得住"变成可重放的证据（不是一句声明）。

跑什么（全部经公开 CLI/API，与人工操作等价）
-------------------------------------------
1. 墙钟护栏负对照：``configs/fx_timeout_negative.json``（挂死 SUT vs 0.5 s 预声明预算）；
2. suite 预算负对照：``configs/fx_budget_negative.json``（预算 1 s，第二个算例被饿死）；
3. 覆盖度失守演练：以 ``fx_integration`` 建基线，再用"缩水版"（1/5 算例）重跑，
   记录回归裁决与退出码（改前实测：status=noted、exit=0）；
4. 证据链篡改演练：复制一份干净 run，改一个算例的 ``rel_error``，
   记录 ``verify_manifest`` 的裁决（改前：引擎从不回验）；
5. 汇总成 ``evidence.json``（每个数字都带来源工件路径 + sha256）并出图。

零领域语义：本模块只驱动合成算例与通用数值，不含任何应用域词条（由
:mod:`tools.benchmark_runner.domain_guard` 扫描验证）。

用法::

    python3 -m tools.benchmark_runner.guard_demo --out artifacts/benchmark_runner_m9703
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import time
from typing import Any, Dict, List

from . import ENGINE_VERSION
from . import artifact as art
from . import config as cfgmod
from . import domain_guard
from . import regression as reg
from . import runner as run

CONFIG_DIR = pathlib.Path(__file__).resolve().parent / "configs"


def _sha(path: str | pathlib.Path) -> str:
    p = pathlib.Path(path)
    return art.sha256_file(p) if p.is_file() else ""


def _rel(root: pathlib.Path, path: str | pathlib.Path) -> str:
    try:
        return str(pathlib.Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _drill_coverage(root: pathlib.Path) -> Dict[str, Any]:
    """覆盖度失守演练：基线 5 算例 → 缩水到 1 算例，看机器是否报警。"""
    base_cfg = cfgmod.load_config(CONFIG_DIR / "fx_integration.json")
    baseline_path = root / "drills" / "coverage_baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    base_run = run.run_config(CONFIG_DIR / "fx_integration.json", out_root=root / "runs",
                              baseline_path=baseline_path, update_baseline=True,
                              run_id="coverage-baseline")

    truncated = dict(base_cfg)
    truncated["cases"] = base_cfg["cases"][:1]
    truncated["suite_criteria"] = [{"metric": "pass_ratio", "op": ">=", "value": 1.0}]
    tcfg = root / "drills" / "truncated_suite.json"
    tcfg.write_text(json.dumps(truncated, indent=2, ensure_ascii=False, default=str))
    shrunk = run.run_config(tcfg, out_root=root / "runs", baseline_path=baseline_path,
                            run_id="coverage-truncated")
    report = shrunk["regression"]
    kinds: Dict[str, int] = {}
    for a in report.get("alerts", []):
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
    return {
        "baseline_cases": len(base_cfg["cases"]),
        "rerun_cases": len(truncated["cases"]),
        "comparison": {
            "status": report["status"],
            "counts": report["counts"],
            "alert_kinds": kinds,
            "has_regression": reg.has_regression(report),
            "gate_passed": shrunk["passed"],
            "exit_code": shrunk["exit_code"],
        },
        "pre_fix_measured": {"status": "noted", "has_regression": False, "exit_code": 0,
                             "note": "v1.0 measured before the fix: vanished cases were only 'warn' "
                                     "and has_regression() ignored them -> exit 0 (silent coverage loss)"},
        "artifacts": {"baseline_run": base_run["paths"]["out_dir"],
                      "truncated_config": str(tcfg),
                      "truncated_run": shrunk["paths"]["out_dir"],
                      "regression_json": shrunk["paths"]["regression"],
                      "regression_sha256": _sha(shrunk["paths"]["regression"])},
    }


def _drill_tamper(root: pathlib.Path, source_run: str) -> Dict[str, Any]:
    """证据链篡改演练：改一个字节，看回验会不会红。"""
    src = pathlib.Path(source_run)
    dst = root / "drills" / "tampered_run"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    clean_verdict = art.verify_manifest(dst)
    target = sorted((dst / "cases").glob("*.json"))[0]
    payload = json.loads(target.read_text())
    before = payload.get("metrics", {}).get("rel_error")
    payload.setdefault("metrics", {})["rel_error"] = 0.0
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tampered_verdict = art.verify_manifest(dst)
    out = {
        "clean_verdict_ok": clean_verdict["ok"],
        "tampered_file": _rel(root, target),
        "rel_error_before": before,
        "rel_error_after": 0.0,
        "tampered_verdict_ok": tampered_verdict["ok"],
        "mismatch_count": len(tampered_verdict["mismatch"]),
        "case_self_hash_errors": len(tampered_verdict["case_self_hash_errors"]),
        "note": "v1.0 had no post-write verification step at all: the manifest sha256 were never "
                "recomputed, so this edit would have gone unnoticed by the engine itself.",
    }
    (root / "drills" / "tamper_verdict.json").write_text(
        json.dumps({**out, "verdict": tampered_verdict}, indent=2, ensure_ascii=False))
    return out


def _figure(root: pathlib.Path, evidence: MappingLike) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir = root / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []

    # 图1：墙钟护栏——预声明预算 vs 实测耗时
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4))
    ax = axes[0]
    labels, measured, budgets = [], [], []
    for suite, entry in evidence["wall_clock"].items():
        for cid, info in entry["cases"].items():
            labels.append(f"{suite}\n{cid}")
            measured.append(max(info["timing_s"] or 0.0, 1e-3))
            budgets.append(info["declared_timeout_s"])
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], measured, width=0.4, label="measured wall time (s)", color="#1565c0")
    ax.bar([i + 0.2 for i in x], budgets, width=0.4, label="predeclared timeout (s)", color="#c62828")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=6, rotation=20, ha="right")
    ax.set_ylabel("seconds (log)")
    ax.set_title("(a) per-case wall time vs predeclared budget\n(stalled cases are cut off, not waited on)",
                 fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=7)

    ax = axes[1]
    names, engine_wall, stall = [], [], []
    for suite, info in evidence["wall_clock"].items():
        names.append(suite)
        engine_wall.append(info["_engine_wall_s"])
        stall.append(info["_requested_stall_s"])
    y = range(len(names))
    ax.barh([i - 0.2 for i in y], stall, height=0.4, color="#9e9e9e",
            label="SUT asked to stall (s)")
    ax.barh([i + 0.2 for i in y], engine_wall, height=0.4, color="#2e7d32",
            label="engine returned in (s)")
    ax.set_xscale("log")
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("seconds (log)")
    ax.set_title("(b) unattended runner returns a verdict\ninstead of hanging on the stalled workload",
                 fontsize=9)
    ax.grid(True, axis="x", alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    p1 = figdir / "fig1_wallclock_guard.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    paths.append(str(p1))

    # 图2：fail-closed 判别力矩阵（演练实测）
    fig, ax = plt.subplots(figsize=(9.6, 4.2))
    scenarios = [
        "stalled SUT\n(beyond timeout)",
        "suite budget\nexhausted",
        "artifact byte\ntampered",
        "artifact file\nmissing",
        "unregistered\nextra artifact",
        "case self-hash\nmismatch",
        "cases vanished\n(coverage loss)",
    ]
    prefix = [False] * len(scenarios)          # v1.0：这几条护栏根本不存在
    post = [
        evidence["wall_clock"]["fx_timeout_negative"]["_verdict_ok"],
        evidence["wall_clock"]["fx_budget_negative"]["_verdict_ok"],
        evidence["tamper"]["tampered_verdict_ok"] is False,
        evidence["integrity_checks"]["missing_detected"],
        evidence["integrity_checks"]["extra_detected"],
        evidence["integrity_checks"]["self_hash_detected"],
        evidence["coverage"]["comparison"]["has_regression"],
    ]
    import numpy as np
    m = np.array([prefix, post], dtype=float)
    ax.imshow(m, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(scenarios, fontsize=7)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["v1.0 (before)", "v1.1 (this wave)"], fontsize=9)
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            ax.text(j, i, "detected" if m[i, j] else "silent", ha="center", va="center",
                    fontsize=7, color="black")
    ax.set_title("fail-closed guard discriminating power (green = the engine catches it)\n"
                 "v1.0 row is 'silent' where the capability did not exist (code-inspected, not measured)",
                 fontsize=9)
    fig.tight_layout()
    p2 = figdir / "fig2_failclosed_matrix.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    paths.append(str(p2))

    # 图3：覆盖度失守的前后对比
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0))
    ax = axes[0]
    cov = evidence["coverage"]
    ax.bar(["baseline\nsuite", "rerun\nsuite"], [cov["baseline_cases"], cov["rerun_cases"]],
           color=["#1565c0", "#c62828"], width=0.5)
    ax.set_ylabel("cases in suite")
    ax.set_title("(a) 5 predeclared cases -> 1 actually ran", fontsize=9)
    for i, v in enumerate([cov["baseline_cases"], cov["rerun_cases"]]):
        ax.text(i, v + 0.05, str(v), ha="center", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)

    ax = axes[1]
    counts = cov["comparison"]["counts"]
    keys = ["missing", "drift", "critical", "compared_cases"]
    vals = [counts.get(k, 0) for k in keys]
    ax.bar(keys, vals, color=["#c62828", "#ef6c00", "#6a1b9a", "#2e7d32"], width=0.55)
    for i, v in enumerate(vals):
        ax.text(i, v + 0.1, str(v), ha="center", fontsize=9)
    ax.set_title(f"(b) regression alerts · status={cov['comparison']['status']} · "
                 f"exit_code={cov['comparison']['exit_code']}\n"
                 f"(v1.0 measured: status=noted, exit_code=0)", fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    p3 = figdir / "fig3_coverage_loss.png"
    fig.savefig(p3, dpi=140)
    plt.close(fig)
    paths.append(str(p3))
    return paths


MappingLike = Dict[str, Any]


def _integrity_checks(root: pathlib.Path, clean_run: str) -> Dict[str, bool]:
    """三类工件级破绽的检出证明（缺失 / 多余 / 算例自哈希）。"""
    src = pathlib.Path(clean_run)
    out: Dict[str, bool] = {}
    work = root / "drills" / "integrity_probe"
    results: Dict[str, Any] = {}

    def _fresh() -> pathlib.Path:
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(src, work)
        return work

    d = _fresh()
    (d / "result.json").unlink()
    results["missing"] = art.verify_manifest(d)
    out["missing_detected"] = bool(results["missing"]["missing"])

    d = _fresh()
    (d / "cases" / "injected.json").write_text("{}")
    results["extra"] = art.verify_manifest(d)
    out["extra_detected"] = "cases/injected.json" in results["extra"]["extra"]

    d = _fresh()
    target = sorted((d / "cases").glob("*.json"))[0]
    payload = json.loads(target.read_text())
    payload["title"] = payload.get("title", "") + " (edited)"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    art.write_manifest(d, {"suite": "probe", "run_id": "probe", "passed": None},
                       {"engine_version": ENGINE_VERSION, "config_sha256": None,
                        "git_sha": None, "generated_at": art.utc_now()})
    # 清单重建后 sha256 层自洽，只剩"算例自带 artifact_sha256"这一层能抓到局部改写
    results["self_hash"] = art.verify_manifest(d)
    out["self_hash_detected"] = bool(results["self_hash"]["case_self_hash_errors"])

    (root / "drills" / "integrity_probe_verdicts.json").write_text(
        json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "root"}
                    for k, v in results.items()}, indent=2, ensure_ascii=False))
    if work.exists():
        shutil.rmtree(work)
    return out


def _wall_clock_entry(cfg_path: pathlib.Path, out_root: pathlib.Path,
                      run_id: str) -> Dict[str, Any]:
    cfg = cfgmod.load_config(cfg_path)
    started = time.monotonic()
    summary = run.run_config(cfg_path, out_root=out_root / "runs", run_id=run_id)
    wall = time.monotonic() - started
    declared = {c["id"]: c["timeout_s"] for c in cfg["cases"]}
    cases = {cid: {"timing_s": info["timing_s"], "status": info["status"],
                   "declared_timeout_s": declared.get(cid)}
             for cid, info in summary["timings"].items()}
    requested_stall = max(float(c.get("inputs", {}).get("seconds", 0.0)) for c in cfg["cases"])
    return {
        "run": {k: summary[k] for k in ("suite", "run_id", "passed", "exit_code",
                                        "suite_metrics", "integrity")},
        "cases": cases,
        "_engine_wall_s": round(wall, 3),
        "_requested_stall_s": requested_stall,
        "_verdict_ok": summary["exit_code"] == run.EXIT_GATE_FAIL,
        "artifacts": {"out_dir": summary["paths"]["out_dir"],
                      "result": summary["paths"]["result"],
                      "result_sha256": _sha(summary["paths"]["result"]),
                      "manifest": summary["paths"]["manifest"],
                      "manifest_sha256": summary["paths"]["manifest_sha256"],
                      "timings": summary["paths"]["timings"]},
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description="benchmark 引擎成熟化增量演示（零领域语义）")
    ap.add_argument("--out", default="artifacts/benchmark_runner_m9703")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # 主线：正示例 suite（建基线 + 回验）
    base = run.run_config(CONFIG_DIR / "fx_integration.json", out_root=root / "runs",
                          baseline_path=root / "drills" / "fx_integration_baseline.json",
                          update_baseline=True, run_id="integration-baseline")

    wall = {
        "fx_timeout_negative": _wall_clock_entry(
            CONFIG_DIR / "fx_timeout_negative.json", root, "timeout-negative"),
        "fx_budget_negative": _wall_clock_entry(
            CONFIG_DIR / "fx_budget_negative.json", root, "budget-negative"),
    }
    coverage = _drill_coverage(root)
    tamper = _drill_tamper(root, base["paths"]["out_dir"])
    integrity = _integrity_checks(root, base["paths"]["out_dir"])
    guard = domain_guard.scan()

    evidence: Dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "generated_at": art.utc_now(),
        "positive_suite": {
            "exit_code": base["exit_code"], "passed": base["passed"],
            "suite_metrics": base["suite_metrics"],
            "regression_status": base["regression"]["status"],
            "integrity_ok": base["integrity"]["ok"],
            "artifacts": {"out_dir": base["paths"]["out_dir"],
                          "manifest": base["paths"]["manifest"],
                          "manifest_sha256": base["paths"]["manifest_sha256"],
                          "result_sha256": _sha(base["paths"]["result"])},
        },
        "wall_clock": wall,
        "coverage": coverage,
        "tamper": tamper,
        "integrity_checks": integrity,
        "domain_guard": {"ok": guard["ok"], "status": guard["status"],
                         "scanned_files": guard["scanned_files"], "n_hits": guard.get("n_hits", 0)},
    }
    figures = _figure(root, evidence)
    evidence["figures"] = [{"path": _rel(root, f), "sha256": _sha(f)} for f in figures]
    out = root / "evidence.json"
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({k: evidence[k] for k in ("engine_version", "positive_suite",
                                              "integrity_checks", "domain_guard")},
                     indent=2, ensure_ascii=False, default=str))
    print(f"evidence: {out} sha256={_sha(out)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())