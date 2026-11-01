"""把阻力基准数据集 v1.0 重建为 v1.2（内部自洽 + 尺度声明 + 出处分级）。

设计原则
--------
1. **数字零手抄**：本脚本从 v1.0 读入继承值，按公开公式/公称主尺度重算派生量，
   写入 v1.2；任何派生都在 case 的 `derivation` 字段里留下公式与来源标签。
2. **不造实验值**：继承的 `experimental_ct` 一律标 `reference_verified: false`；
   凡「速度-参考值」配对已不可复原的（DTMB-5415 四个案例），参考值降级为
   `retired`（`experimental_ct = null`，原值保留在 `legacy_reference_ct`），
   宁可 WITHHELD 也不拿假值去判 pass/fail。
3. **尺度显式**：每个案例声明 `scale`，`speed_ms` 与几何同尺度；DTMB-5415 按
   等 Froude 换算到模型尺度（原 v1.0 把实船航速喂给模型几何，Fr 高达 2.06）。

用法
----
    python3 tools/benchmark_audit/build_dataset_v1_2.py \
        [--in backend/data/benchmark_reference.json] [--out /tmp/v1_2.json]

不指定 --out 时只打印摘要（dry-run），便于审计复核。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List

G = 9.81

# ---------------------------------------------------------------------------
# 公称主尺度（canonical particulars）与水体属性
# ---------------------------------------------------------------------------
FRESH_WATER = {"water_density": 998.0, "kinematic_viscosity": 1.004e-6}
SEA_WATER = {"water_density": 1025.0, "kinematic_viscosity": 1.189e-6}

# DTMB-5415 实船公称值 + v1.0 已声明的模型长度（用于确定缩尺比）
DTMB5415_FULL = {"length_m": 142.0, "beam_m": 19.06, "draft_m": 6.15, "cb": 0.507}
DTMB5415_MODEL_LENGTH = 5.72

# KVLCC2 公称值（Cm/Cwp 用于 Holtrop 湿表面积；S_published 仅作外部锚点校核）
KVLCC2 = {
    "length_m": 320.0, "beam_m": 58.0, "draft_m": 20.8, "cb": 0.8098,
    "cm": 0.9919, "cwp": 0.8708, "s_published_m2": 27194.0,
}

# SUBOFF：v1.0 只给了 D，没有排水体积；容积系数沿用 v1.0 SHIP_GEOMETRY 声明值
SUBOFF = {"length_m": 4.356, "diameter_m": 0.508, "cb_envelope": 0.508}


def holtrop_wetted_surface(L: float, B: float, T: float, cb: float,
                           cm: float | None = None, cwp: float | None = None) -> float:
    """Holtrop–Mennen (1982) 湿表面积；缺省 Cm=0.9+0.1Cb、Cwp=(1+2Cb)/3。"""
    cm = cm if cm is not None else 0.9 + 0.1 * cb
    cwp = cwp if cwp is not None else (1.0 + 2.0 * cb) / 3.0
    factor = (0.453 + 0.4425 * cb - 0.2862 * cm
              - 0.003467 * (B / T) + 0.3696 * cwp)
    return L * (2.0 * T + B) * math.sqrt(cm) * factor


def cf_ittc57(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


# ---------------------------------------------------------------------------
# 逐船重建
# ---------------------------------------------------------------------------

def build_kvlcc2(old_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    L, B, T, cb = KVLCC2["length_m"], KVLCC2["beam_m"], KVLCC2["draft_m"], KVLCC2["cb"]
    rho = SEA_WATER["water_density"]
    nu = SEA_WATER["kinematic_viscosity"]
    S = holtrop_wetted_surface(L, B, T, cb, KVLCC2["cm"], KVLCC2["cwp"])
    vol = cb * L * B * T                      # ∇ = Cb·L·B·T（派生）
    disp_t = vol * rho / 1000.0
    out = []
    for old in old_cases:
        kn = float(old.get("speed_kn") or old["speed_ms"] / 0.514444)
        V = kn * 0.514444
        fr = V / math.sqrt(G * L)
        ct = old.get("experimental_ct")
        rt = 0.5 * rho * S * ct * V ** 2
        out.append({
            "case_id": old["case_id"],
            "ship_type": "KVLCC2",
            "scale": "full",
            **SEA_WATER,
            "speed_kn": round(kn, 3),
            "speed_ms": round(V, 4),
            "froude_number": round(fr, 4),
            "experimental_ct": ct,
            "experimental_rt_n": round(rt, 1),
            "rt_derived_from_ct": True,
            "reference_verified": False,
            "reference_ct_status": "withheld",
            "withheld_reason": (
                "继承自 v1.0 的 Ct 无表号/页码；用 ITTC-57 + 公称湿表面积复算，实船"
                f"量级应为 Cf·(1+k)≈{cf_ittc57(V * L / nu) * 1.2:.5f}，而声明值"
                f" {ct} 高出约 {ct / (cf_ittc57(V * L / nu) * 1.2) - 1:.0%}，"
                "更接近模型尺度 Ct 量级——口径不明，不得用于精度判决"
            ),
            "provenance": {
                "type": "inherited-unverified",
                "locator": "v1.0 benchmark_reference.json case " + old["case_id"],
            },
            "source_citation": old.get("source_citation", ""),
            "geometry": {
                "length_m": L, "beam_m": B, "draft_m": T, "cb": cb,
                "cm": KVLCC2["cm"], "cwp": KVLCC2["cwp"],
                "displacement_m3": round(vol, 2),
                "displacement_t": round(disp_t, 1),
                "wetted_surface_m2": round(S, 2),
            },
            "derivation": {
                "displacement_m3": "Cb·L·B·T",
                "displacement_t": "∇·ρ/1000（ρ=1025 kg/m³ 海水）",
                "wetted_surface_m2": "Holtrop–Mennen 1982（Cm/Cwp 取公称值）",
                "wetted_surface_external_anchor": (
                    f"公称湿表面积 {KVLCC2['s_published_m2']:.0f} m² → 本值相对偏差 "
                    f"{(S - KVLCC2['s_published_m2']) / KVLCC2['s_published_m2'] * 100:+.2f}%"
                ),
                "experimental_rt_n": "由 Ct 反算：0.5·ρ·S·Ct·V²（原值与原 Ct 互斥，已弃用）",
            },
        })
    return out


def build_dtmb5415(old_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lam = DTMB5415_FULL["length_m"] / DTMB5415_MODEL_LENGTH
    L = DTMB5415_MODEL_LENGTH
    B = DTMB5415_FULL["beam_m"] / lam
    T = DTMB5415_FULL["draft_m"] / lam
    cb = DTMB5415_FULL["cb"]
    rho = FRESH_WATER["water_density"]
    nu = FRESH_WATER["kinematic_viscosity"]
    S = holtrop_wetted_surface(L, B, T, cb)
    vol = cb * L * B * T
    out = []
    for old in old_cases:
        kn = float(old.get("speed_kn") or old["speed_ms"] / 0.514444)
        V_full = kn * 0.514444
        fr = V_full / math.sqrt(G * DTMB5415_FULL["length_m"])   # 等 Fr 换算
        V = fr * math.sqrt(G * L)
        out.append({
            "case_id": old["case_id"],
            "ship_type": "DTMB-5415",
            "scale": "model",
            **FRESH_WATER,
            "speed_kn_full_scale": round(kn, 3),
            "speed_ms": round(V, 4),
            "froude_number": round(fr, 4),
            "experimental_ct": None,
            "experimental_rt_n": None,
            "legacy_reference_ct": old.get("experimental_ct"),
            "legacy_speed_ms": old.get("speed_ms"),
            "legacy_froude_number": old.get("froude_number"),
            "reference_verified": False,
            "reference_ct_status": "retired",
            "withheld_reason": (
                "v1.0 把实船航速（5/10/20/30 kn）喂给模型尺度几何（L=5.72 m），"
                f"Fr 高达 {old['speed_ms'] / math.sqrt(G * L):.2f}，速度-参考 Ct 配对不可复原；"
                "按等 Fr 换算重定速度后，原参考值不再对应该工况 → 降级 retired，"
                "补值需查 ITTC 2010 Gothenburg 报告表号"
            ),
            "provenance": {
                "type": "inherited-retired",
                "locator": "v1.0 benchmark_reference.json case " + old["case_id"],
            },
            "source_citation": old.get("source_citation", ""),
            "geometry": {
                "length_m": round(L, 4),
                "beam_m": round(B, 5),
                "draft_m": round(T, 5),
                "cb": cb,
                "displacement_m3": round(vol, 5),
                "displacement_kg": round(vol * rho, 2),
                "wetted_surface_m2": round(S, 3),
            },
            "derivation": {
                "scale_ratio": f"λ = 142.0/5.72 = {lam:.4f}",
                "linear_dimensions": "B,T = 实船公称值/λ",
                "displacement_m3": "Cb·L·B·T（v1.0 的 466.3 kg 与 Cb·L·B·T 不符，已弃用）",
                "wetted_surface_m2": "Holtrop–Mennen 1982，缺省 Cm/Cwp（Cb=0.507 已低于该式常用域，按外推标注）",
                "speed_ms": "等 Froude 换算：V_model = Fr_full·√(g·L_model)",
            },
        })
    return out


def build_suboff(old_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    L, D = SUBOFF["length_m"], SUBOFF["diameter_m"]
    cb = SUBOFF["cb_envelope"]
    rho = FRESH_WATER["water_density"]
    nu = FRESH_WATER["kinematic_viscosity"]
    vol = cb * math.pi / 4.0 * D * D * L          # 回转体：Cb·π/4·D²·L
    S_old = 5.42                                   # v1.0 声明的湿表面积（继承）
    out = []
    for old in old_cases:
        V = float(old["speed_ms"])
        fr = V / math.sqrt(G * L)
        ct = old.get("experimental_ct")
        rt = 0.5 * rho * S_old * ct * V ** 2
        case = {
            "case_id": old["case_id"],
            "ship_type": "SUBOFF",
            "scale": "model",
            **FRESH_WATER,
            "speed_ms": round(V, 4),
            "froude_number": round(fr, 4),
            "experimental_ct": ct,
            "experimental_rt_n": round(rt, 2),
            "rt_derived_from_ct": True,
            "reference_verified": False,
            "reference_ct_status": "withheld",
            "withheld_reason": (
                "继承自 v1.0 的 Ct 只有一句 DTRC-90/012 引文、无表号；"
                "该船为回转体，Holtrop 类方法不适用，精度口径需 CFD/细长体理论单列"
            ),
            "provenance": {
                "type": "inherited-unverified",
                "locator": "v1.0 benchmark_reference.json case " + old["case_id"],
            },
            "source_citation": old.get("source_citation", ""),
            "geometry": {
                "length_m": L,
                "diameter_m": D,
                "solid_type": "body_of_revolution",
                "cb": cb,
                "displacement_m3": round(vol, 6),
                "displacement_kg": round(vol * rho, 3),
                "wetted_surface_m2": S_old,
            },
            "derivation": {
                "displacement_m3": "Cb·π/4·D²·L（Cb 为外接圆柱容积系数，沿用 v1.0 声明值 0.508）",
                "wetted_surface_m2": "继承 v1.0 声明值（回转体无解析湿表面积，未标定）",
                "experimental_rt_n": "由 Ct 反算：0.5·ρ·S·Ct·V²（原 12.5 N 与 Ct 差 7 倍，已弃用）",
            },
        }
        if fr > 0.60:
            case["validity"] = "out_of_range"
            case["validity_reason"] = (
                f"Fr={fr:.3f} 超出排水型阻力基准物理带（≤0.60）；该速度段属高 Fr/空化工况，"
                "需单独口径，不得与低速段混算 pass 率"
            )
        out.append(case)
    return out


def build_v1_2(v1_0: Dict[str, Any]) -> Dict[str, Any]:
    cases = v1_0.get("cases", [])
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for c in cases:
        by_type.setdefault(c["ship_type"], []).append(c)

    new_cases: List[Dict[str, Any]] = []
    new_cases += build_suboff(sorted(by_type.get("SUBOFF", []), key=lambda c: c["case_id"]))
    new_cases += build_dtmb5415(sorted(by_type.get("DTMB-5415", []), key=lambda c: c["case_id"]))
    new_cases += build_kvlcc2(sorted(by_type.get("KVLCC2", []), key=lambda c: c["case_id"]))

    return {
        "description": (
            "Benchmark reference dataset for ship resistance validation. "
            "v1.2 (2026-11-01): 内部自洽重建 + 尺度显式声明 + 出处分级；"
            "几何/运动学量由闭式公式派生（derivation 字段留痕），"
            "继承的实验 Ct 一律 reference_verified=false，"
            "DTMB-5415 四个案例因速度-参考值配对不可复原降级 retired。"
            "判据见 tools/benchmark_audit/audit_resistance_benchmark.py（J1~J7）。"
        ),
        "version": "1.2",
        "supersedes": str(v1_0.get("version", "1.0")),
        "water_density": FRESH_WATER["water_density"],
        "kinematic_viscosity": FRESH_WATER["kinematic_viscosity"],
        "cases": new_cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="backend/data/benchmark_reference.json")
    ap.add_argument("--out", dest="out", default=None)
    args = ap.parse_args()

    v1_0 = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    v1_2 = build_v1_2(v1_0)
    text = json.dumps(v1_2, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}: {len(v1_2['cases'])} cases, version {v1_2['version']}")
    else:
        print(text[:1200])
        print(f"... ({len(v1_2['cases'])} cases, dry-run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())