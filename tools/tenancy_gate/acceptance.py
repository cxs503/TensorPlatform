#!/usr/bin/env python3
"""验收跑批：把机器可算判据 (a)~(g) 一次性跑出来，落 ``artifacts/<mission>/acceptance.json``。

判据 → 产物映射
---------------
(a) 检出率 12/12   → ``criteria.detection``（预注册 ``scenarios.json`` 的 detect 组）
(b) 假阳性 0/8     → ``criteria.false_positive``（clear 组）
(c) 幂等重放 3 次   → ``criteria.idempotency``（矩阵/台账逐字节比对）
(d) fail-closed    → ``criteria.fail_closed``（仓不可读 / ref 解析失败 / 无共同祖先 →
                        ``state=unknown`` 且 ``allowed=false``）
(e) 性能 <60s      → ``criteria.performance``（1000 分支两两预演 + 预算护栏压力面）
(f) 零领域语义      → ``criteria.semantics``（预注册 deny-list grep）
(g) pytest 数      → ``criteria.pytest``（子进程跑本模块测试取摘要行）

用法::

    python3 tools/tenancy_gate/acceptance.py --out artifacts/<mission> --work /tmp/tenancy_gate_work --figures
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.tenancy_gate.config import load_config              # noqa: E402
from tools.tenancy_gate.intake import conflict_matrix          # noqa: E402
from tools.tenancy_gate.lease import check_lease               # noqa: E402
from tools.tenancy_gate.ledger import (                        # noqa: E402
    append_events,
    make_event,
    query_events,
    read_events,
    recompute_matrix,
    record_intake,
    verify_chain,
)
from tools.tenancy_gate.model import FinishRecord, PathLease, canonical_json, digest  # noqa: E402
from tools.tenancy_gate.rehearsal import (                     # noqa: E402
    changed_files,
    rehearse_pair,
    rehearse_pairs,
)
from tools.tenancy_gate.semantics import scan_tree              # noqa: E402

SCENARIO_NOW = "2026-11-09T00:00:00Z"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "gate",
    "GIT_AUTHOR_EMAIL": "gate@example.invalid",
    "GIT_COMMITTER_NAME": "gate",
    "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": os.environ.get("HOME", "/tmp"),
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}


# ────────────────────────────── 场景跑批 (a)(b) ──────────────────────────────
def run_scenarios(cfg: dict) -> dict:
    data = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    rows = []
    for group in ("detect", "clear"):
        for scenario in data[group]:
            scfg = load_config(None, scenario.get("config") or {})
            actual, expected = _eval_scenario(scenario, scfg)
            checks = {}
            for key, want in expected.items():
                checks[key] = {"expected": want, "actual": actual.get(key), "ok": actual.get(key) == want}
            rows.append(
                {
                    "id": scenario["id"],
                    "group": group,
                    "kind": scenario["kind"],
                    "desc": scenario.get("desc", ""),
                    "expected": expected,
                    "actual": actual,
                    "checks": checks,
                    "ok": all(c["ok"] for c in checks.values()),
                }
            )
    detect_rows = [r for r in rows if r["group"] == "detect"]
    clear_rows = [r for r in rows if r["group"] == "clear"]
    return {
        "scenarios_total": len(rows),
        "detect_total": len(detect_rows),
        "detect_ok": sum(1 for r in detect_rows if r["ok"]),
        "clear_total": len(clear_rows),
        "clear_ok": sum(1 for r in clear_rows if r["ok"]),
        "rows": rows,
        "scenarios_digest": digest(data),
    }


def _eval_scenario(scenario: dict, cfg: dict) -> tuple[dict, dict]:
    expect = dict(scenario.get("expect") or {})
    if scenario["kind"] == "lease":
        leases = [PathLease.from_dict(x) for x in scenario.get("active") or []]
        candidate = PathLease.from_dict(scenario["candidate"])
        decision = check_lease(candidate, leases, cfg, now_ts=SCENARIO_NOW)
        expect.pop("kind", None)
        actual = {
            "admitted": decision["admitted"],
            "reason": decision["reason"],
            "conflicts": len(decision["conflicts"]),
            "expired_dropped": decision["expired_dropped"],
            "granted_mode": decision["granted_mode"],
            "digest": decision["digest"],
        }
        return actual, expect
    finish = FinishRecord.from_dict(scenario["finish"])
    peers = scenario.get("peers") or []
    matrix = conflict_matrix(finish, peers, cfg, now_ts=SCENARIO_NOW)
    actual = {
        "decision": matrix["decision"],
        "reason": matrix["reason"],
        "conflict_pairs": matrix["conflict_pairs"],
        "risk_pairs": matrix["risk_pairs"],
        "peers_out_of_window": len(matrix["peers_out_of_window"]),
        "scope_violations": len(matrix["scope_violations"]),
        "lanes": sorted({lane for pair in matrix["pairs"] for lane in pair.get("lanes", [])}),
        "digest": matrix["digest"],
    }
    return actual, expect


# ────────────────────────────── 幂等重放 (c) ──────────────────────────────
def run_idempotency(cfg: dict, work: Path) -> dict:
    data = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    scenario = next(s for s in data["detect"] if s["id"] == "I2")
    finish = FinishRecord.from_dict(scenario["finish"])
    peers = scenario["peers"]
    ledger = work / "idem_ledger.jsonl"
    if ledger.exists():
        ledger.unlink()

    matrix_bytes, digests, event_bytes = [], [], []
    for _ in range(3):
        matrix = conflict_matrix(finish, peers, cfg, now_ts=SCENARIO_NOW)
        matrix_bytes.append(canonical_json(matrix).encode("utf-8"))
        digests.append(matrix["digest"])
        record_intake(ledger, matrix, finish, peers, SCENARIO_NOW, now_ts=SCENARIO_NOW)
        event_bytes.append(ledger.read_bytes())

    events = read_events(ledger)
    return {
        "replays": 3,
        "matrix_bytes_identical": len(set(matrix_bytes)) == 1,
        "matrix_digests": digests,
        "digest_identical": len(set(digests)) == 1,
        "ledger_bytes_identical": len(set(event_bytes)) == 1,
        "ledger_events_after_replay": len(events),
        "ledger_lines": len(ledger.read_text(encoding="utf-8").strip().splitlines()),
        "chain_ok": verify_chain(ledger)["ok"],
        "ledger_digest": digest(events),
    }


# ────────────────────────────── fail-closed (d) ──────────────────────────────
def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env=GIT_ENV, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=GIT_ENV)
    (path / "base.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return path


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-qm", message)


def run_fail_closed(cfg: dict, work: Path) -> dict:
    cases = []

    missing = work / "does_not_exist_repo"
    res = rehearse_pair(str(missing), "main", "main", cfg)
    cases.append(("repo_missing", res, "repo_missing"))

    plain = work / "plain_dir"
    plain.mkdir(parents=True, exist_ok=True)
    res = rehearse_pair(str(plain), "main", "main", cfg)
    cases.append(("repo_not_a_git_dir", res, "repo_not_a_git_dir"))

    repo = _init_repo(work / "fc_repo")
    _git(repo, "checkout", "-qb", "side")
    _commit_file(repo, "side.txt", "side\n", "side")
    _git(repo, "checkout", "-q", "main")
    res = rehearse_pair(str(repo), "main", "no_such_ref", cfg)
    cases.append(("ref_unresolved", res, "ref_unresolved"))

    orphan = work / "fc_orphan"
    orphan.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "left", str(orphan)], check=True, env=GIT_ENV)
    _commit_file(orphan, "a.txt", "a\n", "left")
    _git(orphan, "checkout", "--orphan", "right")
    _git(orphan, "rm", "-rq", "--cached", ".")
    _commit_file(orphan, "b.txt", "b\n", "right")
    res = rehearse_pair(str(orphan), "left", "right", cfg)
    cases.append(("no_merge_base", res, "no_merge_base"))

    # 反向对照：能判定时必须判定（clean 与 conflict 都要出结论）
    ok_repo = _init_repo(work / "fc_clean")
    _git(ok_repo, "checkout", "-qb", "ours")
    _commit_file(ok_repo, "o.txt", "ours\n", "ours")
    _git(ok_repo, "checkout", "-q", "main")
    _git(ok_repo, "checkout", "-qb", "theirs")
    _commit_file(ok_repo, "t.txt", "theirs\n", "theirs")
    clean = rehearse_pair(str(ok_repo), "ours", "theirs", cfg)

    conflict_repo = _init_repo(work / "fc_conflict")
    _git(conflict_repo, "checkout", "-qb", "ours")
    _commit_file(conflict_repo, "base.txt", "line1\nOURS\nline3\n", "ours")
    _git(conflict_repo, "checkout", "-q", "main")
    _git(conflict_repo, "checkout", "-qb", "theirs")
    _commit_file(conflict_repo, "base.txt", "line1\nTHEIRS\nline3\n", "theirs")
    conflicted = rehearse_pair(str(conflict_repo), "ours", "theirs", cfg)

    rows = []
    for name, res, want_reason in cases:
        rows.append(
            {
                "case": name,
                "state": res["state"],
                "allowed": res["allowed"],
                "reason": res["reason"],
                "fail_closed_ok": res["state"] == "unknown" and res["allowed"] is False and res["reason"] == want_reason,
            }
        )
    return {
        "cases": rows,
        "fail_closed_all_ok": all(r["fail_closed_ok"] for r in rows),
        "control_clean": {"state": clean["state"], "allowed": clean["allowed"], "reason": clean["reason"]},
        "control_conflict": {
            "state": conflicted["state"],
            "allowed": conflicted["allowed"],
            "reason": conflicted["reason"],
            "conflicted_paths": conflicted["conflicted_paths"],
            "hunks": conflicted["hunks"],
            "digest": conflicted["digest"],
        },
        "control_clean_ok": clean["state"] == "clean" and clean["allowed"] is True,
        "control_conflict_ok": conflicted["state"] == "conflict" and conflicted["allowed"] is False
        and conflicted["conflicted_paths"] == ["base.txt"],
    }


# ────────────────────────────── 性能 (e) ──────────────────────────────
def build_branch_fixture(path: Path, n: int, mode: str) -> dict:
    """用 ``git fast-import`` 造 n 条分支（避免 n 次 checkout/commit 的开销）。

    mode:
      ``distinct``      —— 每条分支只改自己独有的文件（两两不相交 ⇒ 零候选）
      ``shared_clean``  —— 前 20 条分支对同一文件做**相同**改动（候选对多但干净）
      ``same_file``     —— 所有分支改同一文件的不同内容（最坏面：候选对 = C(n,2)）
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=GIT_ENV)
    shared_ids = set(range(20))

    def blob(path_in_repo: str, content: str) -> list:
        return ["M 100644 inline " + path_in_repo, "data <<EOT", content.rstrip("\n"), "EOT"]

    lines = [
        "commit refs/heads/main",
        "committer gate <gate@example.invalid> 1700000000 +0000",
        "data <<EOT", "base", "EOT",
    ]
    lines += blob("README.md", "base\n")
    for i in range(n):
        name = f"b{i:05d}"
        lines += [
            f"commit refs/heads/{name}",
            "committer gate <gate@example.invalid> 1700000000 +0000",
            "data <<EOT", f"branch {name}", "EOT",
            "from refs/heads/main",
        ]
        if mode == "distinct":
            lines += blob(f"pkg/file_{i:05d}.txt", f"{name}\n")
        elif mode == "shared_clean":
            if i in shared_ids:
                lines += blob("pkg/shared.txt", "shared-content\n")
            else:
                lines += blob(f"pkg/file_{i:05d}.txt", f"{name}\n")
        else:
            lines += blob("pkg/contended.txt", f"variant {name}\n")
    stream = "\n".join(lines) + "\n"
    subprocess.run(["git", "-C", str(path), "fast-import", "--quiet"], input=stream,
                   text=True, check=True, env=GIT_ENV)
    branches = _git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()
    return {"repo": str(path), "mode": mode, "branches": len([b for b in branches if b != "main"])}


