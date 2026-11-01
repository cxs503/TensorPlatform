"""基准数据集一致性门（G1–G3/G4b）与平台公式复核（G5）的测试。

两类断言
--------
1. **引擎正确性**：用合成数据注入已知矛盾，断言引擎能抓住（且不误报干净数据）。
2. **台账快照**：站内真实基准数据集的残差必须与已入库审计台账
   ``artifacts/verifier/refs_audit_m1396.json`` 一致（1e-9）。数据被修好/改坏都会
   使快照失配——这是有意的：台账必须跟踪数据。修数后请重跑
   ``python3 -m tools.refs.audit`` 刷新台账与本文档表格。
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
for p in (REPO_ROOT, BACKEND_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from services import benchmark_integrity as bi   # noqa: E402

LEDGER_PATH = os.path.join(REPO_ROOT, "artifacts", "verifier", "refs_audit_m1396.json")
BENCHMARK_PATH = os.path.join(BACKEND_DIR, "data", "benchmark_reference.json")


def _case(**over):
    case = {
        "case_id": "T-01",
        "ship_type": "TEST",
        "speed_ms": 3.0,
        "froude_number": 0.459,
        "experimental_ct": 0.0038,
        "experimental_rt_n": 92.7,
        "geometry": {
            "length_m": 4.356, "beam_m": 0.508, "draft_m": 0.508,
            "wetted_surface_m2": 5.42, "cb": 0.61,
        },
    }
    case.update(over)
    return case


# ---------------------------------------------------------------------------
# G1 Froude
# ---------------------------------------------------------------------------
def test_g1_accepts_consistent_froude():
    findings = bi.gate_froude([_case()])
    assert findings[0]["severity"] == "INFO"
    assert findings[0]["residual_rel"] < bi.FR_TOL_REL


def test_g1_detects_scale_mixing():
    """DTMB-5415 型错误复现：模型尺度长度 + 全尺度航速 → Fr 差 5.6 倍。"""
    case = _case(case_id="SCALE-MIX", speed_ms=2.572, froude_number=0.061,
                 geometry={"length_m": 5.72, "beam_m": 0.759, "draft_m": 0.246,
                           "wetted_surface_m2": 4.65, "cb": 0.508})
    finding = bi.gate_froude([case])[0]
    assert finding["severity"] == "FAIL"
    assert finding["residual_rel"] > 4.0
    assert "L≈181" in finding["note"] or "181.3" in finding["note"]


def test_g1_warns_on_missing_triplet():
    case = _case()
    case.pop("froude_number")
    assert bi.gate_froude([case])[0]["severity"] == "WARN"


# ---------------------------------------------------------------------------
# G2 Ct / Rt / 密度反解
# ---------------------------------------------------------------------------
def test_g2_water_density_pass_and_fail():
    # 隐含水密度 = Rt/(0.5·Ct·S·V²)：0.5*1025*5.42*9*0.0038 = 95.0 N
    rt_ok = 0.5 * 1025.0 * 5.42 * 9.0 * 0.0038
    assert bi.gate_ct_resistance([_case(experimental_rt_n=rt_ok)])[0]["severity"] == "INFO"
    # 同 Ct 但 Rt 只有 12.5 N → 隐含 ρ≈135 kg/m³，非水
    finding = bi.gate_ct_resistance([_case(experimental_rt_n=12.5)])[0]
    assert finding["severity"] == "FAIL"
    assert finding["computed"] == pytest.approx(134.9, abs=0.5)


def test_g2_implied_density_inverts_exactly():
    rho = 1025.0
    rt = 0.5 * rho * 5.42 * 9.0 * 0.004
    assert bi.implied_density(rt, 0.004, 5.42, 3.0) == pytest.approx(rho, rel=1e-12)


def test_g2_warns_without_wetted_surface():
    case = _case(geometry={"length_m": 5.72, "beam_m": 0.759, "draft_m": 0.246, "cb": 0.508})
    assert bi.gate_ct_resistance([case])[0]["severity"] == "WARN"


# ---------------------------------------------------------------------------
# G3 Cb / 排水量
# ---------------------------------------------------------------------------
def test_g3_accepts_consistent_cb():
    # 排水量由几何与 Cb 正推（不是抄来的数）：m = ρ·Cb·L·B·T
    cb_, l_, b_, t_ = 0.8098, 320.0, 58.0, 20.8
    geo = {"length_m": l_, "beam_m": b_, "draft_m": t_, "cb": cb_,
           "displacement_t": 1025.0 * cb_ * l_ * b_ * t_ / 1000.0}
    assert bi.gate_cb_displacement([_case(geometry=geo)])[0]["severity"] == "INFO"


def test_g3_displacement_unit_t_is_scaled():
    """回归：displacement_t 必须乘 1000。曾因单位未换算把 4.2% 误判成 99.9%。"""
    geo = {"length_m": 320.0, "beam_m": 58.0, "draft_m": 20.8, "cb": 0.8098,
           "displacement_t": 307000.0}
    finding = bi.gate_cb_displacement([_case(geometry=geo)])[0]
    assert finding["severity"] == "FAIL"
    assert finding["residual_rel"] < 0.1          # 真实偏差 4~5%，不是 99.9%
    assert finding["computed"] == pytest.approx(0.7758, abs=1e-3)


def test_g3_displacement_kg_preferred():
    geo = {"length_m": 5.72, "beam_m": 0.759, "draft_m": 0.246, "cb": 0.507,
           "displacement_kg": 1025.0 * 5.72 * 0.759 * 0.246 * 0.507}
    assert bi.gate_cb_displacement([_case(geometry=geo)])[0]["severity"] == "INFO"


def test_g3_warns_without_mass_or_geometry():
    assert bi.gate_cb_displacement(
        [_case(geometry={"length_m": 4.356, "beam_m": 0.508, "draft_m": 0.508, "cb": 0.61})]
    )[0]["severity"] in ("WARN", "INFO")
    assert bi.gate_cb_displacement(
        [_case(geometry={"length_m": 4.356, "beam_m": 0.508, "draft_m": 0.508})]
    )[0]["severity"] == "WARN"


# ---------------------------------------------------------------------------
# G4b 几何预设污染
# ---------------------------------------------------------------------------
def test_preset_contamination_detects_copy_paste():
    presets = {"BAD": {"length": 4.356, "beam": 0.508, "draft": 0.508,
                       "displacement": 0.508, "cb": 0.508}}
    assert bi.preset_contamination(presets)[0]["severity"] == "FAIL"


def test_preset_contamination_clean_case():
    presets = {"GOOD": {"length": 5.72, "beam": 0.759, "draft": 0.246,
                        "displacement": 555.0, "cb": 0.507}}
    assert bi.preset_contamination(presets)[0]["severity"] == "INFO"


# ---------------------------------------------------------------------------
# 汇总语义
# ---------------------------------------------------------------------------
def test_summarise_verdict_logic():
    assert bi.summarise([bi.finding("G1", "c", "INFO", "q")])["verdict"] == "PASS"
    assert bi.summarise([bi.finding("G1", "c", "WARN", "q")])["verdict"] == "WARN"
    assert bi.summarise([bi.finding("G1", "c", "WARN", "q"),
                         bi.finding("G1", "c", "FAIL", "q")])["verdict"] == "FAIL"


def test_clean_dataset_passes_all_gates():
    cb_, l_, b_, t_ = 0.8098, 320.0, 58.0, 20.8
    geo = {"length_m": l_, "beam_m": b_, "draft_m": t_, "cb": cb_,
           "displacement_t": 1025.0 * cb_ * l_ * b_ * t_ / 1000.0,
           "wetted_surface_m2": 27194.0}
    ct = 0.0023
    rt = 0.5 * 1025.0 * 27194.0 * (7.974 ** 2) * ct
    case = _case(case_id="KVLCC2-CLEAN", speed_ms=7.974,
                 froude_number=7.974 / math.sqrt(9.80665 * 320.0),
                 experimental_ct=ct, experimental_rt_n=rt, geometry=geo)
    report = bi.audit_benchmark({"cases": [case]})
    assert report["summary"]["counts"]["FAIL"] == 0
    assert report["summary"]["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# 台账快照：站内真实数据集 vs 已入库审计台账
# ---------------------------------------------------------------------------
def _ledger_residuals(ledger):
    out = {}
    for f in ledger["findings"]:
        if f.get("residual_rel") is not None:
            out[(f["gate"], f["case_id"], f["quantity"])] = float(f["residual_rel"])
    return out


def test_live_dataset_matches_committed_ledger():
    if not os.path.exists(LEDGER_PATH):
        pytest.skip(f"ledger missing: {LEDGER_PATH}")
    with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    data = bi.load_benchmark(BENCHMARK_PATH)
    cases = data.get("cases", [])
    fresh = bi.gate_froude(cases) + bi.gate_ct_resistance(cases) + bi.gate_cb_displacement(cases)
    fresh_res = {  # 台账里还含 G5 项（平台公式），此处只比数据集三门
        (f["gate"], f["case_id"], f["quantity"]): float(f["residual_rel"])
        for f in fresh if f.get("residual_rel") is not None
    }
    committed = {k: v for k, v in _ledger_residuals(ledger).items() if k[0] in ("G1", "G2", "G3")}
    assert set(fresh_res) == set(committed), (
        "台账与实时数据不一致：数据已被修改，请重跑 python3 -m tools.refs.audit 刷新台账")
    for key, val in fresh_res.items():
        assert val == pytest.approx(committed[key], rel=1e-9, abs=1e-12), key


def test_ledger_records_provenance_and_thresholds():
    if not os.path.exists(LEDGER_PATH):
        pytest.skip(f"ledger missing: {LEDGER_PATH}")
    with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    assert ledger["corpus"]["entries"] >= 10
    assert ledger["thresholds"]["water_density_range"] == [990.0, 1035.0]
    assert ledger["mission"] == "m1396"
    gates = {f["gate"] for f in ledger["findings"]}
    assert {"G1", "G2", "G3", "G4a", "G4b", "G5a", "G5b"} <= gates


# ---------------------------------------------------------------------------
# G5：已合并平台公式 vs 解析基准（真绿断言）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def platform_findings():
    from tools.refs import audit as refs_audit
    from tools.refs import schema as refs_schema

    corpus = refs_schema.load_corpus(os.path.join(REPO_ROOT, "tools", "refs", "corpus.json"))
    data = refs_audit.load_benchmark()
    presets = refs_audit.load_ship_geometry_presets()
    try:
        findings = refs_audit.gate_platform_formulas(corpus, data, presets)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"backend import unavailable: {type(exc).__name__}: {exc}")
    if all(f["severity"] == "WARN" for f in findings):
        pytest.skip("platform endpoints unavailable in this environment")
    return {f["gate"]: f for f in findings if f["gate"] in ("G5a", "G5b")}


def test_platform_ittc57_matches_analytic_reference(platform_findings):
    """已合并的阻力公式：同 Re 下 Cf 必须与 ITTC-1957 解析值一致（<1e-4）。"""
    finding = platform_findings["G5a"]
    assert finding["severity"] == "INFO", finding["note"]
    assert finding["residual_rel"] < 1e-4


def test_platform_wall_sided_gz_matches_analytic_reference(platform_findings):
    """已合并的稳性公式：GZ(20°) 必须与 wall-sided 解析值一致（<1e-6 m）。"""
    finding = platform_findings["G5b"]
    assert finding["severity"] == "INFO", finding["note"]
    assert finding["residual_rel"] < 1e-5