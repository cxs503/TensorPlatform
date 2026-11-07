"""IN35-N07 机器裁据：J1–J6 逐条实测 → ``artifacts/IN35-N07/acceptance.json``。

判据（与任务书逐字对应）
------------------------
- **J1** cohort 覆盖率窗口无关：同一 cohort 在 2/7/15/30/90/300 天视图下逐字段差异 = 0；
  且查询时刻推移（+6 h / +2 d / +14 d）后**已冻结** cohort 的覆盖率一分不变。
- **J2** in-flight 增量评价延迟 P95 < 60 s（事件驱动路径：终态 → 判定落库）。
- **J3** 积压排空 SLO：注入 100 行 pending（阈值 50）→ 自动触发排空 ≥90 行、24 h 内。
- **J4** 向后兼容：``summarize()`` / ``stats_queries`` / 评价 API 路由 vs 动手前基线零差异。
- **J5** pytest ≥ 15（本节点新增测试文件收集数）。
- **J6** 监控面板与数据库一致（抽样 10 个单元，独立 SQL 重算，不一致 = 0）。

所有场景都在确定性合成夹具上跑（``fixtures.py``，固定种子），每次运行数字可复算；
线上库只做只读口径对照，不参与判据算术。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

from . import cohorts as ch
from . import contract_baseline as cb
from . import fixtures as fx
from . import panel as pn
from . import triggers as tg
from . import views as vw

SCHEMA = "eval-timeliness-acceptance/1"
ART = pathlib.Path("artifacts/IN35-N07")
DOC = pathlib.Path("docs/IN35-N07_eval_timeliness.md")
TEST_FILES = ("tests/test_eval_timeliness.py",)
BEGIN = "<!-- BEGIN:RESULTS -->"
END = "<!-- END:RESULTS -->"

#: 窗口无关性用的视图（含把 cohort 真筛掉的 2 天视图）
VIEW_DAYS: tuple[int, ...] = (2, 7, 15, 30, 90, 300)
#: 查询时刻推移（ISO 周已冻结的 cohort 数值必须恒定）
LATER_STAMPS: tuple[str, ...] = (
    "2026-11-07T15:00:00+00:00",
    "2026-11-09T00:00:00+00:00",
    "2026-11-21T00:00:00+00:00",
)


# ─────────────────────────── 场景 ───────────────────────────

def _new_fixture(td: str, name: str, **kw) -> tuple[Any, dict[str, Any]]:
    path = f"{td}/{name}.db"
    info = fx.build(path, **kw)
    conn = fx.connect(path)
    tg.ensure_tables(conn)
    return conn, info


def scenario_j1(conn, info: dict[str, Any]) -> dict[str, Any]:
    """窗口无关性（多视图）+ 查询时刻无关性（冻结 cohort）。"""
    as_of = info["as_of"]
    missions, records = ch.fetch_missions(conn), ch.fetch_records(conn)
    cohorts = ch.compute_cohorts(missions, records, as_of=as_of)
    win = vw.window_independence(cohorts, as_of=as_of, days=VIEW_DAYS)

    frozen = {k: v for k, v in cohorts.items() if ch.is_frozen(v, as_of=as_of)}
    time_diffs: list[dict[str, Any]] = []
    for stamp in LATER_STAMPS:
        later = ch.compute_cohorts(missions, records, as_of=stamp)
        for key, row in frozen.items():
            for field in ("coverage", "missions_total", "missions_decided", "missions_pending"):
                if later[key][field] != row[field]:
                    time_diffs.append({"cohort": key, "as_of": stamp, "field": field,
                                       "base": row[field], "later": later[key][field]})
    return {
        "schema": f"{SCHEMA}#j1",
        "as_of": as_of,
        "view_days": list(VIEW_DAYS),
        "cohorts_per_view": win["cohorts_per_view"],
        "window_pairs_compared": win["pairs_compared"],
        "window_diffs": len(win["diffs"]),
        "window_max_abs_diff": win["max_abs_diff"],
        "frozen_cohorts": sorted(frozen),
        "later_stamps": list(LATER_STAMPS),
        "time_point_cells_compared": len(frozen) * len(LATER_STAMPS) * 4,
        "time_point_diffs": time_diffs,
        "coverage_by_cohort": {k: v["coverage"] for k, v in sorted(cohorts.items())},
        "j1_pass": (len(win["diffs"]) == 0 and win["max_abs_diff"] == 0
                    and not time_diffs and len(frozen) >= 1),
    }


def scenario_j2(td: str, *, events: int = 200) -> dict[str, Any]:
    """事件驱动增量评价：``events`` 张终态 mission 逐张触发，测 P95 时延。"""
    conn, info = _new_fixture(td, "j2", unrecorded=events, backlog=0, per_week=30)
    from sqlalchemy import text

    ids = [int(r[0]) for r in conn.execute(
        text("SELECT id FROM tensor_dev_missions WHERE status='succeeded' "
             "AND NOT EXISTS (SELECT 1 FROM outcome_evaluations e WHERE e.mission_id = id) "
             "ORDER BY id"))]
    evs = [tg.on_mission_terminal(conn, mid, status="succeeded", commit=True) for mid in ids]
    slo = tg.inflight_slo(conn)
    conn.close()
    return {
        "schema": f"{SCHEMA}#j2",
        "events_triggered": len(evs),
        "outcome_counts": tg._count(evs, "outcome"),
        "latency_seconds": slo["latency_seconds"],
        "histogram": slo["histogram"],
        "target_p95_seconds": slo["target_p95_seconds"],
        "state": slo["state"],
        "metering": "time.monotonic 墙钟：事件进入 on_mission_terminal → 判定记录落库（不含 API/网关排队）",
        "j2_pass": bool(slo["latency_seconds"]["p95"] is not None
                        and slo["latency_seconds"]["p95"] < slo["target_p95_seconds"]),
    }


def scenario_j3(conn, info: dict[str, Any]) -> dict[str, Any]:
    """积压排空：注入 100 行 pending（阈值 50）→ 自动触发 → 目标 24 h 内 ≥90 行。"""
    from . import inflight as fl

    state = tg.should_drain(conn)
    digest_before = fl.missions_digest(conn)
    run = tg.drain_backlog(conn, commit=True, batch=50)
    digest_after = fl.missions_digest(conn)
    slo = tg.drain_slo(conn)
    evs = tg.events(conn)
    conn.close()
    return {
        "schema": f"{SCHEMA}#j3",
        "injected_pending": int(info["counts"]["pending"]),
        "threshold": int(tg.DEFAULT_BACKLOG_THRESHOLD),
        "triggered_automatically": bool(state["trigger"]),
        "trigger_rule": state["rule"],
        "pending_before": run["pending_before"],
        "pending_after": run["pending_after"],
        "drained": run["drained"],
        "drain_share": run["drain_share"],
        "undecidable_left_pending": run["undecidable"],
        "rejected_written": run["rejected"],
        "elapsed_seconds": run["elapsed_seconds"],
        "target_share": run["target_share"],
        "window_hours": run["window_hours"],
        "met": run["met"],
        "drain_slo_state": slo["state"],
        "batches": len(run["batches"]),
        "fixture_decidable_ceiling_share": info["backlog_decidable_ceiling_share"],
        "dispatcher_untouched": digest_before == digest_after,
        "missions_digest": {"before": digest_before, "after": digest_after},
        "j3_pass": bool(run["drained"] >= 90 and run["met"]
                        and run["elapsed_seconds"] <= tg.DRAIN_WINDOW_HOURS * 3600),
        "notes": ("排空率上限由证据决定：8 张无任何平台事实的成果按纪律保持 pending，"
                  "不写 rejected 凑指标（rejected_written=0）"),
    }


def scenario_j4() -> dict[str, Any]:
    base_path = ART / "baseline_contract.json"
    baseline = json.loads(base_path.read_text(encoding="utf-8"))
    current = cb.capture()
    diffs = cb.diff(baseline, current)
    return {
        "schema": f"{SCHEMA}#j4",
        "baseline_path": str(base_path),
        "baseline_captured_before_changes": True,
        "compared_keys": sorted(k for k in baseline if k != "schema"),
        "diffs": diffs,
        "diff_count": len(diffs),
        "api_routes": current["api_routes"],
        "summarize_coverage": current["summarize_coverage"],
        "j4_pass": not diffs,
    }


def scenario_j5() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "--collect-only", "-q"],
        capture_output=True, text=True, cwd=pathlib.Path.cwd())
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    collected = 0
    for ln in tail:
        if " test" in ln and ("selected" in ln or "collected" in ln):
            for token in ln.split():
                if token.isdigit():
                    collected = int(token)
                    break
    return {
        "schema": f"{SCHEMA}#j5",
        "files": list(TEST_FILES),
        "tests_collected": collected,
        "min_required": 15,
        "collect_exit": proc.returncode,
        "j5_pass": bool(collected >= 15 and proc.returncode == 0),
    }


def scenario_j6(conn, info: dict[str, Any]) -> dict[str, Any]:
    """面板一致性：先制造「已排空 + 已事件化」的真实状态，再抽 10 个单元核对。"""
    tg.drain_backlog(conn, commit=True, batch=50)
    tg.catch_up(conn, limit=10, commit=True)
    panel = pn.build_panel(conn, as_of=info["as_of"])
    chk = pn.panel_consistency(conn, panel, sample=10)
    conn.close()
    mismatches = chk["mismatches"]
    return {
        "schema": f"{SCHEMA}#j6",
        "sample_requested": chk["sample_requested"],
        "cells_checked": chk["cells_checked"],
        "mismatch_count": chk["mismatch_count"],
        "mismatches": mismatches,
        "cells": chk["cells"],
        "panel_backlog_pending": panel["backlog"]["pending_rows"],
        "panel_latency": panel["inflight_latency"]["percentiles_seconds"],
        "panel_coverage": {r["cohort"]: r["coverage"] for r in panel["coverage_trend"]},
        "j6_pass": bool(chk["ok"] and chk["cells_checked"] >= 10),
    }


# ─────────────────────────── 汇总 ───────────────────────────

def run_all() -> dict[str, Any]:
    ART.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        conn1, info1 = _new_fixture(td, "base")
        j1 = scenario_j1(conn1, info1)
        # 面板快照在「排空 + 事件化」之前的稳态上取
        panel_before = pn.build_panel(conn1, as_of=info1["as_of"], view_days=VIEW_DAYS)
        j3 = scenario_j3(conn1, info1)
        conn1.close()

        conn2, info2 = _new_fixture(td, "panel")
        j6 = scenario_j6(conn2, info2)
        conn2.close()

        j2 = scenario_j2(td)
    j4 = scenario_j4()
    j5 = scenario_j5()

    verdicts = {f"J{i}": bool(res[f"j{i}_pass"]) for i, res in
                zip(range(1, 7), (j1, j2, j3, j4, j5, j6))}
    result = {
        "schema": SCHEMA,
        "mission": 6142,
        "node": "IN35-N07",
        "repo": "TensorPlatform",
        "verdicts": verdicts,
        "all_pass": all(verdicts.values()),
        "criteria": {
            "J1": "cohort 覆盖率窗口无关：多视图逐字段差异=0 且冻结 cohort 跨查询时刻不变",
            "J2": "in-flight 增量评价延迟 P95 < 60 s",
            "J3": "注入 100 行 pending，24 h 内排空 ≥90 行（阈值 50 自动触发）",
            "J4": "现有评价 API/summarize 契约零差异（vs 动手前基线）",
            "J5": "pytest ≥ 15",
            "J6": "监控面板抽 10 单元与库一致",
        },
        "J1": j1, "J2": j2, "J3": j3, "J4": j4, "J5": j5, "J6": j6,
        "artifacts": {
            "acceptance": str(ART / "acceptance.json"),
            "panel_before_drain": str(ART / "panel_baseline.json"),
            "baseline_contract": str(ART / "baseline_contract.json"),
            "results_md": str(ART / "results.md"),
        },
        "environment": {
            "python": sys.version.split()[0],
            "engine": "sqlite3（确定性夹具，非线上 PG）",
            "fixture": "tools/evaluation_ops/fixtures.py（seed=%d）" % fx.DEFAULT_SEED,
        },
    }
    (ART / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ART / "panel_baseline.json").write_text(
        json.dumps(panel_before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = render_results_md(result)
    (ART / "results.md").write_text(md, encoding="utf-8")
    inject_into_doc(md)
    return result


def render_results_md(res: dict[str, Any]) -> str:
    """把验收 JSON 渲染成报告表格（数字全部来自 JSON，零手抄）。"""
    j1, j2, j3, j4, j5, j6 = (res["J1"], res["J2"], res["J3"],
                              res["J4"], res["J5"], res["J6"])
    lat = j2["latency_seconds"]
    lines = [
        "### 验收判据总表（`artifacts/IN35-N07/acceptance.json`，程序化生成）",
        "",
        "| 判据 | 实测 | 阈值 | 结论 |",
        "|---|---|---|---|",
        f"| J1 cohort 窗口无关 | 视图差异 {j1['window_diffs']} 处 / max|Δ| {j1['window_max_abs_diff']}，"
        f"查询时刻差异 {len(j1['time_point_diffs'])} 处（{j1['time_point_cells_compared']} 个单元） "
        f"| 差异=0 | {'PASS' if j1['j1_pass'] else 'FAIL'} |",
        f"| J2 in-flight P95 延迟 | P50 {lat['p50']:.4f} s / P95 {lat['p95']:.4f} s / "
        f"P99 {lat['p99']:.4f} s（n={lat['n']}） | P95 < 60 s | "
        f"{'PASS' if j2['j2_pass'] else 'FAIL'} |",
        f"| J3 积压排空 SLO | 注入 {j3['injected_pending']} 行 pending → 排空 "
        f"{j3['drained']} 行（{j3['drain_share']:.2%}），耗时 {j3['elapsed_seconds']:.4f} s | "
        f"≥90 行 / 24 h | {'PASS' if j3['j3_pass'] else 'FAIL'} |",
        f"| J4 向后兼容 | 基线差异 {j4['diff_count']} 处（{len(j4['compared_keys'])} 个契约键） | 差异=0 | "
        f"{'PASS' if j4['j4_pass'] else 'FAIL'} |",
        f"| J5 pytest 数量 | 收集 {j5['tests_collected']} 条 | ≥15 | "
        f"{'PASS' if j5['j5_pass'] else 'FAIL'} |",
        f"| J6 面板一致性 | 抽 {j6['cells_checked']} 单元，不一致 {j6['mismatch_count']} | 不一致=0 | "
        f"{'PASS' if j6['j6_pass'] else 'FAIL'} |",
        "",
        "### cohort 覆盖率（分母/分子/覆盖率，窗口无关口径）",
        "",
        "| cohort | 分母 missions_total | 分子 missions_decided | 覆盖率 | 冻结 |",
        "|---|---|---|---|---|",
    ]
    for row in json.loads((ART / "panel_baseline.json").read_text(encoding="utf-8"))["coverage_trend"]:
        cov = "None（无定义）" if row["coverage"] is None else f"{row['coverage']:.4f}"
        lines.append(f"| {row['cohort']} | {row['missions_total']} | {row['missions_decided']} | "
                     f"{cov} | {row['frozen']} |")
    pcts = json.loads((ART / "panel_baseline.json").read_text(encoding="utf-8"))
    inv = pcts["window_invariance"]
    lines += [
        "",
        "### 窗口无关性：各视图选中的 cohort（视图确实在筛，数值却不变）",
        "",
        "| 视图（天） | 选中 cohort 数 | cohort 集合 |",
        "|---|---|---|",
    ]
    for days, keys in inv["cohorts_per_view"].items():
        lines.append(f"| {days} | {len(keys)} | {', '.join(keys)} |")
    lines += [
        "",
        "### 积压排空（起始 pending 集合口径）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 起始 pending 行 | {j3['pending_before']} |",
        f"| 排空后 pending 行 | {j3['pending_after']} |",
        f"| 已判定（排空） | {j3['drained']} |",
        f"| 证据不足仍 pending | {j3['undecidable_left_pending']} |",
        f"| 写 rejected 行 | {j3['rejected_written']} |",
        f"| 排空份额 / 目标 | {j3['drain_share']:.4f} / {j3['target_share']:.2f} |",
        f"| 墙钟耗时 | {j3['elapsed_seconds']:.4f} s（窗口 {j3['window_hours']} h） |",
        f"| 自动触发 | {j3['triggered_automatically']}（规则：{j3['trigger_rule']}） |",
        "",
        "### in-flight 时延直方图（秒）",
        "",
        "| 区间 | 事件数 |",
        "|---|---|",
    ]
    for b in j2["histogram"]:
        hi = "+∞" if b["hi"] is None else f"{b['hi']}"
        lines.append(f"| [{b['lo']}, {hi}) | {b['n']} |")
    lines.append("")
    return "\n".join(lines)


def inject_into_doc(md: str, doc: pathlib.Path = DOC) -> bool:
    if not doc.exists():
        return False
    text = doc.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        return False
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    doc.write_text(f"{head}{BEGIN}\n{md}{END}{tail}", encoding="utf-8")
    return True


def main() -> int:
    res = run_all()
    print(json.dumps({"verdicts": res["verdicts"], "all_pass": res["all_pass"]},
                     ensure_ascii=False, indent=2))
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())