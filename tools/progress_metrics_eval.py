#!/usr/bin/env python3
"""P148-W2-N08 验收跑批：进展度量/停滞检测/早停换路的标注场景实验 + 真实 PG 回写校验。

设计（判据先于实现，见 ``ops/taskplane/progress_metrics.py`` 模块头 C1–C7）
-------------------------------------------------------------------------
* **标注由构造决定**：场景生成器知道每一拍到底改了没有内容（``add_file/edit_file/
  add_lines`` = 有内容变化；``none/touch`` = 无内容变化）。因此「连续 3 拍无进展」
  这个标签**不是**从被测模块输出反推的，不存在拿答案评答案。
* **正控**：种植停滞段（含从第一拍就停滞、含仅 mtime 触碰的假活）→ 早停必须触发；
* **负控**：正常进展（密集/稀疏两种节奏）→ 早停必须不触发，且观测拍数可数；
* **信息增益**：新工件 → gain>0；仅触碰 mtime → gain==0（负控）；
* **DAG 端到端**：在真实 PostgreSQL 上建探针表（不碰生产行），验证 ``blocked`` 落库与
  调度器可派谓词失效；``campaign_events`` 写入用事务回滚演练验证 CHECK/FK 合法性。

用法::

    python3 tools/progress_metrics_eval.py --out artifacts/p148w2_n08 --figures
    python3 tools/progress_metrics_eval.py --out /tmp/eval --skip-dag --skip-tests

退出码：0=全部判据通过；1=有判据未通过；2=有判据未运行（honest 未跑不算通过）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.taskplane.progress_metrics import (  # noqa: E402
    ACCEPTED_SCHEMAS,
    CampaignNodeStore,
    DAGError,
    ExternalSignalAdapter,
    ProgressOrchestrator,
    ProgressThresholds,
)

UTC = timezone.utc
TEST_FILE = "tests/test_progress_metrics_p148w2_n08.py"
DENYLIST_PATH = REPO_ROOT / "tests/fixtures/domain_term_denylist_p148w2_n08.json"


def load_denylist() -> List[str]:
    """域词禁入表是治理工件（JSON），平台代码里不写域词条本身。"""
    return list(json.loads(DENYLIST_PATH.read_text(encoding="utf-8"))["terms"])


# ── 场景模型 ────────────────────────────────────────────────────────────────
CONTENT_ACTIONS = ("add_file", "edit_file", "add_lines")


@dataclass
class ScenarioSpec:
    name: str
    kind: str                      # "stagnant" | "healthy"
    seed: int
    progress_cycles: int = 0       # 停滞前的进展拍数（不含 warmup）
    stagnant_cycles: int = 4       # 种植停滞段长度
    cadence: int = 1               # 正常场景每 N 拍产出一次
    touch_mode: bool = False       # 停滞段是否用 mtime-only 触碰伪装活动
    total_cycles: int = 12
    pytest_rate: float = 0.8
    criteria: Dict[str, bool] = field(default_factory=lambda: {"c1": True, "c2": False})


def _seed_base_files(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "notes.md").write_text("# notes\n- baseline\n", encoding="utf-8")


def _apply_action(root: Path, kind: str, cycle: int) -> None:
    """只改内容或只碰 mtime —— 这是「假活 vs 真进展」的唯一分界线。"""
    if kind == "add_file":
        p = root / "src" / f"unit_{cycle:03d}.py"
        p.write_text(f"def unit_{cycle}():\n    return {cycle}\n", encoding="utf-8")
    elif kind == "add_lines":
        p = root / "src" / "core.py"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"# added at cycle {cycle}\nSTAGE={cycle}\n")
    elif kind == "edit_file":
        p = root / "src" / "notes.md"
        text = p.read_text(encoding="utf-8")
        p.write_text(text + f"- cycle {cycle} decision recorded\n", encoding="utf-8")
    elif kind == "touch":
        # mtime-only：内容哈希不变，必须被判为「无进展」
        for rel in ("src/core.py", "src/notes.md"):
            q = root / rel
            if q.exists():
                st = q.stat()
                os.utime(q, (st.st_atime, st.st_mtime + 60))
    elif kind == "none":
        pass
    else:  # pragma: no cover
        raise ValueError(f"unknown action {kind}")


def plan_actions(spec: ScenarioSpec) -> List[str]:
    """逐拍动作表（第 0 拍是 warmup，不产生动作，动作从第 1 拍开始）。"""
    actions: List[str] = []
    for i in range(1, spec.total_cycles):
        if spec.kind == "healthy":
            actions.append(("add_file" if i % 2 == 0 else "add_lines") if i % spec.cadence == 0 else "none")
        else:
            if i <= spec.progress_cycles:
                actions.append("add_file" if i % 2 == 0 else "add_lines")
            elif i <= spec.progress_cycles + spec.stagnant_cycles:
                actions.append("touch" if spec.touch_mode else "none")
            else:
                actions.append("none")
    return actions


def run_scenario(spec: ScenarioSpec, workdir: Path,
                 thresholds: Optional[ProgressThresholds] = None) -> Dict[str, Any]:
    th = thresholds or ProgressThresholds()
    root = workdir / spec.name
    if root.exists():
        shutil.rmtree(root)
    _seed_base_files(root)
    orch = ProgressOrchestrator(root, node_id=None, thresholds=th, output_dir=root / "runs" / "progress")
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    steps: List[Dict[str, Any]] = []
    # warmup 拍（基线；不改动作）
    res = orch.tick(pytest_result={"total": 10, "passed": int(spec.pytest_rate * 10), "pass_rate": spec.pytest_rate},
                    criteria=dict(spec.criteria), ts=t0)
    steps.append({"cycle": 0, "action": "warmup", "content_changed": False,
                  "info_gain": res["snapshot"]["info_gain"], "hard_no_progress": res["snapshot"]["hard_no_progress"],
                  "is_stalled": res["verdict"]["is_stalled"], "recommendation": res["verdict"]["recommendation"],
                  "consecutive": res["verdict"]["consecutive_no_progress_cycles"],
                  "composite": res["snapshot"]["composite_score"], "warmup": True})

    early_stop = False
    early_stop_cycle: Optional[int] = None
    report: Optional[Dict[str, Any]] = None
    for idx, action in enumerate(plan_actions(spec), start=1):
        _apply_action(root, action, idx)
        res = orch.tick(pytest_result={"total": 10, "passed": int(spec.pytest_rate * 10),
                                       "pass_rate": spec.pytest_rate},
                        criteria=dict(spec.criteria), ts=t0 + timedelta(minutes=5 * idx))
        if res.get("early_stop_report"):
            early_stop = True
            if early_stop_cycle is None:
                early_stop_cycle = idx
            report = res["early_stop_report"]
        steps.append({"cycle": idx, "action": action, "content_changed": action in CONTENT_ACTIONS,
                      "info_gain": res["snapshot"]["info_gain"],
                      "hard_no_progress": res["snapshot"]["hard_no_progress"],
                      "is_stalled": res["verdict"]["is_stalled"],
                      "recommendation": res["verdict"]["recommendation"],
                      "consecutive": res["verdict"]["consecutive_no_progress_cycles"],
                      "trend_flat": res["verdict"].get("trend_flat", False),
                      "composite": res["snapshot"]["composite_score"], "warmup": False})

    # 标注：尾段硬无进展拍数（由动作表构造，与被测模块输出无关）
    trailing = 0
    for s in steps:
        if s["warmup"]:
            continue
        trailing = trailing + 1 if not s["content_changed"] else 0
        s["label_consecutive_no_progress"] = trailing
        s["label_stalled"] = trailing >= th.no_progress_cycles

    report_check = None
    if report:
        report_check = {
            "honest_fail": report.get("honest_fail") is True,
            "curve_points": len(report.get("progress_curve") or []),
            "alternatives": len(report.get("alternative_paths") or []),
            "sha_len": len(report.get("report_sha256") or ""),
            "file_exists": Path(report.get("report_path") or "").exists(),
        }
    first_no_progress = next((s_["cycle"] for s_ in steps
                              if not s_["warmup"] and not s_["content_changed"]), None)
    # 触发时已累计的连续无进展拍数（1 = 首个无进展拍就触发；目标值 = no_progress_cycles）
    latency = (early_stop_cycle - first_no_progress + 1
               if (early_stop_cycle is not None and first_no_progress is not None) else None)
    return {"name": spec.name, "kind": spec.kind, "seed": spec.seed,
            "report_check": report_check,
            "early_stop_cycle": early_stop_cycle,
            "detection_latency_cycles": latency,
            "spec": {k: v for k, v in spec.__dict__.items()},
            "steps": steps, "early_stop_triggered": early_stop, "early_stop_report": report,
            "journal_ok": orch.journal.verify()[0],
            "snapshot_count": len(steps)}


# ── 判据统计 ────────────────────────────────────────────────────────────────
def confusion(scenarios: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    tp = fp = fn = tn = 0
    trend_total = trend_on_healthy = label_total = 0
    for sc in scenarios:
        for s in sc["steps"]:
            if s["warmup"]:
                continue
            label = bool(s["label_stalled"])
            pred = bool(s["is_stalled"])
            trend_total += 1 if s.get("trend_flat") else 0
            trend_on_healthy += 1 if (s.get("trend_flat") and sc["kind"] == "healthy") else 0
            label_total += 1 if label else 0
            if label and pred:
                tp += 1
            elif label and not pred:
                fn += 1
            elif pred and not label:
                fp += 1
            else:
                tn += 1
    total = tp + fp + fn + tn
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": total,
            "accuracy": round((tp + tn) / total, 6) if total else None,
            "precision": round(tp / (tp + fp), 6) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 6) if (tp + fn) else None,
            "stalled_cycles_labeled": label_total,
            # 斜率规则只作趋势告警：如实报它在正常进展场景里的告警次数（不掩盖噪声）
            "trend_warnings_total": trend_total,
            "trend_warnings_on_healthy_cycles": trend_on_healthy,
            "trend_warning_fire_rate_on_healthy": (round(trend_on_healthy / rates_healthy_n(scenarios), 6)
                                                   if rates_healthy_n(scenarios) else None)}


def rates_healthy_n(scenarios: Sequence[Dict[str, Any]]) -> int:
    return sum(max(0, sc["snapshot_count"] - 1) for sc in scenarios if sc["kind"] == "healthy")


def info_gain_stats(scenarios: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ok = bad = 0
    new_ok = new_n = 0
    touch_ok = touch_n = 0
    warmup_excluded = 0
    for sc in scenarios:
        for s in sc["steps"]:
            if s["warmup"]:
                warmup_excluded += 1
                continue
            changed = bool(s["content_changed"])
            pred = s["info_gain"] > 0
            if changed == pred:
                ok += 1
            else:
                bad += 1
            if changed:
                new_n += 1
                new_ok += 1 if pred else 0
            elif s["action"] == "touch":
                touch_n += 1
                touch_ok += 1 if not pred else 0
    n = ok + bad
    return {"n": n, "correct": ok, "wrong": bad,
            "accuracy": round(ok / n, 6) if n else None,
            "new_artifact_n": new_n, "new_artifact_positive": new_ok,
            "new_artifact_rate": round(new_ok / new_n, 6) if new_n else None,
            "touch_n": touch_n, "touch_zero_gain": touch_ok,
            "touch_zero_gain_rate": round(touch_ok / touch_n, 6) if touch_n else None,
            "warmup_excluded": warmup_excluded}


def trigger_rates(scenarios: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for kind in ("stagnant", "healthy"):
        group = [s for s in scenarios if s["kind"] == kind]
        fired = sum(1 for s in group if s["early_stop_triggered"])
        out[kind] = {"scenarios": len(group), "fired": fired,
                     "rate": round(fired / len(group), 6) if group else None,
                     "cycles_observed": sum(max(0, s["snapshot_count"] - 1) for s in group)}
    return out


# ── DAG 端到端（真实 PG，探针表，不动生产行） ───────────────────────────────
def dag_probe(keep: bool = False) -> Dict[str, Any]:
    store = CampaignNodeStore()
    probe_table = "p148w2_n08_probe"
    result: Dict[str, Any] = {"table": probe_table, "status": "not_run"}
    try:
        rows = store._exec("SELECT COALESCE(min(id), 1) FROM campaign_plans;", {})
        plan_id = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 1
        store._exec(f"DROP TABLE IF EXISTS {probe_table};", {})
        store._exec(
            f"CREATE TABLE {probe_table} AS SELECT id, plan_id, mission_id, name, status, "
            f"block_reason, consecutive_fails, actual_end, updated_at FROM {store.table} WITH NO DATA;", {})
        store._exec(
            f"INSERT INTO {probe_table} (id, plan_id, name, status, consecutive_fails) VALUES "
            f"(900000001, {plan_id}, 'probe-pending', 'pending', 0), "
            f"(900000002, {plan_id}, 'probe-done', 'done', 0);", {})

        probe = CampaignNodeStore(table=probe_table)
        before_eligible = probe.is_dispatch_eligible(900000001)
        block_pending = probe.mark_node_blocked(900000001, "eval: planted stagnation")
        row_after = probe.get_node(900000001) or {}
        block_done = probe.mark_node_blocked(900000002, "eval: planted stagnation")
        row_done_after = probe.get_node(900000002) or {}
        after_eligible = probe.is_dispatch_eligible(900000001)
        event_dry = probe.record_block_event(
            900000001, {"source": "eval", "probe": True}, plan_id=plan_id, dry_run=True)

        result.update({
            "status": "ran",
            "plan_id": plan_id,
            "eligible_before": before_eligible,
            "block": block_pending.as_dict(),
            "status_after": row_after.get("status"),
            "block_reason_after": row_after.get("block_reason"),
            "eligible_after": after_eligible,
            "terminal_block": block_done.as_dict(),
            "terminal_status_unchanged": row_done_after.get("status") == "done",
            "event_insert_dry_run_id": event_dry,
            "event_rollback_left_no_row": _count_events_by_detail(store, "eval"),
            "ok": bool(before_eligible is True and block_pending.blocked
                       and row_after.get("status") == "blocked"
                       and after_eligible is False
                       and block_done.affected == 0
                       and row_done_after.get("status") == "done"
                       and isinstance(event_dry, int)
                       and _count_events_by_detail(store, "eval") == 0),
        })
    except (DAGError, OSError, ValueError) as exc:
        result.update({"status": "not_run", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        try:
            if not keep:
                store._exec(f"DROP TABLE IF EXISTS {probe_table};", {})
        except (DAGError, OSError):
            pass
    return result


def _count_events_by_detail(store: CampaignNodeStore, source: str) -> int:
    rows = store._exec(
        "SELECT count(*) FROM campaign_events WHERE detail->>'source' = :'src';", {"src": source})
    return int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0


# ── 自适应：上游适配层 ──────────────────────────────────────────────────────
def adapter_probe(tmp: Path) -> Dict[str, Any]:
    good = tmp / "external_signals_probe.jsonl"
    good.write_text(
        json.dumps({"schema": ACCEPTED_SCHEMAS[0], "ts": "2026-11-10T08:00:00+00:00",
                    "kind": "resume", "checkpoint_restored": True, "mission_id": 9930}) + "\n"
        + json.dumps({"schema": "tp.unknown.v9", "ts": "2026-11-10T08:05:00+00:00", "kind": "x"}) + "\n",
        encoding="utf-8")
    ok_res = ExternalSignalAdapter().load(good)
    missing = ExternalSignalAdapter().load(tmp / "does_not_exist.jsonl")
    return {"accepted": ok_res.accepted, "rejected": ok_res.rejected,
            "degraded": ok_res.degraded, "kinds": ok_res.kinds,
            "missing_file_degraded": missing.degraded,
            "missing_file_records": len(missing.records),
            "ok": ok_res.accepted == 1 and ok_res.rejected == 1 and ok_res.degraded
                  and missing.degraded and missing.records == []}


# ── 零领域语义体检 ──────────────────────────────────────────────────────────
def domain_scan(paths: Sequence[Path]) -> Dict[str, Any]:
    hits: List[Dict[str, str]] = []
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        for term in DENYLIST_PATH and load_denylist():
            if term.lower() in text:
                hits.append({"file": str(p.relative_to(REPO_ROOT)), "term": term})
    return {"files_scanned": [str(p.relative_to(REPO_ROOT)) for p in paths if p.exists()],
            "hits": hits, "clean": not hits}


# ── 图 ──────────────────────────────────────────────────────────────────────
def make_figures(scenarios: Sequence[Dict[str, Any]], rates: Dict[str, Any],
                 gain: Dict[str, Any], dag: Dict[str, Any], out_dir: Path) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # 图 1：代表性进展曲线（停滞 vs 正常）+ 连续无进展计数器
    stale = next(s for s in scenarios if s["kind"] == "stagnant" and s["seed"] % 2 == 0)
    healthy = next(s for s in scenarios if s["kind"] == "healthy")
    fig, axes = plt.subplots(2, 1, figsize=(8, 6.4), sharex=True)
    for sc, label, color in ((healthy, "healthy progress", "#1f77b4"), (stale, "planted stagnation", "#d62728")):
        xs = [s["cycle"] for s in sc["steps"]]
        axes[0].plot(xs, [s["composite"] for s in sc["steps"]], "o-", color=color, label=label)
        axes[1].plot(xs, [s.get("label_consecutive_no_progress", 0) for s in sc["steps"]], "s--",
                     color=color, alpha=0.55, label=f"{label} (ground truth)")
        axes[1].plot(xs, [s["consecutive"] for s in sc["steps"]], "o-", color=color,
                     label=f"{label} (detector)")
    axes[0].set_ylabel("composite progress score")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)
    axes[1].axhline(3, color="k", lw=1, ls=":", label="early-stop threshold N=3")
    axes[1].set_ylabel("consecutive no-progress cycles")
    axes[1].set_xlabel("cycle (0 = warm-up baseline)")
    axes[1].legend(fontsize=7)
    axes[1].grid(alpha=0.3)
    fig.suptitle("Progress signals and stagnation counter (labels from construction, not from the detector)")
    fig.tight_layout()
    p1 = out_dir / "fig1_progress_and_stagnation.png"
    fig.savefig(p1, dpi=160)
    plt.close(fig)
    figs.append({"name": "fig1_progress_and_stagnation.png",
                 "caption": "Composite progress score and consecutive no-progress counter: healthy vs planted stagnation "
                            "(dashed = ground-truth labels built from the action plan)."})

    # 图 2：控制实验（早停触发率 + 信息增益判定率）
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    kinds = ["stagnant", "healthy"]
    vals = [rates[k]["rate"] * 100 for k in kinds]
    bars = axes[0].bar(["planted stagnation\n(n=%d)" % rates["stagnant"]["scenarios"],
                        "healthy progress\n(n=%d)" % rates["healthy"]["scenarios"]],
                       vals, color=["#d62728", "#2ca02c"])
    axes[0].set_ylim(0, 118)
    axes[0].set_ylabel("early-stop trigger rate [%]")
    for b, v in zip(bars, vals):
        axes[0].text(b.get_x() + b.get_width() / 2, v + 3, f"{v:.1f}%", ha="center", fontsize=9)
    axes[0].grid(axis="y", alpha=0.3)
    axes[0].set_title("Positive / negative control")

    labels = ["all cycles", "new artifact\n(gain>0)", "mtime-only touch\n(gain==0)"]
    gvals = [gain["accuracy"] * 100, gain["new_artifact_rate"] * 100, gain["touch_zero_gain_rate"] * 100]
    bars2 = axes[1].bar(labels, gvals, color=["#1f77b4", "#2ca02c", "#9467bd"])
    axes[1].set_ylim(0, 118)
    axes[1].set_ylabel("accuracy [%]")
    for b, v in zip(bars2, gvals):
        axes[1].text(b.get_x() + b.get_width() / 2, v + 3, f"{v:.1f}%", ha="center", fontsize=9)
    axes[1].grid(axis="y", alpha=0.3)
    axes[1].set_title("Information-gain sign decision")
    fig.tight_layout()
    p2 = out_dir / "fig2_controls.png"
    fig.savefig(p2, dpi=160)
    plt.close(fig)
    figs.append({"name": "fig2_controls.png",
                 "caption": "Machine-checkable controls: early-stop trigger rate (left) and information-gain sign "
                            "accuracy including mtime-only negative control (right)."})

    # 图 3：DAG 回写效果
    if dag.get("status") == "ran":
        fig, ax = plt.subplots(figsize=(6.2, 3.2))
        names = ["dispatch-eligible\nbefore", "dispatch-eligible\nafter block"]
        vals = [1 if dag.get("eligible_before") else 0, 1 if dag.get("eligible_after") else 0]
        bars = ax.bar(names, vals, color=["#1f77b4", "#8c564b"], width=0.5)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["no", "yes"])
        ax.set_ylim(0, 1.35)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.05, "yes" if v else "no", ha="center", fontsize=10)
        ax.set_title(f"campaign_nodes write-back on real PostgreSQL (node {dag.get('block', {}).get('node_id')}); "
                     f"terminal node untouched: {dag.get('terminal_status_unchanged')}")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        p3 = out_dir / "fig3_dag_block.png"
        fig.savefig(p3, dpi=160)
        plt.close(fig)
        figs.append({"name": "fig3_dag_block.png",
                     "caption": "DAG write-back: the probe node stops being dispatch-eligible after being blocked; "
                                "an already-terminal node keeps status 'done' (affected=0)."})
    return figs


# ── 主流程 ──────────────────────────────────────────────────────────────────
def build_scenarios() -> List[ScenarioSpec]:
    specs: List[ScenarioSpec] = []
    # 正控：种植停滞（含从零拍起停滞、含 mtime-only 假活）
    for i in range(10):
        specs.append(ScenarioSpec(
            name=f"stagnant_{i:02d}", kind="stagnant", seed=1000 + i,
            progress_cycles=i % 5,            # 0..4 拍真实进展后停滞
            stagnant_cycles=4 + (i % 3),      # 4..6 拍停滞
            touch_mode=bool(i % 2),           # 一半用 mtime-only 伪装
            total_cycles=12))
    # 负控：正常进展（密集 / 稀疏两种节奏）
    for i in range(10):
        specs.append(ScenarioSpec(
            name=f"healthy_{i:02d}", kind="healthy", seed=2000 + i,
            cadence=1 if i % 2 == 0 else 2,   # 稀疏：每 2 拍才产出一次
            total_cycles=12))
    return specs


def _run_pytest() -> Dict[str, Any]:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"status": "not_run", "reason": "invoked under pytest"}
    proc = subprocess.run([sys.executable, "-m", "pytest", TEST_FILE, "-q", "--tb=short"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True)
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()][-3:]
    return {"status": "ran", "returncode": proc.returncode, "tail": tail,
            "passed": proc.returncode == 0}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P148-W2-N08 acceptance evaluation")
    ap.add_argument("--out", default="artifacts/p148w2_n08")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--skip-dag", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--keep-probe-table", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    th = ProgressThresholds()

    specs = build_scenarios()
    tmp_root = Path(tempfile.mkdtemp(prefix="p148n08_scen_"))
    scenarios: List[Dict[str, Any]] = []
    try:
        for spec in specs:
            scenarios.append(run_scenario(spec, tmp_root, th))
    finally:
        if os.environ.get("P148N08_KEEP_WORKTREES") != "1":
            shutil.rmtree(tmp_root, ignore_errors=True)

    cm = confusion(scenarios)
    gain = info_gain_stats(scenarios)
    rates = trigger_rates(scenarios)
    latencies = [sc["detection_latency_cycles"] for sc in scenarios
                 if sc.get("detection_latency_cycles") is not None]
    rates["detection_latency_cycles"] = {
        "n": len(latencies),
        "min": min(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "target": "== no_progress_cycles (3) 连续无进展拍数",
    }
    dag = {"status": "not_run", "reason": "skipped by flag"} if args.skip_dag else dag_probe(args.keep_probe_table)
    adapter = adapter_probe(out_dir)
    domain = domain_scan([REPO_ROOT / rel for rel in
                          ("ops/taskplane/progress_metrics.py",
                           "tools/progress_metrics_eval.py",
                           "tests/test_progress_metrics_p148w2_n08.py")])

    # 早停报告资产完整性（C6）
    reports = [sc["early_stop_report"] for sc in scenarios if sc.get("early_stop_report")]
    checks = [sc["report_check"] for sc in scenarios if sc.get("report_check")]
    c6_ok = bool(checks) and all(
        c["honest_fail"] and c["curve_points"] >= 3 and c["alternatives"] >= 2
        and c["sha_len"] == 64 and c["file_exists"] for c in checks)

    tests = {"status": "not_run", "reason": "skipped by flag"} if args.skip_tests else _run_pytest()

    criteria = [
        {"id": "C1", "name": "停滞检测：连续3轮无进展→停滞标记准确率",
         "target": "100%", "measured": cm, "passed": cm["accuracy"] == 1.0, "unit": "ratio"},
        {"id": "C2", "name": "早停触发：故意停滞场景→早停触发率（附检测延迟拍数）",
         "target": "100%，延迟=no_progress_cycles",
         "measured": dict(rates["stagnant"], latency=rates["detection_latency_cycles"]),
         "passed": (rates["stagnant"]["rate"] == 1.0
                    and rates["detection_latency_cycles"]["max"] == th.no_progress_cycles),
         "unit": "ratio"},
        {"id": "C3", "name": "负控正常进展：正常场景→早停触发率",
         "target": "0%", "measured": rates["healthy"], "passed": rates["healthy"]["rate"] == 0.0, "unit": "ratio"},
        {"id": "C4", "name": "信息增益：新工件→gain>0 判定准确率（含 mtime-only 负控）",
         "target": "≥90%", "measured": gain,
         "passed": (gain["new_artifact_rate"] or 0) >= 0.9 and (gain["touch_zero_gain_rate"] or 0) == 1.0,
         "unit": "ratio"},
        {"id": "C5", "name": "端到端：与 campaign DAG 调度器对接→停滞节点自动 blocked",
         "target": "blocked 落库且可派谓词失效、终态不被覆盖", "measured": dag,
         "passed": bool(dag.get("ok")), "unit": "bool"},
        {"id": "C6", "name": "报告生成：早停报告含进展曲线+替代推荐+证据链",
         "target": ">=1 份报告，字段齐备", "measured": {"reports": len(reports), "ok": c6_ok},
         "passed": c6_ok, "unit": "bool"},
        {"id": "C7", "name": "pytest 全绿 + 零领域语义",
         "target": "tests passed & domain hits = 0",
         "measured": {"tests": tests, "domain": domain},
         "passed": bool(tests.get("passed")) and domain["clean"], "unit": "bool"},
        {"id": "C8", "name": "上游适配层：白名单 schema、未知 schema 拒绝且降级不崩",
         "target": "accepted=1/rejected=1/degraded=True", "measured": adapter, "passed": adapter["ok"],
         "unit": "bool"},
    ]

    not_run = [c["id"] for c in criteria if c["measured"].get("status") == "not_run"]
    failed = [c["id"] for c in criteria if not c["passed"]]
    summary = {"node": "p148w2-n08-progress-metrics-maturity", "mission_id": 9930,
               "generated_at": datetime.now(UTC).isoformat(),
               "repo": "TensorPlatform",
               "module": "ops/taskplane/progress_metrics.py",
               "test_file": TEST_FILE,
               "thresholds": th.as_dict(),
               "scenarios": {"specs": [s.__dict__ for s in specs],
                             "count": len(scenarios),
                             "cycles_total": sum(s["snapshot_count"] for s in scenarios)},
               "criteria": criteria,
               "detection_latency_cycles": rates["detection_latency_cycles"],
               "failed": failed, "not_run": not_run,
               "all_passed": not failed and not not_run}

    (out_dir / "scenario_results.json").write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "criteria.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    example = next((sc["early_stop_report"] for sc in scenarios if sc.get("early_stop_report")), None)
    if example:
        keep_dir = out_dir / "early_stop_examples"
        keep_dir.mkdir(parents=True, exist_ok=True)
        keep_path = keep_dir / "early_stop_example.json"
        keep_path.write_text(json.dumps(example, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
        try:
            summary["example_early_stop_report"] = str(keep_path.relative_to(REPO_ROOT))
        except ValueError:  # --out 在仓库外（临时对比跑）时保留绝对路径
            summary["example_early_stop_report"] = str(keep_path)
        (out_dir / "criteria.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    figs = make_figures(scenarios, rates, gain, dag, out_dir) if args.figures else []
    if figs:
        (out_dir / "figures_metadata.json").write_text(
            json.dumps(figs, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["figures"] = figs
        (out_dir / "criteria.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps({"criteria": {c["id"]: c["passed"] for c in criteria},
                      "failed": failed, "not_run": not_run,
                      "out": str(out_dir)}, ensure_ascii=False, indent=2))
    if failed:
        return 1
    if not_run:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())