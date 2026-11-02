"""随浪 ω_e ≤ 0 无效频带的判据回归（又在场速/艏向包线内的能量与 σ 完整性）。

判据全部为**闭式可判**（无文献表值、无记忆转录）：
  L1  剪断频率闭式 ω_c = g/(U cosβ) 与代码首个无效频点一致（网格台阶内）；
  L2  ITTC/ISSC 谱尾部能量占比闭式 1 − exp(−B ω_c^-4) 与数值掩码占比一致；
  L3  判据能判假：把 ω_c 故意扰动 20% 后 L2 必须超差（否则判据是恒绿否决器）；
  L4  向后兼容：mask_invalid=False 与历史实现（本文件内独立重算）逐位一致；
  L5  旗标语义：随浪置真、顶浪/横浪置假，且 masked ≤ raw。

运行：pytest backend/tests/feat/test_seakeeping_following_sea.py -q
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

_MOD = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "services", "seakeeping.py"))
_spec = importlib.util.spec_from_file_location("seakeeping_svc", _MOD)
sk = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("seakeeping_svc", sk)
_spec.loader.exec_module(sk)

G = sk.G
KN = 1852.0 / 3600.0
HULL = dict(L=230.0, B=32.2, T=10.8, Cb=0.651, Cwp=0.80,
            displacement_t=51900.0, KG=9.0, GM_T=1.2)
MODEL = sk.SeakeepingModel(**HULL)
HS, T1, GAMMA = 3.0, 8.0, 3.3
OMEGA = [0.10 + i * (2.60 - 0.10) / 5000 for i in range(5001)]
B_ITTC = 691.0 / T1 ** 4


def _tail_fraction_closed(omega_c: float) -> float:
    return 1.0 - math.exp(-B_ITTC * omega_c ** -4.0)


def _stats(speed_kn: float, heading: float, mask: bool):
    sp = sk.spectrum("ittc", HS, T1, OMEGA, gamma=GAMMA)
    return sk.response_statistics(MODEL, sp, heading_deg=heading,
                                  speed=speed_kn * KN, mask_invalid=mask)


# ---------------------------------------------------------------- L1 剪断频率
@pytest.mark.parametrize("speed_kn,heading", [(20.0, 0.0), (15.0, 30.0), (12.0, 45.0)])
def test_invalid_band_onset_matches_closed_form_cutoff(speed_kn, heading):
    """ω_e ≤ 0 ⟺ ω ≥ g/(U cosβ)（深水 k = ω²/g），误差须小于网格台阶。"""
    st = _stats(speed_kn, heading, mask=True)
    omega_c = G / (speed_kn * KN * math.cos(math.radians(heading)))
    assert st["encounter_invalid"] is True
    step = OMEGA[1] - OMEGA[0]
    assert abs(st["omega_e_first_invalid_rad_s"] - omega_c) <= step * 1.001


# ---------------------------------------------------------------- L2 尾部能量
@pytest.mark.parametrize("speed_kn,heading", [(20.0, 0.0), (18.0, 0.0), (15.0, 30.0)])
def test_invalid_energy_fraction_matches_closed_form(speed_kn, heading):
    """ITTC 谱：尾部占比 = 1 − exp(−B ω_c^-4)，数值掩码与之相差 < 0.01。"""
    st = _stats(speed_kn, heading, mask=False)
    omega_c = G / (speed_kn * KN * math.cos(math.radians(heading)))
    assert st["encounter_invalid_energy_fraction"] == pytest.approx(
        _tail_fraction_closed(omega_c), abs=0.01
    )


# ---------------------------------------------------------------- L3 判据能判假
def test_criterion_rejects_perturbed_cutoff():
    """把 ω_c 扰动 ±20%，闭式占比必须显著偏离平台数值（判据非恒真）。"""
    st = _stats(20.0, 0.0, mask=False)
    numeric = st["encounter_invalid_energy_fraction"]
    omega_c = G / (20.0 * KN)
    for factor in (0.8, 1.2):
        perturbed = _tail_fraction_closed(omega_c * factor)
        assert abs(perturbed - numeric) > 0.01


# ---------------------------------------------------------------- L4 向后兼容
def test_legacy_numbers_reproduced_when_mask_off():
    """mask_invalid=False 必须与历史实现（本文件内独立重算）一致，掩码只改无效带。"""
    st = _stats(20.0, 0.0, mask=False)
    sp = sk.spectrum("ittc", HS, T1, OMEGA, gamma=GAMMA)
    curve = MODEL.rao_curve(OMEGA, heading_deg=0.0, speed=20.0 * KN)
    dens = [r * r * s for r, s in zip(curve["heave_rao_m_per_m"], sp["S"])]
    m0 = sk._integrate(dens, OMEGA)
    assert st["sigma_heave"] == pytest.approx(math.sqrt(m0), rel=1e-12)
    assert st["mask_invalid_band"] is False


# ---------------------------------------------------------------- L5 旗标与掩码
def test_flags_and_masking_semantics():
    head = _stats(20.0, 180.0, mask=True)
    assert head["encounter_invalid"] is False
    assert head["encounter_invalid_energy_fraction"] == 0.0
    assert head["sigma_heave"] == pytest.approx(_stats(20.0, 180.0, False)["sigma_heave"], rel=1e-14)

    follow_raw = _stats(20.0, 0.0, mask=False)
    follow_msk = _stats(20.0, 0.0, mask=True)
    assert follow_msk["mask_invalid_band"] is True
    assert 0.0 < follow_msk["encounter_invalid_energy_fraction"] < 1.0
    assert follow_msk["sigma_heave"] < follow_raw["sigma_heave"]
    # 无效带只影响响应积分，入射 m0 不变
    assert follow_msk["m0_incident_m2"] == pytest.approx(follow_raw["m0_incident_m2"], rel=1e-14)