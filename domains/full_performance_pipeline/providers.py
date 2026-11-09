"""配置注入节点：把船型/环境/航线配置摊平为契约字段（不参与物理计算，纯 passthrough）。

设计意图：让“配置项”与“上游模块输出”在契约层可区分——审计时能分清某个字段是
`producer:模块` 供的、还是 `config` 注入的，避免把注入值冒充跨模块数据流。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .contracts import REAL, Contract, Field
from .modules_core import Module

VESSEL_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("lpp_m", "m"), ("beam_m", "m"), ("wetted_area_m2", "m2"), ("form_factor_k", "1"),
    ("displacement_t", "t"), ("kg_m", "m"), ("gm_solid_m", "m"),
    ("area_front_m2", "m2"), ("area_lateral_m2", "m2"), ("lpp_m", "m"),
    ("x_centroid_m", "m"), ("z_centroid_m", "m"), ("x_ref_m", "m"), ("z_ref_m", "m"),
    ("ref_shift_m", "m"), ("available_power_kw", "kW"), ("propulsive_efficiency", "1"),
    ("speed_reference_ms", "m/s"), ("residuary_coef_a", "1"), ("ks_hull_um", "um"),
    ("growth_rate_um_per_d", "um/d"), ("fouling_coverage", "1"), ("days_in_service", "d"),
    ("deadrise_deg", "deg"), ("sigma_v_ratio", "1"), ("deck_length_m", "m"),
    ("deck_breadth_m", "m"), ("bulwark_height_m", "m"), ("gm_required_m", "m"),
    ("heel_limit_deg", "deg"), ("sfoc_g_per_kwh", "g/kWh"),
)

ENV_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("wave_height_m", "m"), ("wave_peak_period_s", "s"), ("wave_rel_heading_deg", "deg"),
    ("wind_speed_ms", "m/s"), ("wind_from_dir_deg", "deg"), ("reflection_efficiency", "1"),
    ("design_fractile", "1"), ("threshold_velocity_ms", "m/s"),
    ("accumulated_volume_m3", "m3"), ("lateral_offset_ratio", "1"),
    ("lat_dep_deg", "deg"), ("lon_dep_deg", "deg"), ("lat_arr_deg", "deg"), ("lon_arr_deg", "deg"),
    ("cx_coeff", "1"), ("cy_coeff", "1"), ("cn_coeff", "1"),
    ("towline_length_m", "m"), ("towline_wet_weight_N_per_m", "N/m"),
    ("bollard_pull_kN", "kN"), ("safety_factor", "1"),
)


def _passthrough_evidence(contract: Contract) -> Dict[str, Any]:
    return {f.name: (REAL, "配置 passthrough（定义性赋值）") for f in contract.provides}


def _dedup(fields) -> Tuple[Field, ...]:
    seen, out = set(), []
    for name, unit in fields:
        if name in seen:
            continue
        seen.add(name)
        kind = "array" if name.endswith("_coeff") else "float"
        out.append(Field(name, unit, kind, f"配置注入：{name}"))
    return tuple(out)


class VesselConfigModule(Module):
    """船型配置节点（passthrough，证据 REAL = 定义性赋值，无物理计算）。"""

    contract = Contract(
        module_id="CFG-vessel",
        title="船型配置注入",
        version="1.0.0",
        requires=(),
        provides=_dedup(VESSEL_FIELDS),
        note="合成船型配置；不含任何真实船资料。",
    )
    evidence = _passthrough_evidence(contract)

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        return {f.name: inputs[f.name] for f in self.contract.provides}


class EnvironmentConfigModule(Module):
    """环境/航线配置节点（passthrough）。"""

    contract = Contract(
        module_id="CFG-environment",
        title="环境与航线配置注入",
        version="1.0.0",
        requires=(),
        provides=_dedup(ENV_FIELDS),
        note="合成海况；非任何实测海域。",
    )
    evidence = _passthrough_evidence(contract)

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        return {f.name: inputs[f.name] for f in self.contract.provides}