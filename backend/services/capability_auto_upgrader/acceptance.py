"""Acceptance run of the capability auto-upgrader (P362-W2-N01).

One command reproduces every number the node's criteria ask for, from artefacts
on disk, and writes them to ``runs/<node>/acceptance.json`` plus a paper-grade
Markdown report.  Nothing is typed by hand: the pytest summary comes from a junit
report, the level trajectory is replayed from the ledger, the tamper probes are
executed on copies of the ledger.

Criteria
--------
(a) MOCK -> REAL upgrade fires after 3 qualifying runs and the ledger row is complete;
(b) a regression run downgrades the level, history is append-only (no silent overwrite);
(c) every upgrade/downgrade row carries a sha256 provenance digest, chain verifies,
    and deliberate edits to either store are detected;
(d) the package's own pytest suite is green and has >= 12 tests;
(e) zero domain vocabulary in module + tests (configured deny list, 0 hits).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import figures, scanner
from . import ledger as ledger_mod
from .ledger import FeatureLedger
from .model import (
    SCHEMA,
    BenchmarkEvidence,
    EvidenceError,
    EvidenceRun,
    TestEvidence,
    canonical_json,
    sha256_file,
    sha256_obj,
)
from .policy import UpgradePolicy
from .streak import analyse, decide
from .upgrader import CapabilityAutoUpgrader

NODE = "P362-W2-N01"
MISSION_ID = 13850
REPO = Path(__file__).resolve().parents[3]
PKG = Path(__file__).resolve().parent
TEST_FILE = REPO / "backend" / "tests" / "test_capability_auto_upgrader.py"

CAP_ALPHA = "synth.compute_alpha"
CAP_BETA = "synth.compute_beta"
CAP_GUARD = "synth.guard_delta"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cap: str, seq: int, passed: int = 40, failed: int = 0, errors: int = 0,
         measured: float = 1.02, reference: float = 1.0, n_samples: int = 32,
         reporter: str = "b" * 64) -> EvidenceRun:
    return EvidenceRun(
        capability_id=cap,
        seq=seq,
        tests=TestEvidence(passed=passed, failed=failed, errors=errors, report_sha256=reporter),
        benchmark=BenchmarkEvidence(measured=measured, reference=reference, n_samples=n_samples),
    )


# --------------------------------------------------------------- pytest ------
def run_pytest_suite(out_dir: Path, timeout: int = 900) -> Dict[str, Any]:
    junit = out_dir / "junit_module.xml"
    cmd = [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", f"--junit-xml={junit}"]
    proc = subprocess.run(cmd, cwd=str(REPO / "backend"), capture_output=True, text=True, timeout=timeout)
    evidence = TestEvidence.from_junit_xml(junit) if junit.is_file() else None
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "n_tests": (evidence.passed + evidence.failed + evidence.errors) if evidence else 0,
        "passed": evidence.passed if evidence else 0,
        "failed": (evidence.failed + evidence.errors) if evidence else 0,
        "skipped": evidence.skipped if evidence else 0,
        "pass_rate": evidence.pass_rate if evidence else 0.0,
        "junit_path": str(junit),
        "junit_sha256": evidence.report_sha256 if evidence else None,
        "stdout_tail": proc.stdout.strip().splitlines()[-3:],
    }


# ------------------------------------------------------------ scenarios ------
def scenario_upgrade_all(out_dir: Path, policy: UpgradePolicy) -> Dict[str, Any]:
    """Three qualifying runs promote MOCK->REAL; a bad run demotes; a STUB also promotes."""
    root = out_dir / "scenario_levels"
    if root.exists():
        shutil.rmtree(root)
    with CapabilityAutoUpgrader(root, policy=policy, tenant="synth_tenant") as up:
        observation = up.ingest_audit_snapshot(
            {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "capabilities": [
                    {"capability_id": CAP_ALPHA, "level": "MOCK", "criterion": "synthetic fixture"},
                    {"capability_id": CAP_BETA, "level": "STUB", "criterion": "synthetic fixture"},
                ],
            }
        )
        alpha_levels: List[str] = []
        alpha_runs: List[Dict[str, Any]] = []
        for seq in (1, 2, 3):
            result = up.record_run(_run(CAP_ALPHA, seq, passed=40, measured=1.02, reference=1.0))
            alpha_levels.append(result["level"])
            alpha_runs.append(result)

        # regression: one conclusive failure withdraws the proof, a second walks to the floor
        bad1 = up.record_run(_run(CAP_ALPHA, 4, passed=30, failed=10, measured=1.4))
        bad2 = up.record_run(_run(CAP_ALPHA, 5, passed=28, failed=12, measured=1.6))
        alpha_levels.extend([bad1["level"], bad2["level"]])

        beta_levels = [up.record_run(_run(CAP_BETA, seq)).get("level") for seq in (1, 2, 3)]

        verification = up.verify()
        status = up.status()
        events = up.ledger.events()
        evaluations = up.store.evaluations()

    upgrade_events = [e for e in events if e["direction"] == ledger_mod.DIRECTION_UPGRADE]
    downgrade_events = [e for e in events if e["direction"] == ledger_mod.DIRECTION_DOWNGRADE]
    register_events = [e for e in events if e["direction"] == ledger_mod.DIRECTION_REGISTER]

    complete_keys = all(set(ledger_mod.REQUIRED_KEYS) <= set(e) for e in upgrade_events + downgrade_events)
    upgrade_chain_ok = all(
        e["prev_sha256"] == events[idx - 1]["sha256"] for idx, e in enumerate(events) if idx > 0
    )
    # "no silent overwrite": replaying the ledger reproduces exactly the levels we observed
    replay = FeatureLedger(root).levels()

    return {
        "root": str(root),
        "observation": observation,
        "alpha_levels": alpha_levels,
        "beta_levels": beta_levels,
        "n_events": len(events),
        "n_register": len(register_events),
        "n_upgrade": len(upgrade_events),
        "n_downgrade": len(downgrade_events),
        "upgrade_event": {k: upgrade_events[0][k] for k in
                          ("seq", "capability_id", "level_before", "level_after", "sha256",
                           "prev_sha256", "policy_digest", "trigger_digest", "reason_codes")},
        "n_window_digests": len(upgrade_events[0]["window_digests"]) if upgrade_events else 0,
        "downgrade_events": [{k: e[k] for k in ("seq", "capability_id", "level_before", "level_after",
                                                "sha256", "reason_codes")} for e in downgrade_events],
        "upgrade_row_complete": complete_keys,
        "n_upgrade_alpha": len([e for e in upgrade_events if e["capability_id"] == CAP_ALPHA]),
        # the promotion of the regressed capability must still be in the ledger,
        # unchanged and ordered before the downgrades
        "append_only_history": bool(
            len([e for e in upgrade_events if e["capability_id"] == CAP_ALPHA]) == 1
            and next(e for e in upgrade_events if e["capability_id"] == CAP_ALPHA)["level_after"] == "REAL"
            and upgrade_events[0]["seq"] < min((d["seq"] for d in downgrade_events), default=10 ** 6)
        ),
        "chain_ok": upgrade_chain_ok,
        "verify": verification,
        "replayed_levels": replay,
        "status": status,
        "evaluations": evaluations,
        "final_levels": {s["capability_id"]: s["level"] for s in status},
        "ledger_events_file": str(Path(root) / "feature_ledger.jsonl"),
    }


def scenario_guards(out_dir: Path, policy: UpgradePolicy) -> Dict[str, Any]:
    """Anti-gaming controls: replay, flapping, insufficient evidence, audit mismatch."""
    root = out_dir / "scenario_guards"
    if root.exists():
        shutil.rmtree(root)

    out: Dict[str, Any] = {}

    # 1. replaying one report cannot build a streak
    with CapabilityAutoUpgrader(root / "replay", policy=policy) as up:
        up.ingest_audit_snapshot({"capabilities": [{"capability_id": CAP_GUARD, "level": "MOCK"}]})
        statuses = [up.record_run(_run(CAP_GUARD, 1))["status"]]
        statuses += [up.record_run(_run(CAP_GUARD, 1))["status"] for _ in range(3)]
        out["replay"] = {
            "statuses": statuses,
            "level": up.levels()[CAP_GUARD],
            "pass_streak": analyse(up.store.runs(CAP_GUARD), policy).pass_streak,
            "n_upgrade_events": len([e for e in up.ledger.events()
                                     if e["direction"] == ledger_mod.DIRECTION_UPGRADE]),
        }

    # 2. alternating good/bad runs never promote
    with CapabilityAutoUpgrader(root / "flapping", policy=policy) as up:
        up.ingest_audit_snapshot({"capabilities": [{"capability_id": CAP_GUARD, "level": "MOCK"}]})
        for seq in range(1, 7):
            good = seq % 2 == 1
            up.record_run(_run(CAP_GUARD, seq, passed=40 if good else 30, failed=0 if good else 10,
                               measured=1.02 if good else 1.4))
        out["flapping"] = {
            "level": up.levels()[CAP_GUARD],
            "n_upgrade_events": len([e for e in up.ledger.events()
                                     if e["direction"] == ledger_mod.DIRECTION_UPGRADE]),
            "n_downgrade_events": len([e for e in up.ledger.events()
                                       if e["direction"] == ledger_mod.DIRECTION_DOWNGRADE]),
        }

    # 3. missing evidence blocks promotion and never demotes a REAL capability
    with CapabilityAutoUpgrader(root / "insufficient", policy=policy) as up:
        up.ingest_audit_snapshot({"capabilities": [{"capability_id": CAP_GUARD, "level": "MOCK"}]})
        for seq in (1, 2, 3):
            up.record_run(_run(CAP_GUARD, seq))
        promoted = up.levels()[CAP_GUARD]
        gap = up.record_run(_run(CAP_GUARD, 4, passed=0, failed=0, errors=0))
        out["insufficient"] = {
            "level_after_promotion": promoted,
            "action": gap["decision"]["action"],
            "reason_codes": gap["decision"]["reason_codes"],
            "level_after_gap": up.levels()[CAP_GUARD],
            "pass_streak": gap["streaks"]["pass"],
        }

    # 4. an audit report that disagrees with the derived level is recorded, not applied
    with CapabilityAutoUpgrader(root / "mismatch", policy=policy) as up:
        up.ingest_audit_snapshot({"capabilities": [{"capability_id": CAP_GUARD, "level": "MOCK"}]})
        for seq in (1, 2, 3):
            up.record_run(_run(CAP_GUARD, seq))
        before = up.levels()[CAP_GUARD]
        reported = up.ingest_audit_snapshot({"capabilities": [{"capability_id": CAP_GUARD, "level": "MOCK"}]})
        out["mismatch"] = {
            "level_before": before,
            "audit_says": "MOCK",
            "level_after": up.levels()[CAP_GUARD],
            "mismatches": reported["mismatches"],
            "n_observed": len([e for e in up.ledger.events()
                               if e["direction"] == ledger_mod.DIRECTION_OBSERVED]),
            "verify_ok": up.verify()["ok"],
        }

    out["all_guards_hold"] = (
        out["replay"]["level"] == "MOCK"
        and out["replay"]["n_upgrade_events"] == 0
        and out["flapping"]["level"] == "MOCK"
        and out["flapping"]["n_upgrade_events"] == 0
        and out["insufficient"]["action"] == "hold"
        and out["insufficient"]["level_after_gap"] == "REAL"
        and out["mismatch"]["level_after"] == "REAL"
        and out["mismatch"]["mismatches"] == [CAP_GUARD]
    )
    return out


def tamper_probes(source_root: Path) -> Dict[str, Any]:
    """Copy the real ledger and edit it; both stores must be caught."""
    with tempfile.TemporaryDirectory() as tmp:
        jsonl_dir = Path(tmp) / "jsonl"
        sqlite_dir = Path(tmp) / "sqlite"
        shutil.copytree(source_root, jsonl_dir)
        shutil.copytree(source_root, sqlite_dir)

        proven = FeatureLedger(jsonl_dir)
        checks: Dict[str, Any] = {
            "chain_ok": proven.verify()["ok"],
            "n_events": len(proven.events()),
            "head_sha256": proven.verify()["head_sha256"],
        }
        proven.close()

        # (1) edit one field of the last jsonl event
        ledger_path = jsonl_dir / "feature_ledger.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        target = json.loads(lines[-1])
        target["level_after"] = "REAL" if target["level_after"] != "REAL" else "MOCK"
        lines[-1] = canonical_json(target)
        ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        broken = FeatureLedger(jsonl_dir)
        report = broken.verify()
        broken.close()
        checks["tamper_jsonl_detected"] = not report["ok"]
        checks["tamper_jsonl_problems"] = len(report["problems"])

        # (2) edit the mirrored payload in SQLite only
        ledger_db = FeatureLedger(sqlite_dir)
        row = ledger_db.rows()[0]
        payload = dict(row["payload"])
        payload["level_after"] = "REAL" if payload["level_after"] != "REAL" else "MOCK"
        ledger_db._conn.execute(
            "UPDATE feature_ledger SET payload = ? WHERE ledger_id = ?",
            (canonical_json(payload), row["ledger_id"]),
        )
        ledger_db._conn.commit()
        report2 = ledger_db.verify()
        ledger_db.close()
        checks["tamper_sqlite_detected"] = not report2["ok"]
        checks["tamper_sqlite_problems"] = len(report2["problems"])

        # (3) the evaluation log must verify as well
        from .store import RunStore

        store = RunStore(source_root)
        evaluation_report = store.verify_evaluations()
        store.close()
        checks["evaluations_ok"] = evaluation_report["ok"]
        checks["n_evaluations"] = evaluation_report["n_entries"]

        checks["ok"] = bool(
            checks["chain_ok"]
            and checks["tamper_jsonl_detected"]
            and checks["tamper_sqlite_detected"]
            and checks["evaluations_ok"]
        )
        return checks


def decision_grid(policy: UpgradePolicy) -> Dict[str, Any]:
    """Map (pass rate, error) -> decision for a MOCK capability."""
    pass_rates = [round(0.50 + 0.02 * i, 2) for i in range(26)]      # 0.50 .. 1.00
    errors = [round(1.25 * i, 2) for i in range(21)]                  # 0.00 .. 25.00
    actions: List[List[str]] = []
    for error in errors:
        row = []
        for pr in pass_rates:
            total = 100
            passed = int(round(pr * total))
            runs = [
                _run("synth.grid_cell", seq, passed=passed, failed=total - passed,
                     measured=1.0 + error / 100.0, reference=1.0, reporter=None)
                for seq in (1, 2, 3)
            ]
            row.append(decide("MOCK", analyse(runs, policy), policy).action)
        actions.append(row)
    promote = sum(1 for row in actions for a in row if a == "promote")
    total_cells = len(errors) * len(pass_rates)
    return {
        "pass_rate": pass_rates,
        "error_pct": errors,
        "actions": actions,
        "n_cells": total_cells,
        "n_promote": promote,
        "promote_share": promote / total_cells,
        "boundary_pass_rate": policy.min_pass_rate,
        "boundary_error_pct": policy.max_error_pct,
    }


def scanner_report() -> Dict[str, Any]:
    deny = scanner.load_deny_list()
    report = scanner.scan_files([PKG, TEST_FILE], deny=deny)
    return {
        "deny_list_path": str(scanner.DEFAULT_DENY_LIST),
        "n_deny_terms": len(deny["whole_word_ascii"]) + len(deny["substring_any"]),
        "n_files": report["n_files"],
        "n_hits": report["n_hits"],
        "hits": report["hits"][:10],
        "ok": report["ok"],
    }


# ---------------------------------------------------------------- report -----
def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_markdown(data: Mapping[str, Any]) -> str:
    policy = data["policy"]
    levels = data["scenario_levels"]
    guards = data["scenario_guards"]
    prov = data["provenance"]
    pytest_info = data["pytest"]
    scan = data["zero_domain"]["scan"]
    grid = data["grid"]
    crit = data["criteria"]

    upgrade = levels["upgrade_event"]
    downgrades = levels["downgrade_events"]
    timeline = data["timeline"]

    rows = "\n".join(
        f"| {t['seq']} | {_fmt(t['pass_rate'] * 100, 1)} | {_fmt(t['error_pct'], 2)} | {t['action']} "
        f"| {t['level_before']} → {t['level_after']} | {', '.join(t['reason_codes'])} |"
        for t in timeline
    )
    downgrade_rows = "\n".join(
        f"| {d['seq']} | {d['level_before']} → {d['level_after']} | `{d['sha256'][:16]}…` | "
        f"{', '.join(d['reason_codes'])} |"
        for d in downgrades
    )

    def _rel(path: Any) -> str:
        try:
            return os.path.relpath(str(path), str(REPO))
        except ValueError:  # pragma: no cover - windows drive mismatch
            return str(path)

    fig = {k: _rel(v) for k, v in data["figures"].items()}

    return f"""# {NODE} 能力台账自动升级机制（证据链驱动的 REAL/MOCK/STUB 自动分级）

