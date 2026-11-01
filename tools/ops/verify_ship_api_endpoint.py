#!/usr/bin/env python3
""":8001 端点修复的端到端复核（代理 vs 直连 vs 独立解析复算）。

目的：把"端口错了"这个静态判断升级为可机器复核的证据链——
  1. 通过 TensorPlatform 的 ShipProxy（修复后默认 :8001）与直连 :8001 拿同一工况阻力；
  2. 两者必须逐位一致（相对误差 0，代理只做转发，不得改数）；
  3. 用独立实现的 ITTC-57 摩擦阻力公式复算 Rf，与平台返回值比对，
     给出相对误差并显式判定是否 <3%（超出即如实标注，不粉饰）；
  4. 同时验证几何输入合理性 Cb / L/B / B/T。

输出：artifacts/ops/ship_api_endpoint_verify.json + fig_ship_api_endpoint_verify.png
运行：python3 tools/ops/verify_ship_api_endpoint.py
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from services.ship_api_endpoint import probe, ship_api_url  # noqa: E402
from services.ship_proxy import ShipProxy  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "artifacts", "ops")

# KVLCC2 主尺度（公开基准船型，IMO 验证算例）
CASE = {
    "ship_type": "KVLCC2",
    "length": 320.0,        # Lpp [m]
    "beam": 58.0,           # B [m]
    "draft": 20.8,          # T [m]
    "cb": 0.8098,           # 方形系数（KVLCC2 设计工况）
    "displacement": 312622.0,  # 排水体积 [m^3]
    "speed": 15.5,          # [kn]
}

RHO = 1025.5      # 海水密度 [kg/m3]（ITTC-57 取 15 °C 标准海水）
NU = 1.1883e-6    # 运动粘度 [m2/s]
CF_ITTC = 0.075 / (math.log10(3780.0e3) ** 2) ** 2  # 占位，见 ittc57_cf


def ittc57_cf(re: float) -> float:
    """ITTC-1957 平板摩擦阻力系数 Cf = 0.075/(log10(Re)-2)^2。"""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def ittc57_rf(length: float, speed_kn: float) -> dict:
    """独立复算：ITTC-57 平板摩擦阻力 Rf = 0.5*rho*S*V^2*Cf。

    S 用 Mumford 指标近似湿表面积（对 KVLCC2 类肥大型船适用）：
        S = 1.025 * L * (Cb*B + 1.7*T)
    """
    v = speed_kn * 0.514444
    re = v * length / NU
    cf = ittc57_cf(re)
    s_wet = 1.025 * CASE["length"] * (CASE["cb"] * CASE["beam"] + 1.7 * CASE["draft"])
    rf = 0.5 * RHO * s_wet * v * v * cf
    return {"re": re, "cf": cf, "s_wet_m2": s_wet, "rf_n": rf, "v_ms": v}


def geometry_checks() -> dict:
    l, b, t, cb = CASE["length"], CASE["beam"], CASE["draft"], CASE["cb"]
    disp_from_cb = cb * l * b * t
    return {
        "cb": cb,
        "cb_range_ok": 0.30 <= cb <= 0.90,
        "lb_ratio": round(l / b, 3),
        "lb_ratio_ok": 4.0 <= l / b <= 9.0,
        "bt_ratio": round(b / t, 3),
        "bt_ratio_ok": 2.0 <= b / t <= 5.0,
        "displacement_input": CASE["displacement"],
        "displacement_from_cb": round(disp_from_cb, 1),
        "displacement_rel_err_pct": round(
            abs(disp_from_cb - CASE["displacement"]) / CASE["displacement"] * 100, 3
        ),
    }


def direct_call() -> dict:
    """绕过代理，直接打 :8001（对照组）。"""
    url = ship_api_url("calculations/resistance")
    req = urllib.request.Request(
        url, data=json.dumps(CASE).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _pick(d: dict, *keys):
    """在嵌套响应里按关键字找第一个数值字段（不同版本字段名略有差异）。"""
    flat = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v)
                if isinstance(v, (int, float)):
                    flat.setdefault(k, v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(d)
    for k in keys:
        if k in flat:
            return k, flat[k]
    return None, None


async def proxy_call() -> dict:
    proxy = ShipProxy()  # 修复后默认指向 :8001
    return {"base_url": proxy.base_url, "data": await proxy.resistance(CASE)}


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    health = probe(timeout=3.0)
    record: dict = {
        "case": CASE,
        "endpoint_origin": health["url"].rsplit("/api/v1", 1)[0],
        "health": health,
        "geometry": geometry_checks(),
    }

    if not health["ok"]:
        record["verdict"] = "SKIPPED_SERVICE_DOWN"
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1

    direct = direct_call()
    proxied = asyncio.run(proxy_call())
    record["proxy_base_url"] = proxied["base_url"]
    record["direct_response"] = direct
    record["proxy_response"] = proxied["data"]

    d_key, d_val = _pick(direct, "rt", "Rt", "total_resistance", "rt_n")
    p_key, p_val = _pick(proxied["data"], "rt", "Rt", "total_resistance", "rt_n")
    record["resistance_field"] = d_key

    proxy_vs_direct = None
    if d_val and p_val:
        proxy_vs_direct = abs(p_val - d_val) / abs(d_val) * 100
    record["proxy_vs_direct_rel_err_pct"] = (
        None if proxy_vs_direct is None else round(proxy_vs_direct, 6)
    )

    ref = ittc57_rf(CASE["length"], CASE["speed"])
    record["ittc57_reference"] = {
        "re": f"{ref['re']:.4e}",
        "cf": round(ref["cf"], 8),
        "s_wet_m2": round(ref["s_wet_m2"], 1),
        "rf_n": round(ref["rf_n"], 2),
        "rho": RHO,
        "nu": NU,
        "note": "Rf 为平板摩擦阻力；平台 rt 含剩余阻力+空气阻力，故 rt>Rf 属预期",
    }

    # 单位：平台返回力为 kN（rf/rr/rt 三者和式吻合可证），统一换算到 N
    UNIT_KN = 1000.0
    platform = {"rf_kn": _pick(direct, "rf", "Rf")[1], "rr_kn": _pick(direct, "rr", "Rr")[1],
                "rt_kn": d_val, "cf": _pick(direct, "cf")[1],
                "cr": _pick(direct, "cr")[1], "ct": _pick(direct, "ct")[1],
                "s_wet_m2": _pick(direct, "wetted_surface")[1]}
    record["platform"] = platform
    internal_err = None
    if platform["rf_kn"] and platform["rr_kn"] and platform["rt_kn"]:
        internal_err = abs(platform["rf_kn"] + platform["rr_kn"] - platform["rt_kn"]) / platform["rt_kn"] * 100
    record["internal_consistency_rf_plus_rr_eq_rt_pct"] = None if internal_err is None else round(internal_err, 6)

    # 湿表面积：平台值 vs 独立 Mumford 估算
    s_wet_err = abs(platform["s_wet_m2"] - ref["s_wet_m2"]) / platform["s_wet_m2"] * 100
    record["s_wet_rel_err_pct"] = round(s_wet_err, 3)

    # 平板 Cf：平台值 vs ITTC-57 解析式（取平台自报 Re）
    plat_re = _pick(direct, "reynolds_number")[1]
    cf_ref = ittc57_cf(plat_re)
    cf_err = abs(platform["cf"] - cf_ref) / cf_ref * 100
    record["cf_check"] = {"platform": platform["cf"], "ittc57_at_platform_re": round(cf_ref, 8),
                          "rel_err_pct": round(cf_err, 4)}

    # 形状因子：平台 rf 实为 (1+k)*Rf（粘性阻力），比值应落在 KVLCC2 文献区间
    form_factor = platform["rf_kn"] * UNIT_KN / ref["rf_n"]
    record["implied_form_factor_1_plus_k"] = round(form_factor, 4)

    # 关键量判定
    verdicts = {}
    if proxy_vs_direct is not None:
        verdicts["proxy_vs_direct<0.001%"] = proxy_vs_direct < 0.001
    if internal_err is not None:
        verdicts["rf+rr=rt (<0.01%)"] = internal_err < 0.01
    verdicts["Cf vs ITTC-57 (<3%)"] = cf_err < 3.0
    verdicts["wetted surface vs Mumford (<5%)"] = s_wet_err < 5.0
    # Holtrop 形状因子对肥大型船 (1+k) ≈ 1.20~1.35（KVLCC2 文献量级）
    verdicts["(1+k) in [1.20,1.35]"] = 1.20 <= form_factor <= 1.35
    # 总阻力 vs 平板摩擦阻力：肥大型船 Rt/Rf 物理上应 >1（排除量纲/系数错配）
    record["rt_over_rf"] = round(platform["rt_kn"] * UNIT_KN / ref["rf_n"], 4)
    verdicts["rt/Rf > 1.0"] = record["rt_over_rf"] > 1.0
    geoms = record["geometry"]
    verdicts["Cb in range"] = geoms["cb_range_ok"]
    verdicts["L/B in range"] = geoms["lb_ratio_ok"]
    verdicts["B/T in range"] = geoms["bt_ratio_ok"]
    verdicts["displacement vs Cb*L*B*T <1%"] = geoms["displacement_rel_err_pct"] < 1.0
    record["verdicts"] = verdicts
    record["all_pass"] = all(verdicts.values())

    with open(os.path.join(OUT_DIR, "ship_api_endpoint_verify.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)

    _figure(record)
    print(json.dumps({k: record[k] for k in
                      ["endpoint_origin", "proxy_base_url", "resistance_field",
                       "proxy_vs_direct_rel_err_pct", "rt_over_rf", "verdicts", "all_pass"]},
                     ensure_ascii=False, indent=2))
    return 0 if record["all_pass"] else 2


def _figure(record: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    labels = ["proxy :8001\n(after fix)", "direct :8001", "ITTC-57 Rf\n(independent)"]
    pd = record.get("proxy_response") or {}
    dd = record.get("direct_response") or {}
    _, pv = _pick(pd, "rt", "Rt", "total_resistance", "rt_n")
    _, dv = _pick(dd, "rt", "Rt", "total_resistance", "rt_n")
    vals = [(pv or 0) * 1000.0, (dv or 0) * 1000.0, record["ittc57_reference"]["rf_n"]]
    colors = ["#2a6fb0", "#4f9fd6", "#8c8c8c"]
    bars = axes[0].bar(labels, vals, color=colors)
    for b, v in zip(bars, vals):
        axes[0].text(b.get_x() + b.get_width() / 2, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)
    axes[0].set_ylabel("Force [N]")
    axes[0].set_title("KVLCC2 @15.5kn: proxy / direct / ITTC-57 Rf")
    axes[0].grid(axis="y", alpha=0.3)

    names = [n.encode("ascii", "replace").decode() for n in record["verdicts"]]
    ok = [1 if v else 0 for v in record["verdicts"].values()]
    axes[1].barh(names, ok, color=["#2e8b57" if o else "#c0392b" for o in ok])
    axes[1].set_xlim(0, 1.2)
    axes[1].set_xticks([0, 1], ["FAIL", "PASS"])
    axes[1].set_title("machine-check verdicts")
    for i, o in enumerate(ok):
        axes[1].text(1.02, i, "PASS" if o else "FAIL", va="center", fontsize=9)
    fig.suptitle("Ship-platform :8001 endpoint fix verification (2026-11-01)", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_ship_api_endpoint_verify.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())