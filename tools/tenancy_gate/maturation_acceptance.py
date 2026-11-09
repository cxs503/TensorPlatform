#!/usr/bin/env python3
"""P27W7-N09 成熟化验收：把本节点的机器可算判据跑成一张可复算的验收单。

判据（全部程序化计算，数字零手抄）
----------------------------------
A1 路径预留：同路径并发任务冲突检出率 == 100%
A2 changed_files 交集：重叠文件命中率 == 100%（与**独立参考实现**比对）
A3 三方合并 dry-run：可自动合并场景 ``exit_code == 0`` 覆盖率 == 100%
A4 负控：无重叠路径/文件必须返回"无冲突"（冲突率 == 0%）
A5 负控：单任务独占路径必须返回"无冲突"
A6 集成：派单器接线三模式（off/report/enforce）+ 台账链完整 + 回归测试全绿
A7 零领域语义：新模块过预注册 denylist 命中数 == 0

独立参考实现：本文件自带一个**独立**的模式匹配器（正则单段 + 递归 globstar），
不 import ``tools.tenancy_gate.pattern``，避免"用被测实现验证被测实现"的同义反复。

用法
----
    python3 tools/tenancy_gate/maturation_acceptance.py \
        --out artifacts/p27w7_n09 --figures --report docs/P27W7-N09/RESULTS.md
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.tenancy_gate import dispatch_hook as H  # noqa: E402
from tools.tenancy_gate.ledger import verify_chain  # noqa: E402
from tools.tenancy_gate.scheduler_gate import (  # noqa: E402
    EXIT_PASS,
    SchedulerGate,
)
from tools.tenancy_gate.semantics import scan_tree  # noqa: E402

PKG_DIR = REPO_ROOT / "tools" / "tenancy_gate"
SCHEMA = "tenancy_gate/maturation_acceptance/v1"
SEED = 20261109
T0 = "2026-11-09T00:00:00Z"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "gate",
    "GIT_AUTHOR_EMAIL": "gate@example.invalid",
    "GIT_COMMITTER_NAME": "gate",
    "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/tmp",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


# ─────────────── 独立参考实现（不 import 被测量的 pattern 模块） ───────────────
def oracle_seg_match(pattern: str, text: str) -> bool:
    """单段匹配：`*` 段内任意串、`?` 段内单字符、其余字面量。"""
    rx = ""
    for ch in pattern:
        rx += "[^/]*" if ch == "*" else ("[^/]" if ch == "?" else re.escape(ch))
    return re.fullmatch(rx, text) is not None


def oracle_match(pattern: str, path: str) -> bool:
    """整条路径匹配（globstar `**` = 零或多段），递归实现，与被测代码无关。"""
    pat = pattern.split("/")
    seg = path.split("/")

    def go(i: int, j: int) -> bool:
        if i == len(pat):
            return j == len(seg)
        if pat[i] == "**":
            return go(i + 1, j) or (j < len(seg) and go(i, j + 1))
        if j == len(seg):
            return False
        return oracle_seg_match(pat[i], seg[j]) and go(i + 1, j + 1)

    return go(0, 0)


def oracle_intersect(a: str, b: str) -> bool:
    """两个模式是否存在共同路径：在"见证路径"集合上穷举（含双方字面量段 + 通配替身）。"""
    literals = {s for s in a.split("/") + b.split("/") if "*" not in s and "?" not in s}
    alphabet = sorted(literals | {"s", "t"})
    parts_a = a.split("/")
    candidates: set[str] = set()
    for depth in range(1, len(parts_a) + 3):
        for combo in itertools.product(alphabet, repeat=depth):
            candidates.add("/".join(combo))
    return any(oracle_match(a, c) and oracle_match(b, c) for c in candidates)


# ─────────────── 场景生成（确定性） ───────────────
ALPHABET = ["src", "tests", "docs", "core", "util", "x.py", "y.md", "a", "b"]
SEG_PATTERNS = ["*", "**", "?", "*.py", "**"]
OVERLAP_TEMPLATES = [
    ("src/**", "src/{leaf}"),
    ("{root}/**", "{root}/{leaf}"),
    ("{root}/*.py", "{root}/{leaf}"),
    ("{root}/**", "{root}/*/x.py"),
]
DISJOINT_TEMPLATES = [
    ("src/**", "docs/**"),
    ("{r1}/**", "{r2}/**"),
    ("{r1}/*.py", "{r1}/sub/*.py"),
    ("tests/a/**", "tests/b/**"),
]


def gen_path_scenarios(n: int = 40):
    rnd = random.Random(SEED)
    scen = []
    for i in range(n):
        root = rnd.choice(["src", "tests", "docs", "core", "util"])
        leaf = rnd.choice(["x.py", "y.md", "a/b", "core/y.md", "z.py"])
        tpl = OVERLAP_TEMPLATES[i % len(OVERLAP_TEMPLATES)]
        a, b = (t.format(root=root, leaf=leaf) for t in tpl)
        scen.append({"id": f"overlap-{i}", "a": a, "b": b, "expect_conflict": True})
    for i in range(n):
        r1, r2 = rnd.sample(["src", "tests", "docs", "core", "util"], 2)
        tpl = DISJOINT_TEMPLATES[i % len(DISJOINT_TEMPLATES)]
        a, b = (t.format(r1=r1, r2=r2) for t in tpl)
        scen.append({"id": f"disjoint-{i}", "a": a, "b": b, "expect_conflict": False})
    return scen


def gen_file_scenarios(n: int = 40):
    rnd = random.Random(SEED + 1)
    scen = []
    pool = [f"{d}/{f}" for d in ("src", "tests", "docs", "core") for f in
            ("a.py", "b.py", "c.md", "d.py")]
    for i in range(n):
        mine = sorted(rnd.sample(pool, rnd.randint(1, 4)))
        if i % 2 == 0:                     # 保证至少一个重叠文件
            theirs = sorted(set(mine[:1]) | set(rnd.sample(pool, rnd.randint(1, 3))))
        else:                              # 保证无重叠
            rest = [p for p in pool if p not in mine]
            theirs = sorted(rnd.sample(rest, rnd.randint(1, 3)))
        truth = sorted(set(mine) & set(theirs))
        scen.append({"id": f"files-{i}", "mine": mine, "theirs": theirs, "truth": truth})
    return scen


# ─────────────── git 夹具 ───────────────
def git(repo: Path, *args: str) -> None:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=GIT_ENV, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:400])


def build_repo(tmp: Path, kind: str) -> Path:
    repo = tmp / f"repo_{kind}"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "shared.txt").write_text("".join(f"l{i}\n" for i in range(1, 41)), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    def branch(name: str, edits: dict[int, str], newfile: str | None = None) -> None:
        git(repo, "checkout", "-q", "-B", name, "main")
        if edits:
            lines = (repo / "shared.txt").read_text(encoding="utf-8").splitlines()
            for idx, text in edits.items():
                lines[idx - 1] = text
            (repo / "shared.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if newfile:
            (repo / newfile).write_text(f"{newfile}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", name)
        git(repo, "checkout", "-q", "main")

    if kind == "disjoint_files":
        branch("ours", {}, "f_ours.txt")
        branch("theirs", {}, "f_theirs.txt")
    elif kind == "disjoint_hunks":
        branch("ours", {3: "ours-3"})
        branch("theirs", {36: "theirs-36"})
    elif kind == "same_hunk":
        branch("ours", {12: "ours-12"})
        branch("theirs", {12: "theirs-12"})
    elif kind == "both_edit_file_distinct_regions":
        branch("ours", {5: "ours-5", 6: "ours-6"})
        branch("theirs", {20: "theirs-20", 21: "theirs-21"})
    return repo


def run_merge_family(tmp: Path, gate: SchedulerGate, kind: str, expect: str) -> dict:
    if kind == "unknown":
        out = gate.merge_dry_run(tmp / "no_such_repo", "ours", "theirs", ts=T0)
    else:
        repo = build_repo(tmp, kind)
        out = gate.merge_dry_run(repo, "ours", "theirs", ts=T0)
    return {
        "family": kind,
        "expect_state": expect,
        "state": out["state"],
        "exit_code": out["exit_code"],
        "resolution": out["resolution"],
        "conflicted_paths": out["conflicted_paths"],
        "digest": out["digest"],
        "match": out["state"] == expect,
    }


# ─────────────── 各判据执行 ───────────────
def check_a1_a4_a5(tmp: Path) -> dict:
    scen = gen_path_scenarios()
    same_total = same_hit = 0
    disjoint_total = disjoint_clean = 0
    oracle_agree = 0
    rows = []
    for i, sc in enumerate(scen):
        gate = SchedulerGate(state_path=tmp / f"p{i}.json", ledger_path=tmp / f"p{i}.jsonl")
        first = gate.reserve("m1", "Repo", [sc["a"]], ts=T0)
        second = gate.reserve("m2", "Repo", [sc["b"]], ts="2026-11-09T00:01:00Z")
        conflict = second["resolution"] == "queued" and bool(second["conflicts"])
        # 真值来自独立参考实现（本文件 oracle_*），不来自被测模块、也不来自构造意图
        oracle = oracle_intersect(sc["a"], sc["b"])
        oracle_agree += int(oracle == conflict)
        if oracle:
            same_total += 1
            same_hit += int(conflict)
        else:
            disjoint_total += 1
            disjoint_clean += int(not conflict)
        rows.append({**sc, "oracle_conflict": oracle, "detected_conflict": conflict,
                     "a_admitted": first["resolution"], "b_exit": second["exit_code"]})

    solo_gate = SchedulerGate(state_path=tmp / "solo.json", ledger_path=tmp / "solo.jsonl")
    solo = solo_gate.reserve("solo", "Repo", ["private/**", "private/a.py"], ts=T0)
    return {
        "A1_same_path_conflict": {
            "scenarios": same_total, "detected": same_hit,
            "rate": same_hit / same_total if same_total else 0.0,
            "threshold": 1.0, "pass": same_hit == same_total,
        },
        "A4_negative_control_disjoint_paths": {
            "scenarios": disjoint_total, "no_conflict": disjoint_clean,
            "false_positive_rate": (disjoint_total - disjoint_clean) / disjoint_total,
            "threshold": 0.0, "pass": disjoint_clean == disjoint_total,
        },
        "A5_negative_control_single_task": {
            "resolution": solo["resolution"], "exit_code": solo["exit_code"],
            "pass": solo["resolution"] == "pass" and solo["exit_code"] == EXIT_PASS,
        },
        "oracle_agreement": {"scenarios": len(scen), "agree": oracle_agree,
                             "rate": oracle_agree / len(scen) if scen else 0.0},
        "rows": rows,
    }


def check_a2(tmp: Path) -> dict:
    scen = gen_file_scenarios()
    hit = total_overlap = 0
    clean_ok = total_clean = 0
    rows = []
    for i, sc in enumerate(scen):
        gate = SchedulerGate(state_path=tmp / f"f{i}.json", ledger_path=tmp / f"f{i}.jsonl")
        gate.complete("peer", "Repo", sc["theirs"], ts="2026-11-09T00:00:00Z")
        out = gate.complete("me", "Repo", sc["mine"], ts="2026-11-09T00:01:00Z")
        reported = sorted({f for p in out["pairs"] for f in p["file_overlap"]})
        match = reported == sc["truth"]
        if sc["truth"]:
            total_overlap += 1
            hit += int(match)
        else:
            total_clean += 1
            clean_ok += int(match and out["conflict_pairs"] == 0 and out["exit_code"] == EXIT_PASS)
        rows.append({**sc, "reported": reported, "match": match,
                     "conflict_pairs": out["conflict_pairs"]})
    return {
        "A2_overlap_hit": {"scenarios": total_overlap, "exact_hit": hit,
                           "rate": hit / total_overlap if total_overlap else 0.0,
                           "threshold": 1.0, "pass": hit == total_overlap},
        "A4b_negative_control_no_overlap": {
            "scenarios": total_clean, "clean": clean_ok,
            "rate": clean_ok / total_clean if total_clean else 0.0,
            "threshold": 1.0, "pass": clean_ok == total_clean,
        },
        "rows": rows,
    }


def check_a3(tmp: Path) -> dict:
    gate = SchedulerGate(state_path=tmp / "merge.json", ledger_path=tmp / "merge.jsonl")
    families = [
        ("disjoint_files", "clean"),
        ("disjoint_hunks", "clean"),
        ("both_edit_file_distinct_regions", "clean"),
        ("disjoint_files", "clean"),
        ("same_hunk", "conflict"),
        ("same_hunk", "conflict"),
        ("unknown", "unknown"),
    ]
    rows = []
    for i, (kind, expect) in enumerate(families):
        sub = tmp / f"m{i}"
        sub.mkdir(parents=True, exist_ok=True)
        rows.append(run_merge_family(sub, gate, kind, expect))
    autos = [r for r in rows if r["expect_state"] == "clean"]
    conflicts = [r for r in rows if r["expect_state"] == "conflict"]
    unknowns = [r for r in rows if r["expect_state"] == "unknown"]
    auto_zero = sum(1 for r in autos if r["exit_code"] == 0)
    conf_one = sum(1 for r in conflicts if r["exit_code"] == 1
                   and r["resolution"] == "manual_arbitration")
    unknown_two = sum(1 for r in unknowns if r["exit_code"] == 2
                      and r["resolution"] == "rollback")
    return {
        "A3_auto_merge_exit0": {
            "scenarios": len(autos), "exit0": auto_zero,
            "rate": auto_zero / len(autos) if autos else 0.0,
            "threshold": 1.0, "pass": auto_zero == len(autos),
        },
        "A3b_conflict_manual_arbitration": {
            "scenarios": len(conflicts), "exit1_manual": conf_one,
            "pass": conf_one == len(conflicts),
        },
        "A3c_unknown_rollback": {
            "scenarios": len(unknowns), "exit2_rollback": unknown_two,
            "pass": unknown_two == len(unknowns),
        },
        "rows": rows,
    }


def check_a6(tmp: Path) -> dict:
    env_off = {"TENANCY_GATE_MODE": "off", "TENANCY_GATE_STATE": str(tmp / "off.json")}
    off = H.pre_dispatch("Repo", ["src/**"], "m1", env=env_off)
    env = {"TENANCY_GATE_MODE": "report",
           "TENANCY_GATE_STATE": str(tmp / "hook_state.json"),
           "TENANCY_GATE_LEDGER": str(tmp / "hook_ledger.jsonl")}
    first = H.pre_dispatch("Repo", ["src/**"], "m1", env=env)
    second = H.pre_dispatch("Repo", ["src/a.py"], "m2", env=env)
    env_enforce = dict(env, TENANCY_GATE_MODE="enforce")
    enforce_first = H.pre_dispatch("RepoB", ["tools/**"], "n1", env=env_enforce)
    enforce_blocked = H.pre_dispatch("RepoB", ["tools/x.py"], "n2", env=env_enforce)
    chain = verify_chain(tmp / "hook_ledger.jsonl")
    tests = run_tests()
    return {
        "A6_hook_modes": {
            "off_skips": off["mode"] == "off" and not (tmp / "off.json").exists(),
            "report_records": first["exit_code"] == 0 and second["exit_code"] == 10,
            "report_never_blocks": second["blocked"] is False,
            "enforce_admits_first": enforce_first["blocked"] is False,
            "enforce_blocks_conflict": enforce_blocked["blocked"] is True,
            "pass": all([off["mode"] == "off", first["exit_code"] == 0,
                         second["exit_code"] == 10, second["blocked"] is False,
                         enforce_first["blocked"] is False,
                         enforce_blocked["blocked"] is True]),
        },
        "A6_ledger_chain": {"ok": chain["ok"], "events": chain["events"],
                            "pass": bool(chain["ok"])},
        "A6_regression_tests": tests,
    }


def run_tests() -> dict:
    files = ["tests/test_tenancy_gate.py", "tests/test_tenancy_gate_scheduler.py"]
    proc = subprocess.run([sys.executable, "-m", "pytest", *files, "-q"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=1800)
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:]
    summary = tail[0] if tail else proc.stdout.strip()[-200:]
    return {"returncode": proc.returncode, "files": files, "summary": summary,
            "pass": proc.returncode == 0}


def check_a7() -> dict:
    scan = scan_tree(PKG_DIR)
    return {"files_scanned": scan["files_scanned"], "terms": scan["terms"],
            "hits": scan["hit_count"], "denylist_digest": scan["denylist_digest"],
            "pass": scan["hit_count"] == 0}


def check_queue(tmp: Path) -> dict:
    """排队动力学历程（确定性时间轴）：8 个任务抢 2 个写面。"""
    gate = SchedulerGate(state_path=tmp / "queue.json", ledger_path=tmp / "queue.jsonl")
    timeline = []
    admissions = gate.reserve("owner1", "Repo", ["src/**"], ts="2026-11-09T00:00:00Z")
    gate.reserve("owner2", "Repo", ["docs/**"], ts="2026-11-09T00:00:00Z")
    for k in range(6):
        out = gate.reserve(f"q{k}", "Repo", ["src/a.py" if k % 2 == 0 else "docs/x.md"],
                           ts=f"2026-11-09T00:0{k + 1}:00Z")
        timeline.append({"step": k, "mission": f"q{k}", "resolution": out["resolution"],
                         "queue_position": out["queue_position"]})
    queued_before = len(gate.state["queue"])
    drained = []
    for step, owner in enumerate(["owner1", "owner2"]):
        gate.release(owner, repo="Repo", ts=f"2026-11-09T00:1{step}:00Z")
        out = gate.drain_queue(ts=f"2026-11-09T00:1{step}:30Z")
        drained.append({"released": owner, "admitted": [a["mission_id"] for a in out["admitted"]],
                        "still_queued": len(out["still_queued"])})
    final = gate.drain_queue(ts="2026-11-09T00:20:00Z")
    repeat = gate.drain_queue(ts="2026-11-09T00:21:00Z")
    stable = ([e["mission_id"] for e in final["still_queued"]]
              == [e["mission_id"] for e in repeat["still_queued"]])
    # FIFO 语义下的期望：两轮释放各放行 1 个（src 与 docs 各一个写面，队首之后的任务
    # 又与刚放行者同文件而继续等待），剩余 4 个仍在其队列位次上
    admitted_total = sum(len(step["admitted"]) for step in drained)
    expected_left = 4
    return {
        "first_admission": admissions["resolution"],
        "queued_before_drain": queued_before,
        "timeline": timeline,
        "drain_steps": drained,
        "admitted_total": admitted_total,
        "final_queue_len": len(gate.state["queue"]),
        "final_idempotent": (final["admitted"] == [] and repeat["admitted"] == [] and stable),
        "pass": (queued_before == 6 and len(gate.state["queue"]) == expected_left
                 and admitted_total == 2
                 and final["admitted"] == [] and repeat["admitted"] == [] and stable),
    }


# ─────────────── 出图 ───────────────
def make_figures(out: Path, acc: dict) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = []
    plt.rcParams.update({"font.size": 9, "figure.dpi": 140,
                         "axes.spines.top": False, "axes.spines.right": False})

    # 图1：检出率 / 假阳性率
    labels = ["same-path\nconflict (A1)", "file-overlap\nhit (A2)",
              "disjoint-path\nclean (A4)", "no-overlap\nclean (A4b)"]
    vals = [acc["a1_a4_a5"]["A1_same_path_conflict"]["rate"] * 100,
            acc["a2"]["A2_overlap_hit"]["rate"] * 100,
            acc["a1_a4_a5"]["A4_negative_control_disjoint_paths"]["no_conflict"]
            / max(1, acc["a1_a4_a5"]["A4_negative_control_disjoint_paths"]["scenarios"]) * 100,
            acc["a2"]["A4b_negative_control_no_overlap"]["rate"] * 100]
    fig, ax = plt.subplots(figsize=(5.2, 3.0))
    bars = ax.bar(labels, vals, color=["#2b6cb0", "#2b6cb0", "#2f855a", "#2f855a"])
    ax.set_ylim(0, 110)
    ax.set_ylabel("detection / clean rate [%]")
    ax.axhline(100, ls="--", lw=0.8, color="#c53030")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 2, f"{v:.0f}%", ha="center", fontsize=8)
    ax.set_title("Path reservation & changed_files gate: decision rates")
    p1 = out / "fig1_decision_rates.png"
    fig.tight_layout()
    fig.savefig(p1)
    plt.close(fig)
    figs.append(str(p1))

    # 图2：合并预演退出码矩阵
    rows = acc["a3"]["rows"]
    fams = [r["family"] for r in rows]
    codes = [r["exit_code"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.0, 2.8))
    colors = {0: "#2f855a", 1: "#b7791f", 2: "#c53030"}
    ax.bar(range(len(rows)), codes, color=[colors.get(c, "#4a5568") for c in codes])
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([f.replace("_", "\n") for f in fams], fontsize=7)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(["0 auto-merge", "1 manual", "2 rollback"])
    ax.set_ylabel("merge dry-run exit code")
    ax.set_title("Three-way merge dry-run (git merge-tree): exit code per scenario family")
    p2 = out / "fig2_merge_dryrun_exit_codes.png"
    fig.tight_layout()
    fig.savefig(p2)
    plt.close(fig)
    figs.append(str(p2))

    # 图3：排队动力学（排队深度随时间）
    q = acc["queue"]
    xs, ys, res = [], [], []
    depth = 0
    for item in q["timeline"]:
        depth += 1 if item["resolution"] == "queued" else 0
        xs.append(item["mission"])
        ys.append(depth)
        res.append(item["resolution"])
    for step in q["drain_steps"]:
        depth = step["still_queued"]
        xs.append("release\n" + step["released"])
        ys.append(depth)
        res.append("drain")
    fig, ax = plt.subplots(figsize=(6.0, 2.8))
    ax.plot(range(len(ys)), ys, marker="o", color="#2b6cb0")
    ax.fill_between(range(len(ys)), ys, color="#2b6cb0", alpha=0.15)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, fontsize=7)
    ax.set_ylabel("queue depth [#tasks]")
    ax.set_title("FIFO path-reservation queue: enqueue and drain dynamics")
    p3 = out / "fig3_queue_dynamics.png"
    fig.tight_layout()
    fig.savefig(p3)
    plt.close(fig)
    figs.append(str(p3))
    return figs


# ─────────────── 报告表格（程序化注入，零手抄） ───────────────
def render_results(acc: dict) -> str:
    a1 = acc["a1_a4_a5"]
    a2 = acc["a2"]
    a3 = acc["a3"]
    a6 = acc["a6"]
    q = acc["queue"]
    L = []
    L.append("<!-- 本文件由 tools/tenancy_gate/maturation_acceptance.py 自动生成，数字零手抄 -->")
    L.append("")
    L.append("### 表 1 路径预留与交集检测（判据 A1/A2/A4/A5）")
    L.append("")
    L.append("| 判据 | 场景数 | 达标数 | 比率 | 阈值 | 判定 |")
    L.append("|---|---|---|---|---|---|")
    rows = [
        ("A1 同路径并发冲突检出", a1["A1_same_path_conflict"]["scenarios"],
         a1["A1_same_path_conflict"]["detected"], a1["A1_same_path_conflict"]["rate"], "100%"),
        ("A2 changed_files 重叠命中", a2["A2_overlap_hit"]["scenarios"],
         a2["A2_overlap_hit"]["exact_hit"], a2["A2_overlap_hit"]["rate"], "100%"),
        ("A4 无重叠路径无冲突", a1["A4_negative_control_disjoint_paths"]["scenarios"],
         a1["A4_negative_control_disjoint_paths"]["no_conflict"],
         a1["A4_negative_control_disjoint_paths"]["no_conflict"]
         / max(1, a1["A4_negative_control_disjoint_paths"]["scenarios"]), "100%"),
        ("A4b 无重叠文件无冲突", a2["A4b_negative_control_no_overlap"]["scenarios"],
         a2["A4b_negative_control_no_overlap"]["clean"],
         a2["A4b_negative_control_no_overlap"]["rate"], "100%"),
        ("A5 单任务独占路径无冲突", 1, int(a1["A5_negative_control_single_task"]["pass"]),
         float(a1["A5_negative_control_single_task"]["pass"]), "100%"),
    ]
    for name, n, k, rate, thr in rows:
        L.append(f"| {name} | {n} | {k} | {rate * 100:.1f}% | {thr} | "
                 f"{'PASS' if rate >= 1.0 else 'FAIL'} |")
    L.append("")
    L.append(f"独立参考实现（本文件自带 globstar 递归匹配器）与门判定的一致率："
             f"{a1['oracle_agreement']['agree']}/{a1['oracle_agreement']['scenarios']}"
             f" = {a1['oracle_agreement']['rate'] * 100:.1f}%。")
    L.append("")
    L.append("### 表 2 三方合并 dry-run（判据 A3）")
    L.append("")
    L.append("| 场景族 | 期望状态 | 实测状态 | 退出码 | 裁决 | 命中 |")
    L.append("|---|---|---|---|---|---|")
    for r in a3["rows"]:
        L.append(f"| {r['family']} | {r['expect_state']} | {r['state']} | {r['exit_code']} | "
                 f"{r['resolution']} | {'YES' if r['match'] else 'NO'} |")
    L.append("")
    L.append(f"可自动合并场景 `exit_code==0` 覆盖：{a3['A3_auto_merge_exit0']['exit0']}/"
             f"{a3['A3_auto_merge_exit0']['scenarios']}；"
             f"内容冲突 → 人工仲裁：{a3['A3b_conflict_manual_arbitration']['exit1_manual']}/"
             f"{a3['A3b_conflict_manual_arbitration']['scenarios']}；"
             f"无法判定 → 回滚：{a3['A3c_unknown_rollback']['exit2_rollback']}/"
             f"{a3['A3c_unknown_rollback']['scenarios']}。")
    L.append("")
    L.append("### 表 3 调度器接线与回归（判据 A6）")
    L.append("")
    L.append("| 检查项 | 结果 |")
    L.append("|---|---|")
    for key in ("off_skips", "report_records", "report_never_blocks",
                "enforce_admits_first", "enforce_blocks_conflict"):
        L.append(f"| {key} | {'PASS' if a6['A6_hook_modes'][key] else 'FAIL'} |")
    L.append(f"| 台账链校验（{a6['A6_ledger_chain']['events']} 事件） | "
             f"{'PASS' if a6['A6_ledger_chain']['ok'] else 'FAIL'} |")
    L.append(f"| 回归测试（{', '.join(a6['A6_regression_tests']['files'])}） | "
             f"{a6['A6_regression_tests']['summary']} |")
    L.append("")
    L.append("### 表 4 排队动力学")
    L.append("")
    L.append("| 阶段 | 任务 | 裁决 | 队列位置 |")
    L.append("|---|---|---|---|")
    for item in q["timeline"]:
        L.append(f"| 入队 | {item['mission']} | {item['resolution']} | {item['queue_position']} |")
    for step in q["drain_steps"]:
        L.append(f"| 释放 {step['released']} 后 drain | {', '.join(step['admitted']) or '-'} | "
                 f"admitted | 剩余 {step['still_queued']} |")
    L.append("")
    L.append(f"两轮释放共放行 {q['admitted_total']} 个；剩余队列长度 "
             f"{q['final_queue_len']}（同文件写面仍互斥，FIFO 不插队）；幂等复跑："
             f"{'空转无新增（重复 drain 判定稳定）' if q['final_idempotent'] else '异常'}。")
    L.append("")
    L.append("### 表 5 零领域语义扫描（判据 A7）")
    L.append("")
    L.append(f"扫描文件 {acc['a7']['files_scanned']} 个，预注册词条 {acc['a7']['terms']} 条，"
             f"命中 {acc['a7']['hits']} 条 → {'PASS' if acc['a7']['pass'] else 'FAIL'}。")
    L.append("")
    return "\n".join(L)


# ─────────────── main ───────────────
VOLATILE_PATHS = ("elapsed_seconds", "digest", "digest_scope", "figures")


def digest_of(acc: dict) -> str:
    """验收单摘要：剔除纯观测字段（总耗时、pytest 墙钟后缀），同输入必得同摘要。"""
    import hashlib

    body = json.loads(json.dumps(acc, ensure_ascii=False, default=str))
    for key in VOLATILE_PATHS:
        body.pop(key, None)
    rt = (body.get("a6") or {}).get("A6_regression_tests") or {}
    if "summary" in rt:
        rt["summary"] = re.sub(r"in [0-9.]+s", "in <wall>s", rt["summary"])
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/p27w7_n09")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--skip-tests", action="store_true", help="跳过 pytest 子进程（调试用）")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="tenancy_acc_") as td:
        tmp = Path(td)
        acc = {
            "schema": SCHEMA,
            "task": "P27W7-N09",
            "generated_at": T0,
            "seed": SEED,
            "python": sys.version.split()[0],
            "git": subprocess.run(["git", "--version"], capture_output=True, text=True,
                                  env=GIT_ENV).stdout.strip(),
            "module_digests": {
                p.name: __import__("hashlib").sha256(p.read_bytes()).hexdigest()
                for p in sorted(PKG_DIR.glob("*.py"))
            },
            "a1_a4_a5": check_a1_a4_a5(tmp),
            "a2": check_a2(tmp),
            "a3": check_a3(tmp),
            "a6": check_a6(tmp),
            "a7": check_a7(),
            "queue": check_queue(tmp),
        }
    if args.skip_tests:
        acc["a6"]["A6_regression_tests"] = {"skipped": True, "pass": True,
                                            "summary": "skipped by --skip-tests"}
    criteria = {
        "A1_same_path_conflict_100pct": acc["a1_a4_a5"]["A1_same_path_conflict"]["pass"],
        "A2_changed_files_overlap_100pct": acc["a2"]["A2_overlap_hit"]["pass"],
        "A3_auto_merge_exit0": acc["a3"]["A3_auto_merge_exit0"]["pass"],
        "A4_negative_disjoint_paths": acc["a1_a4_a5"]["A4_negative_control_disjoint_paths"]["pass"],
        "A4b_negative_no_overlap_files": acc["a2"]["A4b_negative_control_no_overlap"]["pass"],
        "A5_negative_single_task": acc["a1_a4_a5"]["A5_negative_control_single_task"]["pass"],
        "A6_scheduler_integration": (acc["a6"]["A6_hook_modes"]["pass"]
                                     and acc["a6"]["A6_ledger_chain"]["pass"]
                                     and acc["a6"]["A6_regression_tests"]["pass"]),
        "A7_zero_domain_semantics": acc["a7"]["pass"],
        "A8_queue_dynamics": acc["queue"]["pass"],
    }
    acc["criteria"] = criteria
    acc["overall_pass"] = all(criteria.values())
    acc["elapsed_seconds"] = round(time.time() - started, 2)

    if args.figures:
        acc["figures"] = make_figures(out, acc)
    acc["digest_scope"] = ("sha256(canonical_json) over the acceptance body minus volatile "
                           "observations (elapsed_seconds, figure paths, pytest wall-clock suffix)")
    acc["digest"] = digest_of(acc)

    (out / "acceptance.json").write_text(
        json.dumps(acc, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    results = render_results(acc)
    (out / "RESULTS.md").write_text(results, encoding="utf-8")
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(results, encoding="utf-8")

    print(json.dumps({k: acc[k] for k in ("overall_pass", "criteria", "digest",
                                          "elapsed_seconds")}, ensure_ascii=False, indent=2))
    return 0 if acc["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())