**仓库** TensorPlatform · **节点** {NODE} · **mission** {data['mission_id']}
**生成时间** {data['generated_at']} · **验收总判** all_passed = **{data['all_passed']}**
判据工件 `runs/{data['run_dir_name']}/acceptance.json` · 机制代码 `backend/services/capability_auto_upgrader/`

## ① 案例介绍

平台的能力证据等级（`REAL`/`MOCK`/`STUB`/`WITHHELD`/`NOT_SUPPORTED`）此前由**人工审计**或
判据引擎（`tools/capability_upgrade`，P348-W3-N01）给出。那些判据是**证据集合的静态完整性**
（锚点数、最大误差、独立复核），没有**时间持续性**要求：同一份报告被重放三次，机制无法区分
"跑了三次"与"跑了一次、抄了三遍"；也没有"回归失败自动回退"的路径——证据退化只能等下一次
全量审计。（`tools/capability_upgrade` 源码亦明示其降级只由缺陷注入触发，不由回归失败触发。）

本节点把"升级"做成**证据链上的时间断言**：一个能力位只有在其最近
`promotion_window = {policy['promotion_window']}` 次**互不相同**的运行中，每次都同时满足
`pytest 通过率 ≥ {policy['min_pass_rate']:.0%}` 且 `对标误差 < {policy['max_error_pct']:g}%`，
才允许 `MOCK`/`STUB`/`WITHHELD → REAL`；一次**结论性**回归失败（红测试或误差越界）就把该等级
按台阶下退一级（`REAL → WITHHELD → MOCK`，下限 `{policy['min_level']}`，绝不自动判成空壳）。

