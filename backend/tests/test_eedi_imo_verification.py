"""EEDI verification against IMO MEPC.308(73) regulation.

This test module verifies the platform's attained EEDI calculation against
the IMO Marine Environment Protection Committee resolution MEPC.308(73).

Reference:
    IMO MEPC.308(73), "2018 Guidelines on the Method of Calculation of the
    Attained Energy Efficiency Design Index (EEDI) for New Ships"

The EEDI formula (Regulation 2, paragraph 2.2):

    EEDI = [(Π f_j)(Σ P_ME × C_F_ME × SFC_ME) + (P_AE × C_F_AE × SFC_AE)
           - (Σ f_eff × P_eff × C_F_eff × SFC_eff)]
           / [f_i × f_c × f_l × Capacity × f_w × V_ref]

Units:
    - Numerator: g CO2 / h
    - Denominator: tonne × nm / h
    - Result: g CO2 / (tonne × nm)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from services.propulsion import attained_eedi  # noqa: E402


class TestEediImoMepc30873:
    """Verification against IMO MEPC.308(73) worked examples."""

    def test_case1_bulk_carrier_basic(self):
        """Bulk carrier 50,000 DWT, conventional configuration.

        Hand-computed reference:
            ME term = 15,000 × 170 × 3.114 = 7,940,700 g/h
            AE term = 1,000 × 190 × 3.114 = 591,660 g/h
            Numerator = 8,532,360 g/h
            Denominator = 50,000 × 14.0 = 700,000 t·nm/h
            EEDI = 12.189086 g/t·nm
        """
        r = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=15_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
            p_ae_kw=1_000.0,
            sfc_ae_g_kwh=190.0,
            cf_ae=3.114,
        )
        # Hand-computed reference
        me_ref = 15_000.0 * 170.0 * 3.114
        ae_ref = 1_000.0 * 190.0 * 3.114
        num_ref = me_ref + ae_ref
        den_ref = 50_000.0 * 14.0
        eedi_ref = num_ref / den_ref

        assert r.main_engine_term_g_per_h == pytest.approx(me_ref, rel=1e-12)
        assert r.auxiliary_engine_term_g_per_h == pytest.approx(ae_ref, rel=1e-12)
        assert r.numerator_g_per_h == pytest.approx(num_ref, rel=1e-12)
        assert r.denominator_t_nm_per_h == pytest.approx(den_ref, rel=1e-12)
        assert r.eedi_g_co2_per_t_nm == pytest.approx(eedi_ref, rel=1e-10)

    def test_case2_container_innovative_tech(self):
        """Container ship 100,000 DWT with waste heat recovery.

        Hand-computed reference:
            ME term = 40,000 × 165 × 3.206 = 21,159,600 g/h
            AE term = 3,000 × 180 × 3.206 = 1,731,240 g/h
            Innovative credit = 2,000 × 165 × 3.206 = 1,057,980 g/h
            Numerator = 21,832,860 g/h
            Denominator = 100,000 × 22.0 = 2,200,000 t·nm/h
            EEDI = 9.924027 g/t·nm
        """
        r = attained_eedi(
            capacity_t=100_000.0,
            v_ref_kn=22.0,
            p_me_kw=40_000.0,
            sfc_me_g_kwh=165.0,
            cf_me=3.206,
            p_ae_kw=3_000.0,
            sfc_ae_g_kwh=180.0,
            cf_ae=3.206,
            p_eff_kw=2_000.0,
            sfc_eff_g_kwh=165.0,
            cf_eff=3.206,
        )
        me_ref = 40_000.0 * 165.0 * 3.206
        ae_ref = 3_000.0 * 180.0 * 3.206
        eff_ref = 2_000.0 * 165.0 * 3.206
        num_ref = me_ref + ae_ref - eff_ref
        den_ref = 100_000.0 * 22.0
        eedi_ref = num_ref / den_ref

        assert r.main_engine_term_g_per_h == pytest.approx(me_ref, rel=1e-12)
        assert r.auxiliary_engine_term_g_per_h == pytest.approx(ae_ref, rel=1e-12)
        assert r.innovative_term_g_per_h == pytest.approx(eff_ref, rel=1e-12)
        assert r.numerator_g_per_h == pytest.approx(num_ref, rel=1e-12)
        assert r.eedi_g_co2_per_t_nm == pytest.approx(eedi_ref, rel=1e-10)

    def test_case3_correction_factors(self):
        """Ro-Ro passenger ship with capacity/load correction factors.

        Hand-computed reference:
            Numerator = 8,000 × 175 × 3.114 = 4,359,600 g/h
            Denominator = 1.0 × 1.05 × 1.02 × 20,000 × 1.0 × 18.0 = 385,560 t·nm/h
            EEDI = 11.307190 g/t·nm
        """
        r = attained_eedi(
            capacity_t=20_000.0,
            v_ref_kn=18.0,
            p_me_kw=8_000.0,
            sfc_me_g_kwh=175.0,
            cf_me=3.114,
            f_i=1.0,
            f_c=1.05,
            f_l=1.02,
            f_w=1.0,
        )
        num_ref = 8_000.0 * 175.0 * 3.114
        den_ref = 1.0 * 1.05 * 1.02 * 20_000.0 * 1.0 * 18.0
        eedi_ref = num_ref / den_ref

        assert r.numerator_g_per_h == pytest.approx(num_ref, rel=1e-12)
        assert r.denominator_t_nm_per_h == pytest.approx(den_ref, rel=1e-12)
        assert r.eedi_g_co2_per_t_nm == pytest.approx(eedi_ref, rel=1e-10)

    def test_case4_weather_factor_defaults_to_ice_factor(self):
        """Ice-classed vessel: f_w defaults to f_i per IMO guidance.

        When f_w is not separately specified, it should default to f_i
        (the ice class factor), per IMO 2018 Guidelines on Survey and
        Certification of the Attained EEDI.
        """
        r_explicit = attained_eedi(
            capacity_t=30_000.0,
            v_ref_kn=16.0,
            p_me_kw=12_000.0,
            sfc_me_g_kwh=168.0,
            cf_me=3.150,
            f_i=1.02,
            f_w=1.02,
        )
        r_default = attained_eedi(
            capacity_t=30_000.0,
            v_ref_kn=16.0,
            p_me_kw=12_000.0,
            sfc_me_g_kwh=168.0,
            cf_me=3.150,
            f_i=1.02,
            f_w=None,  # Should default to f_i
        )
        assert r_explicit.eedi_g_co2_per_t_nm == pytest.approx(
            r_default.eedi_g_co2_per_t_nm, rel=1e-12
        )

    def test_unit_chain_numerator_grams_per_hour(self):
        """Verify numerator is in g/h: kW × g/kWh × dimensionless = g/h."""
        r = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=10_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
        )
        # 10,000 kW × 170 g/kWh × 3.114 = 5,303,800 g/h
        expected = 10_000.0 * 170.0 * 3.114
        assert r.numerator_g_per_h == pytest.approx(expected, rel=1e-12)

    def test_unit_chain_denominator_tonne_nm_per_hour(self):
        """Verify denominator is in t·nm/h: t × kn = t·nm/h (1 kn = 1 nm/h)."""
        r = attained_eedi(
            capacity_t=100_000.0,
            v_ref_kn=12.0,
            p_me_kw=5_000.0,
            sfc_me_g_kwh=160.0,
            cf_me=3.0,
        )
        # 100,000 t × 12.0 kn = 1,200,000 t·nm/h
        expected = 100_000.0 * 12.0
        assert r.denominator_t_nm_per_h == pytest.approx(expected, rel=1e-12)

    def test_correction_factors_multiply_denominator(self):
        """f_c and f_l enter the denominator multiplicatively."""
        base = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=10_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
        )
        corrected = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=10_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
            f_c=1.05,
            f_l=1.02,
        )
        # EEDI_base / EEDI_corrected = f_c × f_l
        ratio = base.eedi_g_co2_per_t_nm / corrected.eedi_g_co2_per_t_nm
        assert ratio == pytest.approx(1.05 * 1.02, rel=1e-12)

    def test_innovative_technology_reduces_eedi(self):
        """Innovative technology credit (f_eff × P_eff) reduces EEDI."""
        base = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=10_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
        )
        with_innovation = attained_eedi(
            capacity_t=50_000.0,
            v_ref_kn=14.0,
            p_me_kw=10_000.0,
            sfc_me_g_kwh=170.0,
            cf_me=3.114,
            p_eff_kw=500.0,
            sfc_eff_g_kwh=170.0,
            cf_eff=3.114,
        )
        assert with_innovation.eedi_g_co2_per_t_nm < base.eedi_g_co2_per_t_nm
