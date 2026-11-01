"""m1693 verification debt: EEDI / EEXI (green shipping) closed-form anchors.

Why this file exists
--------------------
Two independent attained-EEDI implementations ship in this tree:

* ``services.propulsion.attained_eedi``      -- full MEPC.308(73) form with the
  correction factors ``f_i`` (capacity), ``f_c`` (cubic), ``f_l`` (load) and
  ``f_w`` (weather / speed decrease);
* ``services.polar.cii.attained_eedi``       -- simplified single-fuel form with
  all correction factors documented as 1.

Before m1693 the first one multiplied ``f_i`` into the *numerator* while its own
docstring said ``/ (f_i f_c f_l Capacity f_w V_ref)``, and additionally aliased
the weather factor to ``f_i`` (``f_w = f_i``).  No test covered ``f_i`` at all,
so both defects were invisible.  The two bugs interacted: with the default
``f_w`` the ``f_i`` factors cancelled, so a naive "does f_i lower the EEDI?"
check still passed -- only an explicit ``f_w`` or a non-zero innovative-power
term exposed them.

Reference for every assertion here is the guideline formula written out by hand
in the test body (closed form, no library call), i.e. an *analytic* anchor.
Absolute EEDI levels of real ships cannot be validated offline (no fleet / trial
dataset in this repository) -> that part is reported WITHHELD, not PASS.
"""

from __future__ import annotations

import math

import pytest

from services.polar import cii as cii_mod
from services.propulsion import attained_eedi

#: C_F for HFO per MEPC.364(79) (the value used by services.polar.cii).
CF_HFO = 3.114

#: A representative Handysize/Handymax bulker case used through the file.
BASE = dict(
    capacity_t=80_000.0,
    v_ref_kn=14.5,
    p_me_kw=9_000.0,
    sfc_me_g_kwh=170.0,
    cf_me=CF_HFO,
)


def _closed_form(capacity, v_ref, p_me, sfc_me, cf_me, p_ae=0.0, sfc_ae=0.0,
                 p_eff=0.0, sfc_eff=0.0, f_eff=1.0, f_i=1.0, f_c=1.0,
                 f_l=1.0, f_w=1.0):
    """MEPC.308(73) attained EEDI, transcribed by hand (prod f_j = 1)."""
    numerator_g_per_h = (p_me * sfc_me * cf_me) + (p_ae * sfc_ae * cf_me) \
        - (f_eff * p_eff * sfc_eff * cf_me)
    denominator_t_nm_per_h = f_i * f_c * f_l * capacity * f_w * v_ref
    return numerator_g_per_h / denominator_t_nm_per_h


# ---------------------------------------------------------------------------
# 1. formula-level agreement (the reference is the hand-written expression)
# ---------------------------------------------------------------------------