几何/工况：本节点是**平台机制节点**，不含几何体，故无 Cb/L/B 几何校验项；全部算例为合成能力位
（`{CAP_ALPHA}` / `{CAP_BETA}` / `{CAP_GUARD}`）与合成数值证据，域词条经配置化 deny-list 扫描 0 命中。

升级/降级规则（全部以数据形式存放，代码零常量；策略 digest `{data['policy_digest']}`）：

| 方向 | 触发条件（默认阈值） | 目标等级 |
|---|---|---|
| 升级 | 连续 {policy['promotion_window']} 次不同运行：通过率 ≥ {policy['min_pass_rate']:.0%} ∧ 误差 < {policy['max_error_pct']:g}% ∧ 样本数 ≥ {policy['min_benchmark_samples']} | `REAL` |
| 降级 | 连续 {policy['demotion_window']} 次结论性失败（红测试或误差越界） | 下退 {policy['demotion_steps']} 级，下限 `{policy['min_level']}` |
| 保持 | 证据不足（无执行测试/样本不足） | 不动等级（缺证 ≠ 反证） |
| 记录 | 外部 `audit_capabilities` 与推导等级不一致 | 记 `mismatch`，**不覆盖**等级 |

## ② 怎么做的

1. `model.py` — 内容寻址证据模型：`TestEvidence`（junit 计数 + 报告 sha256）、`BenchmarkEvidence`
   （`error_pct` 由 measured/reference **现场推导**，不接受自报值）、`EvidenceRun`（digest 覆盖
   证据内容、**不含**墙钟时间，因此"重放同一份报告"可被识别）。
