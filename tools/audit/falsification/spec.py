"""单一真值能力台账构建器（capabilities.json）。

从两处抽取并**合成一行**：
  1. 已入库对账台账 `artifacts/capability_ledger/capability_ledger_resolved.json`
     （21 项能力的 claimed_level / reconciled_level / divergence / line_sha256）；
  2. 本模块的**探针注册表**（entrypoint + callable_spec：可执行调用规格、扰动样本、
     解析对拍项、单调性序列），全部为可序列化的「已展平样本」，不含 eval/闭包，
     因此台账可被任意进程机器执行与复核。

要求（任务判据）：
  - 覆盖审计工具返回的 21 项，id 集合对称差为空；
  - 每行含 id / name / entrypoint / evidence_grade / evidence_ref / callable_spec。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

from . import knives

ROOT = pathlib.Path(__file__).resolve().parents[3]
LEDGER_REL = "artifacts/capability_ledger/capability_ledger_resolved.json"
SNAPSHOT_REL = "tests/fixtures/capability_ledger/capabilities_snapshot.json"
OUT_DIR_REL = "artifacts/capability_falsification"

RHO = knives.RHO_DEFAULT
NU = knives.NU_DEFAULT
SOUND_TOL_M = 0.05  # 排水量自洽门（平台 D3/m1624 门禁，样本全部自洽构造）


# ---------------------------------------------------------------- 工具
def _disp_volume(L: float, B: float, T: float, cb: float) -> float:
    return round(L * B * T * cb, 6)


def _disp_tonnes(L: float, B: float, T: float, cb: float, rho: float = RHO) -> float:
    return round(rho * L * B * T * cb / 1000.0, 6)


def _sample(label: str, body: Dict[str, Any], knobs: Dict[str, Any]) -> Dict[str, Any]:
    return {"label": label, "knobs": knobs, "body": body}


def _res_body(L: float, B: float, T: float, cb: float, speed_kn: float,
              depth_ratio: float | None = None) -> Dict[str, Any]:
    body = {"length": L, "beam": B, "draft": T, "cb": cb, "speed": speed_kn,
            "speed_unit": "kn", "displacement": _disp_volume(L, B, T, cb),
            "water_density": RHO, "water_viscosity": NU}
    if depth_ratio is not None:
        body["depth_ratio"] = depth_ratio
    return body


def _stab_body(L: float, B: float, T: float, cb: float, kg: float,
               depth: float | None = None) -> Dict[str, Any]:
    body = {"length": L, "beam": B, "draft": T, "cb": cb, "kg": kg,
            "displacement": _disp_tonnes(L, B, T, cb), "water_density": RHO}
    if depth is not None:
        body["depth"] = depth
    return body


def _group(driver: str, spec: str, samples: List[Dict[str, Any]],
           require: List[str], confirm: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {"driver": driver, "spec": spec, "require": require,
            "samples": samples, "confirm": confirm or []}


# ---------------------------------------------------------------- 各能力探针
def _spec_resistance() -> Dict[str, Any]:
    L, B, T, cb = 100.0, 20.0, 8.0, 0.60
    base = _sample("base", _res_body(L, B, T, cb, 14.0), {"speed": 14.0})
    speed = _group(
        "speed", "量级 ×1.0/×0.7/×1.3（任务判据）",
        [base,
         _sample("speed_x0.70", _res_body(L, B, T, cb, 9.8), {"speed": 9.8}),
         _sample("speed_x1.30", _res_body(L, B, T, cb, 18.2), {"speed": 18.2})],
        ["rt"],
        [_sample("speed_x0.50", _res_body(L, B, T, cb, 7.0), {"speed": 7.0}),
         _sample("speed_x1.50", _res_body(L, B, T, cb, 21.0), {"speed": 21.0})])
    cb_grp = _group(
        "cb", "Cb∈[0.35,0.90] 内扰动",
        [_sample(f"cb_{c}", _res_body(L, B, T, c, 14.0), {"cb": c})
         for c in (0.45, 0.60, 0.75)],
        ["rt"],
        [_sample("cb_0.35", _res_body(L, B, T, 0.35, 14.0), {"cb": 0.35}),
         _sample("cb_0.90", _res_body(L, B, T, 0.90, 14.0), {"cb": 0.90})])
    lb_grp = _group(
        "length_beam_ratio", "L/B∈[3,12] 内扰动（排水量随几何重算）",
        [_sample("lb_3.0", _res_body(60.0, 20.0, T, cb, 14.0), {"L_over_B": 3.0}),
         _sample("lb_5.0", _res_body(100.0, 20.0, T, cb, 14.0), {"L_over_B": 5.0}),
         _sample("lb_7.0", _res_body(140.0, 20.0, T, cb, 14.0), {"L_over_B": 7.0})],
        ["rt"],
        [_sample("lb_3.5", _res_body(70.0, 20.0, T, cb, 14.0), {"L_over_B": 3.5}),
         _sample("lb_12.0", _res_body(240.0, 20.0, T, cb, 14.0), {"L_over_B": 12.0})])
    bt_grp = _group(
        "beam_draft_ratio", "B/T∈[1.5,6.5] 内扰动（排水量随几何重算）",
        [_sample("bt_1.5", _res_body(100.0, 12.0, T, cb, 14.0), {"B_over_T": 1.5}),
         _sample("bt_2.5", _res_body(100.0, 20.0, T, cb, 14.0), {"B_over_T": 2.5}),
         _sample("bt_3.125", _res_body(100.0, 25.0, T, cb, 14.0), {"B_over_T": 3.125})],
        ["rt"],
        [_sample("bt_1.6", _res_body(100.0, 12.8, T, cb, 14.0), {"B_over_T": 1.6}),
         # B/T 上限样本：同时把 L 拉长以守住 L/B∈[3,12]（几何包络两个约束必须同时满足）
         _sample("bt_6.5", _res_body(160.0, 52.0, T, cb, 14.0), {"B_over_T": 6.5})])
    scaled = _sample("froude_scale_x1.2",
                     _res_body(120.0, 24.0, 9.6, cb, 14.0 * 1.2 ** 0.5), {"scale": 1.2})
    return {
        "kind": "http_json", "method": "POST",
        "path": "/api/v1/calculations/resistance",
        "extract_keys": ["rt", "cf", "cf_total", "cr", "rf", "wetted_surface",
                         "froude_number", "reynolds_number", "cb_from_dims",
                         "displacement_consistency_pct"],
        "extra_samples": [scaled],
        "perturbation_groups": [speed, cb_grp, lb_grp, bt_grp],
        "analytic_checks": [
            {"id": "ittc57_cf", "observed_key": "cf", "ref_mode": "fn_body",
             "ref_fn": "ittc57_cf_of_body", "kind": "analytic", "tolerance": 0.15,
             "formula": "Cf=0.075/(log10(Re)-2)^2, Re=V·L/ν（ITTC-1957，审计侧独立实现）"},
            {"id": "rf_identity", "observed_key": "rf", "ref_mode": "fn_response",
             "ref_fn": "rf_of_response", "kind": "identity",
             "formula": "Rf=0.5·ρ·V^2·S·Cf（精确恒等式，ρ=1025, V 由 kn 换算）"},
            {"id": "cr_froude_similarity", "observed_key": "cr",
             "ref_mode": "other_sample", "other_sample": "froude_scale_x1.2",
             "other_key": "cr", "kind": "analytic", "tolerance": 0.15,
             "formula": "同 Fr、同 Cb、同 L/B、B/T 的几何放大 1.2 倍：Cr 必须不变（量纲分析精确结论）"},
        ],
        "monotone_series": [
            {"id": "rt_vs_speed", "x_key": "speed", "y_key": "rt", "direction": "increasing",
             "points": [_sample(f"speed_{s}", _res_body(L, B, T, cb, s), {"speed": s})
                        for s in (7.0, 9.8, 14.0, 18.2, 21.0)]},
        ],
        "applicable_knives": ["A", "B", "C"],
    }


def _spec_stability() -> Dict[str, Any]:
    L, B, T, cb = 100.0, 20.0, 8.0, 0.60
    base = _sample("base", _stab_body(L, B, T, cb, 8.0), {"kg": 8.0})
    kg_grp = _group(
        "kg", "量级 ×1.0/×0.75/×1.25（KG 是 GM 的单调减因子）",
        [base,
         _sample("kg_6.0", _stab_body(L, B, T, cb, 6.0), {"kg": 6.0}),
         _sample("kg_10.0", _stab_body(L, B, T, cb, 10.0), {"kg": 10.0})],
        ["gm"],
        [_sample("kg_5.0", _stab_body(L, B, T, cb, 5.0), {"kg": 5.0}),
         _sample("kg_11.0", _stab_body(L, B, T, cb, 11.0), {"kg": 11.0})])
    beam_grp = _group(
        "beam", "B∈[0.9,1.1]×B0（排水量随几何重算）",
        [_sample("beam_18.0", _stab_body(L, 18.0, T, cb, 8.0), {"beam": 18.0}),
         _sample("beam_20.0", _stab_body(L, 20.0, T, cb, 8.0), {"beam": 20.0}),
         _sample("beam_22.0", _stab_body(L, 22.0, T, cb, 8.0), {"beam": 22.0})],
        ["gm", "bm"],
        [_sample("beam_16.0", _stab_body(L, 16.0, T, cb, 8.0), {"beam": 16.0}),
         _sample("beam_24.0", _stab_body(L, 24.0, T, cb, 8.0), {"beam": 24.0})])
    return {
        "kind": "http_json", "method": "POST",
        "path": "/api/v1/calculations/stability",
        "extract_keys": ["kb", "bm", "km", "gm", "gz_max", "gz_max_angle",
                         "heel_validity_limit_deg"],
        "extra_samples": [],
        "perturbation_groups": [kg_grp, beam_grp],
        "analytic_checks": [
            {"id": "gm_identity", "observed_key": "gm", "ref_mode": "fn_response",
             "ref_fn": "gm_of_response", "kind": "identity",
             "formula": "GM = KB + BM − KG（初稳性高恒等式，精确）"},
            {"id": "gz_wallsided_max", "observed_key": "gz_max", "ref_mode": "fn_response",
             "ref_fn": "gz_max_of_response", "kind": "analytic", "tolerance": 0.15,
             "formula": "GZ(φ)=sinφ(GM+BM/2·tan^2φ) 的极大值（审计侧 0.005° 网格独立求根）"},
        ],
        "monotone_series": [
            {"id": "gm_vs_kg", "x_key": "kg", "y_key": "gm", "direction": "decreasing",
             "points": [_sample(f"kg_{k}", _stab_body(L, B, T, cb, k), {"kg": k})
                        for k in (5.0, 6.0, 8.0, 10.0, 11.0)]},
        ],
        "applicable_knives": ["A", "B", "C"],
    }


def _spec_open_water() -> Dict[str, Any]:
    base = {"J": 0.4, "P_D": 1.0, "Z": 5, "Ae_Ao": 0.55}

    def s(label, **kw):
        body = dict(base)
        body.update(kw)
        return _sample(label, body, dict(kw))

    j_grp = _group("J", "量级 ×1.0/×0.5/×1.5（J 是 KT/KQ 的进速自变量）",
                   [s("J_0.2", J=0.2), s("J_0.4", J=0.4), s("J_0.6", J=0.6)],
                   ["KT", "KQ"],
                   [s("J_0.1", J=0.1), s("J_0.8", J=0.8)])
    pd_grp = _group("P_D", "量级 ×1.0/×0.8/×1.2（螺距比必须影响 KT/KQ）",
                    [s("P_D_0.8", P_D=0.8), s("P_D_1.0", P_D=1.0), s("P_D_1.2", P_D=1.2)],
                    ["KT", "KQ"],
                    [s("P_D_0.6", P_D=0.6), s("P_D_1.4", P_D=1.4)])
    ae_grp = _group("Ae_Ao", "量级 ×1.0/×0.73/×1.36（盘面比必须影响 KT/KQ）",
                    [s("AeAo_0.40", Ae_Ao=0.40), s("AeAo_0.55", Ae_Ao=0.55),
                     s("AeAo_0.75", Ae_Ao=0.75)],
                    ["KT", "KQ"],
                    [s("AeAo_0.30", Ae_Ao=0.30), s("AeAo_0.95", Ae_Ao=0.95)])
    return {
        "kind": "http_json", "method": "POST",
        "path": "/api/v1/propeller-open-water/open_water",
        "extract_keys": ["KT", "KQ", "eta0"],
        "extra_samples": [],
        "perturbation_groups": [j_grp, pd_grp, ae_grp],
        "analytic_checks": [
            {"id": "eta0_identity", "observed_key": "eta0", "ref_mode": "fn_response",
             "ref_fn": "eta0_of_response", "kind": "identity",
             "formula": "eta0 = J·KT/(2π·KQ)（敞水效率恒等式，精确）"},
        ],
        "monotone_series": [
            {"id": "KQ_vs_J", "x_key": "J", "y_key": "KQ", "direction": "decreasing",
             "points": [s(f"Jm_{j}", J=j) for j in (0.1, 0.2, 0.4, 0.6, 0.8)]},
            {"id": "KT_vs_J", "x_key": "J", "y_key": "KT", "direction": "decreasing",
             "points": [s(f"Jt_{j}", J=j) for j in (0.1, 0.2, 0.4, 0.6, 0.8)]},
            # 主判据之外的信息性序列：常用工作区间 J∈[0.2,0.8] 内的 KQ 单调性
            {"id": "KQ_vs_J_operating", "x_key": "J", "y_key": "KQ", "direction": "decreasing",
             "points": [s(f"Jk_{j}", J=j) for j in (0.2, 0.3, 0.4, 0.6, 0.8)]},
        ],
        "applicable_knives": ["A", "B", "C"],
    }


def _spec_hull() -> Dict[str, Any]:
    def s(label, L, B, T, cb):
        return _sample(label, {"Lpp": L, "B": B, "T": T, "Cb": cb},
                       {"Lpp": L, "B": B, "T": T, "Cb": cb})

    cb_grp = _group("Cb", "Cb∈[0.35,0.90] 内扰动（任务判据）",
                    [s("Cb_0.50", 100, 20, 8, 0.50), s("Cb_0.60", 100, 20, 8, 0.60),
                     s("Cb_0.70", 100, 20, 8, 0.70)],
                    ["hydrostatics.displacement_volume",
                     "hydrostatics.wetted_surface"],
                    [s("Cb_0.40", 100, 20, 8, 0.40), s("Cb_0.85", 100, 20, 8, 0.85)])
    beam_grp = _group("beam", "量级 ×1.0/×0.9/×1.1",
                      [s("B_18", 100, 18, 8, 0.60), s("B_20", 100, 20, 8, 0.60),
                       s("B_22", 100, 22, 8, 0.60)],
                      ["hydrostatics.displacement_volume",
                       "hydrostatics.wetted_surface"],
                      [s("B_16", 100, 16, 8, 0.60), s("B_24", 100, 24, 8, 0.60)])
    return {
        "kind": "http_json", "method": "POST",
        "path": "/api/v1/ship-hull/generate",
        "extract_keys": ["hydrostatics.displacement_volume",
                         "hydrostatics.wetted_surface", "hydrostatics.LCB"],
        "extra_samples": [],
        "perturbation_groups": [cb_grp, beam_grp],
        "analytic_checks": [
            {"id": "volume_identity", "observed_key": "hydrostatics.displacement_volume",
             "ref_mode": "fn_body", "ref_fn": "volume_of_body", "kind": "identity",
             "formula": "∇ = L·B·T·Cb（排水体积恒等式，精确）"},
        ],
        "monotone_series": [
            {"id": "volume_vs_Cb", "x_key": "Cb",
             "y_key": "hydrostatics.displacement_volume", "direction": "increasing",
             "points": [s(f"Cbm_{c}", 100, 20, 8, c) for c in (0.40, 0.50, 0.60, 0.70, 0.85)]},
            {"id": "wetted_vs_Cb", "x_key": "Cb",
             "y_key": "hydrostatics.wetted_surface", "direction": "increasing",
             "points": [s(f"Cbw_{c}", 100, 20, 8, c) for c in (0.40, 0.50, 0.60, 0.70, 0.85)]},
        ],
        "applicable_knives": ["A", "B", "C"],
    }


def _spec_ai() -> Dict[str, Any]:
    L, B, T, cb = 100.0, 20.0, 8.0, 0.60
    base = _sample("base", _res_body(L, B, T, cb, 14.0), {"speed": 14.0})
    speed = _group("speed", "量级 ×1.0/×0.7/×1.3",
                   [base,
                    _sample("speed_x0.70", _res_body(L, B, T, cb, 9.8), {"speed": 9.8}),
                    _sample("speed_x1.30", _res_body(L, B, T, cb, 18.2), {"speed": 18.2})],
                   ["predicted_rt"],
                   [_sample("speed_x0.50", _res_body(L, B, T, cb, 7.0), {"speed": 7.0}),
                    _sample("speed_x1.50", _res_body(L, B, T, cb, 21.0), {"speed": 21.0})])
    beam = _group("beam", "量级 ×1.0/×0.9/×1.1（排水量随几何重算）",
                  [_sample("beam_18.0", _res_body(L, 18.0, T, cb, 14.0), {"beam": 18.0}),
                   _sample("beam_20.0", _res_body(L, 20.0, T, cb, 14.0), {"beam": 20.0}),
                   _sample("beam_22.0", _res_body(L, 22.0, T, cb, 14.0), {"beam": 22.0})],
                  ["predicted_rt"],
                  [_sample("beam_16.0", _res_body(L, 16.0, T, cb, 14.0), {"beam": 16.0}),
                   _sample("beam_24.0", _res_body(L, 24.0, T, cb, 14.0), {"beam": 24.0})])
    return {
        "kind": "http_json", "method": "POST",
        "path": "/api/v1/ai-models/predict/resistance",
        "extract_keys": ["predicted_rt", "predicted_cf", "predicted_cr"],
        "extra_samples": [],
        "perturbation_groups": [speed, beam],
        "analytic_checks": [
            {"id": "surrogate_vs_physics_rt", "observed_key": "predicted_rt",
             "ref_mode": "cross_endpoint", "cross_path": "/api/v1/calculations/resistance",
             "cross_extract": "rt", "sample": "base", "kind": "consistency", "tolerance": 0.15,
             "formula": "代理模型 R_t 与同输入物理链（Holtrop 链）R_t 一致性 <15%"},
        ],
        "monotone_series": [
            {"id": "predicted_rt_vs_speed", "x_key": "speed", "y_key": "predicted_rt",
             "direction": "increasing",
             "points": [_sample(f"speed_{s}", _res_body(L, B, T, cb, s), {"speed": s})
                        for s in (7.0, 9.8, 14.0, 18.2, 21.0)]},
        ],
        "applicable_knives": ["A", "B", "C"],
    }


# ---------------------------------------------------------------- 注册表
CAP_META: Dict[str, Dict[str, Any]] = {
    "propeller.open_water": {
        "name": "螺旋桨敞水性能（KT/KQ/eta0）", "domain": "推进",
        "entrypoint": {"kind": "http", "method": "POST",
                       "path": "/api/v1/propeller-open-water/open_water"},
        "builder": _spec_open_water},
    "calc.stability": {
        "name": "初稳性/大倾角稳性（GM/GZ）", "domain": "稳性",
        "entrypoint": {"kind": "http", "method": "POST",
                       "path": "/api/v1/calculations/stability"},
        "builder": _spec_stability},
    "calc.resistance": {
        "name": "静水阻力预报（ITTC-57+Holtrop 剩余阻力）", "domain": "阻力",
        "entrypoint": {"kind": "http", "method": "POST",
                       "path": "/api/v1/calculations/resistance"},
        "builder": _spec_resistance},
    "geometry.hydro_curves": {
        "name": "船体静水力曲线", "domain": "几何",
        "entrypoint": {"kind": "http_unresolved", "method": "GET", "path": None,
                       "candidates": ["/api/v1/geometry/hydro-curves",
                                      "/api/v1/ship-hull/hydro-curves",
                                      "/api/v1/calculations/hydro-curves"]},
        "builder": None},
    "geometry.generate": {
        "name": "几何模型生成", "domain": "几何",
        "entrypoint": {"kind": "http_unresolved", "method": "POST", "path": None,
                       "candidates": ["/api/v1/geometry/generate", "/api/v1/models/"]},
        "builder": None},
    "geometry.list_models": {
        "name": "几何模型列表", "domain": "几何",
        "entrypoint": {"kind": "http", "method": "GET", "path": "/api/v1/models/"},
        "builder": None},
    "geometry.verify_official": {
        "name": "官方几何校核", "domain": "几何",
        "entrypoint": {"kind": "http_unresolved", "method": "GET", "path": None,
                       "candidates": ["/api/v1/geometry/verify",
                                      "/api/v1/ship-hull/verify"]},
        "builder": None},
    "shiphull.generate": {
        "name": "船体型线生成（NURBS+静水力）", "domain": "几何",
        "entrypoint": {"kind": "http", "method": "POST",
                       "path": "/api/v1/ship-hull/generate"},
        "builder": _spec_hull},
    "stdmodels.lookup": {
        "name": "标准船/桨/规范库查询", "domain": "数据",
        "entrypoint": {"kind": "http", "method": "GET",
                       "path": "/api/v1/standard-models/ships"},
        "builder": None},
    "gov.stats": {
        "name": "平台统计/版本gov", "domain": "平台",
        "entrypoint": {"kind": "http", "method": "GET", "path": "/stats"},
        "builder": None},
    "knowledge.docs": {
        "name": "知识文档库", "domain": "平台",
        "entrypoint": {"kind": "http", "method": "GET", "path": "/api/v1/knowledge/docs"},
        "builder": None},
    "ai.predict": {
        "name": "AI 代理模型阻力预测（MLP）", "domain": "AI",
        "entrypoint": {"kind": "http", "method": "POST",
                       "path": "/api/v1/ai-models/predict/resistance"},
        "builder": _spec_ai},
    "methods.computation": {
        "name": "计算方法注册表", "domain": "方法",
        "entrypoint": {"kind": "http", "method": "GET",
                       "path": "/api/v1/calculations/methods"},
        "builder": None},
    "misc.stubs": {
        "name": "杂项占位端点", "domain": "杂项",
        "entrypoint": {"kind": "unknown", "method": None, "path": None,
                       "candidates": []},
        "builder": None},
}

_DEVOPS = {
    "dev.gate_5415": "5415 基准门禁脚本",
    "dev.git": "仓内 git 操作",
    "dev.logs": "服务日志读取",
    "dev.nginx_audit": "nginx 配置审计",
    "dev.probe": "在线探针",
    "dev.restart": "服务重启（运维）",
    "dev.services": "服务状态查询",
}
for _cid, _nm in _DEVOPS.items():
    CAP_META[_cid] = {
        "name": _nm, "domain": "开发",
        "entrypoint": {"kind": "devops", "method": None, "path": None,
                       "note": "运维/开发动作，非数值能力；三刀不适用（无输入→输出映射）"},
        "builder": None,
    }

NO_KNIFE_REASON = {
    "http": "查询/列表类能力：无「数值输入→数值输出」映射，三刀不适用（不得据此判 REAL）",
    "http_unresolved": "openapi.json 中无对应端点；候选路径全部 404 → 在线不可调用",
    "devops": "运维/开发动作，非数值能力，三刀不适用",
    "unknown": "台账无入口信息（注册表未给出端点），三刀不适用",
}


def load_reconciled() -> Dict[str, Any]:
    return json.loads((ROOT / LEDGER_REL).read_text(encoding="utf-8"))


def build_ledger() -> Dict[str, Any]:
    resolved = load_reconciled()
    rows = []
    for c in resolved["capabilities"]:
        cid = c["id"]
        meta = CAP_META.get(cid)
        if meta is None:  # 台账里出现未登记能力：显式成行，不静默丢
            meta = {"name": cid, "domain": c.get("domain", "?"),
                    "entrypoint": {"kind": "unknown", "method": None, "path": None},
                    "builder": None}
        spec = meta["builder"]() if meta["builder"] else {
            "kind": "no_numeric_mapping",
            "path": (meta["entrypoint"] or {}).get("path"),
            "reason": NO_KNIFE_REASON.get((meta["entrypoint"] or {}).get("kind"), "未知入口类型"),
            "applicable_knives": [],
        }
        rows.append({
            "id": cid,
            "name": meta["name"],
            "domain": c.get("domain") or meta["domain"],
            "entrypoint": meta["entrypoint"],
            "evidence_grade": c.get("claimed_level"),
            "evidence_ref": {
                "ledger": LEDGER_REL,
                "claimed_level": c.get("claimed_level"),
                "reconciled_level": c.get("reconciled_level"),
                "divergence": c.get("divergence"),
                "line_sha256": c.get("line_sha256"),
                "n_evidence_rows": len(c.get("evidence") or []),
                "evidence_report_ids": [e.get("report_id") for e in (c.get("evidence") or [])],
            },
            "callable_spec": spec,
            # ---- 工具兼容字段（ops/tp_worker.py::_render_caps 可直接渲染） ----
            "claimed_level": c.get("claimed_level"),
            "reconciled_level": c.get("reconciled_level"),
            "divergence": c.get("divergence"),
        })
    rows.sort(key=lambda r: r["id"])
    return {
        "schema_version": "1.0",
        "title": "船舶总体性能平台能力台账（唯一真值 + 可执行证伪规格）",
        "generated_by": "tools/audit/falsification/spec.py::build_ledger",
        "source_ledger": LEDGER_REL,
        "knife_criteria": {
            "A": f"扰动不变性：相对变化 < {knives.INVARIANCE_TOL:g} 判 MOCK（含加宽确认集）",
            "B": f"解析解/恒等式对拍：相对误差 > {knives.ANALYTIC_TOL:g} 判 MOCK",
            "C": "单调性：违反物理单调方向判 MOCK",
        },
        "n_capabilities": len(rows),
        "capabilities": rows,
    }


def main() -> None:
    out_dir = ROOT / OUT_DIR_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = build_ledger()
    (out_dir / "capabilities.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[spec] wrote {OUT_DIR_REL}/capabilities.json ({ledger['n_capabilities']} capabilities)")


if __name__ == "__main__":
    main()