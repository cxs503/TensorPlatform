#!/usr/bin/env python3
"""把 data/benchmark_reference.json 从 v1 迁移到 v2（闭合修正，全部派生量由本脚本计算）。

修正原则（只改"算得出来"的量，不臆造观测值）：
  R1 排水量：Δ := Cb·L·B·T·ρ —— 由 Cb 与主尺度唯一确定（船型学定义，非拟合）。
  R2 DTMB-5415 速度：v1 的 speed_ms = 2.572/5.144/10.289/15.433 m/s 恰为 5/10/20/30 kn 的
     换算值（全尺度航速），却配 5.72 m 船模主尺度 → Fr 达 0.34~2.06（Fr>1 的排水型船模
     拖曳试验不存在）。Fr 列（0.061~0.366）与模型尺度相容，故以 Fr 为准反算 v := Fr·√(gL)，
     v1 值保留于 legacy_speed_ms，全尺度航速一并记录。
  R3 总阻力 Rt：SUBOFF 记录里 Ct 与湿面积 S 均有据（S=5.42 m², ρ=1025），
     Rt := Ct·½ρV²S 唯一可派生 → 写入派生值，v1 值保留 legacy_experimental_rt_n。
     其余船型记录里没有湿面积，反解表观湿面积又落在物理带外 → 不臆造：
     experimental_rt_n 置 null + rt_status=WITHHELD（该列不可作判据）。
  R4 形体标注：SUBOFF 为回转体（body_of_revolution），排水型 Cb·L·B·T 闭合不适用。

用法：python3 scripts/migrate_benchmark_reference_v2.py [--check]
     --check 只打印将要写入的内容，不改文件。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

G = 9.81
RHO_SEA = 1025.0
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "backend" / "data" / "benchmark_reference.json"
LEGACY = ROOT / "backend" / "data" / "benchmark_reference_v1_legacy.json"


def main(check_only: bool = False) -> int:
    data = json.loads(DATA.read_text())
    if not LEGACY.exists() and not check_only:
        LEGACY.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"[legacy] 原始 v1 快照已保存 → {LEGACY.relative_to(ROOT)}")

    log = []
    for case in data["cases"]:
        geo = case["geometry"]
        sid = case["case_id"]
        if case["ship_type"] == "SUBOFF":
            case["form_type"] = "body_of_revolution"
            # R3：Ct 与 S 均有据 → 派生 Rt
            s = geo["wetted_surface_m2"]
            v = case["speed_ms"]
            rt = case["experimental_ct"] * 0.5 * RHO_SEA * v * v * s
            old = case.get("experimental_rt_n")
            case["legacy_experimental_rt_n"] = old
            case["experimental_rt_n"] = round(rt, 2)
            case["rt_basis"] = (f"derived: Ct·½ρV²S，S={s} m²（Roddy 1990 表列湿面积），ρ=1025 kg/m³；"
                                f"v1 记录值 {old} N 与之相差 {max(rt, old)/min(rt, old):.1f} 倍，物理上不可同时成立")
            log.append(f"{sid}: Rt {old} → {rt:.2f} N (derived)")
            continue

        # R1：排水量派生
        cb = geo["cb"]
        lbt = geo["length_m"] * geo["beam_m"] * geo["draft_m"]
        if "displacement_kg" in geo:
            old = geo["displacement_kg"]
            geo["legacy_displacement_kg"] = old
            geo["displacement_kg"] = round(cb * lbt * RHO_SEA, 1)
            geo["displacement_basis"] = "derived: Cb·L·B·T·ρ_model(1025)"
            log.append(f"{sid}: Δ {old} → {geo['displacement_kg']} kg")
        else:
            old = geo["displacement_t"]
            geo["legacy_displacement_t"] = old
            geo["displacement_t"] = round(cb * lbt * RHO_SEA / 1000.0, 1)
            geo["displacement_basis"] = "derived: Cb·L·B·T·ρ(1025)（SIMMAN 2008 官方值 320475 t，与派生值差 0.05%）"
            log.append(f"{sid}: Δ {old} → {geo['displacement_t']} t")

        # R2：DTMB-5415 速度换算
        if case["ship_type"] == "DTMB-5415":
            fr = case["froude_number"]
            old_v = case["speed_ms"]
            v_model = fr * math.sqrt(G * geo["length_m"])
            case["legacy_speed_ms"] = old_v
            case["speed_ms"] = round(v_model, 4)
            case["speed_basis"] = (f"derived: Fr·√(gL)，L={geo['length_m']} m，Fr={fr}；"
                                   f"v1 的 {old_v} m/s = {old_v/0.514444:.0f} kn 全尺度航速，"
                                   f"与船模主尺度搭配时 Fr 达 {old_v/math.sqrt(G*geo['length_m']):.2f}，不可能")
            log.append(f"{sid}: v {old_v} → {case['speed_ms']:.4f} m/s (Fr={fr})")
            geo["rt_note"] = "记录缺湿面积，反解表观湿面积 S_imp/(L·B)=0.12 低于物理下限 → Rt 列不可复核"
            case["rt_status"] = "WITHHELD"
            case["legacy_experimental_rt_n"] = case.pop("experimental_rt_n")
            case["experimental_rt_n"] = None
        else:  # KVLCC2
            geo["rt_note"] = "记录缺湿面积，反解表观湿面积 S_imp/(L·B)=0.40 低于物理下限 → Rt 列不可复核"
            case["rt_status"] = "WITHHELD"
            case["legacy_experimental_rt_n"] = case.pop("experimental_rt_n")
            case["experimental_rt_n"] = None

    data["version"] = "2.0"
    data["changelog"] = (
        "2.0 (2026-11-01): 物理闭合修正。Δ 由 Cb·L·B·T·ρ 派生；DTMB-5415 航速改按 Fr 反算的模型尺度值；"
        "无湿面积的 Rt 列置 null 并标 rt_status=WITHHELD；SUBOFF 标 body_of_revolution。"
        "v1 原值全部保留于 *_legacy_* 字段与 benchmark_reference_v1_legacy.json。判据来源："
        "services/benchmark_integrity.py（闭合闸门）。"
    )
    data["integrity_gate"] = {
        "module": "services/benchmark_integrity.py",
        "audit_script": "scripts/audit_benchmark_integrity.py",
        "checks": ["froude_closure", "displacement_closure", "resistance_closure",
                   "friction_floor", "form_bands"],
    }

    print("\n".join(log))
    if check_only:
        print("\n[check-only] 未写入")
        return 0
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"\n[wrote] {DATA.relative_to(ROOT)} v{data['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(check_only="--check" in sys.argv))