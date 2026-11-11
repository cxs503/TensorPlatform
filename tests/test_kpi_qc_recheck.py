"""KPI-QC-RECHECK 计量能力测试 —— 覆盖口径不变式、产物可复现、第二实现一致性。

铁律：这些测试**不需要数据库**（FakeReader 注入合成行），因此可在 CI 上跑；
真实读数由 `python3 -m tools.kpi_qc_recheck.measure` 落盘 + `verify` 复算证明
（见 artifacts/kpi_qc_recheck/ 与 docs/kpi/KPI_QC_RECHECK_L2.md）。

断言的是**不变式**而非"代码跑过了"：
* 分子 ⊆ 分母、阶梯单调 D3 ≤ D2 ≤ D1 ≤ D0
* auto_scan 不得进分子（口径纯度）
* 未复核清单与已复核清单构成分母的划分（无重复无遗漏）
* 计数必须精确相等；第二实现与自报值逐位一致；篡改必 fail-closed
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.kpi_qc_recheck import spec  # noqa: E402
from tools.kpi_qc_recheck import verify as kverify  # noqa: E402
from tools.kpi_qc_recheck.measure import (  # noqa: E402
    aggregate,
    dedupe_denominator,
    measure,
    measure_with_session,
    write_artifacts,
)


# ── 合成夹具：8 条 PR / 6 个 mission / 2 个仓 ─────────────────────────

DEN = [
    {"mission_id": 1, "repo": "RepoA", "pr": "p1", "merged_at": "2026-11-05T12:00:00Z"},
    {"mission_id": 1, "repo": "RepoA", "pr": "p1", "merged_at": "2026-11-05T08:00:00Z"},  # 重复行
    {"mission_id": 1, "repo": "RepoA", "pr": "p2", "merged_at": "2026-11-05T09:00:00Z"},
    {"mission_id": 2, "repo": "RepoA", "pr": "p3", "merged_at": "2026-11-05T10:00:00Z"},
    {"mission_id": 3, "repo": "RepoB", "pr": "p4", "merged_at": "2026-11-05T11:00:00Z"},
    {"mission_id": 4, "repo": "RepoB", "pr": "p5", "merged_at": "2026-11-06T11:00:00Z"},
    {"mission_id": 4, "repo": "RepoB", "pr": "p6", "merged_at": "2026-11-07T11:00:00Z"},
    {"mission_id": 5, "repo": "RepoB", "pr": "p7", "merged_at": "2026-11-07T12:00:00Z"},
    {"mission_id": 6, "repo": "RepoA", "pr": "p8", "merged_at": "2026-11-06T06:00:00Z"},
]

EVAL = [
    # mission1: 独立复核通过 → 计入分子（2 条 PR）
    {"mission_id": 1, "reviewer_kind": "independent", "review_status": "reviewed",
     "status": "confirmed", "cross_model_verified": False, "decided_at": "2026-11-05T10:00:00Z"},
    # mission2: 只有 auto_scan 通过 → 计入 D1，绝不计入分子
    {"mission_id": 2, "reviewer_kind": "auto_scan", "review_status": "reviewed",
     "status": "confirmed", "cross_model_verified": False, "decided_at": "2026-11-05T11:00:00Z"},
    # mission3: 只有 pending 占位行 → 计入 D0，不计入 D1/D2
    {"mission_id": 3, "reviewer_kind": "", "review_status": "unreviewed",
     "status": "pending", "cross_model_verified": False, "decided_at": ""},
    # mission5: 独立复核但被拒 → 不计入分子
    {"mission_id": 5, "reviewer_kind": "independent", "review_status": "reviewed",
     "status": "rejected", "cross_model_verified": False, "decided_at": "2026-11-07T13:00:00Z"},
    # mission6: 独立复核通过 + 异构交叉 → 计入 D2 与 D3
    {"mission_id": 6, "reviewer_kind": "independent", "review_status": "reviewed",
     "status": "confirmed", "cross_model_verified": True, "decided_at": "2026-11-06T09:00:00Z"},
]

# 期望值（手工核算，独立于被测代码）
N_PRS, N_MISSIONS, M_PRS = 8, 6, 3
D0, D1, D2, D3 = 6, 4, 3, 1
RATE = 37.5


class FakeReader:
    """实现 Reader 协议的最小合成数据源（脱库）。"""

    def __init__(self, den=None, evals=None, backlog=None, supply=None):
        self._den = DEN if den is None else den
        self._evals = EVAL if evals is None else evals
        self._backlog = {"pending_rows": 7, "pending_missions": 7, "pending_succeeded_missions": 1} \
            if backlog is None else backlog
        self._supply = supply or [{"day": "2026-11-05", "review_status": "reviewed",
                                   "status": "confirmed", "rows": 2}]

    def denominator(self, days):
        return list(self._den)

    def evaluations(self, mission_ids):
        ids = set(mission_ids)
        return [e for e in self._evals if e["mission_id"] in ids]

    def review_supply(self, since):
        return list(self._supply)

    def pending_backlog(self):
        return dict(self._backlog)


# ── 口径与算术 ────────────────────────────────────────────────────────

def test_aggregate_expected_numbers():
    r = aggregate(DEN, EVAL, days=30, measured_at="2026-11-11T00:00:00Z")
    assert r["denominator"]["merged_prs"] == N_PRS
    assert r["denominator"]["merged_missions"] == N_MISSIONS
    assert r["numerator"]["ind_reviewed_prs"] == M_PRS
    assert r["current"] == RATE
    assert r["gap"] == round(spec.TARGET - RATE, 2) == 62.5
    assert r["unit"] == "percent" and r["target"] == 100.0
    assert (r["ladder"]["D0_any_record"]["prs"], r["ladder"]["D1_any_confirmed"]["prs"],
            r["ladder"]["D2_independent_confirmed"]["prs"],
            r["ladder"]["D3_independent_cross_model"]["prs"]) == (D0, D1, D2, D3)


def test_latency_median_from_first_independent_decision():
    r = aggregate(DEN, EVAL, days=30)
    # 时延按 PR 计（同一 mission 的多条 PR 各自计时）：
    # p1 08:00→10:00=2h、p2 09:00→10:00=1h、p8 06:00→09:00=3h → median 2h（3 条样本）
    assert r["latency"]["n"] == 3
    assert r["latency"]["median_h"] == pytest.approx(2.0)


def test_dedupe_keeps_earliest_merged_at():
    rows = dedupe_denominator(DEN)
    assert len(rows) == N_PRS
    p1 = [r for r in rows if r["pr"] == "p1"][0]
    assert p1["merged_at"] == "2026-11-05T08:00:00Z"


def test_auto_scan_never_enters_numerator():
    """口径纯度：auto_scan reviewed+confirmed 的 mission 只进 D1，不进 D2/分子。"""
    r = aggregate(DEN, EVAL, days=30)
    assert r["ladder"]["D1_any_confirmed"]["prs"] - r["ladder"]["D2_independent_confirmed"]["prs"] == 1
    assert r["numerator"]["ind_reviewed_prs"] == M_PRS


def test_ladder_is_monotone():
    r = aggregate(DEN, EVAL, days=30)
    ladder = [r["ladder"][k]["prs"] for k in
              ("D3_independent_cross_model", "D2_independent_confirmed",
               "D1_any_confirmed", "D0_any_record")]
    assert ladder == sorted(ladder), f"阶梯必须 D3≤D2≤D1≤D0，实际 {ladder}"
    assert all(v <= N_PRS for v in ladder)


def test_unreviewed_partition_covers_denominator():
    r = aggregate(DEN, EVAL, days=30)
    un = {(p["repo"], p["pr"]) for p in r["unreviewed_prs"]}
    rv = {("RepoA", "p1"), ("RepoA", "p2"), ("RepoA", "p8")}
    assert un.isdisjoint(rv)
    assert len(un) == N_PRS - M_PRS == 5
    assert un | rv == {(d["repo"], d["pr"]) for d in dedupe_denominator(DEN)}
    # 未复核不等于无记录：p3 只有 auto_scan 记录、p4 只有 pending 占位行、
    # p7 是独立复核被拒 —— 三者都被判为"未通过独立复核"，但 has_any_review=True
    assert [p["pr"] for p in r["unreviewed_prs"] if p["has_any_review"]] == ["p3", "p4", "p7"]


def test_per_repo_reconciles_to_denominator():
    r = aggregate(DEN, EVAL, days=30)
    assert sum(x["merged_prs"] for x in r["per_repo"]) == N_PRS
    assert sum(x["ind_reviewed"] for x in r["per_repo"]) == M_PRS
    by = {x["repo"]: x for x in r["per_repo"]}
    assert by["RepoA"]["merged_prs"] == 4 and by["RepoA"]["ind_reviewed"] == 3
    assert by["RepoB"]["ind_reviewed"] == 0


def test_phase_split_partitions_denominator():
    r = aggregate(DEN, EVAL, days=30)
    ps = r["phase_split"]
    assert ps["before_or_on_pivot"]["cutoff"] == "2026-11-06"  # 最后一次独立结论日
    assert (ps["before_or_on_pivot"]["merged_prs"] + ps["after_pivot"]["merged_prs"]) == N_PRS
    assert ps["before_or_on_pivot"]["ind_reviewed"] == M_PRS
    assert ps["after_pivot"]["ind_reviewed"] == 0


def test_empty_denominator_is_zero_not_error():
    r = aggregate([], [], days=30)
    assert r["denominator"]["merged_prs"] == 0
    assert r["current"] == 0.0 and r["gap"] == 100.0
    assert r["latency"]["median_h"] is None


def test_measure_through_reader():
    r, raw = measure(FakeReader(), days=30)
    assert r["current"] == RATE
    assert len(raw["denominator"]) == N_PRS
    assert r["pending_backlog"]["pending_rows"] == 7
    assert r["review_supply"][0]["rows"] == 2


# ── 产物与 provenance ────────────────────────────────────────────────

def _run_to(tmp_path):
    r, raw = measure(FakeReader(), days=30, )
    r["measured_at"] = "2026-11-11T00:00:00Z"     # 固定时间戳以便比对可复现性
    paths = write_artifacts(r, raw, str(tmp_path))
    return r, paths


def test_artifacts_written_with_provenance(tmp_path):
    r, paths = _run_to(tmp_path)
    need = {"kpi_result.json", "recompute.sql", "unreviewed_prs.csv", "per_repo.csv",
            "daily.csv", "raw_denominator.csv", "raw_evaluations.csv", "provenance.json"}
    assert need <= {pathlib.Path(p).name for p in paths.values()}
    assert all(pathlib.Path(p).exists() for p in paths.values())
    prov = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert set(prov["files"]) == need - {"provenance.json"}
    assert all(len(v["sha256"]) == 64 for v in prov["files"].values())
    assert prov["counts"]["denominator_rows"] == N_PRS
    res = json.loads((tmp_path / "kpi_result.json").read_text(encoding="utf-8"))
    assert res["current"] == RATE and res["unreviewed_count"] == 5
    assert "mission_lifecycle_log" in (tmp_path / "recompute.sql").read_text(encoding="utf-8")


def test_provenance_reproducible_across_runs(tmp_path):
    _r1, _p1 = _run_to(tmp_path / "a")
    _r2, _p2 = _run_to(tmp_path / "b")
    p1 = json.loads((tmp_path / "a" / "provenance.json").read_text(encoding="utf-8"))
    p2 = json.loads((tmp_path / "b" / "provenance.json").read_text(encoding="utf-8"))
    assert p1["result_sha256"] == p2["result_sha256"], "同一输入 + 同一时间戳 → 结果摘要必须一致"
    for f in ("raw_denominator.csv", "raw_evaluations.csv", "recompute.sql"):
        assert p1["files"][f]["sha256"] == p2["files"][f]["sha256"]


# ── 第二实现（L1/L4）与 fail-closed ─────────────────────────────────

def test_independent_second_implementation_agrees(tmp_path):
    _r, _p = _run_to(tmp_path)
    ref = kverify.recompute_from_raw(str(tmp_path))
    got = json.loads((tmp_path / "kpi_result.json").read_text(encoding="utf-8"))
    bench = kverify.compare(ref, got, 30)
    assert bench["all_pass"], bench
    assert (ref["n_merged_prs"], ref["m_ind_reviewed"]) == (N_PRS, M_PRS)
    assert all(m["rel_err"] == 0.0 for m in bench["metrics"])


def test_verify_fails_closed_when_result_tampered(tmp_path):
    r, _p = _run_to(tmp_path)
    res_path = tmp_path / "kpi_result.json"
    res = json.loads(res_path.read_text(encoding="utf-8"))
    res["numerator"]["ind_reviewed_prs"] = M_PRS + 1        # 篡改分子
    res["current"] = round(100.0 * (M_PRS + 1) / N_PRS, 2)
    res_path.write_text(json.dumps(res), encoding="utf-8")
    rc = kverify.main(["--raw-dir", str(tmp_path), "--result", str(res_path),
                       "--out", str(tmp_path / "bench.json")])
    assert rc == 2
    bench = json.loads((tmp_path / "bench.json").read_text(encoding="utf-8"))
    assert bench["all_pass"] is False
    # 分子被篡改 → 分子与由其派生的通过率同时报 FAIL，且不掩盖为"部分通过"
    assert [m["metric"] for m in bench["metrics"] if not m["pass"]] == \
        ["numerator_ind_reviewed_prs", "kpi_current_pct"]


def test_measure_with_session_fails_closed(monkeypatch):
    import tools.kpi_qc_recheck.measure as m
    import tools.kpi_qc_recheck.queries as q

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(q.SessionReader, "denominator", boom)
    out = m.measure_with_session(object(), days=30)
    assert out["status"] == "unavailable"
    assert out["current"] is None           # 计量失败 ≠ 读数 0
    assert out["kpi_key"] == spec.KPI_KEY


# ── 平台侧集成（L3）：KPI 面板真调用计量能力 ─────────────────────────

def test_platform_dashboard_exposes_qc_dimension(monkeypatch):
    import services.platform_kpi as pk
    import tools.kpi_qc_recheck.measure as m

    monkeypatch.setattr(pk, "_compute_repro_rate", lambda db: 0.9)
    monkeypatch.setattr(pk, "_compute_kb_samples", lambda db: 200)
    monkeypatch.setattr(pk, "_compute_unattended", lambda db: 0.95)
    monkeypatch.setattr(pk, "_compute_benchmark", lambda db: 9)
    monkeypatch.setattr(m, "measure_with_session", lambda db, days=30: {
        "kpi_key": spec.KPI_KEY, "anchor": spec.ANCHOR, "current": 5.11,
        "numerator": 274, "denominator": 5359, "target": 100.0, "gap": 94.89,
        "status": "measured", "window_days": days,
    })
    monkeypatch.setattr(pk, "measure_with_session", m.measure_with_session, raising=False)
    pk._CACHE = {}
    kpis = pk.compute_all_kpis(db=None)
    assert kpis["qc_recheck_rate"]["value"] == 5.11
    assert kpis["qc_recheck_rate"]["target"] == 100.0
    assert kpis["qc_recheck_rate"]["pass"] is False
    assert kpis["qc_recheck_rate"]["denominator"] == 5359
    assert kpis["all_pass"] is True                    # 存量 4 维语义不变
    assert kpis["all_pass_including_qc"] is False       # 新维度不达标
    pk._CACHE = {}


def test_platform_dashboard_survives_measurement_failure(monkeypatch):
    import services.platform_kpi as pk

    monkeypatch.setattr(pk, "_compute_repro_rate", lambda db: 0.9)
    monkeypatch.setattr(pk, "_compute_kb_samples", lambda db: 200)
    monkeypatch.setattr(pk, "_compute_unattended", lambda db: 0.95)
    monkeypatch.setattr(pk, "_compute_benchmark", lambda db: 9)
    monkeypatch.setattr(pk, "_compute_qc_recheck",
                        lambda db, days=30: {"current": None, "status": "unavailable",
                                             "numerator": None, "denominator": None,
                                             "gap": None, "target": 100.0})
    pk._CACHE = {}
    kpis = pk.compute_all_kpis(db=None)
    assert kpis["qc_recheck_rate"]["status"] == "unavailable"
    assert kpis["qc_recheck_rate"]["pass"] is False
    assert kpis["all_pass_including_qc"] is False
    pk._CACHE = {}