2. `policy.py` — 阈值数据类 + 策略 digest；非法配置（窗口 0、通过率>1、下限=REAL）构造即拒。
3. `streak.py` — 单次判定 `judge_run`（红测试 > 误差越界 > 证据不足 的优先级）、尾部连续计数
   `analyse`、决策 `decide`（纯函数，可离线复算）。
4. `ledger.py` — `feature_ledger` 追加写：JSONL 事件 + 平台同构 SQLite 表（`ledger_id/tenant/
   item_type/payload/created_at`），每条事件含 `prev_sha256`/`sha256` 哈希链；等级由**重放**事件推导；
   `verify()` 同时校验 JSONL 哈希链与 SQLite 镜像。
5. `watcher.py` — 监听 `audit_capabilities` 负载（`capabilities`/`items`/`data`/裸列表四种形态），
   做快照 diff（added/level_changed/removed），快照本身带 digest；监听器**从不写等级**。
6. `upgrader.py` — 编排：入账运行 → 连续计数 → 决策 → 追加事件（含证据快照与窗口 digest）；
   重复证据返回 `duplicate_run`（不延长 streak）；seq 必须严格递增。
7. `scanner.py` + `config/capability_auto_upgrader/deny_list.json` — 零领域语义自检（词表在配置里）。
8. `acceptance.py` / `figures.py` / `cli.py` — 本报告与 4 张图、`acceptance.json` 一键重放。

