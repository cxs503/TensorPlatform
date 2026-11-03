"""Ship-domain provider registration for the core validation seam.

The physics implementations live in the domain payload zone
(``api.domain.resistance_model`` / ``api.domain.holtrop_mennen`` /
``api.domain.ship_resistance``); this module is the single place that
publishes them to ``services.validation_providers`` so core audit layers can
consume physics without importing it.  When the ship payload migrates off
TensorPlatform (P2 ruling), the registration -- and only the registration --
moves with it.
"""
from __future__ import annotations

import sys

from services.validation_providers import register_provider

DOMAIN = "ship_resistance"


def _spec_load(name: str):
    """spec-load 域模块并缓存进 sys.modules —— 重复加载取同一对象，保证重复
    注册命中 register_provider 的同对象幂等分支（跨测试语境安全）。"""
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    import importlib.util as ilu
    from pathlib import Path
    spec = ilu.spec_from_file_location(
        name, Path(__file__).resolve().parent / (name + ".py"))
    mod = ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def register_ship_resistance_providers() -> None:
    try:
        from api.domain import (
            holtrop_mennen as hm,
            resistance_model as rm,
            ship_resistance as sr,
        )
    except Exception:  # spec-load 语境（核心测试按路径加载模块，无 api 包重依赖）
        rm = _spec_load("resistance_model")
        hm = _spec_load("holtrop_mennen")
        sr = _spec_load("ship_resistance")

    register_provider(DOMAIN, "constant:g_m_s2", rm.G)
    register_provider(DOMAIN, "constant:rho_sea_water_kg_m3", rm.RHO_SEA_WATER)
    register_provider(DOMAIN, "constant:nu_sea_water_15c_m2_s", rm.NU_SEA_WATER_15C)
    register_provider(DOMAIN, "friction:ittc1957", rm.ittc57_cf)
    # M9 尾巴（#95）：hm/sr 迁入载荷区后新发布的能力。hm 的海水物性与 rm 数值
    # 相同但对象不同 —— 不重复注册（register_provider 异对象即 raise）。
    register_provider(DOMAIN, "model:holtrop_resistance", hm.holtrop_resistance)
    register_provider(DOMAIN, "constant:rho_freshwater_15c_kg_m3", hm.RHO_FRESHWATER)
    register_provider(DOMAIN, "constant:default_water_density_kg_m3", sr.DEFAULT_WATER_DENSITY)
    register_provider(
        DOMAIN, "constant:default_kinematic_viscosity_m2_s", sr.DEFAULT_KINEMATIC_VISCOSITY)
