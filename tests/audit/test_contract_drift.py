"""合同漂移矩阵的离线回归（m1762 / tp-infra-1）。

两条线：
1. **分类器单元判据**（合成输入，纯函数，不碰网络）；
2. **AST 合同抽取**：从 ``backend/api/calculations.py`` 静态抽取，零 import
   （不需要 psycopg2 / DB / 起服务），防止合同改了而矩阵没跟上；
3. **快照回归**：``artifacts/drift_probe.json`` 里记录的在线裁决，钉住
   「在线稳性端点可达性 = 0」「KG 扫描消失角恒定 = 硬编码」两个结论。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "verif", "contract_drift"))

import contract_drift as cd  # noqa: E402

CALC = os.path.join(ROOT, "backend", "api", "calculations.py")
ARTIFACT = os.path.join(ROOT, "tools", "verif", "contract_drift", "artifacts", "drift_probe.json")


def _live(req_required, req_props, resp_props=()):
    return {"req_required": list(req_required), "req_props": list(req_props),
            "resp_ref": "X", "resp_props": list(resp_props)}


def test_same_contract_when_live_matches_repo():
    repo = cd.ModelContract(name="R", required=["gm_m", "heel_angle_deg"],
                            optional=[], aliases={})
    out = cd.classify(repo, None, _live(["gm_m"], ["gm_m", "heel_angle_deg"]),
                      "/api/v1/calculations/stability")
    assert out["status"] == cd.STATUS_SAME
    assert out["response_drift"] is True  # 受证输出一个都没有 → 输出侧仍漂移


def test_hidden_inputs_when_live_drops_verified_fields():
    repo = cd.ModelContract(name="R", required=["gm_m"], optional=["depth_m"], aliases={})
    out = cd.classify(repo, None, _live(["gm_m"], ["gm_m"]),
                      "/api/v1/calculations/stability")
    assert out["status"] == cd.STATUS_HIDDEN
    assert "depth_m" in out["live_hides_verified_inputs"]
    assert out["reachability"] < 1.0


def test_topology_drift_when_live_demands_new_and_hides_verified():
    repo = cd.ModelContract(name="R", required=["gm_m"], optional=[], aliases={})
    out = cd.classify(repo, None, _live(["cb", "kg"], ["cb", "kg"]),
                      "/api/v1/calculations/stability")
    assert out["status"] == cd.STATUS_TOPOLOGY
    assert out["reachability"] == 0.0


def test_input_drift_when_live_demands_unseen_only():
    repo = cd.ModelContract(name="R", required=["a"], optional=[], aliases={})
    out = cd.classify(repo, None, _live(["a", "asset_id"], ["a", "asset_id"]),
                      "/api/v1/calculations/hydrostatics")
    assert out["status"] == cd.STATUS_INPUT_DRIFT
    assert out["live_demands_unseen_inputs"] == ["asset_id"]


def test_alias_counts_as_accepted():
    repo = cd.ModelContract(name="R", required=["displacement_t"], optional=[],
                            aliases={"displacement_t": ["displacement"]})
    out = cd.classify(repo, None, _live(["displacement"], ["displacement"]),
                      "/api/v1/calculations/hydrostatics")
    assert not out["live_demands_unseen_inputs"]


def test_ast_router_prefix_from_calculations():
    assert cd.parse_router_prefix(CALC) == "/calculations"


def test_ast_models_expose_adjudication_fields():
    models = cd.parse_models(CALC)
    assert "StabilityRequest" in models and "StabilityResponse" in models
    req, resp = models["StabilityRequest"], models["StabilityResponse"]
    for f in ("gm_m", "bm_m", "kb_m", "heel_angle_deg"):
        assert f in req.required, f
    assert "displacement" in req.aliases.get("displacement_t", [])
    for f in ("gz_basis", "wall_sided_valid", "gz_box_exact_m"):
        assert f in resp.optional or f in resp.required, f


@pytest.mark.skipif(not os.path.exists(ARTIFACT), reason="无在线快照工件")
def test_snapshot_live_stability_unreachable():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    st = next(r for r in d["contract_matrix"]["rows"]
              if r["endpoint"] == "POST /api/v1/calculations/stability")
    assert st["status"] == cd.STATUS_TOPOLOGY
    assert st["reachability"] == 0.0
    assert "gm_m" in st["live_hides_verified_inputs"]
    assert d["stability"]["repo_canonical_dialect"]["status"] == 422


@pytest.mark.skipif(not os.path.exists(ARTIFACT), reason="无在线快照工件")
def test_snapshot_live_hydrostatics_errors_exceed_gate():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    rows = {r["label"]: r for r in d["stability"]["comparison_vs_analytic"]}
    assert abs(rows["KB"]["rel_pct"] - 60.0) < 0.2      # 解析 T/2
    assert abs(rows["BM"]["rel_pct"] - 35.0) < 0.2      # 解析 B^2/(12T)
    assert abs(rows["GM"]["rel_pct"]) > 15.0            # 远超 15% 拒收线


@pytest.mark.skipif(not os.path.exists(ARTIFACT), reason="无在线快照工件")
def test_snapshot_vanishing_angle_is_hardcoded():
    d = json.load(open(ARTIFACT, encoding="utf-8"))
    hard = [h for h in d["hardcode_sensitivity"] if h.get("live")]
    assert len(hard) >= 3
    assert len({h["live"]["vanishing_angle"] for h in hard}) == 1   # 不随 KG 变
    assert len({h["live"]["gz_max_angle"] for h in hard}) == 1
    assert len({h["live"]["gm"] for h in hard}) > 1                 # 但 GM 确实随 KG 变