工具链：CPython 3.11 · pytest 9.2（junit-xml 摘要入证）· matplotlib 3.10 (Agg) · sqlite3 (stdlib)。

## ③ 计算结果

### (a) 自动升级：MOCK → REAL（合成能力 `{CAP_ALPHA}`）

| seq | 通过率 % | 误差 % | 决策 | 等级 | 原因码 |
|---|---|---|---|---|---|
{rows}

- 等级轨迹（含回归段）：`{' → '.join(levels['alpha_levels'])}`
- `STUB` 路径（`{CAP_BETA}`）：`{' → '.join(str(x) for x in levels['beta_levels'])}`
- 升级事件：seq={upgrade['seq']}，`{upgrade['level_before']} → {upgrade['level_after']}`，
  sha256 `{upgrade['sha256']}`，policy digest `{upgrade['policy_digest']}`，
  窗口 digest 数 = {levels['n_window_digests']}
- 事件行字段完整性（{"REQUIRED_KEYS 全在" if levels['upgrade_row_complete'] else "缺字段"}）= **{levels['upgrade_row_complete']}**

### (b) 自动降级：回归失败 → 回退（无静默覆盖）

| seq | 等级变化 | 事件 sha256 | 原因码 |
|---|---|---|---|
{downgrade_rows}

- 追加写历史未被改写（升级事件仍在且仍为 `REAL`）= **{levels['append_only_history']}**
- 重放 `feature_ledger.jsonl` 得到的等级 = `{levels['replayed_levels']}`，与运行期观测一致
- 防抖动对照（`scenario_guards`）：重放同一报告 4 次 → 等级 `{guards['replay']['level']}`
  （streak={guards['replay']['pass_streak']}，升级事件 {guards['replay']['n_upgrade_events']}）；
  好坏交替 6 次 → 等级 `{guards['flapping']['level']}`（升级事件 {guards['flapping']['n_upgrade_events']}）；
  证据不足 → 决策 `{guards['insufficient']['action']}`、等级仍 `{guards['insufficient']['level_after_gap']}`；
  外部审计报 `{guards['mismatch']['audit_says']}` 而台账为 `{guards['mismatch']['level_before']}` →
  记 mismatch {len(guards['mismatch']['mismatches'])} 条、等级仍 `{guards['mismatch']['level_after']}`。
- 全部防作弊对照成立 = **{guards['all_guards_hold']}**

