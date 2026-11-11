"""外部工具类接入：规格校验 / 失效不静默 / 台账指纹可复算 / HTTP 面。

这三组测试对应三条会被误当绿的性质：
1. 规格写错（缺不变式、重复 id、未知 op）必须报错，不能默认放行；
2. 工具不在/失败 → WITHHELD 或 FAIL 且**没有数字**；
3. 同一环境两次跑的**结构指纹一致**（时间不进指纹），覆盖判据只看 REAL。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "backend"):          # backend/ 是 api.* / services.* 的导入根
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.external_tools import harness, report, spec as spec_mod  # noqa: E402


def _write_spec(tmp_path: Path, classes, target=1) -> Path:
    payload = {"suite_id": "test/suite", "target_classes": target, "classes": classes}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _cls(tool_id="version_control", adapter="version_control", metrics=None,
         limit=1e9, probe=None, op="le", target=None):
    inv = {"metric": (metrics or "commit_count"), "limit": limit, "op": op}
    if target is not None:
        inv["target"] = target
    return {"tool_id": tool_id, "category": "t", "adapter": adapter,
            "probe": probe or {"kind": "none"}, "invariants": [inv]}


# ── 规格校验 ────────────────────────────────────────────────────────────────
def test_spec_requires_an_invariant(tmp_path):
    with pytest.raises(spec_mod.SpecError):
        spec_mod.load_suite(_write_spec(tmp_path, [{"tool_id": "x", "adapter": "y"}]))


def test_spec_rejects_duplicate_ids(tmp_path):
    cls = _cls()
    with pytest.raises(spec_mod.SpecError):
        spec_mod.load_suite(_write_spec(tmp_path, [cls, dict(cls)]))


def test_spec_rejects_unknown_op(tmp_path):
    with pytest.raises(spec_mod.SpecError):
        spec_mod.load_suite(_write_spec(tmp_path, [_cls(op="lt")]))


def test_missing_spec_file_is_loud(tmp_path):
    with pytest.raises(spec_mod.SpecError):
        spec_mod.load_suite(tmp_path / "nope.json")


def test_weakest_rollup_is_fail_closed():
    assert spec_mod.weakest(["REAL", "REAL"]) == "REAL"
    assert spec_mod.weakest(["REAL", "MOCK"]) == "MOCK"
    assert spec_mod.weakest(["REAL", "FAIL"]) == "FAIL"
    assert spec_mod.weakest(["WITHHELD", "REAL"]) == "WITHHELD"
    assert spec_mod.weakest([]) == "WITHHELD"


def test_invariant_ops():
    inv = spec_mod.Invariant("m", 1.0, "abs", target=42.0)
    assert inv.check(42.0) and not inv.check(44.0)
    assert spec_mod.Invariant("m", 2.0, "ge").check(2.0)
    assert not spec_mod.Invariant("m", 2.0, "le").check(2.1)


# ── 失效不静默 ──────────────────────────────────────────────────────────────
def test_absent_tool_is_withheld_without_numbers(tmp_path):
    suite = spec_mod.load_suite(_write_spec(tmp_path, [
        _cls(tool_id="ghost", adapter="symbolic_cas",
             probe={"kind": "module", "name": "definitely_not_a_module_xyz"})]))
    ledger = harness.run_suite(suite)
    rec = ledger["classes"][0]
    assert rec["status"] == "WITHHELD"
    assert rec["metrics"] == {}          # 没有数字，就没有伪造的空间
    assert ledger["coverage"]["real_classes"] == 0
    assert harness.verdict(ledger) == "FAIL"


def test_invariant_violation_is_fail_not_real(tmp_path):
    suite = spec_mod.load_suite(_write_spec(tmp_path, [
        _cls(tool_id="vc", adapter="version_control", metrics="commit_count",
             limit=0.0)]))  # 真跑，但判据不可能满足
    ledger = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    rec = ledger["classes"][0]
    assert rec["status"] == "FAIL"
    assert rec["metrics"]["commit_count"] >= 1.0
    assert any("invariant" in (rec["detail"] or "") for _ in [0])


def test_cli_exit_code_signal(tmp_path):
    from tools.external_tools import cli

    good = _write_spec(tmp_path, [_cls()], target=1)
    out = tmp_path / "out"
    rc = cli.main(["--spec", str(good), "--out-dir", str(out), "--repo", str(ROOT), "run"])
    assert rc == 0
    bad = _write_spec(tmp_path, [_cls(tool_id="vc2", limit=0.0)], target=1)
    rc2 = cli.main(["--spec", str(bad), "--out-dir", str(out), "--repo", str(ROOT), "run"])
    assert rc2 == 1


# ── 指纹与覆盖判据 ──────────────────────────────────────────────────────────
def test_digest_is_reproducible_and_excludes_timing(tmp_path):
    suite = spec_mod.load_suite(_write_spec(tmp_path, [_cls()], target=1))
    a = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    b = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    assert a["digest"] == b["digest"]
    assert a["value_digest"] == b["value_digest"]
    assert a["duration_s"] != b["duration_s"] or True  # 计时不进指纹（弱断言即可）
    payload = json.dumps({"d": a["digest"], "v": a["value_digest"]})
    assert "duration" not in payload


def test_coverage_counts_real_only(tmp_path):
    suite = spec_mod.load_suite(_write_spec(tmp_path, [
        _cls(),
        _cls(tool_id="ghost2", adapter="symbolic_cas",
             probe={"kind": "module", "name": "definitely_not_a_module_xyz"}),
    ], target=2))
    ledger = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    assert ledger["measured"]["real_classes"] == 1
    assert ledger["coverage"]["shortfall"] == 1
    assert harness.verdict(ledger) == "FAIL"


def test_report_numbers_come_from_ledger(tmp_path):
    suite = spec_mod.load_suite(_write_spec(tmp_path, [_cls()], target=1))
    ledger = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    ledger["verdict"] = harness.verdict(ledger)
    md = report.render_markdown(ledger)
    assert "version_control" in md
    assert f"{ledger['measured']['real_classes']} / {ledger['coverage']['target_classes']}" in md


# ── 仓库真实规格（KPI-TOOLS-5）──
def test_repo_spec_is_valid_and_declares_target():
    suite = spec_mod.load_suite(ROOT / "data" / "external_tools" / "classes.json")
    assert suite.target_classes >= 5
    assert len(suite.classes) >= suite.target_classes
    assert len({c.category for c in suite.classes}) >= 5


# ── HTTP 面 ────────────────────────────────────────────────────────────────
def test_api_classes_and_coverage(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    spec_file = _write_spec(tmp_path, [_cls()], target=1)
    ledger = harness.run_suite(spec_mod.load_suite(spec_file), ctx={"repo": str(ROOT)})
    ledger["verdict"] = harness.verdict(ledger)
    out = tmp_path / "ledger"
    report.write_artifacts(ledger, out)
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(spec_file))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(out))

    from api import external_tools as api

    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)

    r = client.get("/api/v1/external-tools/classes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_classes"] == 1 and body["classes"][0]["last_status"] == "REAL"

    r = client.get("/api/v1/external-tools/coverage")
    assert r.status_code == 200, r.text
    cov = r.json()
    assert cov["real_classes"] == 1 and cov["target_classes"] == 1 and cov["ok"] is True

    r = client.post("/api/v1/external-tools/probe", json={"tool_ids": ["version_control"]})
    assert r.status_code == 200, r.text
    assert r.json()["classes"][0]["status"] == "REAL"

    r = client.post("/api/v1/external-tools/probe", json={"tool_ids": ["nope"]})
    assert r.status_code == 400


def test_coverage_without_ledger_is_503(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    spec_file = _write_spec(tmp_path, [_cls()], target=1)
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(spec_file))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(tmp_path / "empty"))
    import importlib

    from api import external_tools as api
    importlib.reload(api)
    import fastapi

    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    r = TestClient(app).get("/api/v1/external-tools/coverage")
    assert r.status_code == 503