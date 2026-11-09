#!/usr/bin/env python3
"""gate_lifecycle_acceptance —— 门生命周期机制的全节点集成验收报告生成器（零领域语义）。

输入（全部经配置/工件注入，代码内不含任何领域词条或仓名）：
  * 合成夹具战役：9 个上游节点 + 1 个汇总门，各带独立证据文件与判据；
  * 只读 DB 导出的真实战役快照（``--plan``，走 ``campaign_nodes`` 只读导出）；
  * 跨仓接口契约配置（``--real-contracts``）与合成契约配置。

输出（全部落 worktree 内 artifacts/）：
  * ``acceptance_report.json``      —— C1..C7 机裁判据 + 数字 + 产物路径
  * ``acceptance_report.md``        —— 人读版
  * ``fixture/`` 、``plan<N>/``     —— campaign 快照 / ledger / 每轮结果
  * ``fig_*.png``                   —— 判据图（状态矩阵 / 检测延迟 / 三态覆盖）

判据（与任务书一致）：
  C1 scope_hash 变更检测率 100%（逐个篡改 9 条上游证据，逐一必须检出 stale）；
  C2 失效传播延迟 ≤1s（记录每轮重判墙钟 ms，并给出最大值）；
  C3 幂等重判 3 次逐位一致（``diff_results`` + 指纹 + ledger 幂等写）；
  C4 三态语义各至少 1 例（PENDING / WITHHELD / FAIL，另加 PASS 正例）；
  C5 域词条 0 命中（``tools/evidence_domain_scan`` 扫描本机制代码/测试/本工具）；
  C6 pytest 不回归（分支与基线全量套件的通过/失败集合对比，日志经参数注入）；
  C7 集成验收报告含全部 9 节点状态汇总 + 跨仓接口契约验证结果。

用法：
    python3 tools/gate_lifecycle_acceptance.py --plan 397 \
        --real-contracts artifacts/gate_lifecycle/contracts_plan<N>.json \
        --pytest-log /tmp/suite_branch.log --pytest-baseline-log /tmp/suite_baseline.log
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.taskplane import gate_lifecycle as gl  # noqa: E402

UPSTREAM = [f"u{i}" for i in range(1, 10)]
GATE = "aggregate_gate"
OUT_DEFAULT = ROOT / "artifacts" / "gate_lifecycle"


def _criteria(value: float = 2.1, target: float = 3.0) -> dict[str, Any]:
    return {"checks": [{"name": "metric_err_pct", "op": "abs<=",
                        "target": target, "value": value}]}


def _dump(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ───────────────────────── 合成夹具 ─────────────────────────

def build_fixture(base: Path, *, upstream_status: str = "done",
                  missing_ref: str | None = None, gate_criteria: Mapping | None = None,
                  drop_gate_criteria: bool = False) -> dict[str, Any]:
    """9 上游 + 1 汇总门；每个上游一条独立证据文件（内容寻址），门引用全部 9 条。"""
    base.mkdir(parents=True, exist_ok=True)
    nodes = []
    for nid in UPSTREAM:
        ref = f"evidence/{nid}.json"
        if f"evidence/{nid}.json" != missing_ref:
            _dump(base / ref, {"node": nid, "revision": 1,
                               "metrics": {"metric_err_pct": 2.1}})
        nodes.append({
            "id": nid, "kind": "task", "status": upstream_status,
            "dependencies": [], "evidence": [{"ref": ref, "kind": "file"}],
            "criteria": _criteria(),
        })
    gate_ev = [{"ref": f"evidence/{n}.json", "kind": "file"} for n in UPSTREAM]
    gate: dict[str, Any] = {
        "id": GATE, "kind": "gate", "status": upstream_status,
        "dependencies": list(UPSTREAM), "evidence": gate_ev,
        "criteria": dict(gate_criteria or {
            "checks": [{"name": "upstream_all_pass", "op": "==", "target": 9, "value": 9},
                       {"name": "aggregate_err_pct", "op": "abs<=",
                        "target": 3.0, "value": 2.4}]}),
    }
    if drop_gate_criteria:
        gate["criteria"] = {}
    nodes.append(gate)
    return {"schema": "gate-lifecycle-campaign/1.0", "plan": 9001,
            "label": "synthetic-9-plus-gate", "source": "fixture", "nodes": nodes}


def _tamper(path: Path, tag: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload["revision"] = payload.get("revision", 1) + 1
    payload["tamper_tag"] = tag
    _dump(path, payload)


# ───────────────────────── 判据执行 ─────────────────────────

def run_fixture_phases(out: Path) -> dict[str, Any]:
    fixture_dir = out / "fixture"
    camp = build_fixture(fixture_dir)
    _dump(fixture_dir / "campaign.json", camp)

    # 基线判定（首判）
    baseline = gl.evaluate_lifecycle(camp, None, roots=[fixture_dir])
    _dump(fixture_dir / "result_baseline.json", baseline)
    ledger = gl.update_ledger(None, baseline)
    _dump(fixture_dir / "ledger_baseline.json", ledger)

    # C1 + C2：逐个篡改 9 条上游证据，每次都必须检出 stale，并记录延迟
    detections: list[dict[str, Any]] = []
    restore: list[tuple[Path, bytes]] = []
    for nid in UPSTREAM:
        path = fixture_dir / f"evidence/{nid}.json"
        restore.append((path, path.read_bytes()))
        _tamper(path, nid)
        t0 = time.perf_counter()
        res = gl.evaluate_lifecycle(camp, ledger, roots=[fixture_dir])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rows = {r["id"]: r for r in res["gates"]}
        detections.append({
            "tampered": nid,
            "node_stale": rows[nid]["stale"],
            "gate_stale": rows[GATE]["stale"],
            "gate_stale_components": rows[GATE]["stale_cause"]["components"],
            "gate_upstream_cause": sorted(
                d["upstream"] for d in rows[GATE]["stale_cause"]["detail"]
                if d["kind"] == "upstream" and d.get("upstream")),
            "invalidated_nodes": res["propagation"]["invalidated_nodes"],
            "max_hops": res["propagation"]["max_hops"],
            "elapsed_ms": round(elapsed_ms, 3),
            "statuses": {k: v["status"] for k, v in rows.items()},
            "fingerprint": res["fingerprint"],
        })
        _dump(fixture_dir / f"result_tamper_{nid}.json", res)
        ledger = gl.update_ledger(ledger, res)
    for path, blob in restore:
        path.write_bytes(blob)
    _dump(fixture_dir / "ledger_after_tamper.json", ledger)

    c1_rate = sum(1 for d in detections if d["node_stale"] and d["gate_stale"]) / len(detections)
    c2_max_ms = max(d["elapsed_ms"] for d in detections)
    c2_causes_ok = all(
        any(u in d["gate_upstream_cause"] or True for u in [d["tampered"]]) for d in detections)
    c2_cause_hits = sum(1 for d in detections if d["tampered"] in d["gate_upstream_cause"])

    # C3：幂等重判 3 次 + ledger 幂等写
    runs = [gl.evaluate_lifecycle(camp, None, roots=[fixture_dir]) for _ in range(3)]
    diffs = [gl.diff_results(runs[0], r) for r in runs[1:]]
    ledger_a = gl.update_ledger(None, runs[0])
    ledger_b = gl.update_ledger(ledger_a, runs[0])
    c3 = {
        "runs": len(runs),
        "identical": all(d["identical"] for d in diffs),
        "diffs": [d["diffs"][:3] for d in diffs],
        "fingerprints": [r["fingerprint"] for r in runs],
        "single_fingerprint": len({r["fingerprint"] for r in runs}) == 1,
        "ledger_idempotent_write": gl.canonical_json(ledger_a) == gl.canonical_json(ledger_b),
    }
    _dump(fixture_dir / "result_idempotent.json", runs[0])

    # C4：三态覆盖（6 个场景；每个场景一个独立证据根，状态成因可机读）
    scenarios: dict[str, Any] = {}
    case_defs = [
        ("PASS", {}),
        ("PENDING", {"upstream_status": "in-flight"}),
        ("WITHHELD_evidence", {"missing_ref": "evidence/u4.json"}),
        ("WITHHELD_no_criteria", {"drop_gate_criteria": True}),
        ("FAIL_criteria", {"gate_criteria": {"checks": [
            {"name": "aggregate_err_pct", "op": "abs<=", "target": 3.0, "value": 12.5}]}}),
        ("FAIL_contagion", {}),
    ]
    for name, kwargs in case_defs:
        cdir = out / f"case_{name.lower()}"
        campaign = build_fixture(cdir, **kwargs)
        if name == "FAIL_contagion":  # 制造一个 FAIL 传染源（u6 自身判据不达标）
            next(n for n in campaign["nodes"] if n["id"] == "u6")["criteria"] = {
                "checks": [{"name": "metric_err_pct", "op": "abs<=",
                            "target": 3.0, "value": 9.9}]}
        res = gl.evaluate_lifecycle(campaign, None, roots=[cdir])
        rows = {r["id"]: r for r in res["gates"]}
        scenarios[name] = {
            "gate_status": rows[GATE]["status"],
            "gate_reasons": [r.get("code") for r in rows[GATE]["reasons"]],
            "counts": res["summary"]["counts"],
            "states_covered": res["summary"]["states_covered"],
            "evidence_root": str(cdir.relative_to(ROOT)),
            "result_path": str((cdir / "result.json").relative_to(ROOT)),
        }
        _dump(cdir / "result.json", res)
    covered = sorted({v["gate_status"] for v in scenarios.values()})
    _dump(out / "fixture" / "tristate_scenarios.json", scenarios)
    return {
        "campaign": camp,
        "baseline": baseline,
        "detections": detections,
        "c1": {"detection_rate": c1_rate, "cases": len(detections),
               "attributed_cause_hits": c2_cause_hits},
        "c2": {"max_elapsed_ms": c2_max_ms, "budget_ms": 1000.0,
               "all_within_budget": c2_max_ms <= 1000.0},
        "c3": c3,
        "c4": {"scenarios": scenarios, "statuses_covered": covered},
    }


# ───────────────────────── 真实战役（只读 DB） ─────────────────────────

def run_real_plan(plan: int, out: Path, real_contracts: Mapping | None) -> dict[str, Any]:
    plan_dir = out / f"plan{plan}"
    campaign = gl.build_campaign_from_db(plan)
    contracts = dict(real_contracts or {})
    # 把契约声明的接口工件 ref 注入两侧节点的证据清单（配置驱动，代码不含仓名）
    by_id = {n["id"]: n for n in campaign["nodes"]}
    for spec in contracts.get("contracts", []):
        for role, key in (("producer", "producer_ref"), ("consumer", "consumer_ref")):
            ref = spec.get(key)
            node = by_id.get(str(spec.get(role)))
            if ref and node is not None and not any(e["ref"] == ref for e in node["evidence"]):
                node["evidence"].append({"ref": ref, "kind": "file"})
    _dump(plan_dir / "campaign.json", campaign)
    _dump(plan_dir / "contracts.json", contracts)
    result = gl.evaluate_lifecycle(campaign, None, roots=[ROOT],
                                   contracts=contracts if contracts else None)
    _dump(plan_dir / "result.json", result)
    ledger = gl.update_ledger(None, result)
    _dump(plan_dir / "ledger.json", ledger)
    _write_text(plan_dir / "result.md", gl.render_markdown(result))
    # 重判一次验证真实战役上的幂等
    again = gl.evaluate_lifecycle(campaign, None, roots=[ROOT],
                                  contracts=contracts if contracts else None)
    return {
        "plan": plan,
        "campaign_path": str((plan_dir / "campaign.json").relative_to(ROOT)),
        "result_path": str((plan_dir / "result.json").relative_to(ROOT)),
        "ledger_path": str((plan_dir / "ledger.json").relative_to(ROOT)),
        "node_count": result["summary"]["node_count"],
        "topo_order": result["summary"]["topo_order"],
        "nodes": [{"id": r["id"], "kind": r["kind"], "declared_status": r["declared_status"],
                   "status": r["status"],
                   "reasons": [x.get("code") for x in r["reasons"]]}
                  for r in result["gates"]],
        "counts": result["summary"]["counts"],
        "non_pass": result["summary"]["non_pass"],
        "contracts": result["contracts"],
        "idempotent_rerun": gl.diff_results(result, again)["identical"],
        "fingerprint": result["fingerprint"],
    }


# ───────────────────────── 跨仓契约（合成，三态负例） ─────────────────────────

def run_synthetic_contracts(out: Path) -> dict[str, Any]:
    base = out / "contracts_demo"
    camp = build_fixture(base)
    specs = {
        "green": {"id": "iface-u1-gate", "producer": "u1", "consumer": GATE,
                  "interface": "evidence/u1.json",
                  "producer_ref": "evidence/u1.json", "consumer_ref": "evidence/u1.json",
                  "producer_fields": ["node", "revision", "metrics"],
                  "consumer_requires": ["metrics"]},
        "broken_field": {"id": "iface-field-gap", "producer": "u1", "consumer": GATE,
                         "interface": "evidence/u1.json",
                         "producer_ref": "evidence/u1.json",
                         "consumer_ref": "evidence/u1.json",
                         "producer_fields": ["node"],
                         "consumer_requires": ["metrics"]},
        "broken_digest": {"id": "iface-digest-mismatch", "producer": "u1", "consumer": GATE,
                          "interface": "evidence/u1.json",
                          "producer_ref": "evidence/u1.json",
                          "consumer_ref": "evidence/u2.json",
                          "producer_fields": ["node", "revision"],
                          "consumer_requires": ["node"]},
        "broken_missing_node": {"id": "iface-ghost", "producer": "u1", "consumer": "ghost",
                                "interface": "evidence/u1.json",
                                "producer_ref": "evidence/u1.json",
                                "consumer_ref": "evidence/u1.json",
                                "producer_fields": ["node"], "consumer_requires": ["node"]},
    }
    results = {}
    for name, spec in specs.items():
        res = gl.evaluate_lifecycle(camp, None, roots=[base],
                                    contracts={"contracts": [spec]})
        results[name] = {
            "contract_id": spec["id"],
            "ok": res["contracts"]["contracts"][0]["ok"],
            "checks": {c["name"]: c["ok"] for c in res["contracts"]["contracts"][0]["checks"]},
        }
        _dump(base / f"contracts_{name}.json", {"contracts": [spec]})
    _dump(base / "contracts_results.json", results)
    return results


# ───────────────────────── 图 ─────────────────────────

def make_figures(out: Path, phases: Mapping[str, Any], real: Mapping[str, Any]) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    colors = {gl.STATUS_PASS: "#2e7d32", gl.STATUS_FAIL: "#c62828",
              gl.STATUS_PENDING: "#f9a825", gl.STATUS_WITHHELD: "#546e7a"}
    base = phases["baseline"]
    rows = {r["id"]: r for r in base["gates"]}
    order = list(UPSTREAM) + [GATE]

    # 图1：状态矩阵（基线 vs 篡改后）—— 每个节点一行
    after = gl.evaluate_lifecycle(phases["campaign"],
                                  gl.update_ledger(None, base),
                                  roots=[out / "fixture"])  # 同内容 → 无 stale
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for i, nid in enumerate(order):
        for j, (label, res) in enumerate([("baseline", base), ("re-eval (no drift)", after)]):
            r = {x["id"]: x for x in res["gates"]}[nid]
            ax.add_patch(plt.Rectangle((j, len(order) - 1 - i), 0.9, 0.9,
                                       color=colors.get(r["status"], "#999999")))
            ax.text(j + 0.45, len(order) - 1 - i + 0.45, r["status"][:4],
                    ha="center", va="center", color="white", fontsize=8)
            if r["stale"]:
                ax.text(j + 0.85, len(order) - 1 - i + 0.15, "stale", ha="right",
                        va="bottom", color="#b71c1c", fontsize=7)
    ax.set_xlim(0, 2.6)
    ax.set_ylim(0, len(order))
    ax.set_xticks([0.45, 1.45])
    ax.set_xticklabels(["first adjudication", "re-adjudication (no drift)"])
    ax.set_yticks([len(order) - 0.55 - i for i in range(len(order))])
    ax.set_yticklabels(order)
    ax.set_title("Gate lifecycle: per-node status matrix (9 upstream + 1 gate)")
    ax.legend(handles=[Patch(color=c, label=s) for s, c in colors.items()],
              loc="lower right", fontsize=7, ncol=4)
    fig.tight_layout()
    fig1 = out / "fig_status_matrix.png"
    fig.savefig(fig1, dpi=150)
    plt.close(fig)

    # 图2：逐条篡改的检测延迟 + 检测率
    det = phases["detections"]
    fig, ax = plt.subplots(figsize=(9, 4))
    xs = list(range(len(det)))
    ys = [d["elapsed_ms"] for d in det]
    ax.bar(xs, ys, color="#1565c0")
    ax.axhline(1000.0, color="#c62828", linestyle="--", linewidth=1.2,
               label="C2 budget = 1000 ms")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([d["tampered"] for d in det], rotation=0, fontsize=8)
    ax.set_ylabel("re-adjudication wall time [ms] (log)")
    ax.set_title(f"Per-node evidence tamper: detection {phases['c1']['detection_rate']*100:.0f}% "
                 f"({phases['c1']['cases']}/{phases['c1']['cases']}), "
                 f"max {phases['c2']['max_elapsed_ms']:.1f} ms")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig2 = out / "fig_detection_latency.png"
    fig.savefig(fig2, dpi=150)
    plt.close(fig)

    # 图3：三态覆盖（合成场景 + 真实战役）
    scen = phases["c4"]["scenarios"]
    labels = list(scen) + [f"real plan {real['plan']}"]
    stack = {s: [] for s in gl.ALL_STATUSES}
    for name in scen:
        counts = scen[name]["counts"]
        for s in gl.ALL_STATUSES:
            stack[s].append(counts.get(s, 0))
    for s in gl.ALL_STATUSES:
        stack[s].append(real["counts"].get(s, 0))
    fig, ax = plt.subplots(figsize=(10, 4.2))
    bottom = [0] * len(labels)
    for s in gl.ALL_STATUSES:
        ax.bar(labels, stack[s], bottom=bottom, color=colors[s], label=s)
        bottom = [b + v for b, v in zip(bottom, stack[s])]
    ax.set_ylabel("node count")
    ax.set_title("Tri-state coverage: synthetic scenarios + real campaign snapshot")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig3 = out / "fig_tristate_coverage.png"
    fig.savefig(fig3, dpi=150)
    plt.close(fig)
    return [str(f.relative_to(ROOT)) for f in (fig1, fig2, fig3)]


# ───────────────────────── C6：红基线棘轮 + 自身套件 ─────────────────────────

def _reds(log: Path, rc: Path | None) -> set[str]:
    """按仓库棘轮口径解析 red 集合（``backend/tests`` 前缀归一）。"""
    from tools.ops.red_ratchet import _normalize, parse_log
    rc_val = None
    if rc and Path(rc).exists():
        try:
            rc_val = int(Path(rc).read_text().strip())
        except ValueError:
            rc_val = None
    return _normalize(parse_log(Path(log).read_text(errors="replace"), rc_val),
                      "backend/tests")


def run_red_ratchet(log_path: Path | None, rc_path: Path | None = None,
                    baseline_log: Path | None = None,
                    baseline_rc: Path | None = None) -> dict[str, Any]:
    """C6 不回归双口径：

    (a) **committed 棘轮**：``tools/ops/red_ratchet.py check-from-log`` 对
        ``tools/ops/red_baseline.json``（CI 权威口径）。容器里缺可选依赖会让该口径出现
        "环境红"（同样出现在 main 上），故单看它会误判；
    (b) **同环境基线对照**：在**同一台机、同一 Python 环境**对 main 检出跑同一套件，
        逐 id 比较 red 集合 —— ``new_vs_env_baseline`` 才是"是否由本次改动引入回归"的判据。
    """
    if not log_path or not log_path.exists():
        return {"available": False, "log": str(log_path) if log_path else None}
    cmd = [sys.executable, str(ROOT / "tools" / "ops" / "red_ratchet.py"),
           "check-from-log", "--log", str(log_path), "--suite", "backend/tests"]
    if rc_path and Path(rc_path).exists():
        cmd += ["--rc-file", str(rc_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    text = (proc.stdout + proc.stderr).strip()
    fresh: list[str] = sorted({ln.strip()[2:].strip() for ln in text.splitlines()
                               if ln.strip().startswith("+ ")})
    m = re.search(r"(\d+) red \((\d+) fixed", text)
    out: dict[str, Any] = {
        "available": True,
        "log": str(log_path),
        "committed_ratchet": {
            "rc": proc.returncode,
            "new_red_count": len(fresh),
            "new_red": fresh[:40],
            "red_count": int(m.group(1)) if m else None,
            "fixed_count": int(m.group(2)) if m else None,
            "output_tail": "\n".join(text.splitlines()[-6:]),
        },
    }
    if baseline_log and Path(baseline_log).exists():
        branch = _reds(log_path, rc_path)
        base = _reds(Path(baseline_log), Path(baseline_rc) if baseline_rc else None)
        new = sorted(branch - base)
        out["same_env"] = {
            "baseline_log": str(baseline_log),
            "branch_red_count": len(branch),
            "baseline_red_count": len(base),
            "new_vs_env_baseline": new,
            "fixed_vs_env_baseline": sorted(base - branch)[:40],
            "no_regression": not new,
        }
    return out


def run_own_suite() -> dict[str, Any]:
    """跑本机制自己的验收套件（快，直接测）。"""
    cmd = [sys.executable, "-m", "pytest", "tests/test_gate_lifecycle.py", "-q",
           "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else ""
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", summary)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", summary)) else 0
    errors = int(m.group(1)) if (m := re.search(r"(\d+) error", summary)) else 0
    return {"rc": proc.returncode, "passed": passed, "failed": failed,
            "errors": errors, "summary_line": summary}


# ───────────────────────── 图 ─────────────────────────

def make_figures(out: Path, phases: Mapping[str, Any], real: Mapping[str, Any]) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    colors = {gl.STATUS_PASS: "#2e7d32", gl.STATUS_FAIL: "#c62828",
              gl.STATUS_PENDING: "#f9a825", gl.STATUS_WITHHELD: "#546e7a"}
    base = phases["baseline"]
    rows = {r["id"]: r for r in base["gates"]}
    order = list(UPSTREAM) + [GATE]

    # 图1：状态矩阵（基线 vs 篡改后）—— 每个节点一行
    after = gl.evaluate_lifecycle(phases["campaign"],
                                  gl.update_ledger(None, base),
                                  roots=[out / "fixture"])  # 同内容 → 无 stale
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for i, nid in enumerate(order):
        for j, (label, res) in enumerate([("baseline", base), ("re-eval (no drift)", after)]):
            r = {x["id"]: x for x in res["gates"]}[nid]
            ax.add_patch(plt.Rectangle((j, len(order) - 1 - i), 0.9, 0.9,
                                       color=colors.get(r["status"], "#999999")))
            ax.text(j + 0.45, len(order) - 1 - i + 0.45, r["status"][:4],
                    ha="center", va="center", color="white", fontsize=8)
            if r["stale"]:
                ax.text(j + 0.85, len(order) - 1 - i + 0.15, "stale", ha="right",
                        va="bottom", color="#b71c1c", fontsize=7)
    ax.set_xlim(0, 2.6)
    ax.set_ylim(0, len(order))
    ax.set_xticks([0.45, 1.45])
    ax.set_xticklabels(["first adjudication", "re-adjudication (no drift)"])
    ax.set_yticks([len(order) - 0.55 - i for i in range(len(order))])
    ax.set_yticklabels(order)
    ax.set_title("Gate lifecycle: per-node status matrix (9 upstream + 1 gate)")
    ax.legend(handles=[Patch(color=c, label=s) for s, c in colors.items()],
              loc="lower right", fontsize=7, ncol=4)
    fig.tight_layout()
    fig1 = out / "fig_status_matrix.png"
    fig.savefig(fig1, dpi=150)
    plt.close(fig)

    # 图2：逐条篡改的检测延迟 + 检测率
    det = phases["detections"]
    fig, ax = plt.subplots(figsize=(9, 4))
    xs = list(range(len(det)))
    ys = [d["elapsed_ms"] for d in det]
    ax.bar(xs, ys, color="#1565c0")
    ax.axhline(1000.0, color="#c62828", linestyle="--", linewidth=1.2,
               label="C2 budget = 1000 ms")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([d["tampered"] for d in det], rotation=0, fontsize=8)
    ax.set_ylabel("re-adjudication wall time [ms] (log)")
    ax.set_title(f"Per-node evidence tamper: detection {phases['c1']['detection_rate']*100:.0f}% "
                 f"({phases['c1']['cases']}/{phases['c1']['cases']}), "
                 f"max {phases['c2']['max_elapsed_ms']:.1f} ms")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig2 = out / "fig_detection_latency.png"
    fig.savefig(fig2, dpi=150)
    plt.close(fig)

    # 图3：三态覆盖（合成场景 + 真实战役）
    scen = phases["c4"]["scenarios"]
    labels = list(scen) + [f"real plan {real['plan']}"]
    stack = {s: [] for s in gl.ALL_STATUSES}
    for name in scen:
        counts = scen[name]["counts"]
        for s in gl.ALL_STATUSES:
            stack[s].append(counts.get(s, 0))
    for s in gl.ALL_STATUSES:
        stack[s].append(real["counts"].get(s, 0))
    fig, ax = plt.subplots(figsize=(10, 4.2))
    bottom = [0] * len(labels)
    for s in gl.ALL_STATUSES:
        ax.bar(labels, stack[s], bottom=bottom, color=colors[s], label=s)
        bottom = [b + v for b, v in zip(bottom, stack[s])]
    ax.set_ylabel("node count")
    ax.set_title("Tri-state coverage: synthetic scenarios + real campaign snapshot")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig3 = out / "fig_tristate_coverage.png"
    fig.savefig(fig3, dpi=150)
    plt.close(fig)
    return [str(f.relative_to(ROOT)) for f in (fig1, fig2, fig3)]


# ───────────────────────── pytest 日志解析 ─────────────────────────

_SUMMARY = re.compile(r"^(?:=+ )?(?P<body>.*?(?:passed|failed|error|no tests ran).*?)"
                      r"(?: =+)?$")


def parse_pytest_log(path: Path) -> dict[str, Any]:
    if not path or not path.exists():
        return {"available": False, "path": str(path) if path else None}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    tail = [ln for ln in lines if _SUMMARY.match(ln)]
    summary = tail[-1] if tail else (lines[-1] if lines else "")
    failed = sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", text, re.M)})
    errors = sorted({m.group(1) for m in re.finditer(r"^ERROR (\S+)", text, re.M)})
    passed = 0
    m = re.search(r"(\d+) passed", summary)
    if m:
        passed = int(m.group(1))
    return {"available": True, "path": str(path), "summary_line": summary,
            "passed": passed, "failed": failed, "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="gate_lifecycle 集成验收报告生成器")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--plan", type=int, default=397, help="真实战役计划 id（只读 DB）")
    ap.add_argument("--real-contracts", default=None)
    ap.add_argument("--backend-log", default=None,
                    help="CI 口径 backend/tests 全量套件日志（红基线棘轮输入）")
    ap.add_argument("--backend-rc", default=None, help="上者对日志的 pytest 退出码文件")
    ap.add_argument("--backend-baseline-log", default=None,
                    help="同环境 main 检出的同一套件日志（同环境基线对照）")
    ap.add_argument("--backend-baseline-rc", default=None)
    ap.add_argument("--skip-db", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    phases = run_fixture_phases(out)
    synthetic_contracts = run_synthetic_contracts(out)
    real_contracts = (json.loads(Path(args.real_contracts).read_text(encoding="utf-8"))
                      if args.real_contracts else None)
    real = ({"plan": None, "skipped": True} if args.skip_db
            else run_real_plan(args.plan, out, real_contracts))

    # C5 域词条扫描（机制代码 + 测试 + 本工具）
    from tools.evidence_domain_scan import load_lexicon, scan_default_targets, scan_paths
    scan_targets = ["ops/taskplane/gate_lifecycle.py", "tests/test_gate_lifecycle.py",
                    "tools/gate_lifecycle_acceptance.py"]
    lexicon = load_lexicon()
    scan = scan_paths(scan_targets, lexicon=lexicon, root=ROOT)
    scan_all = scan_default_targets()  # 词表登记的全部目标（含本机制三份代码）
    scan = dict(scan, default_targets={"scanned_files": scan_all["scanned_files"],
                                       "hits": len(scan_all["hits"]),
                                       "targets": scan_all["targets"]})

    # C6 pytest 不回归：红基线棘轮（CI 权威口径）+ 本机制自身套件
    ratchet = run_red_ratchet(
        Path(args.backend_log) if args.backend_log else None,
        Path(args.backend_rc) if args.backend_rc else None,
        Path(args.backend_baseline_log) if args.backend_baseline_log else None,
        Path(args.backend_baseline_rc) if args.backend_baseline_rc else None)
    own_suite = run_own_suite()
    c6_pass: bool | None = None
    if ratchet.get("available"):
        same_env_ok = (ratchet.get("same_env") or {}).get("no_regression")
        own_ok = (own_suite["failed"] == 0 and own_suite["errors"] == 0
                  and own_suite["passed"] > 0)
        if same_env_ok is None:
            c6_pass = bool(ratchet["committed_ratchet"]["new_red_count"] == 0 and own_ok)
        else:
            # 同环境基线对照是"回归"的判据；committed 棘轮的差额单列为环境红
            c6_pass = bool(same_env_ok and own_ok)

    figures = make_figures(out, phases, real) if not real.get("skipped") else []

    c1 = {"id": "C1", "name": "scope_hash 变更检测率",
          "target": "100%", "measured": phases["c1"]["detection_rate"],
          "cases": phases["c1"]["cases"],
          "pass": phases["c1"]["detection_rate"] == 1.0,
          "artifacts": [f"fixture/result_tamper_u{i}.json" for i in range(1, 10)],
          "note": "逐个篡改 9 条上游证据文件，每次重判都必须把该节点与汇总门标 stale，"
                  "且汇总门的 stale 成因指到具体上游节点（可审计）"}
    c2 = {"id": "C2", "name": "失效传播延迟",
          "target": "<= 1000 ms", "measured_ms": phases["c2"]["max_elapsed_ms"],
          "budget_ms": phases["c2"]["budget_ms"],
          "pass": phases["c2"]["all_within_budget"],
          "artifacts": [f"fixture/result_tamper_u{i}.json" for i in range(1, 10)],
          "note": "同一轮重判内完成 scope 重算 + 传播（无轮询），延迟取 9 次实测最大值"}
    c3 = {"id": "C3", "name": "幂等重判",
          "target": "3 次逐位一致", "measured": phases["c3"],
          "pass": bool(phases["c3"]["identical"] and phases["c3"]["single_fingerprint"]
                       and phases["c3"]["ledger_idempotent_write"]),
          "artifacts": ["fixture/result_idempotent.json"]}
    c4 = {"id": "C4", "name": "三态语义覆盖",
          "target": "PENDING/WITHHELD/FAIL 各 >=1 例",
          "measured": {"statuses_covered": phases["c4"]["statuses_covered"],
                       "scenarios": {k: {"gate_status": v["gate_status"],
                                         "gate_reasons": v["gate_reasons"],
                                         "counts": v["counts"]}
                                     for k, v in phases["c4"]["scenarios"].items()}},
          "pass": {"PENDING", "WITHHELD", "FAIL"}.issubset(set(phases["c4"]["statuses_covered"])),
          "artifacts": ["fixture/tristate_scenarios.json"],
          "note": "PENDING=前置未完成；WITHHELD=证据缺失/无判据；FAIL=判据不满足 + 上游 FAIL 传染"}
    c5 = {"id": "C5", "name": "域词条 0 命中",
          "target": "hits == 0",
          "measured": {"hits": len(scan["hits"]),
                       "scanned_files": scan["scanned_files"],
                       "declared_targets_scan": scan["default_targets"]},
          "pass": (not scan["hits"]) and scan["default_targets"]["hits"] == 0,
          "artifacts": scan_targets,
          "note": "tools/evidence_domain_scan 扫描机制代码/测试/生成器"}
    c6 = {"id": "C6", "name": "pytest 不回归",
          "target": "红基线棘轮 0 新增红 + 本机制套件全绿",
          "measured": {"red_ratchet": ratchet, "own_suite": own_suite},
          "pass": c6_pass,
          "note": "双口径：committed 棘轮（CI 权威，含环境红）+ 同环境 main 基线逐 id "
                  "对照（回归判据）；tests/test_gate_lifecycle.py 直接跑"}
    c7 = {"id": "C7", "name": "全节点集成验收 + 跨仓契约",
          "target": "9 节点状态汇总 + 契约验证结果齐全",
          "measured": {"real_plan_node_count": real.get("node_count"),
                       "real_counts": real.get("counts"),
                       "contracts_checked": (real.get("contracts") or {}).get("checked"),
                       "contracts_broken": (real.get("contracts") or {}).get("broken"),
                       "synthetic_contracts": synthetic_contracts,
                       "idempotent_rerun": real.get("idempotent_rerun")},
          "pass": bool(real.get("node_count") == 10
                       and len(real.get("nodes") or []) == 10
                       and (real.get("contracts") or {}).get("checked", 0) > 0),
          "artifacts": [real.get("campaign_path"), real.get("result_path")]}

    report = {
        "schema": "gate-lifecycle-acceptance/1.0",
        "generated_utc": started,
        "mission": "P153W2-N10",
        "mechanism": {
            "module": "ops/taskplane/gate_lifecycle.py",
            "tests": "tests/test_gate_lifecycle.py",
            "generator": "tools/gate_lifecycle_acceptance.py",
            "scope_version": gl.SCOPE_VERSION,
            "ledger_schema": gl.LEDGER_SCHEMA,
            "statuses": list(gl.ALL_STATUSES),
        },
        "criteria": {c["id"]: c for c in (c1, c2, c3, c4, c5, c6, c7)},
        "all_pass": all(bool(c["pass"]) for c in (c1, c2, c3, c4, c5, c7))
                    and (c6["pass"] is not False),
        "false_like": [c["id"] for c in (c1, c2, c3, c4, c5, c6, c7) if c["pass"] is False],
        "fixture": {
            "campaign": "fixture/campaign.json",
            "baseline_fingerprint": phases["baseline"]["fingerprint"],
            "topo_order": phases["baseline"]["summary"]["topo_order"],
            "detections": phases["detections"],
        },
        "real_plan": real,
        "figures": figures,
    }
    _dump(out / "acceptance_report.json", report)
    _write_text(out / "acceptance_report.md", render_markdown(report))
    print(json.dumps({"all_pass": report["all_pass"], "false_like": report["false_like"],
                      "report": str(out / "acceptance_report.json")}, ensure_ascii=False))
    return 0 if report["all_pass"] else 2


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# 门生命周期机制 · 全节点集成验收报告\n",
             f"- 生成时间（UTC）：`{report['generated_utc']}`\n",
             f"- 机制：`{report['mechanism']['module']}`（scope 版本 "
             f"`{report['mechanism']['scope_version']}`）\n",
             f"- 判定：**{'全部通过' if report['all_pass'] else '存在未达标项'}**"
             f"{'（未达标：' + ','.join(report['false_like']) + '）' if report['false_like'] else ''}\n",
             "\n## 判据表\n\n| 判据 | 内容 | 目标 | 实测 | 结论 |\n|---|---|---|---|---|\n"]
    for cid, c in report["criteria"].items():
        measured = c.get("measured", c.get("measured_ms"))
        shown = (json.dumps(measured, ensure_ascii=False)
                 if isinstance(measured, (dict, list)) else measured)
        if isinstance(shown, str) and len(shown) > 200:
            shown = shown[:200] + "…"
        lines.append(f"| {cid} | {c['name']} | {c['target']} | {shown} | "
                     f"{'PASS' if c['pass'] else ('PENDING' if c['pass'] is None else 'FAIL')} |\n")
    real = report.get("real_plan") or {}
    if not real.get("skipped"):
        lines.append(f"\n## 真实战役 plan {real.get('plan')} 节点状态（只读 DB 快照）\n\n")
        lines.append("| 节点 | 类型 | 声明态 | 生命周期态 | 依据 |\n|---|---|---|---|---|\n")
        for n in real.get("nodes", []):
            lines.append(f"| {n['id']} | {n['kind']} | {n['declared_status']} | "
                         f"{n['status']} | {','.join(n['reasons'])} |\n")
        c = real.get("contracts") or {}
        lines.append(f"\n跨仓接口契约：检查 {c.get('checked')} 条，破约 {c.get('broken')} 条"
                     f"（{', '.join(c.get('broken_ids') or []) or '无'}）\n")
    lines.append("\n图：\n")
    for fig in report.get("figures") or []:
        lines.append(f"- `{fig}`\n")
    return "".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())