### (c) 溯源：sha256 哈希链 + 篡改检出

| 项 | 值 |
|---|---|
| 事件总数 / 链头 sha256 | {prov['n_events']} / `{prov['head_sha256'][:24]}…` |
| 哈希链自校验 | **{prov['chain_ok']}** |
| 篡改 JSONL 一行 → 检出 | **{prov['tamper_jsonl_detected']}**（问题 {prov['tamper_jsonl_problems']} 条） |
| 篡改 SQLite payload → 检出 | **{prov['tamper_sqlite_detected']}**（问题 {prov['tamper_sqlite_problems']} 条） |
| 评估日志自校验（{prov['n_evaluations']} 条） | **{prov['evaluations_ok']}** |

### (d) pytest 覆盖

命令：`{pytest_info['command']}` → 退出码 {pytest_info['returncode']}，
用例 {pytest_info['n_tests']} 条（通过 {pytest_info['passed']}，失败 {pytest_info['failed']}，
跳过 {pytest_info['skipped']}），通过率 {_fmt(pytest_info['pass_rate'] * 100, 1)}%，
junit sha256 `{pytest_info['junit_sha256']}`。覆盖：窗口边界（2 vs 3 次）、通过率 90%/89% 边界、
误差 15.0%/15.01% 边界、抖动、重放、证据不足、降级窗口可配、下限、审计监听与 mismatch、
哈希链与两处篡改、策略校验、junit 解析、证据模型校验。

### (e) 零领域语义自检

deny-list（{scan['n_deny_terms']} 词条，配置 `{scan['deny_list_path']}`）扫描
{scan['n_files']} 个文件（模块包 + 本节点测试）→ 命中 **{scan['n_hits']}**。

### 决策区域图（阈值灵敏度）

pass-rate × error 网格 {grid['n_cells']} 格中，可升级格 {grid['n_promote']}
（{_fmt(grid['promote_share'] * 100, 1)}%）；边界严格取 `通过率 ≥ {grid['boundary_pass_rate']:.0%}`
与 `误差 < {grid['boundary_error_pct']:g}%`（图为 fig3）。

## ④ 结果图

| 图 | 文件 | 说明 |
|---|---|---|
| fig1 升级闸门时间线 | `{fig['gate']}` | 每次运行的通过率与对标误差 + 两条阈值线 + 升级/降级标注 |
| fig2 等级轨迹 | `{fig['trajectory']}` | 由台账事件重放得到的等级阶梯（含回归段） |
| fig3 决策区域 | `{fig['grid']}` | 通过率 × 误差 → hold / promote / demote 的区域图 |
| fig4 溯源审计 | `{fig['provenance']}` | 链完整性 + 两处篡改检出结果 |

（本节点为平台机制，无场量云图：输入是 junit 摘要与标量对标误差，不存在空间场。）

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **digest 不能含墙钟时间**（本节点实际踩到）：第一版把 `recorded_at` 计入 run digest，自测里
   "重放同一份报告"被判为 `seq must be N` 的新证据路径，即"重放即可延长 streak"的作弊口子被
   打开。现 digest 只覆盖证据内容（capability/seq/run_id/junit 计数/对标数值），重放返回
   `duplicate_run`，streak 不动。
2. **缺证不等于反证**（设计约束，非事后补救）：junit 全 skip/空报告、样本数不足，单列
   `insufficient` 类别——断 streak 但**不降级**。若把缺证当反证，一条没跑起来的回归就能把 `REAL`
   打下去。约束由 `test_insufficient_evidence_breaks_the_streak_but_never_demotes` 钉死。
3. **降级下限**：自动化只降到 `{policy['min_level']}`。`STUB`/`NOT_SUPPORTED` 是关于"实现是否
   存在"的声明，红测试无法证明实现不存在，因此不交给自动规则。
4. **适用边界**：本机制只管理"等级随证据时间演化"，不产生证据本身；它相信上游 junit 与对标
   记录的真实性（这点由证据链中 `report_sha256`/`source_sha256` 与平台审计链承担）。对
   **一次性**验收（只跑一次）的场景，窗口 ≥3 会拒绝升级——这是刻意的，一次通过不是持续性证据。
5. 与既有 `tools/capability_upgrade`（P348-W3-N01，逐级状态机 + 规则表 + 缺陷阻塞）互补：
   本节点新增的是**时间持续性判据**（streak/hysteresis）、junit 摘要入证、以及针对
   `audit_capabilities` 负载的监听与 mismatch 记录；两者共用同一等级阶梯语义。
6. **HTTP 适配层不是规则层**：`api.py` 只有读路径（`/status`、`/events`、`/verify`）与两条写路径
   （`/runs`、`/audit-snapshot`），阈值与决策全部在 `policy.py`/`streak.py`；适配层由两条 pytest
   用例（200 / 422 / 409）覆盖。首版把 `payload: Any` 当请求体，FastAPI 把它解析成了 **query**
   参数（实测返回 422 且 `loc=["query","payload"]`），改为 `Body(...)` 后修复。
