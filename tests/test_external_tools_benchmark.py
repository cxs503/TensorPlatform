"""Benchmark 面：ref→got→误差 的算术必须可机裁，且不许在无参考值时编百分比。

四条会被误当绿的性质，逐条有测试：

1. 声明了 ``ref`` 的指标 → 行里必须有 ref/got/abs_err，且 rel_err = abs_err/|ref|；
2. 参考值是精确 0（恒等式残差）→ ``rel_err`` 必须为 None（除以零 = 编百分比）；
3. 规格没声明 ``ref`` → 行标 ``unscored``，不参与 3%/15% 统计；
4. 台账里没有该指标（WITHHELD/FAIL）→ ref 在、got 空、误差空，不得由 ref 反推 got。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "backend", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.external_tools import benchmark as bench_mod  # noqa: E402
from tools.external_tools import harness, report, spec as spec_mod  # noqa: E402


def _suite(cls_entries, target=1):
    payload = {"suite_id": "test/bench", "target_classes": target,
               "classes": cls_entries}
    p = ROOT / "pytest_tmp_spec.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    try:
        return spec_mod.load_suite(p)
    finally:
        p.unlink(missing_ok=True)


def _ledger(records):
    return {"suite_id": "test/bench", "generated_at": "2026-01-01T00:00:00+00:00",
            "digest": "d" * 64, "value_digest": "v" * 64, "classes": records}


def _record(tool_id, status="REAL", metrics=None, invs=None):
    return {"tool_id": tool_id, "category": "t", "status": status,
            "metrics": metrics or {}, "invariants": invs or [],
            "detail": "", "call": {}, "probe": {}}


def _inv(metric, limit, op="le", target=None, ref=None, ref_source="", ok=True):
    out = {"metric": metric, "limit": limit, "op": op, "target": target,
           "observed": None, "ok": ok}
    if ref is not None:
        out["ref"] = ref
    if ref_source:
        out["ref_source"] = ref_source
    return out


def test_value_ref_yields_relative_error():
    suite = _suite([{"tool_id": "quad", "category": "t", "adapter": "x",
                     "probe": {"kind": "none"},
                     "invariants": [{"metric": "v", "limit": 1e-6, "op": "abs",
                                     "target": 3.0, "ref": 3.0,
                                     "ref_source": "closed form"}]}])
    led = _ledger([_record("quad", metrics={"v": 3.000003},
                           invs=[_inv("v", 1e-6, "abs", target=3.0,
                                      ref=3.0, ref_source="closed form")])])
    table = bench_mod.build(led, suite)
    row = table["rows"][0]
    assert row["kind"] == "value" and row["ref"] == 3.0
    assert row["abs_err"] == pytest.approx(3e-6)
    assert row["rel_err"] == pytest.approx(1e-6)
    assert row["within_3pct"] is True and row["within_15pct"] is True
    assert table["summary"]["n_scored_rel"] == 1


def test_identity_residual_has_no_relative_error():
    suite = _suite([{"tool_id": "ident", "category": "t", "adapter": "x",
                     "probe": {"kind": "none"},
                     "invariants": [{"metric": "residual", "limit": 1e-9,
                                     "op": "le", "ref": 0.0}]}])
    led = _ledger([_record("ident", metrics={"residual": 1e-12},
                           invs=[_inv("residual", 1e-9, ref=0.0)])])
    row = bench_mod.build(led, suite)["rows"][0]
    assert row["ref"] == 0.0 and row["abs_err"] == pytest.approx(1e-12)
    assert row["rel_err"] is None and row["within_3pct"] is None


def test_no_reference_is_unscored_not_green():
    suite = _suite([{"tool_id": "novc", "category": "t", "adapter": "x",
                     "probe": {"kind": "none"},
                     "invariants": [{"metric": "png_bytes", "limit": 10.0, "op": "ge"}]}])
    led = _ledger([_record("novc", metrics={"png_bytes": 5000.0},
                           invs=[_inv("png_bytes", 10.0, "ge")])])
    table = bench_mod.build(led, suite)
    row = table["rows"][0]
    assert row["kind"] == "unscored"
    assert table["summary"]["n_unscored"] == 1
    assert table["summary"]["n_scored_rel"] == 0


def test_withheld_metric_keeps_ref_but_no_got():
    suite = _suite([{"tool_id": "db", "category": "t", "adapter": "x",
                     "probe": {"kind": "none"},
                     "invariants": [{"metric": "answer", "limit": 0.0, "op": "abs",
                                     "target": 42.0, "ref": 42.0}]}])
    led = _ledger([_record("db", status="WITHHELD",
                           invs=[_inv("answer", 0.0, "abs", target=42.0, ref=42.0,
                                      ok=False)])])
    table = bench_mod.build(led, suite)
    row = table["rows"][0]
    assert row["ref"] == 42.0 and row["observed"] is None
    assert row["abs_err"] is None and row["rel_err"] is None
    assert table["summary"]["all_invariants_ok"] is False


def test_markdown_carries_every_case():
    suite = _suite([{"tool_id": "quad", "category": "t", "adapter": "x",
                     "probe": {"kind": "none"},
                     "invariants": [{"metric": "v", "limit": 1e-6, "op": "le",
                                     "ref": 0.0}]}])
    led = _ledger([_record("quad", metrics={"v": 1e-9},
                           invs=[_inv("v", 1e-6)])])
    md = bench_mod.render_markdown(bench_mod.build(led, suite))
    assert "`quad.v`" in md and "ref vs got" in md


def test_repo_spec_declares_references_for_value_metrics():
    suite = spec_mod.load_suite(ROOT / "data" / "external_tools" / "classes.json")
    with_ref = [inv for cls in suite.classes for inv in cls.invariants if inv.ref is not None]
    assert len(with_ref) >= 12, "benchmark needs declared refs, not self-comparison"
    valued = [inv for inv in with_ref if inv.ref != 0.0]
    assert len(valued) >= 6, "at least six non-zero references (ref-vs-got, not residual)"
    for inv in with_ref:
        assert inv.ref_source, f"{inv.metric}: a reference without a source is not a reference"


def test_local_socket_dsn_candidate_is_offered(monkeypatch):
    from tools.external_tools import adapters

    monkeypatch.setenv("TP_PG_DB", "tensorplatform")
    monkeypatch.setenv("TP_PG_USER", "nobody")
    monkeypatch.setenv("TP_PG_PASSWORD", "")
    cands = adapters.dsn_candidates({"repo": ""})
    sources = [s for _, s in cands]
    assert any("local socket" in s for s in sources), sources


def test_benchmark_endpoint_reads_persisted_ledger(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    suite = spec_mod.load_suite(_write_spec(tmp_path))
    ledger = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    ledger["verdict"] = harness.verdict(ledger)
    report.write_artifacts(ledger, tmp_path / "ledger")
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(tmp_path / "spec.json"))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(tmp_path / "ledger"))

    from api import external_tools as api

    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    r = TestClient(app).get("/api/v1/external-tools/benchmark")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anchor_slug"] == "KPI-TOOLS-5"
    assert body["summary"]["n_cases"] == len(body["rows"]) > 0


def test_benchmark_endpoint_without_ledger_is_503(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import fastapi

    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(_write_spec(tmp_path)))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(tmp_path / "empty"))
    from api import external_tools as api_mod

    app = fastapi.FastAPI()
    app.include_router(api_mod.router, prefix="/api/v1")
    assert TestClient(app).get("/api/v1/external-tools/benchmark").status_code == 503


def _write_spec(tmp_path: Path) -> Path:
    """One fast class (version_control) so the HTTP test does not run 10 probes."""
    payload = {"suite_id": "test/http-bench", "target_classes": 1, "classes": [{
        "tool_id": "version_control", "category": "vcs", "adapter": "version_control",
        "probe": {"kind": "executable", "name": "git"},
        "invariants": [{"metric": "commit_count", "limit": 1.0, "op": "ge"}]}]}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p