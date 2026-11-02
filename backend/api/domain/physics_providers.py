"""Ship-domain provider registration for the core validation seam.

The physics implementations live in the domain payload zone
(``api.domain.resistance_model``); this module is the single place that
publishes them to ``services.validation_providers`` so core audit layers can
consume physics without importing it.  When the ship payload migrates off
TensorPlatform (P2 ruling), the registration -- and only the registration --
moves with it.
"""
from __future__ import annotations

from services.validation_providers import register_provider

DOMAIN = "ship_resistance"


def register_ship_resistance_providers() -> None:
    from api.domain import resistance_model as rm

    register_provider(DOMAIN, "constant:g_m_s2", rm.G)
    register_provider(DOMAIN, "constant:rho_sea_water_kg_m3", rm.RHO_SEA_WATER)
    register_provider(DOMAIN, "constant:nu_sea_water_15c_m2_s", rm.NU_SEA_WATER_15C)
    register_provider(DOMAIN, "friction:ittc1957", rm.ittc57_cf)
