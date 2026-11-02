"""Validation provider seam tests (R2 boundary inversion, 2026-11-02).

Core audit layers consume domain physics ONLY through
services.validation_providers.  Physics implementations live in the domain
payload zone (api.domain.resistance_model), registered by
api.domain.physics_providers at startup.
"""
import pytest

from services.validation_providers import (
    ProviderNotRegistered,
    friction_line,
    physical_constant,
    register_provider,
    require_provider,
    reset_providers,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_providers()
    yield
    reset_providers()


class TestRegistrySemantics:
    def test_register_require_roundtrip(self):
        def line(re_):
            return 0.003
        register_provider("d", "friction:ittc1957", line)
        assert require_provider("d", "friction:ittc1957") is line

    def test_idempotent_same_object(self):
        line = lambda re_: 0.003  # noqa: E731
        register_provider("d", "f", line)
        register_provider("d", "f", line)  # no raise

    def test_refuses_silent_physics_swap(self):
        register_provider("d", "f", lambda re_: 0.003)
        with pytest.raises(ProviderNotRegistered, match="refusing to silently swap"):
            register_provider("d", "f", lambda re_: 0.004)

    def test_unknown_lists_registered(self):
        register_provider("d", "f", lambda re_: 0.003)
        with pytest.raises(ProviderNotRegistered, match=r"registered: \['d/f'\]"):
            require_provider("nope", "missing")

    def test_empty_registry_error_mentions_wiring(self):
        with pytest.raises(ProviderNotRegistered, match="Wire the domain package"):
            physical_constant("ship_resistance", "rho_sea_water_kg_m3")


class TestDomainWiring:
    def test_ship_constants_via_seam(self):
        from api.domain.physics_providers import (
            register_ship_resistance_providers,
        )
        register_ship_resistance_providers()
        assert physical_constant("ship_resistance", "g_m_s2") == 9.81
        assert physical_constant("ship_resistance", "rho_sea_water_kg_m3") == 1025.0
        nu = physical_constant("ship_resistance", "nu_sea_water_15c_m2_s")
        assert 1.0e-6 < nu < 2.0e-6

    def test_friction_line_matches_ittc1957_closed_form(self):
        import math
        from api.domain.physics_providers import (
            register_ship_resistance_providers,
        )
        register_ship_resistance_providers()
        for re_ in (1e6, 1e7, 1e8):
            got = friction_line("ship_resistance", "ittc1957", re_)
            want = 0.075 / (math.log10(re_) - 2.0) ** 2
            assert got == pytest.approx(want, rel=1e-12)

    def test_double_registration_is_safe(self):
        from api.domain.physics_providers import (
            register_ship_resistance_providers,
        )
        register_ship_resistance_providers()
        register_ship_resistance_providers()  # app startup + test import


_CASE = {
    "case_id": "X",
    "speed_ms": 3.0,
    "experimental_ct": 0.0038,
    "experimental_rt_n": 92.52,
    "geometry": {"length_m": 4.356, "beam_m": 0.508, "draft_m": 0.476,
                 "cb": 0.542, "wetted_surface_m2": 5.42},
}


class TestCoreAuditConsumesSeam:
    def test_ship_benchmark_integrity_needs_wiring(self):
        """Unwired domain -> loud ProviderNotRegistered, never silent physics."""
        from services import ship_benchmark_integrity as sbi
        with pytest.raises(ProviderNotRegistered, match="ship_resistance"):
            sbi.check_ct_rt_identity(dict(_CASE))

    def test_ship_benchmark_integrity_runs_when_wired(self):
        from api.domain.physics_providers import (
            register_ship_resistance_providers,
        )
        from services import ship_benchmark_integrity as sbi
        register_ship_resistance_providers()
        out = sbi.check_ct_rt_identity(dict(_CASE))
        assert out["name"] == "ct_rt_identity"
        assert out["status"] in ("PASS", "FAIL")  # physics executed, not WITHHELD
