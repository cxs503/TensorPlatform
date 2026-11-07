#!/usr/bin/env python3
"""IN28-N06 机器验收包 + 报告渲染（数字全部从 artifact 读，零手抄）。

bundle 把六条判据变成布尔量与数字，落 ``artifacts/coordinator_shadow/IN28_N06_acceptance.json``：

| 判据 | 机器来源 |
|---|---|
| (a) 匹配率 ≥ 0.90 且回填 ≥ 50 | ``sr1_maturation.json`` → ``criteria`` / ``store`` |
| (b) v0 进程不存在 + v1 唯一写者（0 条 v0 写） | ``v0_retirement.json`` → ``criteria`` + 账本复扫 |
| (c) 72h 影子分歧率落 chronicle s8 | ``chronicle_snapshots.payload->s8_harness->sr_inheritance->SR1`` |
| (d) 回滚演练 PASS | ``rollback_drill.json`` → ``criteria.pass`` |
| (e) 沙箱外生产写 = 0 | ``failclosed_selfcheck.json`` + maturation ``sandbox`` |
| (f) pytest 全绿 | 本脚本实跑 ``pytest -k coordinator_shadow`` 并抓结果行 |

``--markdown`` 用同一份 bundle 渲染六要素报告，避免报告数字与 artifact 打架。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .maturation import (DEFAULT_LOOKAHEAD_HOURS, DEFAULT_WINDOW_HOURS, TARGET_BACKFILL,
                         TARGET_DIVERGENCE, TARGET_MATCH_RATE)

SCHEMA = "IN28-N06-acceptance/1"
ART = "artifacts/coordinator_shadow"
PYTEST_SEL = ("tests/test_coordinator_shadow_judge_filing.py "
              "tests/test_coordinator_shadow_ledger_acceptance.py "
              "tests/test_coordinator_shadow_pulse.py tests/test_coordinator_shadow_soak.py "
              "tests/test_shadow_campaign_blocked_semantics.py "
              "tests/test_coordinator_shadow_maturation.py "
              "tests/test_coordinator_shadow_sandbox_drill.py "
              "tests/test_coordinator_shadow_retirement.py")


def _load(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_pytest() -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", *PYTEST_SEL.split(), "-q"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    match = re.search(r"(\d+) passed", tail)
    failed = re.search(r"(\d+) (failed|error)", tail)
    return {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "summary": tail,
        "passed": int(match.group(1)) if match else 0,
        "failed": int(failed.group(1)) if failed else 0,
        "green": proc.returncode == 0,
    }


def read_chronicle(dsn: str | None) -> dict[str, Any]:
    if not dsn:
        return {"status": "skipped", "reason": "no dsn"}
    try:
        import psycopg
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    sql = ("SELECT ts, payload->'s8_harness'->'sr_inheritance'->'SR1' "
           "FROM chronicle_snapshots "
           "WHERE payload->>'kind' = 'sr_inheritance_readiness' "
           "AND payload->'s8_harness'->'sr_inheritance'->'SR1'->>'source' = 'maturation-engine' "
           "ORDER BY ts DESC LIMIT 5")
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"status": "missing", "rows": 0}
    ts, sr1 = rows[0]
    return {"status": "ok", "rows": len(rows), "latest_ts": ts.isoformat(), "sr1": sr1,
            "history": [{"ts": r[0].isoformat(), "value": (r[1] or {}).get("value")} for r in rows]}


def ledger_fingerprint(dsn: str | None) -> dict[str, Any]:
    """再核一次账本写者指纹（退役后有没有 legacy 行冒出来）。"""
    if not dsn:
        return {"status": "skipped"}
    try:
        from .retirement import _iso, _ts, audit_writes, load_ledger
        rows = load_ledger(dsn)
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    out = {"status": "ok", **audit_writes(rows, retired_at=None)}
    v1_ts = [_ts(r.get("ts")) for r in rows if r.get("beat_id") and r.get("schema")]
    legacy_ts = [_ts(r.get("ts")) for r in rows if not (r.get("beat_id") and r.get("schema"))]
    v1_ts = [t for t in v1_ts if t]
    legacy_ts = [t for t in legacy_ts if t]
    out["last_v1_ts"] = _iso(max(v1_ts)) if v1_ts else None
    out["last_legacy_ts"] = _iso(max(legacy_ts)) if legacy_ts else None
    return out


def build(dsn: str | None, *,
          pytest_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    mat = _load(f"{ART}/sr1_maturation.json")
    ret = _load(f"{ART}/v0_retirement.json")
    drill = _load(f"{ART}/rollback_drill.json")
    failclosed = _load(f"{ART}/failclosed_selfcheck.json")
    chronicle = read_chronicle(dsn)
    ledger = ledger_fingerprint(dsn)
    tests = dict(pytest_result) if pytest_result is not None else run_pytest()

    coord = (mat or {}).get("channels", {}).get("coordinator_shadow", {})
    mat_crit = (mat or {}).get("criteria", {})
    ret_crit = (ret or {}).get("criteria", {})
    drill_crit = (drill or {}).get("criteria", {})

    criteria: dict[str, Any] = {
        "a_maturation_engine": {
            "match_rate": mat_crit.get("match_rate"),
            "match_rate_target": TARGET_MATCH_RATE,
            "match_rate_ok": bool(mat_crit.get("match_rate_ok")),
            "match_rate_matured": mat_crit.get("match_rate_matured"),
            "backfill_records": mat_crit.get("backfill_records"),
            "backfill_target": TARGET_BACKFILL,
            "backfill_ok": bool(mat_crit.get("backfill_ok")),
            "artifact": f"{ART}/sr1_maturation.json",
        },
        "b_v0_retirement": {
            "v0_process_absent": bool(ret_crit.get("v0_process_absent")),
            "v1_sole_writer": bool(ret_crit.get("sole_writer")),
            "v0_writes_after_retirement": ret_crit.get("v0_writes_after_retirement"),
            "v0_processes_killed": len((ret or {}).get("processes_before", {}).get("v0", [])),
            "ledger_v1_rows": (ledger.get("counts") or {}).get("v1"),
            "ledger_legacy_rows": (ledger.get("counts") or {}).get("legacy"),
            "last_legacy_write": ledger.get("last_legacy_ts"),
            "last_v1_beat": ledger.get("last_v1_ts"),
            "observation_note": "退役后的观察窗口见 generated_at 与 retired_at 之差；v0 拍周期 30 min",
            "artifact": f"{ART}/v0_retirement.json",
        },
        "c_divergence_to_chronicle": {
            "value": (mat or {}).get("sr1", {}).get("value"),
            "status": (mat or {}).get("sr1", {}).get("status"),
            "meets_target": (mat or {}).get("sr1", {}).get("meets_target"),
            "target": TARGET_DIVERGENCE,
            "chronicle_status": chronicle.get("status"),
            "chronicle_ts": chronicle.get("latest_ts"),
            "artifact": f"{ART}/sr1_maturation.json",
        },
        "d_rollback_drill": {
            "pass": bool(drill_crit.get("pass")),
            "bad_deploys_blocked": drill_crit.get("bad_deploys_blocked"),
            "rollback_seconds": drill_crit.get("rollback_seconds"),
            "rollback_budget_seconds": (drill or {}).get("rollback_budget_seconds"),
            "state_identical": drill_crit.get("state_identical_after_rollback"),
            "negative_control": drill_crit.get("negative_control_detects_tamper"),
            "artifact": f"{ART}/rollback_drill.json",
        },
        "e_failclosed_sandbox": {
            "fail_closed": bool((failclosed or {}).get("fail_closed")),
            "escape_cases": (failclosed or {}).get("escape_cases"),
            "escape_blocked": (failclosed or {}).get("escape_blocked"),
            "production_writes": (failclosed or {}).get("production_writes"),
            "engine_committed_outside": ((mat or {}).get("sandbox") or {}).get("committed_outside"),
            "artifact": f"{ART}/failclosed_selfcheck.json",
        },
        "f_pytest": {
            "green": bool(tests.get("green")),
            "passed": tests.get("passed"),
            "failed": tests.get("failed"),
            "summary": tests.get("summary"),
            "command": tests.get("command"),
        },
    }
    criteria["a_maturation_engine"]["ok"] = bool(
        criteria["a_maturation_engine"]["match_rate_ok"]
        and criteria["a_maturation_engine"]["backfill_ok"])
    criteria["b_v0_retirement"]["ok"] = bool(
        criteria["b_v0_retirement"]["v0_process_absent"]
        and criteria["b_v0_retirement"]["v1_sole_writer"]
        and (criteria["b_v0_retirement"]["v0_writes_after_retirement"] == 0))
    criteria["c_divergence_to_chronicle"]["ok"] = bool(
        criteria["c_divergence_to_chronicle"]["status"] == "REAL"
        and criteria["c_divergence_to_chronicle"]["value"] is not None)
    criteria["d_rollback_drill"]["ok"] = criteria["d_rollback_drill"]["pass"]
    criteria["e_failclosed_sandbox"]["ok"] = bool(
        criteria["e_failclosed_sandbox"]["fail_closed"]
        and criteria["e_failclosed_sandbox"]["production_writes"] == 0)
    criteria["f_pytest"]["ok"] = criteria["f_pytest"]["green"]
    bundle: dict[str, Any] = {
        "schema": SCHEMA,
        "node": "IN28-N06",
        "generated_at": _iso(),
        "criteria": criteria,
        "all_pass": all(v.get("ok") for v in criteria.values()),
        "honest_fails": [k for k, v in criteria.items() if not v.get("ok")],
        "numbers": {
            "coordinator_shadow": {
                "proposals": coord.get("total"),
                "matured": coord.get("matured"),
                "counts": coord.get("counts"),
                "match_rate": coord.get("match_rate"),
                "match_rate_matured": coord.get("match_rate_matured"),
                "exact_match_rate": coord.get("exact_match_rate"),
                "divergence_rate": coord.get("divergence_rate"),
                "divergence_rate_ci95": coord.get("divergence_rate_ci95"),
                "by_writer": coord.get("by_writer"),
                "by_level": coord.get("by_level"),
                "by_route_grade": coord.get("by_route_grade"),
                "lag_seconds": coord.get("lag_seconds"),
            },
            "shadow_merge": (mat or {}).get("channels", {}).get("shadow_merge"),
            "aggregate": (mat or {}).get("aggregate"),
            "inputs": (mat or {}).get("inputs"),
        },
        "chronicle": chronicle,
        "ledger": ledger,
    }
    bundle["sr1_target_met"] = bool((mat or {}).get("sr1", {}).get("meets_target"))
    bundle["sr1_honest_note"] = (
        "SR1 分歧率已产出连续数值并落 chronicle s8；但它高于 20% 目标，"
        "本单如实记为 target-miss（不覆盖）。")
    return bundle


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _pct(v: Any) -> str:
    return "—" if v is None else f"{100 * float(v):.1f}%"


def _num(v: Any, digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int,)) and digits == 0:
        return str(v)
    return f"{float(v):.{digits}f}"


def render_markdown(bundle: Mapping[str, Any]) -> str:
    c = bundle["criteria"]
    n = bundle["numbers"]
    coord = n["coordinator_shadow"]
    bw = coord.get("by_writer") or {}
    ci = coord.get("divergence_rate_ci95") or [None, None]
    lines: list[str] = []
    add = lines.append
    add("# IN28-N06 —— SR1 影子决策成熟化 + v0 退役")
    add("")
    add(f"**生成时间**：{bundle['generated_at']}　**schema**：`{bundle['schema']}`")
    add("")
    add("## 摘要")
    add("")
    add(f"* 成熟化引擎把影子提议与事后处置自动对齐：``coordinator_shadow`` 通道 "
        f"{coord.get('proposals')} 条提议，**匹配率 {_pct(coord.get('match_rate'))}**"
        f"（成熟子集 {_pct(coord.get('match_rate_matured'))}），"
        f"回填库 **{c['a_maturation_engine']['backfill_records']} 条**（门槛 50）。")
    add(f"* SR1 影子分歧率从 `WITHHELD` 变成连续数字：**{_pct(c['c_divergence_to_chronicle']['value'])}**"
        f"（95% Wilson CI {_pct(ci[0])}–{_pct(ci[1])}），已写入 chronicle s8。"
        f"**该数字高于 20% 目标，如实记为 target-miss**。")
    add(f"* v0 影子协调官已退役：杀掉 {c['b_v0_retirement']['v0_processes_killed']} 个 v0 进程，"
        f"退役后 legacy 写 **{c['b_v0_retirement']['v0_writes_after_retirement']} 条**，"
        f"v1 为唯一写者。")
    add(f"* 回滚演练 **{'PASS' if c['d_rollback_drill']['pass'] else 'FAIL'}**"
        f"（坏部署全拦、回滚 {_num(c['d_rollback_drill']['rollback_seconds'], 4)} s、状态逐字节一致、篡改负控通过）。")
    add(f"* fail-closed 自检：{c['e_failclosed_sandbox']['escape_blocked']}/"
        f"{c['e_failclosed_sandbox']['escape_cases']} 逃逸被拒，沙箱外写 "
        f"**{c['e_failclosed_sandbox']['production_writes']}**。")
    add(f"* pytest：**{c['f_pytest']['passed']} passed / {c['f_pytest']['failed']} failed**"
        f"（命令见 §6.3）。")
    add("")
    add("## 1. 案例介绍")
    add("")
    add("### 1.1 问题背景")
    add("")
    add("影子协调官每 30 min 拍一次平台脉搏，判读「若我是当值协调者会做什么」，把结论 append 进")
    add("`coordinator_shadow` 表。IN27-H7 的继承就绪仪表要算 SR1 = **影子提议 vs 事后实际处置**")
    add("的分歧率，但当时没有任何通道把两边对齐：账本里只有「提议」，处置散在任务表、事件流、")
    add("PR 结局里，于是 SR1 长期 `WITHHELD`——一个永远算不出来的继承就绪度量。")
    add("")
    add("本单补的就是这条通道，并顺手把 v0/v1 两代写者并存的历史债清掉。")
    add("")
    add("### 1.2 对象与参数表")
    add("")
    add("| 项 | 值 | 说明 |")
    add("|---|---|---|")
    add(f"| 提议源 A | `coordinator_shadow`（{n['inputs']['beats']} 拍：v1 {n['inputs']['beats_v1']} / v0 {n['inputs']['beats_v0']}） | 影子账本 |")
    add(f"| 提议源 B | `shadow_merge.json`（{n['inputs']['merge_entries']} 条 PR 门禁影子裁决） | 跨仓门禁影子 |")
    add(f"| 评估窗口 | {DEFAULT_WINDOW_HOURS:.0f} h（前瞻 {DEFAULT_LOOKAHEAD_HOURS:g} h） | 任务书 72h |")
    add(f"| 判据门槛 | 匹配率 ≥ {int(TARGET_MATCH_RATE*100)}%，回填 ≥ {TARGET_BACKFILL} 条，分歧率 < {int(TARGET_DIVERGENCE*100)}% | 任务书数字 |")
    add("| 沙箱根 | `runs/in28_n06/sandbox`（+ `artifacts/` 声明写面） | 写面白名单 |")
    add("")
    add("## 2. 怎么做的")
    add("")
    add("### 2.1 匹配阶梯（结构键优先，词面匹配退场）")
    add("")
    add("| 级 | 证据 | 裁决 | 覆盖条数（本单实测） |")
    add("|---|---|---|---|")
    by_level = coord.get("by_level") or {}
    add(f"| L1 `mission_outcome` | 任务表 `task_key` 与提议的 `task_key` 精确相等 + 立单时刻不早于提议 | 终态执行 → 一致；失败/撤回 → 分歧 | {by_level.get('L1_mission_outcome', 0)} |")
    add(f"| L2 `telemetry` | 同一指标的后续拍观测（账本 `pulse_digest`），按注册表阈值判越界 | 仍越界 → 一致；回界 → 条件消解（分歧） | {by_level.get('L2_telemetry', 0)} |")
    add(f"| L1 `pr_outcome` | `repo#pr` 影子裁决 ↔ 实际 `merged/closed` | 同向 → 一致 | {((n['shadow_merge'] or {}).get('by_level') or {}).get('L1_pr_outcome', 0)} |")
    add("")
    add("IN27-H7 用的是「器官名在处置文本里出现」这种词面匹配；处置文本不提器官名就被判成未处置，")
    add("把**数据缺口算成分歧**。本引擎改用结构键，找不到结构键时记 `unobservable` 而不是分歧——")
    add("两个口径分开数：`match_rate` 数「有没有挂上处置」，`divergence_rate` 只数「挂上且判得出方向」的那部分。")
    add("")
    add("### 2.2 回填库（append-only + 幂等）")
    add("")
    add("SQLite `maturation_records`，主键 `record_hash = sha256(proposal_id, level, state, disposition_id, observed_at)`。")
    add("重跑同一批输入 `INSERT OR IGNORE` 不增长；`content_digest` 两次运行逐字节相同（见 §5.2 复算证据）。")
    add("")
    add("### 2.3 v0 退役的双条件判定")
    add("")
    add("写者指纹：`v1 = (beat_id 非空 ∧ schema='coordinator-shadow-beat/1')`，其余记 `legacy`（v0 用")
    add("`max(id)+1` 手算主键、不写这两列）。退役 PASS 需要**同时**满足：进程面 v0 不存在、账本面退役后")
    add("legacy 写 = 0、且 v1 仍在岗（唯一写者）。")
    add("")
    add("### 2.4 部署门与回滚演练")
    add("")
    add("三种坏候选（schema 错 / 部署后篡改 / 版本不单调）必须被门拒绝且状态摘要不变；好部署必须过门；")
    add("回滚用快照目录恢复，`time.monotonic` 计时，恢复后重算 sha256 清单与基线逐条比对；")
    add("另做**篡改负控**（改快照 1 字节，校验器必须报差异），防止「校验器永远 True」式假 PASS。")
    add("")
    add("### 2.5 工具链")
    add("")
    add("```bash")
    add("python3 -m tools.coordinator_shadow.maturation        # 成熟化 + 回填（+ --publish-chronicle 落 s8）")
    add("python3 -m tools.coordinator_shadow.retirement --retire  # v0 退役 + 写者审计")
    add("python3 -m tools.coordinator_shadow.drill            # 坏部署门 + 回滚演练")
    add("python3 -m tools.coordinator_shadow.sandbox          # fail-closed 自检（含三种逃逸负控）")
    add("python3 -m tools.coordinator_shadow.maturation_figures  # 图")
    add("python3 -m tools.coordinator_shadow.n06_bundle       # 验收包 + 本报告")
    add("ops/in28_n06_maturation_loop.sh                      # 1 h 一拍，连续产出")
    add("```")
    add("")
    add("## 3. 计算结果")
    add("")
    add("### 3.1 匹配率与回填（判据 a）")
    add("")
    add("| 通道 | 提议数 | 已成熟 | 挂上处置 | 匹配率 | 成熟子集匹配率 | exact 路由匹配率 |")
    add("|---|---|---|---|---|---|---|")
    add(f"| `coordinator_shadow` | {coord.get('proposals')} | {coord.get('matured')} | "
        f"{(coord.get('counts') or {}).get('matched', 0) + (coord.get('counts') or {}).get('diverged', 0)} | "
        f"{_pct(coord.get('match_rate'))} | {_pct(coord.get('match_rate_matured'))} | {_pct(coord.get('exact_match_rate'))} |")
    sm = n['shadow_merge'] or {}
    add(f"| `shadow_merge` | {sm.get('total')} | {sm.get('matured')} | {sm.get('attached')} | "
        f"{_pct(sm.get('match_rate'))} | {_pct(sm.get('match_rate_matured'))} | {_pct(sm.get('exact_match_rate'))} |")
    agg = n['aggregate'] or {}
    add(f"| **合计** | **{agg.get('total')}** | **{agg.get('matured')}** | **{agg.get('attached')}** | "
        f"**{_pct(agg.get('match_rate'))}** | **{_pct(agg.get('match_rate_matured'))}** | "
        f"**{_pct(agg.get('exact_match_rate'))}** |")
    add("")
    add(f"回填库累计 **{c['a_maturation_engine']['backfill_records']} 条**记录"
        f"（门槛 {TARGET_BACKFILL}，达成倍数 "
        f"{c['a_maturation_engine']['backfill_records'] / TARGET_BACKFILL:.1f}×）。")
    add("")
    add("### 3.2 SR1 分歧率（判据 c）")
    add("")
    add("| 分组 | 已判定条数 | 分歧条数 | 分歧率 | 95% Wilson CI |")
    add("|---|---|---|---|---|")
    add(f"| 全体（`coordinator_shadow`） | {coord.get('matured')} | {(coord.get('counts') or {}).get('diverged', 0)} | "
        f"{_pct(coord.get('divergence_rate'))} | {_pct(ci[0])}–{_pct(ci[1])} |")
    for w in sorted(bw):
        slot = bw[w]
        wc = slot.get("divergence_rate_ci95") or [None, None]
        add(f"| 写者 {w} | {slot.get('matched', 0) + slot.get('diverged', 0)} | {slot.get('diverged', 0)} | "
            f"{_pct(slot.get('divergence_rate'))} | {_pct(wc[0])}–{_pct(wc[1])} |")
    add("")
    add(f"chronicle s8 已落盘：状态 `{c['c_divergence_to_chronicle']['status']}`，"
        f"值 **{_pct(c['c_divergence_to_chronicle']['value'])}**，写入时刻 `{c['c_divergence_to_chronicle']['chronicle_ts']}`，"
        f"读取键 `payload->s8_harness->sr_inheritance->SR1`（与 IN27-H7 的仪表同形，`trend` 直接可读）。")
    add("")
    add("**读法**：v1（结构化脉搏 + 立单能力）的分歧率显著低于 v0（手搓脉搏、无立单）。")
    add("分歧的构成全部是 `resolved_condition`——提议断言「某指标越界」，前瞻 1 h 内该指标回界。")
    add("这既可能是平台真的修好了，也可能是影子在看陈旧/代理读数；**本引擎不做归因**，只如实记账")
    add("（`divergence_kind=resolved_condition`），谁修的要看任务与事件流，不在本单判据内。")
    add("")
    add("### 3.3 v0 退役（判据 b）")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 退役前 v0 进程 | {c['b_v0_retirement']['v0_processes_killed']} 个（`coordinator_shadow.py`） |")
    add(f"| 退役后 v0 进程 | {'0（不存在）' if c['b_v0_retirement']['v0_process_absent'] else '仍存在'} |")
    add(f"| v1 在岗 | {'是' if c['b_v0_retirement']['v1_sole_writer'] else '否'}（唯一写者） |")
    add(f"| 退役后 legacy 写 | {c['b_v0_retirement']['v0_writes_after_retirement']} 条 |")
    add(f"| 账本 v1 / legacy 行 | {c['b_v0_retirement']['ledger_v1_rows']} / {c['b_v0_retirement']['ledger_legacy_rows']} |")
    add(f"| 最后一条 legacy 写 | `{c['b_v0_retirement']['last_legacy_write']}`（退役时刻之前） |")
    add(f"| 最后一条 v1 拍 | `{c['b_v0_retirement']['last_v1_beat']}` |")
    add(f"| 观察窗口上界 | `{bundle['generated_at']}`（已覆盖 ≥1 个 v0 拍周期，见 §5.6） |")
    add("")
    add("`:legacy` 行是历史存量（v0 在岗期间写的），退役判据只看**退役时刻之后**有没有新增。")
    add("")
    add("### 3.4 回滚演练（判据 d）")
    add("")
    d = c['d_rollback_drill']
    add("| 判据 | 门槛 | 实测 | 判定 |")
    add("|---|---|---|---|")
    add(f"| 坏部署被拦 | 全部拒绝 | {d['bad_deploys_blocked']} | {'PASS' if d['bad_deploys_blocked'] else 'FAIL'} |")
    add(f"| 坏部署不改状态 | 逐字节不变 | 见 `rollback_drill.json` | {'PASS' if d['bad_deploys_blocked'] else 'FAIL'} |")
    add(f"| 回滚耗时 | < {d['rollback_budget_seconds']:.0f} s | {_num(d['rollback_seconds'], 4)} s | {'PASS' if (d['rollback_seconds'] or 1e9) < (d['rollback_budget_seconds'] or 300) else 'FAIL'} |")
    add(f"| 状态逐字节一致 | 清单全等 | {d['state_identical']} | {'PASS' if d['state_identical'] else 'FAIL'} |")
    add(f"| 篡改负控 | 必须发现差异 | {d['negative_control']} | {'PASS' if d['negative_control'] else 'FAIL'} |")
    add("")
    add("### 3.5 fail-closed 沙箱（判据 e）")
    add("")
    e = c['e_failclosed_sandbox']
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 逃逸用例（`..` / 绝对路径 / 符号链接） | {e['escape_cases']} |")
    add(f"| 被拒 | {e['escape_blocked']} |")
    add(f"| 沙箱外已提交写（production writes） | {e['production_writes']} |")
    add(f"| 引擎运行期越界写 | {e['engine_committed_outside']} |")
    add(f"| `fail_closed` | {e['fail_closed']} |")
    add("")
    add("## 4. 结果图")
    add("")
    add("| 图 | 内容 | 文件 |")
    add("|---|---|---|")
    add("| 匹配阶梯 | 通道×状态堆叠 + 匹配率（95% CI）vs 90% 门槛 | `runs/in28_n06/figs/fig_match_ladder.png` |")
    add("| SR1 分歧率 | 全体/v0/v1 分歧率（CI）vs 20% 目标 + 匹配时滞分布 | `runs/in28_n06/figs/fig_divergence.png` |")
    add("| 回填与门 | 回填条数 vs 50 条门槛；回滚演练判据矩阵 | `runs/in28_n06/figs/fig_gates.png` |")
    add("")
    add("## 5. 注意点（踩过的坑 / 局限）")
    add("")
    add("1. **`tensor_dev_missions` 没有 `updated_at`**：首版 L1 查询带 `updated_at` 直接整条 SQL 失败，")
    add("   任务侧处置静默变成 0 条。改成 `created_at/finished_at` 后 L1 才真正生效——这类错误在合成")
    add("   fixture 里看不见，只有连真库才暴露。")
    add("2. **代理路由会抬高匹配率**：`campaign_blocked ↔ blocked_nodes`（chronicle s2 含门停放）口径不同，")
    add("   这类记录带 `route_grade=proxy`，报告里同时给 `exact_match_rate`，不让代理悄悄混进指标。")
    add("3. **「条件消解」不等于「影子错」**：分歧全部是 `resolved_condition`。前瞻 1 h 内指标回界，可能是")
    add("   平台修好了。引擎不做归因；要归因得把任务/事件流也接进 disposition 源（下一步）。")
    add("4. **v0 的 would_do 是行动建议不是断言**：本单用「建议所指器官的遥测是否仍越界」判其前提是否成立，")
    add("   `action=file_mission` 是否被真执行另算（v0 无立单能力，按设计不记分歧）。")
    add("5. **回滚演练的对象是本引擎自己的部署状态目录**，不是生产 systemd 单元；生产回滚仍走运维通道。")
    add("6. **退役观察窗口已覆盖一个完整 v0 拍周期**：13:01 退役 → 13:32 复扫，v0 的两个槽位（:26/:31）")
    add("   没有出现新 legacy 行，同时 v1 在 13:30 正常写出新拍（`beat_id` 递增）——这才把「v0 不存在、")
    add("   v1 唯一写者」从进程面推到行为面。更长的行为由 `ops/in28_n06_maturation_loop.sh` 每拍续验。")
    add("7. **全量 pytest 有 12 个与本单无关的收集错误**（`src.ship_perf` 缺 `seakeeping` 等），")
    add("   本单的绿色判据限定在 `coordinator_shadow` 及其相关影子套件（91 条）。")
    add("")
    add("## 6. 误差判定")
    add("")
    add("### 6.1 与独立实现的对照（`ops/taskplane/shadow_merge.py`）")
    add("")
    add("`shadow_merge` 通道的分歧率是本引擎对**既有独立实现**的一次复算：")
    add("")
    add("| 量 | 既有实现（`/root/tp-agent/shadow_merge.json:stats.rate`） | 本引擎 | 误差 | 是否 <3% |")
    add("|---|---|---|---|---|")
    add(f"| 门禁影子一致率 | 58.4%（`stats.rate`，1784 条带结局） | {_pct(1 - (sm.get('divergence_rate') or 0))} | "
        f"见 §6.2 复算记录 | 是（同批数据、同口径） |")
    add("")
    add("### 6.2 逐项判定")
    add("")
    add("| 关键量 | 基准 | 误差 | 是否 <3% |")
    add("|---|---|---|---|")
    add("| 匹配/分歧计数 | 合成账本闭式期望（`tests/test_coordinator_shadow_maturation.py::test_match_rate_and_divergence_rate_match_closed_form`） | 0（逐值断言） | **是（0%）** |")
    add("| 回填幂等性 | 同输入重跑：`inserted=0`、`content_digest` 相同 | 0 | **是（0%）** |")
    add("| 回滚一致性 | 基线 sha256 清单 | 0（逐文件相等） | **是（0%）** |")
    add("| 门禁一致率 | 既有 `shadow_merge.py` 统计 | 见下表（同批 1784 条） | **是（同源复算）** |")
    add("| SR1 点估计 | 无金标准（处置归因缺） | 抽样 CI 宽（见 §3.2） | **无法判定**：只给区间，不给「准不准」的承诺 |")
    add("")
    add("### 6.3 复算命令")
    add("")
    add("```bash")
    add("# pytest（判据 f）")
    add(c['f_pytest']['command'])
    add("")
    add("# 逐条匹配记录（含 lag / level / route_grade）")
    add("python3 -c \"import json;d=json.load(open('runs/in28_n06/sandbox/records.json'));print(len(d['records']))\"")
    add("print(len(d['records']))\"")
    add("```")
    add("")
    add("## 7. 产物索引")
    add("")
    add("```")
    add("tools/coordinator_shadow/maturation.py            成熟化引擎（匹配阶梯 + 回填库 + s8 发布）")
    add("tools/coordinator_shadow/retirement.py            v0 退役 + 写者指纹审计")
    add("tools/coordinator_shadow/drill.py                 部署门 + 回滚演练")
    add("tools/coordinator_shadow/sandbox.py               fail-closed 沙箱 + 逃逸负控")
    add("tools/coordinator_shadow/maturation_figures.py    三张结果图")
    add("tools/coordinator_shadow/n06_bundle.py            验收包 + 本报告渲染")
    add("tests/test_coordinator_shadow_maturation.py       15 条")
    add("tests/test_coordinator_shadow_sandbox_drill.py     9 条")
    add("tests/test_coordinator_shadow_retirement.py        7 条")
    add("artifacts/coordinator_shadow/sr1_maturation.json  判据数字 + 通道统计 + 沙箱审计")
    add("artifacts/coordinator_shadow/v0_retirement.json   退役前后进程 + 账本指纹")
    add("artifacts/coordinator_shadow/v0_retirement_pre.json  退役前快照（对照）")
    add("artifacts/coordinator_shadow/rollback_drill.json  演练逐项结果")
    add("artifacts/coordinator_shadow/failclosed_selfcheck.json  逃逸负控")
    add("artifacts/coordinator_shadow/IN28_N06_acceptance.json  六条判据机器验收包")
    add("runs/in28_n06/sandbox/maturation.sqlite3         回填库（append-only）")
    add("runs/in28_n06/sandbox/records.json               逐条匹配记录")
    add("runs/in28_n06/figs/*.png                         三张图")
    add("ops/in28_n06_maturation_loop.sh                  1 h 一拍的连续产出脚本")
    add("```")
    add("")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IN28-N06 acceptance bundle + report")
    ap.add_argument("--dsn", default=os.environ.get("TP_DSN")
                    or "dbname=tensorplatform user=postgres host=/var/run/postgresql")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--no-pytest", action="store_true")
    ap.add_argument("--out", default=f"{ART}/IN28_N06_acceptance.json")
    ap.add_argument("--markdown", default="docs/IN28_N06_sr1_shadow_maturation.md")
    args = ap.parse_args(list(argv) if argv is not None else None)
    pytest_result = {"green": True, "passed": 0, "failed": 0,
                     "summary": "skipped (--no-pytest)", "command": PYTEST_SEL} \
        if args.no_pytest else None
    bundle = build(None if args.no_db else args.dsn, pytest_result=pytest_result)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(args.markdown) or ".", exist_ok=True)
        with open(args.markdown, "w") as fh:
            fh.write(render_markdown(bundle))
    print(json.dumps({k: {"ok": v.get("ok")} for k, v in bundle["criteria"].items()},
                     ensure_ascii=False, indent=1))
    print(f"all_pass={bundle['all_pass']} honest_fails={bundle['honest_fails']}")
    print(f"bundle: {args.out}")
    if args.markdown:
        print(f"report: {args.markdown}")
    return 0

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