class TestAttainedEediClosedForm:

    def test_full_factor_case_matches_hand_formula(self):
        got = attained_eedi(
            **BASE, p_ae_kw=600.0, sfc_ae_g_kwh=190.0,
            p_eff_kw=400.0, sfc_eff_g_kwh=170.0, f_eff=0.95,
            f_i=1.3, f_c=1.05, f_l=1.02, f_w=1.08,
        )
        want = _closed_form(
            BASE["capacity_t"], BASE["v_ref_kn"], BASE["p_me_kw"],
            BASE["sfc_me_g_kwh"], BASE["cf_me"],
            p_ae=600.0, sfc_ae=190.0, p_eff=400.0, sfc_eff=170.0,
            f_eff=0.95, f_i=1.3, f_c=1.05, f_l=1.02, f_w=1.08,
        )
        assert got.eedi_g_co2_per_t_nm == pytest.approx(want, rel=1e-12)

    def test_f_i_enters_denominator_only(self):
        """f_i = 1.3 (ice-class capacity) must scale EEDI by exactly 1/1.3."""
        plain = attained_eedi(**BASE, f_w=1.0)
        ice = attained_eedi(**BASE, f_i=1.3, f_w=1.0)
        assert plain.eedi_g_co2_per_t_nm / ice.eedi_g_co2_per_t_nm == pytest.approx(
            1.3, rel=1e-12
        )

    def test_f_w_defaults_to_unity_not_to_f_i(self):
        explicit = attained_eedi(**BASE, f_i=1.3, f_w=1.0)
        default = attained_eedi(**BASE, f_i=1.3)
        assert default.eedi_g_co2_per_t_nm == pytest.approx(
            explicit.eedi_g_co2_per_t_nm, rel=1e-15
        )
        assert default.eedi_g_co2_per_t_nm == pytest.approx(
            _closed_form(BASE["capacity_t"], BASE["v_ref_kn"], BASE["p_me_kw"],
                         BASE["sfc_me_g_kwh"], BASE["cf_me"], f_i=1.3),
            rel=1e-12,
        )

    def test_innovative_term_divided_by_f_i_exactly_once(self):
        """Regression for the old ``f_i*(me+ae) - eff`` numerator.

        Dividing only the innovative term by f_i (old behaviour) understates the
        innovative credit; the corrected form divides the whole bracket once.
        """
        common = dict(BASE, p_eff_kw=1_500.0, sfc_eff_g_kwh=170.0,
                      p_ae_kw=600.0, sfc_ae_g_kwh=190.0, f_i=1.3, f_w=1.10)
        got = attained_eedi(**common)
        me = BASE["p_me_kw"] * BASE["sfc_me_g_kwh"] * BASE["cf_me"]
        ae = 600.0 * 190.0 * BASE["cf_me"]
        eff = 1_500.0 * 170.0 * BASE["cf_me"]
        denom = 1.3 * BASE["capacity_t"] * 1.10 * BASE["v_ref_kn"]
        assert got.eedi_g_co2_per_t_nm == pytest.approx((me + ae - eff) / denom,
                                                        rel=1e-12)
        old_form = (1.3 * (me + ae) - eff) / (1.3 * denom)   # pre-fix numerator
        assert got.eedi_g_co2_per_t_nm != pytest.approx(old_form, rel=1e-6)

    def test_old_defect_was_material(self):
        """The pre-m1693 answer differed from the guideline value by >3 %.

        Two independent exposures are quantified so the audit can state how bad
        the defect was instead of just "it changed":
        (a) explicit weather factor, no innovative power -> error = f_i (30 %);
        (b) default weather factor with a 20 % innovative-power credit -> 5.8 %.
        """
        cap, v, p_me, sfc, cf = (BASE["capacity_t"], BASE["v_ref_kn"],
                                 BASE["p_me_kw"], BASE["sfc_me_g_kwh"],
                                 BASE["cf_me"])
        # (a) f_i with an explicit f_w: old code left f_i in the numerator.
        f_i, f_w = 1.3, 1.10
        me = p_me * sfc * cf
        correct = me / (f_i * cap * f_w * v)
        old = f_i * me / (f_i * cap * f_w * v)
        assert abs(old / correct - 1.0) * 100.0 == pytest.approx(30.0, rel=1e-12)
        # (b) default f_w (was aliased to f_i) + innovative credit e/X = 20 %.
        e_over_x = 0.20
        correct_b = (1.0 - e_over_x) / (f_i * cap * v)
        old_b = (1.0 - e_over_x / f_i) / (f_i * cap * v)
        err_pct = abs(old_b / correct_b - 1.0) * 100.0
        assert err_pct > 5.0 and err_pct < 6.5

    def test_c_corrections_still_scale_inversely(self):
        base = attained_eedi(**BASE)
        corr = attained_eedi(**BASE, f_c=1.05, f_l=1.02)
        assert base.eedi_g_co2_per_t_nm / corr.eedi_g_co2_per_t_nm == pytest.approx(
            1.05 * 1.02, rel=1e-12
        )

    def test_numerator_and_denominator_terms_are_reported(self):
        r = attained_eedi(**BASE, p_ae_kw=600.0, sfc_ae_g_kwh=190.0,
                          p_eff_kw=400.0, sfc_eff_g_kwh=170.0, f_i=1.4)
        me = BASE["p_me_kw"] * BASE["sfc_me_g_kwh"] * BASE["cf_me"]
        ae = 600.0 * 190.0 * BASE["cf_me"]
        eff = 400.0 * 170.0 * BASE["cf_me"]
        assert r.main_engine_term_g_per_h == pytest.approx(me, rel=1e-14)
        assert r.auxiliary_engine_term_g_per_h == pytest.approx(ae, rel=1e-14)
        assert r.innovative_term_g_per_h == pytest.approx(eff, rel=1e-14)
        # numerator excludes f_i (denominator factor only)
        assert r.numerator_g_per_h == pytest.approx(me + ae - eff, rel=1e-14)
        assert r.denominator_t_nm_per_h == pytest.approx(
            1.4 * BASE["capacity_t"] * BASE["v_ref_kn"], rel=1e-14
        )

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_non_positive_factors_rejected(self, bad):
        with pytest.raises(ValueError):
            attained_eedi(**BASE, f_i=bad)
        with pytest.raises(ValueError):
            attained_eedi(**BASE, f_w=bad)


# ---------------------------------------------------------------------------
# 2. cross-implementation consistency of the two attained-EEDI paths
# ---------------------------------------------------------------------------

class TestCrossImplementation:

    def test_two_paths_agree_when_corrections_are_one(self):
        a = attained_eedi(**BASE, p_ae_kw=600.0, sfc_ae_g_kwh=190.0)
        b = cii_mod.attained_eedi(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"],
            p_ae_kw=600.0, sfoc_ae_g_kwh=190.0,
        )
        assert a.eedi_g_co2_per_t_nm == pytest.approx(b, rel=1e-12)

    def test_hfo_carbon_factor_sources_differ_by_1_3e_4(self):
        """Documented, not a defect: 3.114 (MEPC.364(79)) vs 3.1144 (legacy).

        ``services.propulsion`` takes C_F from the caller, ``services.polar.cii``
        from its own table, so callers must pass the same C_F to compare the two
        values.  The 1.28e-4 spread is exactly the factor ratio and is recorded
        as a not-to-exceed tolerance for cross-checks.
        """
        legacy_cf = 3.1144
        a = attained_eedi(**dict(BASE, cf_me=legacy_cf))
        b = cii_mod.attained_eedi(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"],
        )
        rel = abs(a.eedi_g_co2_per_t_nm / b - 1.0)
        assert rel == pytest.approx(legacy_cf / CF_HFO - 1.0, rel=1e-9)
        assert 1.0e-4 < rel < 1.5e-4


