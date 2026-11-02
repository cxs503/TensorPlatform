#!/usr/bin/env python3
"""振动噪声域（vibration/noise）验证欠账探针 —— 被测平台 http://127.0.0.1:8001

背景
----
船舶总体性能 9 学科中，振动噪声域在 `docs/verification_debt/` 下原先**零覆盖**：
既无判据资产，也无参考实现。本探针补上这块欠账，做法与 m1780/m1817 一致：

1. 对在线端点 `POST /api/v1/vibration/predict` 施加**判据**（契约 / 输入依赖性 /
   量纲规格），把结论落成机器可裁的证据 `artifacts/vibration_gate.json`；
2. 提供一份**真实参考实现**（REAL），给出该域真正的设计控制量：
   船体梁两节点垂向弯曲固有频率 f2v 与螺旋桨叶频激励 f_b，供未来 REAL 实现对标。

参考实现的基准（不引用任何实测，纯解析 + 独立数值复核）
------------------------------------------------------
* **解析**：均匀 free-free 欧拉-伯努利梁的第 1 阶（船体梁两节点）弯曲模态
      f2v = λ1^2/(2π) * sqrt(EI / (m' L^4)),  λ1 = 4.7300408 rad
  λ1 为 free-free 梁频率方程 cos λ cosh λ = 1 的最小非零根，是闭式精确解。
* **独立数值复核**：一致质量矩阵 2 自由度/节点梁有限元（200 单元），取第 3 个
  特征值（前两个为刚体模态）。解析与 FEM 的相对偏差即为参考实现的可信度证据。
* **剖面惯性矩 I** 按 IACS UR S7 最小中剖面模数 W_min 反推：I = W_min * D/2，
  W_min = C*L^2*B*(Cb+0.7)*1e-6 [m^3]，C = 10.75 - ((300-L)/100)^1.5 (90<=L<=300)。
* **垂向附连水质量** 取 1.25 倍船体质量（垂向振动惯例量级 0.2~0.3 倍排水量）。

本文件的输出只用于**判据与基准**，不改动被测算例平台。

用法
----
    python3 tools/ref_vibration_8001.py            # 探测 + 写工件
    python3 tools/ref_vibration_8001.py --offline  # 只算参考基准，不联网
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "docs", "verification_debt", "vibration_noise", "artifacts")

BASE = os.environ.get("SHIP_API_BASE", "http://127.0.0.1:8001")
ENDPOINT = "/api/v1/vibration/predict"

# 判据阈值：两条不同船型的响应若逐字段相对差 < 1%，视为与输入无关（MOCK 指纹）
INPUT_INDEPENDENCE_TOL = 0.01

LAMBDA1 = 4.7300408  # free-free 梁频率方程 cos λ cosh λ = 1 的首个非零根
E_STEEL = 2.06e11     # Pa
ADDED_MASS_FACTOR = 1.25


# --------------------------------------------------------------------------
# 1. 参考实现（REAL）：船体梁两节点垂向固有频率
# --------------------------------------------------------------------------
def free_free_beam_f1_exact(L: float, EI: float, mbar: float) -> float:
    """均匀 free-free 欧拉-伯努利梁第 1 阶弯曲频率 [Hz]（闭式精确解）。"""
    return LAMBDA1**2 / (2.0 * np.pi) * float(np.sqrt(EI / (mbar * L**4)))


def free_free_beam_f1_fem(L: float, EI: float, mbar: float, n_elem: int = 200) -> float:
    """一致质量矩阵梁有限元独立复核（2 DOF/节点，free-free）。

    返回第 1 阶弹性弯曲频率 [Hz]（跳过两个刚体模态）。
    """
    le = L / n_elem
    ndof = 2 * (n_elem + 1)
    K = np.zeros((ndof, ndof))
    M = np.zeros((ndof, ndof))
    ke = EI / le**3 * np.array(
        [
            [12.0, 6 * le, -12.0, 6 * le],
            [6 * le, 4 * le * le, -6 * le, 2 * le * le],
            [-12.0, -6 * le, 12.0, -6 * le],
            [6 * le, 2 * le * le, -6 * le, 4 * le * le],
        ]
    )
    me = mbar * le / 420.0 * np.array(
        [
            [156.0, 22 * le, 54.0, -13 * le],
            [22 * le, 4 * le * le, 13 * le, -3 * le * le],
            [54.0, 13 * le, 156.0, -22 * le],
            [-13 * le, -3 * le * le, -22 * le, 4 * le * le],
        ]
    )
    for e in range(n_elem):
        s = slice(2 * e, 2 * e + 4)
        K[s, s] += ke
        M[s, s] += me
    # 广义对称特征问题；free-free 梁恰有 2 个刚体模态（平移/转动），其后再为弹性模态。
    # 注意：不可用 ``1e-6*max(eigval)`` 作阈值——梁的特征值随模态号按 λ^4 增长，
    # 高 DOF 下该阈值会把 1 阶弹性模态一并滤掉（本探针初版即踩此坑，误差 >100x）。
    eigvals = np.sort(np.linalg.eigvals(np.linalg.solve(M, K)).real)
    rigid, elastic = eigvals[:2], [ev for ev in eigvals[2:] if ev > 0.0]
    if not elastic:
        raise RuntimeError("FEM 未解出弹性模态")
    f1_sq = elastic[0]
    if max(abs(rigid[0]), abs(rigid[1])) > 1e-3 * f1_sq:
        raise RuntimeError("刚体模态分离失败：前两个特征值并非刚体模态")
    return float(np.sqrt(f1_sq) / (2.0 * np.pi))


def min_section_modulus(L: float, B: float, Cb: float) -> float:
    """IACS UR S7 最小中剖面模数 W_min [m^3]（90<=L<=350 适用段）。"""
    if L <= 300.0:
        C = 10.75 - ((300.0 - L) / 100.0) ** 1.5
    else:
        C = 10.75
    return float(C * L**2 * B * (Cb + 0.7) * 1e-6)


def hull_girder_2node(ship: dict) -> dict:
    """船体梁两节点垂向固有频率参考值及其构成量。"""
    L, B, D, Cb = ship["L"], ship["B"], ship["D"], ship["Cb"]
    delta_kg = ship["displacement_t"] * 1000.0
    W_min = min_section_modulus(L, B, Cb)
    I = W_min * D / 2.0                        # m^4（中和轴取半深）
    mbar = delta_kg * ADDED_MASS_FACTOR / L    # kg/m
    f_exact = free_free_beam_f1_exact(L, E_STEEL * I, mbar)
    f_fem = free_free_beam_f1_fem(L, E_STEEL * I, mbar)
    return {
        "class": ship["class"],
        "L": L,
        "B": B,
        "D": D,
        "Cb": Cb,
        "displacement_t": ship["displacement_t"],
        "W_min_m3": round(W_min, 3),
        "I_m4": round(I, 2),
        "mass_per_length_kg_m": round(mbar, 1),
        "f2v_exact_Hz": round(f_exact, 4),
        "f2v_fem_Hz": round(f_fem, 4),
        "ref_selfcheck_rel_err": abs(f_fem - f_exact) / f_exact,
        "blade_rate_Hz": round(ship["rpm"] * ship["blades"] / 60.0, 3),
        "blade_rate_rpm": ship["rpm"],
        "blades": ship["blades"],
    }


SHIPS = [
    dict(class_="Feeder-1000TEU", L=145.0, B=23.0, D=13.0, Cb=0.65,
         displacement_t=18000.0, rpm=140.0, blades=4),
    dict(class_="Panamax-4500TEU", L=230.0, B=32.2, D=19.0, Cb=0.62,
         displacement_t=52000.0, rpm=110.0, blades=4),
    dict(class_="PostPanamax-10000TEU", L=300.0, B=42.8, D=24.0, Cb=0.63,
         displacement_t=110000.0, rpm=90.0, blades=4),
    dict(class_="ULCS-20000TEU", L=399.0, B=58.6, D=30.5, Cb=0.66,
         displacement_t=220000.0, rpm=80.0, blades=4),
]


def build_reference() -> list[dict]:
    out = []
    for s in SHIPS:
        s2 = dict(s)
        s2["class"] = s2.pop("class_")
        out.append(hull_girder_2node(s2))
    return out


# --------------------------------------------------------------------------
# 2. 在线探测：判据 VIB-001/002/003/004
# --------------------------------------------------------------------------
def _post(ship_id: str, timeout: float = 10.0) -> dict:
    url = f"{BASE}{ENDPOINT}?ship_id={urllib.parse.quote(ship_id)}"
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return {"http": r.status, "body": json.loads(r.read().decode())}


def _post_raw(ship_id: str | None, timeout: float = 10.0) -> dict:
    url = BASE + ENDPOINT + ("" if ship_id is None else f"?ship_id={urllib.parse.quote(ship_id)}")
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"http": r.status, "body": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": json.loads(e.read().decode())}
    except Exception as e:  # noqa: BLE001
        return {"http": None, "body": {"error": repr(e)}}


def probe(reference: list[dict]) -> dict:
    ship_ids = [r["class"] for r in reference]
    responses = []
    for sid in ship_ids:
        responses.append({"ship_id": sid, **_post_raw(sid)})

    reachable = all(r["http"] is not None for r in responses)

    # VIB-001 契约：合法输入 200；缺参 422 且 loc 指名 ship_id
    c1 = [{"ship_id": r["ship_id"], "http": r["http"], "pass": r["http"] == 200} for r in responses]
    missing = _post_raw(None)
    loc = []
    if isinstance(missing["body"], dict):
        for item in missing["body"].get("detail", []) or []:
            loc.extend(item.get("loc", []))
    c_missing = {
        "http": missing["http"],
        "loc": loc,
        "pass": missing["http"] == 422 and "ship_id" in loc,
    }

    # VIB-002 输入依赖性：跨 4 个船级（L 145→399 m，2.75x）响应是否变化
    numeric_fields = []
    if reachable and responses and isinstance(responses[0]["body"], dict):
        for k, v in responses[0]["body"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_fields.append(k)
    spread = {}
    for f in numeric_fields:
        vals = [float(r["body"][f]) for r in responses if isinstance(r["body"], dict) and f in r["body"]]
        if vals:
            lo, hi = min(vals), max(vals)
            denom = max(abs(lo), abs(hi), 1e-12)
            spread[f] = {
                "min": lo,
                "max": hi,
                "rel_range": (hi - lo) / denom,
                "platform_constant": (hi - lo) / denom < INPUT_INDEPENDENCE_TOL,
            }
    all_constant = bool(spread) and all(s["platform_constant"] for s in spread.values())

    # VIB-003 参考量动态范围：平台响应相对基准的可分辨性
    f2v = [r["f2v_exact_Hz"] for r in reference]
    ref_rel_range = (max(f2v) - min(f2v)) / max(f2v)

    # VIB-004 量纲/带宽/测点规格：响应是否声明单位、统计量、频带、位置
    declared = sorted(
        k for k in (responses[0]["body"] if reachable and isinstance(responses[0]["body"], dict) else {})
    )
    spec_keys = set(declared) & {"unit", "units", "band", "band_hz", "location", "rms_or_peak", "statistic"}
    spec_ok = bool(spec_keys)

    verdict = {
        "VIB-001_contract": {
            "criterion": "合法 ship_id 必须 200；缺参必须 422 且 loc 指名 ship_id",
            "legal": c1,
            "missing_param": c_missing,
            "pass": reachable and all(x["pass"] for x in c1) and c_missing["pass"],
        },
        "VIB-002_input_dependence": {
            "criterion": (
                "跨船级响应必须变化：任一数值字段相对变化 >1%"
                f"（{len(ship_ids)} 个船型，L 145→399 m）"
            ),
            "per_field": spread,
            "all_fields_constant": all_constant,
            "pass": reachable and bool(spread) and not all_constant,
        },
        "VIB-003_reference_dynamic_range": {
            "criterion": "基准量 f2v 跨船级相对变化（供对照：真实实现应能分辨）",
            "f2v_min_Hz": min(f2v),
            "f2v_max_Hz": max(f2v),
            "reference_rel_range": ref_rel_range,
            "platform_rel_range": {k: v["rel_range"] for k, v in spread.items()},
            "pass": None,
            "adjudication": "n/a —— 对照量（非平台判据），用于量化平台「与输入无关」的可分辨性缺口",
            "note": "平台无任何频率类输出，共振裕度评估不可达",
        },
        "VIB-004_specification": {
            "criterion": "响应须声明单位/统计量/频带/测点，否则精度判决为 WITHHELD（不得记 PASS）",
            "response_keys": declared,
            "spec_keys_present": sorted(spec_keys),
            "pass": spec_ok,
        },
    }

    # 聚合证据级
    if not reachable:
        level = "WITHHELD"
    elif all_constant:
        level = "MOCK"
    elif verdict["VIB-001_contract"]["pass"] and verdict["VIB-002_input_dependence"]["pass"]:
        level = "REAL" if verdict["VIB-004_specification"]["pass"] else "PARTIAL"
    else:
        level = "STUB"

    return {
        "endpoint": BASE + ENDPOINT,
        "probed_at": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "reachable": reachable,
        "responses": responses,
        "verdicts": verdict,
        "evidence_level": level,
    }


# --------------------------------------------------------------------------
# 3. 工件写出
# --------------------------------------------------------------------------
def _fmt(x, nd=4):
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def write_artifacts(ref: list[dict], live: dict | None) -> str:
    os.makedirs(ART, exist_ok=True)
    payload = {"reference": ref, "live": live}
    path = os.path.join(ART, "vibration_gate.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    lines = ["<!-- 本表由 tools/ref_vibration_8001.py 程序化生成，禁止手改 -->", ""]
    lines += ["### 表 1 船体梁两节点垂向固有频率参考值（REAL：闭式精确解 + FEM 独立复核）", ""]
    lines.append(
        "| 船级 | L [m] | B [m] | D [m] | Cb | Δ [t] | W_min [m³] | I [m⁴] | "
        "f2v 解析 [Hz] | f2v FEM [Hz] | 自检相对偏差 | 叶频 f_b [Hz] |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in ref:
        lines.append(
            f"| {r['class']} | {_fmt(r['L'],1)} | {_fmt(r['B'],1)} | {_fmt(r['D'],1)} | "
            f"{_fmt(r['Cb'],2)} | {_fmt(r['displacement_t'],0)} | {_fmt(r['W_min_m3'],2)} | "
            f"{_fmt(r['I_m4'],1)} | {_fmt(r['f2v_exact_Hz'],4)} | {_fmt(r['f2v_fem_Hz'],4)} | "
            f"{r['ref_selfcheck_rel_err']:.2e} | {_fmt(r['blade_rate_Hz'],3)} |"
        )

    lines += ["", "### 表 2 在线端点实测响应（同一端点，4 个船级）", ""]
    if live and live["reachable"]:
        keys = [k for k in live["responses"][0]["body"]]
        lines.append("| ship_id | " + " | ".join(keys) + " |")
        lines.append("|---" * (len(keys) + 1) + "|")
        for r in live["responses"]:
            lines.append(f"| {r['ship_id']} | " + " | ".join(str(r['body'].get(k)) for k in keys) + " |")
    else:
        lines.append("平台不可达（WITHHELD）—— 不得记为 PASS。")

    lines += ["", "### 表 3 判据判决矩阵", ""]
    if live:
        v = live["verdicts"]
        lines.append("| 判据 | 内容 | 结论 |")
        lines.append("|---|---|---|")
        lines.append(f"| VIB-001 契约 | 合法 200 / 缺参 422 指名 | {'PASS' if v['VIB-001_contract']['pass'] else 'FAIL'} |")
        lines.append(
            f"| VIB-002 输入依赖性 | 跨 4 船级数值字段相对变化 >1% | "
            f"{'PASS' if v['VIB-002_input_dependence']['pass'] else 'FAIL'} |"
        )
        lines.append(
            f"| VIB-003 基准动态范围 | f2v 真实变化 {v['VIB-003_reference_dynamic_range']['reference_rel_range']*100:.1f}% 对照 | 见行 |"
        )
        lines.append(
            f"| VIB-004 规格声明 | 单位/统计量/频带/测点 | "
            f"{'PASS' if v['VIB-004_specification']['pass'] else 'FAIL（精度判决 WITHHELD）'} |"
        )
        lines.append(f"| 聚合证据级 | — | **{live['evidence_level']}** |")
        lines.append("")
        lines.append("| 数值字段 | 跨船级 min | max | 相对变化 | 与输入无关 |")
        lines.append("|---|---|---|---|---|")
        for k, s in v["VIB-002_input_dependence"]["per_field"].items():
            lines.append(
                f"| {k} | {_fmt(s['min'],4)} | {_fmt(s['max'],4)} | {s['rel_range']*100:.4f}% | "
                f"{'是' if s['platform_constant'] else '否'} |"
            )
    with open(os.path.join(ART, "tables.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="只算参考基准，不联网探测")
    args = ap.parse_args(argv)

    ref = build_reference()
    max_err = max(r["ref_selfcheck_rel_err"] for r in ref)
    print(f"[ref] f2v 解析 vs FEM 最大相对偏差 = {max_err:.2e}")
    for r in ref:
        print(
            f"[ref] {r['class']:<22} L={r['L']:.0f} f2v={r['f2v_exact_Hz']:.4f} Hz  "
            f"f_b={r['blade_rate_Hz']:.3f} Hz  I={r['I_m4']:.1f} m^4"
        )

    live = None
    if not args.offline:
        try:
            live = probe(ref)
            print(f"[live] reachable={live['reachable']} level={live['evidence_level']}")
            for name, v in live["verdicts"].items():
                print(f"[live] {name:<32} pass={v.get('pass')}")
        except Exception as e:  # noqa: BLE001
            print(f"[live] 探测失败（记 WITHHELD，不得记 PASS）：{e!r}", file=sys.stderr)

    path = write_artifacts(ref, live)
    print(f"[out] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())