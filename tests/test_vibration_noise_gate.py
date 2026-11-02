"""振动噪声域（vibration/noise）验证欠账的机器裁决（m1871）。

本套件补上船舶 9 学科中唯一零覆盖的振动噪声域判据。三件事：

1. **参考实现自检**：``tools/ref_vibration_8001.py`` 的船体梁两节点垂向固有频率
   闭式精确解（free-free 欧拉-伯努利梁 λ1=4.7300408）必须与 200 单元一致质量
   梁有限元独立解吻合到 <1%。这条判据把一个「参考值」变成了可裁的数值事实。
2. **工件↔判据一致性**：``artifacts/vibration_gate.json`` 里存的平台响应，必须能
   重新推出其中记录的 verdict（人工翻案即 FAIL）。
3. **在线复核**：平台可达时再打一次端点，确认 MOCK 指纹仍在；不可达则 skip，
   绝不把不可达记成 PASS。

运行：``pytest tests/test_vibration_noise_gate.py -q``
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ref_vibration_8001 as rv  # noqa: E402

GATE = os.path.join(
    ROOT, "docs", "verification_debt", "vibration_noise", "artifacts", "vibration_gate.json"
)


@pytest.fixture(scope="module")
def gate():
    if not os.path.exists(GATE):
        pytest.skip(f"工件缺失（先跑 tools/ref_vibration_8001.py）：{GATE}")
    with open(GATE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 参考实现自检
def test_reference_ships_present(gate):
    ref = gate["reference"]
    assert len(ref) == 4
    names = {r["class"] for r in ref}
    assert names == {"Feeder-1000TEU", "Panamax-4500TEU",
                     "PostPanamax-10000TEU", "ULCS-20000TEU"}


@pytest.mark.parametrize("idx", [0, 1, 2, 3])
def test_analytic_matches_fem_under_1pct(gate, idx):
    """闭式精确解 vs 200 单元梁 FEM 独立复核，相对偏差须 <1%。"""
    r = gate["reference"][idx]
    rel = abs(r["f2v_fem_Hz"] - r["f2v_exact_Hz"]) / r["f2v_exact_Hz"]
    assert rel < 0.01, f"{r['class']}: 解析 {r['f2v_exact_Hz']} vs FEM {r['f2v_fem_Hz']} 偏差 {rel:.3e}"


def test_reference_recomputed_matches_artifact(gate):
    """工件里的参考值必须能被工具重算复现（零手抄）。"""
    for r in gate["reference"]:
        ship = dict(
            class_=r["class"], L=r["L"], B=r["B"], D=r["D"], Cb=r["Cb"],
            displacement_t=r["displacement_t"], rpm=r["blade_rate_rpm"], blades=r["blades"],
        )
        s2 = dict(ship)
        s2["class"] = s2.pop("class_")
        recomputed = rv.hull_girder_2node(s2)
        assert recomputed["f2v_exact_Hz"] == pytest.approx(r["f2v_exact_Hz"], rel=1e-6)
        assert recomputed["blade_rate_Hz"] == pytest.approx(r["blade_rate_Hz"], rel=1e-9)


def test_reference_is_size_dependent(gate):
    """参考量必须随船级显著变化（否则参考实现本身失去判别力）。"""
    f2v = [r["f2v_exact_Hz"] for r in gate["reference"]]
    rel_range = (max(f2v) - min(f2v)) / max(f2v)
    assert rel_range > 0.30, f"f2v 跨船级相对变化仅 {rel_range:.3f}，参考实现判别力不足"


def test_blade_rate_never_coincides_with_two_node(gate):
    """物理合理性的旁证：叶频远高于两节点频率，不构成共振风险。"""
    for r in gate["reference"]:
        assert r["blade_rate_Hz"] > 3.0 * r["f2v_exact_Hz"]


# ------------------------------------------------------- 工件 → verdict 重推
def test_verdict_contract_rederived(gate):
    live = gate["live"]
    if not live or not live["reachable"]:
        pytest.skip("平台不可达：WITHHELD，不记 PASS")
    v = live["verdicts"]["VIB-001_contract"]
    legal_ok = all(x["pass"] for x in v["legal"])
    assert v["pass"] == (legal_ok and v["missing_param"]["pass"])


def test_verdict_mock_fingerprint_rederived(gate):
    """从存下来的响应重算「与输入无关」结论，翻转即 FAIL。"""
    live = gate["live"]
    if not live or not live["reachable"]:
        pytest.skip("平台不可达：WITHHELD，不记 PASS")
    bodies = [r["body"] for r in live["responses"]]
    numeric = [k for k, val in bodies[0].items() if isinstance(val, (int, float))
               and not isinstance(val, bool)]
    assert numeric, "响应中无数值字段"
    all_const = True
    for k in numeric:
        vals = [float(b[k]) for b in bodies]
        denom = max(abs(min(vals)), abs(max(vals)), 1e-12)
        if (max(vals) - min(vals)) / denom >= rv.INPUT_INDEPENDENCE_TOL:
            all_const = False
    stored = live["verdicts"]["VIB-002_input_dependence"]
    assert stored["all_fields_constant"] == all_const
    # 当前实测应为常数（MOCK）；若已修复为真实实现则此处应改为断言 pass
    assert stored["pass"] == (not all_const)


def test_evidence_level_consistent_with_verdicts(gate):
    live = gate["live"]
    if not live or not live["reachable"]:
        assert live is None or live["evidence_level"] == "WITHHELD"
        return
    v = live["verdicts"]
    if v["VIB-002_input_dependence"]["all_fields_constant"]:
        assert live["evidence_level"] == "MOCK"
    else:
        assert live["evidence_level"] in {"REAL", "PARTIAL", "STUB"}


def test_specification_verdict_is_withheld_not_pass(gate):
    """未声明单位/统计量/频带/测点 → 精度判决必须 WITHHELD，不得记 PASS。"""
    live = gate["live"]
    if not live or not live["reachable"]:
        pytest.skip("平台不可达")
    v = live["verdicts"]["VIB-004_specification"]
    has_spec = bool(v["spec_keys_present"])
    assert v["pass"] == has_spec


# --------------------------------------------------------------- 在线复核
def _probe_once():
    try:
        return rv.probe(rv.build_reference())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"平台不可达（跳过，绝不计 PASS）：{e!r}")


def test_live_endpoint_still_returns_200():
    live = _probe_once()
    assert live["reachable"] is True
    for r in live["responses"]:
        assert r["http"] == 200


def test_live_endpoint_still_input_invariant():
    live = _probe_once()
    assert live["verdicts"]["VIB-002_input_dependence"]["all_fields_constant"] is True, (
        "平台振动端点似已变为输入相关的真实实现——请复核后把本用例与工件证据级同步更新"
    )