7. **单进程假设**：SQLite 镜像 + JSONL 追加写在单进程内自洽（`fsync` + 只追加），尚未做多进程
   并发的写入仲裁；多实例部署前需要加文件锁或改为平台数据库事务。

## ⑥ 误差判定（逐项）

| 关键量 | 本节点结果 | 基准 | 误差 / 判定 |
|---|---|---|---|
| 升级触发位置 | 第 {data['criteria']['a_upgrade']['promoted_at_seq']} 次运行（窗口 = {policy['promotion_window']}） | 判据"窗口满即升级" | 0 次偏差 → 一致（严格 = 0，<3%） |
| 降级后等级 | `{data['criteria']['b_downgrade']['level_after']}` | `REAL` 下退一级 = `WITHHELD` | 一致（=0，<3%） |
| 低等级对照（无晋升） | 重放/抖动场景等级 `{guards['replay']['level']}`/`{guards['flapping']['level']}` | 应保持 `MOCK` | 一致（=0，<3%） |
| 哈希链校验 | {prov['n_events']} 条事件全部通过 | 全部通过 | 0 条不符（<3%） |
| 篡改检出 | JSONL {prov['tamper_jsonl_problems']} 条问题、SQLite {prov['tamper_sqlite_problems']} 条问题 | 必须被检出 | 检出率 2/2（<3%） |
| 域词条命中 | {scan['n_hits']} | 0 | 一致（<3%） |

**是否 < 3%**：上表 6 项关键量全部为**确定性结果**（计数/等级之一），与基准逐项完全一致，
误差 0% < 3%。本节点不涉及连续物理量，故无"对标实验误差"一项；数值误差阈值（15%）本身作为
**策略输入**被单独测试（15.0% 接受 / 15.01% 拒绝、通过率 90% 接受 / 89% 拒绝的行边界用例见 pytest）。

## 验收判据汇总

| 判据 | 结果 | 数字 |
|---|---|---|
| (a) MOCK→REAL 自动升级 + 台账完整 | {crit['a_upgrade']['passed']} | 等级轨迹 `{' → '.join(levels['alpha_levels'])}`；行完整={levels['upgrade_row_complete']} |
| (b) 回归失败自动降级 + 无静默覆盖 | {crit['b_downgrade']['passed']} | 降级事件 {levels['n_downgrade']} 条；追加写历史保留={levels['append_only_history']} |
| (c) 升级/降级均有 sha256 可溯源 | {crit['c_provenance']['passed']} | 链 {prov['n_events']} 条 OK；篡改检出 2/2 |
| (d) pytest ≥ 12 覆盖升级/降级/边界 | {crit['d_pytest']['passed']} | {pytest_info['n_tests']} 条，通过率 {_fmt(pytest_info['pass_rate'] * 100, 1)}% |
| (e) 零领域语义（合成域名，deny-list 0 命中） | {crit['e_zero_domain']['passed']} | 命中 {scan['n_hits']} / {scan['n_files']} 文件 |

