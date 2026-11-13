"""子集探针的裁决词：PARTIAL（未评估）不得被读成 FAIL 或 PASS。

缺陷（KPI-TOOLS-5 返修轮2 · 台账缺口 3）：``POST /external-tools/probe`` 只探子集时，
coverage 仍以全量目标（如 5 类）作分母 → ``verdict=FAIL``，而响应的 ``partial`` 字段
又是在 ``verdict()`` 之后才写进 ledger，调用方按 ``verdict`` 读取就把「可用」读成
「失败」。这里把修复后的三条性质钉住：

1. 子集全 REAL → ``PARTIAL``（KPI 未评估），且 ``kpi_evaluated=False``；
2. 子集里真出问题 → 仍是 ``FAIL``（PARTIAL 不是免罪符）；
3. 全量跑 → 依旧 PASS/FAIL，``partial=False``，且只有全量跑的结果能落盘成 KPI 台账。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "backend"):          # backend/ 是 api.* / services.* 的导入根
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.external_tools import cli, harness, report, spec as spec_mod  # noqa: E402


def _cls(tool_id="version_control", adapter="version_control", metrics=None,
         limit=1e9, op="le", probe=None):
    return {"tool_id": tool_id, "category": "t", "adapter": adapter,
            "probe": probe or {"kind": "none"},
            "invariants": [{"metric": metrics or "commit_count", "limit": limit, "op": op}]}


def _write_spec(tmp_path: Path, classes, target=5, name="spec.json") -> Path:
    payload = {"suite_id": "test/partial", "target_classes": target, "classes": classes}
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ── 1. 子集全 REAL → PARTIAL ────────────────────────────────────────────────
def test_subset_all_real_is_partial_not_fail(tmp_path):
    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    suite = spec_mod.load_suite(spec_file)

    subset = harness.run_suite(suite, only=["vc_a"], ctx={"repo": str(ROOT)})
    assert subset["partial"] is True
    assert subset["selected_tool_ids"] == ["vc_a"]
    assert subset["coverage"]["ok"] is False           # 目标没被验证，不是被证伪
    assert harness.verdict(subset) == harness.VERDICT_PARTIAL

    full = harness.run_suite(suite, ctx={"repo": str(ROOT)})
    assert full["partial"] is False
    assert harness.verdict(full) == harness.VERDICT_PASS


# ── 2. 子集里的真问题仍是 FAIL（PARTIAL 不等于免罪）────────────────────────
def test_subset_with_violation_stays_fail(tmp_path):
    spec_file = _write_spec(tmp_path, [_cls("vc_bad", limit=0.0)], target=1)
    suite = spec_mod.load_suite(spec_file)
    subset = harness.run_suite(suite, only=["vc_bad"], ctx={"repo": str(ROOT)})
    assert subset["classes"][0]["status"] == "FAIL"
    assert harness.verdict(subset) == harness.VERDICT_FAIL


def test_subset_with_absent_tool_stays_fail(tmp_path):
    spec_file = _write_spec(tmp_path, [_cls("ghost", adapter="symbolic_cas",
                                           probe={"kind": "module",
                                                  "name": "no_such_module_partial_xyz"})],
                            target=1)
    subset = harness.run_suite(spec_mod.load_suite(spec_file), only=["ghost"])
    assert subset["classes"][0]["status"] == "WITHHELD"
    assert harness.verdict(subset) == harness.VERDICT_FAIL


# ── 3. 服务层：PARTIAL 不能落成 KPI 台账 ────────────────────────────────────
def test_service_probe_marks_partial_and_never_persists(tmp_path, monkeypatch):
    from services import external_tools_service as svc

    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    out = tmp_path / "ledger"
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(spec_file))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(out))

    subset = svc.probe(["vc_a"], persist=True)          # persist 对子集无效
    assert subset["verdict"] == harness.VERDICT_PARTIAL
    assert subset["partial"] is True and subset["kpi_evaluated"] is False
    assert subset["artifacts"] == {}
    assert not (out / "latest.json").exists()           # 子集不能冒充 KPI 台账
    with pytest.raises(svc.ExternalToolsError):
        svc.coverage()

    full = svc.probe(persist=True)
    assert full["verdict"] == harness.VERDICT_PASS
    assert full["kpi_evaluated"] is True
    assert (out / "latest.json").exists()
    assert svc.coverage()["real_classes"] == 2


# ── 4. HTTP 面：probe 子集的 verdict 就是 PARTIAL ───────────────────────────
def test_api_probe_subset_returns_partial(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    out = tmp_path / "ledger"
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(spec_file))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(out))

    from api import external_tools as api

    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app)

    r = client.post("/api/v1/external-tools/probe",
                    json={"tool_ids": ["vc_a"], "repo": str(ROOT)})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "PARTIAL"
    assert body["partial"] is True and body["kpi_evaluated"] is False

    r2 = client.post("/api/v1/external-tools/probe", json={"repo": str(ROOT)})
    assert r2.status_code == 200
    assert r2.json()["verdict"] == "PASS"


# ── 5. CLI：子集退出码 3（不同 PASS=0 / FAIL=1），输出带 subset 标注 ────────
def test_cli_subset_exit_code_is_three(tmp_path, capsys):
    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    out = tmp_path / "out"

    rc = cli.main(["--spec", str(spec_file), "--out-dir", str(out), "--repo", str(ROOT),
                   "run", "--only", "vc_a"])
    printed = capsys.readouterr().out
    assert rc == 3, printed
    assert "verdict=PARTIAL" in printed and "subset" in printed
    assert not (out / "latest.json").exists()           # 子集不得占据 KPI 读槽
    assert list(out.glob("external_tools_*.json"))      # 但留痕（取证用）

    rc_full = cli.main(["--spec", str(spec_file), "--out-dir", str(out), "--repo", str(ROOT),
                        "run"])
    capsys.readouterr()
    assert rc_full == 0
    assert (out / "latest.json").exists()


# ── 7. 子集台账占住 latest.json 时，KPI 端点必须拒绝作答而不是报假缺口 ──────
def test_coverage_rejects_partial_ledger(tmp_path, monkeypatch):
    from services import external_tools_service as svc

    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    out = tmp_path / "ledger"
    out.mkdir()
    subset = harness.run_suite(spec_mod.load_suite(spec_file), only=["vc_a"],
                               ctx={"repo": str(ROOT)})
    subset["verdict"] = harness.verdict(subset)
    (out / "latest.json").write_text(json.dumps(subset), encoding="utf-8")
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_SPEC", str(spec_file))
    monkeypatch.setenv("TENSOR_EXTERNAL_TOOLS_DIR", str(out))

    with pytest.raises(svc.ExternalToolsError) as exc:
        svc.coverage()
    assert "PARTIAL" in str(exc.value)
    with pytest.raises(svc.ExternalToolsError):
        svc.benchmark()


# ── 6. 报告页把 PARTIAL 原样搬出来（不做粉饰）───────────────────────────────
def test_markdown_reports_partial_verdict(tmp_path):
    spec_file = _write_spec(tmp_path, [_cls("vc_a"), _cls("vc_b")], target=2)
    ledger = harness.run_suite(spec_mod.load_suite(spec_file), only=["vc_a"],
                               ctx={"repo": str(ROOT)})
    ledger["verdict"] = harness.verdict(ledger)
    md = report.render_markdown(ledger)
    assert "PARTIAL" in md