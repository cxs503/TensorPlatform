"""评分器稳健性 / 交叉验证 / 外部效度验收测试（IN38-N09）。

判据：
1. 换行序、重复行、单 cohort 单独算 → 逐字段不变（评分器不依赖输入顺序/分片）。
2. 分母为 0 → ``None`` 并单列；分母非 0 的 0 是真值。
3. 外部效度：两条独立读取链路（线上 vs 快照）给同一组数；链路被篡改时必须报 diff。
4. 交叉验证：SQLite 无 PG 聚合函数时**如实报 not_supported**，不假通过。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import cohorts as ch  # noqa: E402
from tools.evaluation_ops import robustness as rb  # noqa: E402

AS_OF = "2026-11-07T00:00:00+00:00"

MISSIONS = []
RECORDS = []
for w, week in enumerate(["2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02"]):
    for i in range(3):
        mid = w * 10 + i + 1
        MISSIONS.append({"id": mid, "repo": "SyntheticRepo",
                         "finished_at": f"{week}T0{i}:00:00+00:00"})
        if i == 0 and w < 3:      # 最后一周刻意只留 pending → 该 cohort 的 P90 无定义
            RECORDS.append({"mission_id": mid, "status": "confirmed",
                            "decided_at": f"{week}T12:00:00+00:00"})
        elif i == 1:
            RECORDS.append({"mission_id": mid, "status": "pending", "decided_at": None})


def test_order_invariance():
    base = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    got = rb.order_invariance(MISSIONS, RECORDS, base, as_of=AS_OF, trials=6)
    assert got["identical"] is True
    assert got["diffs"] == []
    assert got["trials"] == 6


def test_duplicate_row_invariance():
    base = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    got = rb.duplicate_invariance(MISSIONS, RECORDS, base, as_of=AS_OF, copies=3)
    assert got["identical"] is True


def test_locality_invariance():
    base = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    got = rb.locality_invariance(MISSIONS, RECORDS, base, as_of=AS_OF)
    assert got["identical"] is True
    assert got["cohorts"] == len(base)


def test_denominator_semantics_clean():
    got = rb.denominator_none(MISSIONS, RECORDS, as_of=AS_OF)
    assert got["ok"] is True, got["violations"]
    assert got["violations"] == []
    assert any("p90" in c for c in got["undefined_cases"])


def test_denominator_violation_is_detected():
    """负控：把 P90 的 None 伪造成 0.0 → 判据必须抓到（不许静默通过）。"""
    rows = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    tampered = {k: dict(v) for k, v in rows.items()}
    key = next(k for k, v in tampered.items() if v["latency_n"] == 0)
    tampered[key]["p90_latency_hours"] = 0.0
    orig = rb.compute_cohorts
    try:
        rb.compute_cohorts = lambda *a, **kw: tampered   # type: ignore[assignment]
        got = rb.denominator_none(MISSIONS, RECORDS, as_of=AS_OF)
    finally:
        rb.compute_cohorts = orig
    assert got["ok"] is False
    assert any("P90" in v["reason"] for v in got["violations"])


def test_external_validity_identical():
    live = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    snap = ch.compute_cohorts(list(reversed(MISSIONS)), list(reversed(RECORDS)), as_of=AS_OF)
    got = rb.external_validity(live, snap, name="snapshot")
    assert got["identical"] is True
    assert got["cohorts_compared"] == len(live)


def test_external_validity_detects_divergence():
    live = ch.compute_cohorts(MISSIONS, RECORDS, as_of=AS_OF)
    snap = {k: dict(v) for k, v in live.items()}
    key = sorted(snap)[0]
    snap[key]["missions_decided"] = snap[key]["missions_decided"] + 1
    got = rb.external_validity(live, snap, name="snapshot")
    assert got["identical"] is False
    assert got["diffs"] and got["diffs"][0]["cohort_key"] == key


def test_crosscheck_honest_not_supported_on_sqlite():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    conn = create_engine("sqlite+pysqlite://", future=True,
                         connect_args={"check_same_thread": False},
                         poolclass=StaticPool).connect()
    got = ch.sql_crosscheck(conn, {})
    assert got["supported"] is False
    assert "sqlite" in got["reason"]


def test_score_invariants_bundle_ok():
    got = rb.score_invariants(MISSIONS, RECORDS, as_of=AS_OF, trials=3)
    assert got["ok"] is True
    assert got["cohorts"] == 4
    for section in ("order", "duplicates", "locality", "denominator"):
        assert got[section] is not None