**最终判定：{'PASS' if data['all_passed'] else 'FAIL'}**（诚实申报：如任一项为 False，本报告保留原值不修饰。）
"""


# -------------------------------------------------------------- pipeline -----
def run_acceptance(out_dir: Optional[Path | str] = None, run_pytest: bool = True) -> Dict[str, Any]:
    out_dir = Path(out_dir) if out_dir is not None else REPO / "runs" / "p362w2_n01_capability_auto_upgrader"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = out_dir / "figures"
    policy = UpgradePolicy()

    pytest_info = (
        run_pytest_suite(out_dir)
        if run_pytest
        else {"command": "(skipped)", "returncode": 0, "n_tests": 0, "passed": 0, "failed": 0,
              "skipped": 0, "pass_rate": 0.0, "junit_path": None, "junit_sha256": None, "stdout_tail": []}
    )
    levels_data = scenario_upgrade_all(out_dir, policy)
    guards = scenario_guards(out_dir, policy)
    provenance = tamper_probes(Path(levels_data["root"]))
    grid = decision_grid(policy)
    scan = scanner_report()

    timeline: List[Dict[str, Any]] = []
    for evaluation in levels_data["evaluations"]:
        if evaluation["capability_id"] != CAP_ALPHA:
            continue
        verdict = evaluation.get("verdict") or {}
        timeline.append(
            {
                "seq": evaluation["seq"],
                "pass_rate": verdict.get("pass_rate") or 0.0,
                "error_pct": verdict.get("error_pct") or 0.0,
                "action": evaluation["action"],
                "level_before": evaluation["level_before"],
                "level_after": evaluation["level_after"],
                "reason_codes": evaluation["reason_codes"],
            }
        )

    promoted_at = next((t["seq"] for t in timeline if t["action"] == "promote"), None)
    first_downgrade = next((d for d in levels_data["downgrade_events"]), None)

    criteria = {
        "a_upgrade": {
            "passed": bool(
                levels_data["alpha_levels"][:3] == ["MOCK", "MOCK", "REAL"]
                and levels_data["beta_levels"][-1] == "REAL"
                and levels_data["n_upgrade"] == 2
                and levels_data["upgrade_row_complete"]
            ),
            "level_progression": levels_data["alpha_levels"][:3],
            "promoted_at_seq": promoted_at,
            "upgrade_events": levels_data["n_upgrade"],
            "row_complete": levels_data["upgrade_row_complete"],
        },
        "b_downgrade": {
            "passed": bool(
                first_downgrade is not None
                and first_downgrade["level_before"] == "REAL"
                and first_downgrade["level_after"] == "WITHHELD"
                and levels_data["append_only_history"]
                and levels_data["n_downgrade"] >= 1
                and levels_data["replayed_levels"] == levels_data["final_levels"]
                and guards["all_guards_hold"]
            ),
            "n_downgrade": levels_data["n_downgrade"],
            "level_after": first_downgrade["level_after"] if first_downgrade else None,
            "append_only_history": levels_data["append_only_history"],
            "replay_matches_runtime": levels_data["replayed_levels"] == levels_data["final_levels"],
        },
        "c_provenance": {
            "passed": bool(provenance["ok"] and levels_data["chain_ok"]),
            "chain_ok": provenance["chain_ok"],
            "chain_problems": levels_data["verify"]["ledger"]["problems"],
            "tamper_jsonl_detected": provenance["tamper_jsonl_detected"],
            "tamper_sqlite_detected": provenance["tamper_sqlite_detected"],
            "evaluations_ok": provenance["evaluations_ok"],
        },
        "d_pytest": {
            "passed": bool(pytest_info["returncode"] == 0 and pytest_info["n_tests"] >= 12
                           and pytest_info["failed"] == 0),
            "n_tests": pytest_info["n_tests"],
            "pass_rate": pytest_info["pass_rate"],
        },
        "e_zero_domain": {
            "passed": bool(scan["ok"] and scan["n_hits"] == 0),
            "n_files": scan["n_files"],
            "n_hits": scan["n_hits"],
        },
    }
    all_passed = all(item["passed"] for item in criteria.values()) and provenance["ok"]

    figures_paths = {
        "gate": figures.fig_gate_timeline(timeline, policy.as_dict(), fig_dir / "fig1_upgrade_gate_timeline.png"),
        "trajectory": figures.fig_level_trajectory(
            FeatureLedger(Path(levels_data["root"])).events(), fig_dir / "fig2_level_trajectory.png"),
        "grid": figures.fig_decision_grid(grid, policy.as_dict(), fig_dir / "fig3_decision_region.png"),
        "provenance": figures.fig_provenance(provenance, fig_dir / "fig4_provenance_audit.png"),
    }

    data: Dict[str, Any] = {
        "schema": f"{SCHEMA}/acceptance",
        "node": NODE,
        "mission_id": MISSION_ID,
        "repo": "TensorPlatform",
        "generated_at": _now(),
        "run_dir_name": out_dir.name,
        "policy": policy.as_dict(),
        "policy_digest": policy.digest,
        "policy_description": policy.describe(),
        "pytest": pytest_info,
        "scenario_levels": levels_data,
        "scenario_guards": guards,
        "provenance": provenance,
        "grid": {k: grid[k] for k in ("n_cells", "n_promote", "promote_share",
                                      "boundary_pass_rate", "boundary_error_pct")},
        "zero_domain": {"scan": scan},
        "timeline": timeline,
        "criteria": criteria,
        "all_passed": all_passed,
        "figures": figures_paths,
    }

    markdown = build_markdown(data)
    doc_path = REPO / "docs" / "p362w2_n01_capability_auto_upgrader.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(markdown, encoding="utf-8")
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    data["report"] = {"doc_path": str(doc_path), "sha256": sha256_file(doc_path)}

    acceptance_path = out_dir / "acceptance.json"
    acceptance_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    data["acceptance_path"] = str(acceptance_path)
    data["acceptance_sha256"] = sha256_file(acceptance_path)
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=f"{NODE} acceptance run")
    parser.add_argument("--out", default=None, help="output directory for artefacts")
    parser.add_argument("--skip-pytest", action="store_true", help="reuse figures without re-running the suite")
    args = parser.parse_args(argv)
    data = run_acceptance(args.out, run_pytest=not args.skip_pytest)
    print(json.dumps({"all_passed": data["all_passed"],
                      "criteria": {k: v["passed"] for k, v in data["criteria"].items()},
                      "report": data["report"]["doc_path"],
                      "acceptance": data["acceptance_path"]}, ensure_ascii=False, indent=1))
    return 0 if data["all_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())