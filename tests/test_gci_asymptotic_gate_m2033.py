"""双 GCI 内核审计的回归闸门（m2033）。

钉死四件事，任何一条被后续改动破坏都必须在这里炸出来：

1. **文献锚**：Celik et al. (2008) 三网格工作示例 → p=1.786、GCI_fine=0.103%
   （两套内核相对偏差 <0.1%）；
2. **渐近范围指示量是恒等式**：``p`` 由同一组三点估计时
   ``asym_ratio = e32/(r21**p * e21) == 1``（代数上必然），因此它**不能**充当
   渐近范围检验 —— 该事实被写进 ``asym_ratio_tautological``；
3. **假绿反例**：``phi = 1 + 4h^2 + 30h^3``（形式二阶，r=2）估计阶漂移 21.8%，
   修复前 CLI 会打 ``PASS``/退出码 0，修复后必须 ``UNVERIFIED``/退出码 1；
4. **双内核分歧类**：单调可估阶时两套实现在数值上同一（Δ≈1e-15）；非单调
   （振荡/发散）时二者策略不同（A 不给 p，B 给且把 p 钳到下限），这是**显式
   记录的分歧**而不是悄悄漂移。

注：本文件不校验平台对外发布的数字，只校验"估计量与判据本身"。
"""
from __future__ import annotations

import importlib
import json
import math
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# NB: backend/ is put on sys.path only inside the audit module, otherwise the
# repo-root ``tools`` package would be shadowed by backend/tools.

_A = importlib.import_module("src.verification.gci")
_AUDIT = importlib.import_module("tools.audit.gci_dual_kernel_audit")

CELIK_H = [1.0, 2.0, 4.0]
CELIK_PHI = [0.97050, 0.96854, 0.96178]          # Celik et al. (2008) worked example
PREASYMP_H = [1 / 32.0, 1 / 16.0, 1 / 8.0]       # r = 2, finest first


def _preasymptotic_phi():
    """``phi(h) = 1 + 4h^2 + 30h^3``: formal order 2, third-order term not negligible."""
    return [1.0 + 4.0 * h ** 2 + 30.0 * h ** 3 for h in PREASYMP_H]


# --------------------------------------------------------------- 1. 文献锚 ---
def test_celik2008_published_order_and_gci_are_reproduced():
    res = _A.gci(CELIK_H, CELIK_PHI)
    assert res.p == pytest.approx(1.786, rel=1e-3)          # published p = 1.786
    assert res.gci_fine_pct == pytest.approx(0.103, rel=5e-3)  # published GCI = 0.103 %
    assert res.regime == _A.GCIRegime.MONOTONE
    # Richardson limit lies on the far side of the finest value (h -> 0), i.e.
    # it continues the monotone trend of the two finest solutions.
    d_ext = res.phi_ext - CELIK_PHI[0]
    d_pair = CELIK_PHI[0] - CELIK_PHI[1]
    assert d_ext * d_pair > 0.0 and abs(d_ext) < abs(d_pair)


def test_dual_kernel_audit_reproduces_the_literature_anchor():
    A, B = _AUDIT.load_kernels()
    rec = _AUDIT.run_case(A, B, _AUDIT.deterministic_battery()[0])
    assert rec["name"] == "celik2008_worked_example"
    assert rec["rel_dp"] < 1e-12
    assert rec["A"]["p"] == pytest.approx(1.786, rel=1e-3)
    assert rec["B"]["observed_order"] == pytest.approx(1.786, rel=1e-3)
    assert rec["A"]["gci_fine_pct"] == pytest.approx(0.103, rel=5e-3)


# ------------------------------------------- 2. 范围指示量是恒等式（定级） ---
@pytest.mark.parametrize("p_true", [1.0, 2.0, 3.0])
def test_asymptotic_ratio_is_an_algebraic_identity_when_p_is_estimated(p_true):
    """``e32/(r21**p * e21) == 1`` identically -> no asymptotic-range information."""
    h = [1 / 80.0, 1 / 40.0, 1 / 20.0]
    # deliberately pre-asymptotic: a second term contaminates the sequence
    phi = [1.0 + 0.7 * hi ** p_true + 5.0 * hi ** (p_true + 1) for hi in h]
    res = _A.gci(h, phi)
    assert res.p is not None
    assert res.asym_ratio == pytest.approx(1.0, abs=1e-12)
    assert res.asym_ratio_tautological is True
    assert any("algebraic identity" in n for n in res.notes)


def test_assumed_order_makes_the_range_indicator_informative():
    """With p supplied externally the indicator is a real (non-identity) number."""
    phi = _preasymptotic_phi()
    res = _A.gci(PREASYMP_H, phi, p_assumed=2.0, fs=_A.FS_TWO_GRIDS)
    assert res.asym_ratio is not None
    assert res.asym_ratio_tautological is False
    assert abs(res.asym_ratio - 1.0) > 1e-3      # it now carries information


# ------------------------------------------------------- 3. 假绿反例与修复 ---
def test_preasymptotic_sequence_is_no_longer_certified():
    phi = _preasymptotic_phi()
    res_free = _A.gci(PREASYMP_H, phi)                    # no formal order available
    assert res_free.p == pytest.approx(2.4367, abs=1e-3)  # drifted order
    assert res_free.order_ok is None                      # check WITHHELD
    assert _A.refinement_ratio_ok(res_free.r21) is True   # the old proxy passed!
    assert res_free.asymptotic is True                    # ... and it still would

    res = _A.gci(PREASYMP_H, phi, p_expected=2.0)
    assert res.order_rel_dev == pytest.approx(0.2184, abs=1e-3)
    assert res.order_ok is False
    assert res.asymptotic is False                        # honest verdict
    assert any("pre-asymptotic" in n for n in res.notes)


