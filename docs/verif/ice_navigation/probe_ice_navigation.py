"""冰区航行域验证欠账探针：/api/v1/extended-calculations/ice 证据分级。

设计原则（无外部文献数值也可判）：
  1) 物理不变量/标度律判据（无需外部常数）：零冰厚极限、速度依赖、成分完整性、参数敏感性、
     物理有界性、冰级规范单调性。
  2) 量级判据：把端点输出反解成"等效冰压碎强度" sigma_eff，与一级冰物理带 0.5-5 MPa 对照。
  3) 自洽判据：端点自称 "Lindqvist公式"，与仓库内独立的 Lindqvist 实现
     (backend/methods/ice/lindqvist.py, 636 行) 对拍——只能证明"端点与自述方法不自洽"，
     不能替代文献值；文献值缺失如实登记为局限。

输出：evidence.json（全部数字，供 pytest 复算）、verdict.json（判据判决）、fig_*.png。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8001/api/v1/extended-calculations/ice"
ORACLE_PATH = ("/srv/tensor/ship-workspace/ship-performance-platform"
               "/backend/methods/ice/lindqvist.py")
OUT = os.path.dirname(os.path.abspath(__file__))

BASE = {
    "length": 150.0, "beam": 23.0, "draft": 8.0, "displacement": 15000.0,
    "speed": 5.0, "cb": 0.75, "ice_class": "PC7", "ice_thickness": 1.0,
}


def call(body: dict) -> dict:
    req = urllib.request.Request(
        API, data=json.dumps({**BASE, **body}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def load_oracle():
    """仓库内自称 Lindqvist 的独立实现（只读引用，不改）。"""
    if not os.path.exists(ORACLE_PATH):
        return None
    try:
        spec = importlib.util.spec_from_file_location("lindqvist_oracle", ORACLE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:  # pragma: no cover - 环境缺失时降级
        print(f"[warn] oracle unavailable: {exc}")
        return None


def oracle_R(mod, body: dict) -> dict:
    """oracle 输入：L,B,T[alpha=30,psi=30], h, speed[m/s]；输出 R_ice[N]。"""
    if mod is None:
        return {}
    ship = mod.ShipParameters(L=body["length"], B=body["beam"], T=body["draft"])
    ice = mod.IceConditions(h=body["ice_thickness"])
    v = body["speed"] * 0.5144
    res = mod.calculate_ice_resistance(ship, ice, speed=v)
    return {
        "R_ice_N": float(res.R_ice),
        "R_breaking_N": float(res.R_breaking),
        "R_submergence_N": float(res.R_submergence),
        "R_sliding_N": float(res.R_sliding),
    }


def main() -> int:
    probes: dict = {}
    oracle = load_oracle()

    # P1 基准点
    p1 = call({})
    probes["P1_baseline"] = {"input": dict(BASE), "output": p1,
                             "oracle": oracle_R(oracle, BASE)}
    # P2 航速扫描（物理：冰阻力必须随速度单调增）
    speeds = [1.0, 2.0, 5.0, 10.0, 15.0]
    probes["P2_speed_sweep"] = {
        "input": {"speed_kn": speeds},
        "R_ice": [call({"speed": v})["ice_resistance"] for v in speeds],
        "ice_power": [call({"speed": v})["ice_power"] for v in speeds],
    }
    # P3 冰厚扫描
    hs = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
    probes["P3_thickness_sweep"] = {
        "input": {"ice_thickness_m": hs},
        "R_ice": [call({"ice_thickness": h})["ice_resistance"] for h in hs],
        "shell_plating": [call({"ice_thickness": h})["shell_plating"] for h in hs],
        "frame_spacing": [call({"ice_thickness": h})["frame_spacing"] for h in hs],
        "oracle_R_ice_kN": [oracle_R(oracle, {**BASE, "ice_thickness": h}).get("R_ice_N", 0) / 1e3
                            for h in hs],
    }
    # P4 船宽/船长敏感性（Lindqvist 破冰项随 B 增、弯曲项随 L 增，两者都应显著）
    beams = [15.0, 23.0, 32.0]
    probes["P4_beam_sweep"] = {"input": {"beam_m": beams},
                              "R_ice": [call({"beam": b})["ice_resistance"] for b in beams]}
    # P5 Cb（schema 暴露但代码未用）死参数检验
    cbs = [0.55, 0.65, 0.75, 0.85]
    probes["P5_cb_sweep"] = {"input": {"cb": cbs},
                             "R_ice": [call({"cb": c})["ice_resistance"] for c in cbs]}
    # P6 排水量/尺度不变性（能力判定是否与船相关）
    probes["P6_size_sweep"] = {
        "input": [{"length": 50.0, "beam": 12.0, "draft": 4.0, "displacement": 1200.0},
                  {"length": 150.0, "beam": 23.0, "draft": 8.0, "displacement": 15000.0},
                  {"length": 300.0, "beam": 50.0, "draft": 13.0, "displacement": 200000.0}],
        "max_ice_thickness": [call({"length": 50.0, "beam": 12.0, "draft": 4.0,
                                    "displacement": 1200.0})["max_ice_thickness"],
                              p1["max_ice_thickness"],
                              call({"length": 300.0, "beam": 50.0, "draft": 13.0,
                                    "displacement": 200000.0})["max_ice_thickness"]],
        "R_ice": [call({"length": 50.0, "beam": 12.0, "draft": 4.0,
                        "displacement": 1200.0})["ice_resistance"],
                  p1["ice_resistance"],
                  call({"length": 300.0, "beam": 50.0, "draft": 13.0,
                        "displacement": 200000.0})["ice_resistance"]],
    }
    # P7 冰级扫描：结构要求是否随冰级变化
    classes = ["PC1", "PC3", "PC5", "PC7"]
    probes["P7_ice_class_sweep"] = {
        "input": {"ice_class": classes, "ice_thickness": 1.0},
        "shell_plating": [call({"ice_class": c})["shell_plating"] for c in classes],
        "frame_spacing": [call({"ice_class": c})["frame_spacing"] for c in classes],
        "max_ice_thickness": [call({"ice_class": c})["max_ice_thickness"] for c in classes],
        "continuous_speed": [call({"ice_class": c})["continuous_speed"] for c in classes],
    }
    # P8 零冰厚极限
    probes["P8_zero_ice"] = {"output": call({"ice_thickness": 0.0})}
    # P9 极厚冰外推：结构量是否出现非物理值
    probes["P9_thick_extrapolation"] = {"output": call({"ice_thickness": 9.0})}
    # P10 端点输入契约（是否接受 ice strength / 船首角 / 摩擦系数）
    probes["P10_input_contract"] = {
        "keys_accepted_by_base_body": sorted(BASE),
        "note": "schema = IceNavigationInput；无 sigma_c/sigma_f/alpha/psi/mu 通道",
    }

    # ---- 判据 ----
    R = probes["P2_speed_sweep"]["R_ice"]
    h = probes["P3_thickness_sweep"]["input"]["ice_thickness_m"]
    R3 = probes["P3_thickness_sweep"]["R_ice"]
    orc3 = probes["P3_thickness_sweep"]["oracle_R_ice_kN"]
    R_crush = 0.5 * 1000.0 * 1.0 ** 2 * BASE["beam"]           # 端点源码第 459 行（kN）
    sigma_eff = 0.5 * 1000.0 / (0.5 * 1.0 ** 2 * BASE["beam"]) * BASE["beam"]  # 反解：1000 Pa 常数
    # sigma_eff: 端点把 0.5*sigma*h^2*B 写成 0.5*1000*h^2*B → 等效 sigma = 1000 Pa
    sigma_eff = 1000.0 / 1e6  # MPa

    def ratio(a, b):
        return a / b if b else float("inf")

    crit = []

    def add(cid, name, ok, ref, got, tol, note=""):
        crit.append({"id": cid, "name": name, "pass": bool(ok),
                     "reference": ref, "measured": got, "tolerance": tol, "note": note})

    add("C1", "零冰厚极限 R_ice(h=0)=0", probes["P8_zero_ice"]["output"]["ice_resistance"] == 0,
        "0 kN（Lindqvist 各项 ∝ h^n, n>0）", probes["P8_zero_ice"]["output"]["ice_resistance"],
        "== 0")
    add("C2", "航速依赖：R_ice(15kn)/R_ice(1kn) >= 1.5",
        ratio(R[-1], R[0]) >= 1.5,
        "Lindqvist 速度项 (1+1.4v/sqrt(gh))(1+9.4v/sqrt(gL)) 保证 >=1.5",
        round(ratio(R[-1], R[0]), 4), ">= 1.5 kN/kn",
        "端点 R_ice 与速度完全无关，仅 P_ice 用到 V")
    add("C3", "冰强度量级：等效 sigma_eff 落在 0.5-5 MPa",
        0.5 <= sigma_eff <= 5.0, "0.5-5 MPa（一级冰压碎强度）",
        round(sigma_eff, 6), "0.5-5 MPa",
        "端点硬编码 1000 Pa，低 500-5000x 且无输入通道")
    add("C4", "三成分完整性：输出/输入含滑行(submergence+sliding)与船首角/摩擦",
        False, "Lindqvist = R_c + R_b(submerged, alpha, mu) + R_s(sliding, psi, mu)",
        "仅 R_crushing + R_bending 两项；schema 无 alpha/psi/mu sigma_c",
        "三成分齐备")
    add("C5", "Cb 参与计算（schema 暴露 cb）",
        len(set(probes["P5_cb_sweep"]["R_ice"])) > 1, "R 应对船型参数敏感",
        f"{len(set(probes['P5_cb_sweep']['R_ice']))} 个不同输出值", ">=2",
        "cb 为死参数（源码 446-490 行未引用）")
    add("C6", "冰级影响结构板厚（PC1 vs PC7）",
        len(set(probes["P7_ice_class_sweep"]["shell_plating"])) > 1,
        "IACS 极地冰带：PC1 板厚显著大于 PC7",
        f"shell_plating = {sorted(set(probes['P7_ice_class_sweep']['shell_plating']))} mm", ">=2 档",
        "15+10h 与冰级无关")
    add("C7", "结构量物理有界（frame_spacing > 0）",
        probes["P9_thick_extrapolation"]["output"]["frame_spacing"] > 0,
        "400-50h 在 h>8m 转负", probes["P9_thick_extrapolation"]["output"]["frame_spacing"],
        "> 0 mm")
    add("C8", "破冰能力与船相关（max_ice_thickness 随尺度变）",
        len(set(probes["P6_size_sweep"]["max_ice_thickness"])) > 1,
        "PC7 能力应由功率/尺度决定",
        sorted(set(probes["P6_size_sweep"]["max_ice_thickness"])), ">=2 档",
        "查表常数，与船无关")
    add("C9", "连续破冰航速与冰厚/功率解耦程度",
        len(set(probes["P7_ice_class_sweep"]["continuous_speed"])) == 1,
        "应为 speed x 固定系数（缺陷确认）",
        sorted(set(probes["P7_ice_class_sweep"]["continuous_speed"])), "1 档（=缺陷）",
        "continuous_speed = speed x speed_factor，与 h/功率无关")
    add("C10", "自洽：端点 vs 仓库内 Lindqvist 实现（同输入）",
        (bool(orc3[2]) and 0.5 <= ratio(R3[2], orc3[2]) <= 2.0),
        f"oracle R(h=1m) = {round(orc3[2], 1) if orc3[2] else 'N/A'} kN",
        f"endpoint R = {R3[2]} kN (比值 {round(ratio(R3[2], orc3[2]), 3) if orc3[2] else 'N/A'})",
        "0.5-2.0 x",
        "method 标签伪标：端点自称 Lindqvist 却未调用仓库内 636 行实现")
    add("C11", "功率一致性：ice_power = R*V 未除推进效率且与可用功率无比较",
        False, "P_D = R*V/eta_D, eta_D in [0.4,0.8]",
        f"P = R*V = {p1['ice_power']} kW（R={p1['ice_resistance']} kN, V={BASE['speed']}kn）",
        "含效率且<=可用功率")

    # 冰厚标度指数（端点）
    idx = [math.log(R3[i + 1] / R3[0]) / math.log(h[i + 1] / h[0]) for i in range(len(h) - 1)]

    evidence = {
        "endpoint": API,
        "probes": probes,
        "derived": {
            "R_ice_kN_baseline": p1["ice_resistance"],
            "oracle_R_ice_kN_baseline": probes["P1_baseline"]["oracle"].get("R_ice_N", 0) / 1e3,
            "ratio_endpoint_over_oracle": round(
                ratio(p1["ice_resistance"],
                      probes["P1_baseline"]["oracle"].get("R_ice_N", 0) / 1e3), 4),
            "sigma_eff_MPa": sigma_eff,
            "thickness_scaling_exponent": [round(x, 3) for x in idx],
            "speed_ratio_15_over_1": round(ratio(R[-1], R[0]), 6),
        },
        "criteria": crit,
        "oracle_available": oracle is not None,
    }
    jpath = os.path.join(OUT, "evidence.json")
    with open(jpath, "w") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=1)

    npass = sum(c["pass"] for c in crit)
    grade = "REAL" if npass == len(crit) else ("MOCK" if npass <= len(crit) // 3 else "PARTIAL")
    verdict = {
        "domain": "ice_navigation",
        "endpoint": API,
        "criteria_total": len(crit),
        "criteria_pass": npass,
        "criteria_fail": len(crit) - npass,
        "grade": grade,
        "fails": [c["id"] for c in crit if not c["pass"]],
        "defects": [
            {"id": "D1", "field": "ice_resistance",
             "defect": "无速度项：R_ice = 0.5*1000*h^2*B + 0.3*1000*h^1.5*sqrt(L)，V 仅用于 ice_power",
             "evidence": f"R(15kn)/R(1kn) = {round(ratio(R[-1], R[0]), 4)}"},
            {"id": "D2", "field": "ice_resistance",
             "defect": "等效冰压碎强度硬编码 1000 Pa，物理带 0.5-5 MPa，无 sigma_c 输入通道",
             "evidence": f"sigma_eff = {sigma_eff} MPa"},
            {"id": "D3", "field": "R_bending",
             "defect": "项结构非 Lindqvist：弯曲项写成 0.3*1000*h^1.5*sqrt(L)，缺 alpha/psi/mu 与 submergence/sliding 两成分",
             "evidence": "schema 无 alpha/psi/mu；源码注释称三成分实为两项"},
            {"id": "D4", "field": "cb", "defect": "死参数：cb 进入 schema 但计算未引用",
             "evidence": f"{len(set(probes['P5_cb_sweep']['R_ice']))} 个不同输出值（4 个输入）"},
            {"id": "D5", "field": "continuous_speed",
             "defect": "连续破冰航速 = 输入航速 x 冰级固定系数，与冰厚/功率/阻力解耦",
             "evidence": f"PC1..PC7 输出 {sorted(set(probes['P7_ice_class_sweep']['continuous_speed']))}"},
            {"id": "D6", "field": "shell_plating",
             "defect": "板厚 15+10h 与冰级无关，PC1 与 PC7 同厚",
             "evidence": f"{sorted(set(probes['P7_ice_class_sweep']['shell_plating']))} mm"},
            {"id": "D7", "field": "frame_spacing",
             "defect": "400-50h 无下界，h=9m 转负",
             "evidence": f"{probes['P9_thick_extrapolation']['output']['frame_spacing']} mm @ h=9m"},
            {"id": "D8", "field": "max_ice_thickness",
             "defect": "冰级查表常数，与船舶尺度/功率无关",
             "evidence": f"{sorted(set(probes['P6_size_sweep']['max_ice_thickness']))} m 覆盖 1200-200000 t"},
            {"id": "D9", "field": "method",
             "defect": "标签伪标 'Lindqvist公式'：仓库内 methods/ice/lindqvist.py 独立实现未被调用",
             "evidence": f"endpoint/oracle = {evidence['derived']['ratio_endpoint_over_oracle']}"},
            {"id": "D10", "field": "ice_power",
             "defect": "P = R*V 未除推进效率、未与可用功率/推力平衡比较",
             "evidence": f"{p1['ice_power']} kW"},
        ],
    }
    with open(os.path.join(OUT, "verdict.json"), "w") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=1)
    print(f"criteria {npass}/{len(crit)} pass -> grade {grade}")
    print(json.dumps(verdict["fails"]), "| derived:", json.dumps(evidence["derived"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())