# ---------------------------------------------------------------------------
# 3. EEXI: attained 75 % load factor, required line and compliance verdict
# ---------------------------------------------------------------------------

class TestEexi:

    def test_attained_eexi_applies_75_percent_to_main_engine_only(self):
        eexi = cii_mod.attained_eexi(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"],
            p_ae_kw=600.0, sfoc_ae_g_kwh=190.0,
        )
        want = (0.75 * BASE["p_me_kw"] * BASE["sfc_me_g_kwh"] * CF_HFO
                + 600.0 * 190.0 * CF_HFO) / (BASE["capacity_t"] * BASE["v_ref_kn"])
        assert eexi == pytest.approx(want, rel=1e-12)

    def test_attained_eexi_me_only_is_75_percent_of_eedi(self):
        eedi = cii_mod.attained_eedi(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"],
        )
        eexi = cii_mod.attained_eexi(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"],
        )
        assert eexi / eedi == pytest.approx(0.75, rel=1e-12)

    def test_required_eexi_scales_reference_line(self):
        ref = cii_mod.eedi_reference_value("bulk_carrier", 80_000.0)
        req = cii_mod.required_eexi("bulk_carrier", 80_000.0, 20.0)
        assert req["eexi_reference"] == pytest.approx(ref, rel=1e-15)
        assert req["required_eexi"] == pytest.approx(0.8 * ref, rel=1e-15)

    def test_required_eexi_is_monotone_in_reduction(self):
        vals = [cii_mod.required_eexi("tanker", 150_000.0, r)["required_eexi"]
                for r in (0.0, 10.0, 20.0, 30.0)]
        assert all(a > b for a, b in zip(vals, vals[1:]))

    @pytest.mark.parametrize("bad", [-1.0, 100.0, 120.0])
    def test_required_eexi_rejects_impossible_reduction(self, bad):
        with pytest.raises(ValueError):
            cii_mod.required_eexi("bulk_carrier", 80_000.0, bad)

    def test_eexi_status_verdict_and_margin(self):
        st = cii_mod.eexi_status(
            BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
            BASE["capacity_t"], BASE["v_ref_kn"], 20.0, p_ae_kw=600.0,
            sfoc_ae_g_kwh=190.0,
        )
        required = 0.8 * cii_mod.eedi_reference_value("bulk_carrier", 80_000.0)
        assert st["required_eexi"] == pytest.approx(required, rel=1e-15)
        assert st["margin_pct"] == pytest.approx(
            100.0 * (required - st["attained_eexi"]) / required, rel=1e-12)
        assert st["compliant"] is (st["attained_eexi"] <= required)

    def test_eexi_status_flips_with_reduction(self):
        args = (BASE["p_me_kw"], BASE["sfc_me_g_kwh"], "heavy_fuel_oil",
                BASE["capacity_t"], BASE["v_ref_kn"])
        lenient = cii_mod.eexi_status(*args, 0.0)
        strict = cii_mod.eexi_status(*args, 60.0)
        assert lenient["compliant"] is True
        assert strict["compliant"] is False


# ---------------------------------------------------------------------------
# 4. EEDI reference line / phase algebra used by the compliance path
# ---------------------------------------------------------------------------

class TestEediRequired:

    def test_phase_factors(self):
        ref = cii_mod.eedi_reference_value("bulk_carrier", 80_000.0)
        for phase, pct in ((0, 0.0), (1, 10.0), (2, 20.0), (3, 30.0)):
            req = cii_mod.required_eedi("bulk_carrier", 80_000.0, phase)
            assert req["required_eedi"] == pytest.approx(
                ref * (1.0 - pct / 100.0), rel=1e-15)

    def test_reference_line_dimensioning(self):
        """EEDI_ref ~ DWT^-c: doubling capacity must scale by 2^-c exactly."""
        r1 = cii_mod.eedi_reference_value("bulk_carrier", 80_000.0)
        r2 = cii_mod.eedi_reference_value("bulk_carrier", 160_000.0)
        assert r1 / r2 == pytest.approx(math.pow(2.0, 0.477), rel=1e-12)

    def test_reference_line_value_is_bracketed(self):
        """Physical sanity bracket for bulk carriers (2-9 g CO2/(t nm)).

        Deliberately wide: the coefficients themselves (MEPC.203(62) table) are
        read off a guideline and are NOT re-derived here (WITHHELD).
        """
        for dwt in (35_000.0, 80_000.0, 180_000.0):
            v = cii_mod.eedi_reference_value("bulk_carrier", dwt)
            assert 2.0 < v < 9.0