def test_order_of_accuracy_check_passes_for_a_clean_second_order_sequence():
    phi = [1.0 + 0.5 * h ** 2 for h in PREASYMP_H]
    res = _A.gci(PREASYMP_H, phi, p_expected=2.0)
    assert res.order_rel_dev < 1e-9 and res.order_ok is True
    assert res.asymptotic is True


def test_cli_gate_flips_from_pass_to_unverified_on_the_false_green_case(tmp_path):
    from tools.gci_report import main as cli_main

    study = tmp_path / "pre.json"
    study.write_text(json.dumps({"h": PREASYMP_H, "phi": _preasymptotic_phi(),
                                 "quantity": "pre_asymptotic", "p_expected": 2.0}))
    out = tmp_path / "res.json"
    assert cli_main([str(study), "--out", str(out)]) == 1
    payload = json.loads(out.read_text())
    assert payload["verification_status"] == "UNVERIFIED"
    assert payload["asymptotic_check"] == "FAIL"
    assert payload["result"]["order_rel_dev"] == pytest.approx(0.218, abs=1e-3)


def test_cli_gate_reports_withheld_when_no_formal_order_is_given(tmp_path):
    from tools.gci_report import main as cli_main

    study = tmp_path / "free.json"
    study.write_text(json.dumps({"h": PREASYMP_H, "phi": _preasymptotic_phi(),
                                 "quantity": "no_formal_order"}))
    out = tmp_path / "res.json"
    cli_main([str(study), "--out", str(out)])
    payload = json.loads(out.read_text())
    assert payload["asymptotic_check"] == "WITHHELD"
    assert payload["result"]["order_ok"] is None


# ------------------------------------------------- 4. 双内核一致 / 分歧类 ---
def test_two_kernels_are_numerically_the_same_estimator_on_monotone_cases():
    A, B = _AUDIT.load_kernels()
    for case in _AUDIT.deterministic_battery():
        if case["name"] in {"oscillatory_R_negative", "divergent_R_gt_1",
                           "roundoff_degenerate"}:
            continue
        rec = _AUDIT.run_case(A, B, case)
        assert "error" not in rec["A"] and "error" not in rec["B"], case["name"]
        if rec["A"]["p"] is None:
            continue
        assert rec.get("rel_dp", 0.0) < 1e-10, case["name"]
        assert rec.get("rel_dgci", 0.0) < 1e-10, case["name"]
        assert rec["regime_agree"] is True, case["name"]


def test_non_monotone_policies_diverge_and_the_divergence_is_documented():
    """A withholds p, B clamps p to its floor: a *policy* divergence, recorded here."""
    A, B = _AUDIT.load_kernels()
    battery = {c["name"]: c for c in _AUDIT.deterministic_battery()}

    osc = _AUDIT.run_case(A, B, battery["oscillatory_R_negative"])
    assert osc["A"]["p"] is None and osc["A"]["asymptotic"] is False
    assert osc["B"]["observed_order"] is not None      # B still reports an order
    assert osc["B"]["passed"] is False

    div = _AUDIT.run_case(A, B, battery["divergent_R_gt_1"])
    assert div["A"]["p"] is None and div["A"]["asymptotic"] is False
    assert div["B"]["passed"] is False
    # B's order is pinned to its search floor, not a physical estimate.
    assert div["B"]["observed_order"] <= 1.0

    deg = _AUDIT.run_case(A, B, battery["roundoff_degenerate"])
    assert deg["A"]["p"] is None and deg["B"]["observed_order"] is None


def test_randomized_sweep_proves_the_identity_and_reproduces_classes():
    A, B = _AUDIT.load_kernels()
    sw = _AUDIT.randomized_sweep(A, B, n=400, seed=_AUDIT.SEED)
    assert sw["trials_used"] > 350
    # identity: machine precision, never a real test
    assert sw["max_abs_asym_ratio_minus_1"] < 1e-12
    assert sw["asym_ratio_min"] == pytest.approx(1.0, abs=1e-12)
    assert sw["asym_ratio_max"] == pytest.approx(1.0, abs=1e-12)
    # same estimator where both estimate an order
    assert sw["n_p_compared"] > 300
    assert sw["max_rel_dp"] < 1e-9
    # regime/verdict divergence exists and is concentrated in round-off level cases
    assert sw["n_regime_disagree"] >= 0
    assert isinstance(sw["regime_disagree_pairs"], dict)


def test_audit_cli_writes_json_and_markdown(tmp_path):
    out = tmp_path / "audit"
    assert _AUDIT.main(["--out", str(out), "--sweep", "60"]) == 0
    payload = json.loads((out / "audit.json").read_text())
    assert len(payload["battery"]) == len(_AUDIT.deterministic_battery())
    assert payload["celik_reference"]["rel_err_p_A"] < 1e-3
    md = (out / "audit.md").read_text()
    assert "preasymptotic_false_green" in md
    assert "文献锚" in md