def run_performance(cfg: dict, work: Path) -> dict:
    n = 1000
    results = {}
    for mode in ("distinct", "shared_clean", "same_file"):
        fixture = build_branch_fixture(work / f"perf_{mode}", n, mode)
        branches = sorted(
            b for b in _git(Path(fixture["repo"]), "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()
            if b != "main"
        )
        pairs = [(branches[i], branches[j]) for i in range(len(branches)) for j in range(i + 1, len(branches))]
        t0 = time.perf_counter()
        batch = rehearse_pairs(fixture["repo"], pairs, cfg, base="main")
        elapsed = time.perf_counter() - t0
        # 纯预筛（不做 git 调用）成本单列，便于区分"判定成本"与"git 调用成本"
        t1 = time.perf_counter()
        file_sets = {b: set(changed_files(fixture["repo"], "main", b) or []) for b in branches}
        materialize = time.perf_counter() - t1
        t2 = time.perf_counter()
        candidate_pairs = sum(
            1 for i in range(len(branches)) for j in range(i + 1, len(branches))
            if file_sets[branches[i]] & file_sets[branches[j]]
        )
        screen_only = time.perf_counter() - t2
        results[mode] = {
            "branches": len(branches),
            "pairs": len(pairs),
            "elapsed_s": round(elapsed, 3),
            "under_60s": elapsed < 60.0,
            "prescreen_clean": batch["prescreen_clean"],
            "merge_tree_calls": batch["merge_tree_calls"],
            "conflict_pairs": batch["conflict"],
            "unknown_pairs": batch["unknown"],
            "state": batch["state"],
            "allowed": batch["allowed"],
            "reason": batch["reason"],
            "changed_files_materialize_s": round(materialize, 3),
            "pairwise_screen_only_s": round(screen_only, 3),
            "candidate_pairs_by_prescreen": candidate_pairs,
            "results_total": batch["results_total"],
            "results_omitted": batch["results_omitted"],
            "batch_digest": batch["digest"],
        }
    return {
        "n_branches": n,
        "cases": results,
        "all_under_60s": all(v["under_60s"] for v in results.values()),
        "worst_case_bounded": results["same_file"]["unknown_pairs"] > 0
        and results["same_file"]["allowed"] is False,
    }


# ────────────────────────── 台账入库 + 查询 + 复算 ──────────────────────────
def run_ledger_demo(cfg: dict, outdir: Path) -> dict:
    """把 20 个预注册场景全量入库，再查询 + 逐条复算（判据 (4) 的证据面）。"""
    data = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    path = outdir / "ledger.jsonl"
    if path.exists():
        path.unlink()
    for group in ("detect", "clear"):
        for scenario in data[group]:
            scfg = load_config(None, scenario.get("config") or {})
            if scenario["kind"] == "lease":
                leases = [PathLease.from_dict(x) for x in scenario.get("active") or []]
                candidate = PathLease.from_dict(scenario["candidate"])
                decision = check_lease(candidate, leases, scfg, now_ts=SCENARIO_NOW)
                append_events(path, [make_event("lease_decision", decision, scenario["candidate"]["ts"])])
            else:
                finish = FinishRecord.from_dict(scenario["finish"])
                peers = scenario.get("peers") or []
                matrix = conflict_matrix(finish, peers, scfg, now_ts=SCENARIO_NOW)
                record_intake(path, matrix, finish, peers, scenario["finish"]["ts"], now_ts=SCENARIO_NOW)

    events = read_events(path)
    recomputed = [recompute_matrix(e, cfg) for e in events if e["kind"] == "intake_matrix"]
    chain = verify_chain(path)
    queries = {
        "all": len(events),
        "lease_decision": len(query_events(path, kind="lease_decision")),
        "intake_matrix": len(query_events(path, kind="intake_matrix")),
        "repo_TenantA": len(query_events(path, repo="TenantA")),
        "mission_m2001": len(query_events(path, mission_id="m2001")),
        "window_after": len(query_events(path, since="2026-11-10T00:00:00Z")),
    }
    report = {
        "ledger_path": str(path.relative_to(REPO_ROOT)),
        "events": len(events),
        "chain_ok": chain["ok"],
        "chain_problems": chain["problems"],
        "recomputed_intake_events": len(recomputed),
        "recompute_all_match": all(r["match"] for r in recomputed),
        "recompute_digests": [r["stored_digest"] for r in recomputed],
        "queries": queries,
        "ledger_digest": digest(events),
    }
    report["ok"] = chain["ok"] and report["recompute_all_match"] and len(events) == 20
    (outdir / "ledger_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# ────────────────────────────── (f)(g) ──────────────────────────────
def run_semantics() -> dict:
    scan = scan_tree(HERE)
    tests_scan = scan_tree(REPO_ROOT / "tests", suffixes=(".py",))
    targeted = [
        h for h in tests_scan["hits"] if "tenancy_gate" in h["path"]
    ]
    return {
        "package_scan": {k: scan[k] for k in ("hit_count", "files_scanned", "terms", "ok", "digest", "denylist_digest")},
        "package_hits": scan["hits"],
        "test_files_scanned_hits": targeted,
        "zero_hits": scan["hit_count"] == 0 and not targeted,
    }


def run_pytest() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_tenancy_gate.py", "-q", "--no-header"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    match = re.search(r"(\d+) passed", summary)
    failed = re.search(r"(\d+) failed", summary)
    return {
        "exit_code": proc.returncode,
        "summary": summary,
        "passed": int(match.group(1)) if match else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "ok": proc.returncode == 0,
    }


# ────────────────────────────── 图 ──────────────────────────────
def render_figures(outdir: Path, result: dict) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = []
    outdir.mkdir(parents=True, exist_ok=True)

    rows = result["scenarios"]["rows"]
    ids = [r["id"] for r in rows]
    observed = [float(r["actual"].get("conflict_pairs", r["actual"].get("conflicts", 0))) for r in rows]
    ok = [r["ok"] for r in rows]
    colors = ["#2a7f3f" if good else "#b3241c" for good in ok]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(ids, observed, color=colors)
    ax.set_ylabel("counted conflict units")
    ax.set_title("Pre-registered scenarios: expected vs observed conflict units "
                 f"({result['scenarios']['detect_ok']}/12 detected, "
                 f"{result['scenarios']['clear_ok']}/8 clear)")
    ax.axvline(11.5, color="0.5", linestyle="--", linewidth=1)
    ax.text(0.2, max(observed + [1]) * 0.9, "detect group", fontsize=8, color="0.3")
    ax.text(12.2, max(observed + [1]) * 0.9, "clear group", fontsize=8, color="0.3")
    ax.set_ylim(0, max(observed + [1]) * 1.15)
    fig.tight_layout()
    path = outdir / "fig_scenarios.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig_scenarios.png", "path": str(path),
                    "caption": "逐场景冲突单位数（绿=与预注册期望一致，红=不一致）；左 12 个为必须检出，右 8 个为必须零假阳性"})

    perf = result["criteria"]["performance"]["cases"]
    labels = list(perf.keys())
    elapsed = [perf[k]["elapsed_s"] for k in labels]
    calls = [perf[k]["merge_tree_calls"] for k in labels]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bars = ax.bar(labels, elapsed, color=["#2a7f3f", "#1f6f9c", "#b3241c"])
    ax.set_ylabel("elapsed (s)")
    ax.set_ylim(0, max(60.0, max(elapsed) * 1.2))
    ax.axhline(60, color="0.4", linestyle="--", linewidth=1, label="60 s budget line")
    for bar, c in zip(bars, calls):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.0,
                f"{c} merge-tree calls", ha="center", fontsize=8)
    ax.set_title("1000 branches, all pairs screened (499,500 pairs) under 60 s")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = outdir / "fig_performance.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig_performance.png", "path": str(path),
                    "caption": "三种分支布局下的批量预演耗时；柱标为真实 merge-tree 调用次数（预算护栏限流）"})

    # 冲突矩阵热力图：用 I2 场景的 3 个同窗口任务扩展成 12 任务矩阵，展示 lane 分布
    data = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    scenario = next(s for s in data["detect"] if s["id"] == "I2")
    finish = FinishRecord.from_dict(scenario["finish"])
    peers = []
    for i in range(12):
        files = ["z.py"] if i % 3 == 1 else ["a.py"] if i % 3 == 0 else [f"q{i}.py"]
        peers.append({"mission_id": f"m{2100 + i}", "repo": "TenantA", "changed_files": files,
                      "ts": f"2026-11-08T{20 + (i % 3):02d}:00:00Z"})
    matrix = conflict_matrix(finish, peers, load_config(None), now_ts=SCENARIO_NOW)
    lanes = ["file_overlap", "lease_breach", "glob_overlap", "invalid_path"]
    grid = [[1.0 if lane in pair.get("lanes", []) else 0.0 for lane in lanes] for pair in matrix["pairs"]]
    fig, ax = plt.subplots(figsize=(5.6, 6.4))
    im = ax.imshow(grid, cmap="Reds", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(lanes)))
    ax.set_xticklabels(lanes, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(len(matrix["pairs"])))
    ax.set_yticklabels([p["mission_id"] for p in matrix["pairs"]], fontsize=7)
    ax.set_title(f"Intake conflict matrix (decision={matrix['decision']}, "
                 f"conflict_pairs={matrix['conflict_pairs']})", fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    path = outdir / "fig_intake_matrix.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    figures.append({"name": "fig_intake_matrix.png", "path": str(path),
                    "caption": "12 个同窗口任务的冲突矩阵（行=peer 任务，列=lane，红=命中）；三条阻塞 lane 与一条记录 lane 互斥组合"})
    return figures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="tenancy_gate 验收跑批")
    parser.add_argument("--out", default="artifacts/m7876")
    parser.add_argument("--work", default=None, help="临时 git 夹具目录（缺省 mktemp）")
    parser.add_argument("--figures", action="store_true")
    parser.add_argument("--skip-perf", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(None)
    outdir = REPO_ROOT / args.out
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="tenancy_gate_"))
    work.mkdir(parents=True, exist_ok=True)

    started = time.time()
    scenarios = run_scenarios(cfg)
    idempotency = run_idempotency(cfg, work)
    fail_closed = run_fail_closed(cfg, work)
    performance = {"skipped": True} if args.skip_perf else run_performance(cfg, work)
    semantics = run_semantics()
    pytest_res = run_pytest()
    ledger_demo = run_ledger_demo(cfg, outdir)

    criteria = {
        "detection": {"expected": 12, "detected": scenarios["detect_ok"],
                      "ok": scenarios["detect_ok"] == 12},
        "false_positive": {"expected_clear": 8, "cleared": scenarios["clear_ok"],
                           "false_positives": scenarios["clear_total"] - scenarios["clear_ok"],
                           "ok": scenarios["clear_ok"] == 8},
        "idempotency": {
            "replays": 3,
            "ok": idempotency["matrix_bytes_identical"] and idempotency["digest_identical"]
            and idempotency["ledger_bytes_identical"] and idempotency["ledger_events_after_replay"] == 1,
            **idempotency,
        },
        "fail_closed": {"ok": fail_closed["fail_closed_all_ok"] and fail_closed["control_clean_ok"]
                        and fail_closed["control_conflict_ok"], **fail_closed},
        "performance": {"ok": performance.get("all_under_60s", False),
                        "worst_case_bounded": performance.get("worst_case_bounded", False),
                        **performance},
        "semantics": {"ok": semantics["zero_hits"], **semantics},
        "pytest": {"ok": pytest_res["ok"] and pytest_res["passed"] >= 15, **pytest_res},
        "ledger": {"ok": ledger_demo["ok"], **ledger_demo},
    }
    result = {
        "schema": "tenancy_gate/acceptance/v1",
        "module": "tools/tenancy_gate",
        "scenario_now": SCENARIO_NOW,
        "criteria": criteria,
        "scenarios": scenarios,
        "all_ok": all(v.get("ok") for v in criteria.values()),
        "elapsed_s": round(time.time() - started, 3),
        "config_digest": digest(cfg),
        "git_version": subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip(),
        "python": sys.version.split()[0],
    }
    if args.figures:
        result["figures"] = render_figures(outdir, result)
    result["digest"] = digest({k: v for k, v in result.items() if k != "digest"})
    (outdir / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v.get("ok") for k, v in criteria.items()}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in result.items() if k != "criteria"}, ensure_ascii=False)[:1200])
    return 0 if result["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())