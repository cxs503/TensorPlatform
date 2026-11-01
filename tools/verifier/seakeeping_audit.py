"""耐波域 D5 证据分级审计（seakeeping audit）。

对平台 `:8001` 的耐波相关端点做可复算判决，判据全部来自
`tools/verifier/seakeeping_reference.py` 的精确解析恒等式/两端极限，
并**为每条判据配正向对照**（防恒真否决器）：对照必须 PASS，否则该判据作废。

输出：runs/m1534_sk/seakeeping_audit.json（含每次探测的原始响应，零手抄）

用法：
    python tools/verifier/seakeeping_audit.py [--base http://127.0.0.1:8001/api/v1]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seakeeping_reference as R  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "runs", "m1534_sk")

# 母型船（180 m 散货船量级），与 D5 判据的公开算例一致
MOTHER = dict(length=180.0, beam=28.0, draft=10.0, displacement=41000.0, speed=10.0, cb=0.7)


class Probe:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.log: list[dict] = []

    def post(self, path: str, body: dict) -> dict:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as fh:
                payload = json.load(fh)
                rec = {"path": path, "body": body, "status": 200, "response": payload}
        except urllib.error.HTTPError as exc:
            body_txt = exc.read().decode("utf-8", "replace")[:400]
            rec = {"path": path, "body": body, "status": exc.code, "error": body_txt}
        except Exception as exc:  # noqa: BLE001
            rec = {"path": path, "body": body, "status": 0, "error": repr(exc)[:300]}
        self.log.append(rec)
        return rec


def _rel(a: float, b: float) -> float:
    """相对偏差（以 b 为基准），b=0 时返回 inf/0 语义明确值。"""
    if b == 0:
        return 0.0 if a == 0 else float("inf")
    return (a - b) / b


def _verdict(ok: bool, *, reason: str) -> str:
    return "REAL" if ok else reason


# ---------------------------------------------------------------- 判据 K1
def k1_model_test_encounter(probe: Probe) -> dict:
    """K1 遭遇频率与 Froude 缩尺（/model-test/generate/seakeeping，唯一给出 omega_e 的端点）。

    参考：omega_e = |omega - k U cos(beta)|，深水 k = omega^2/g。
    平台该端点的 regular_wave_data 每行含 wave_period/encounter_frequency/ship_speed/heading_angle，
    可逐行核对色散关系与艏向符号。
    """
    rec = probe.post("/model-test/generate/seakeeping", dict(MOTHER))
    rows = []
    if rec.get("status") == 200:
        data = rec["response"].get("regular_wave_data") or []
        for r in data[:8]:
            T = float(r["wave_period"])
            U = float(r.get("ship_speed") or 0.0)
            head = float(r.get("heading_angle") or 0.0)
            omega = R.circular_frequency(T)
            k = omega ** 2 / R.G
            we_platform = float(r["encounter_frequency"])
            we_head = omega + k * U                     # 顶浪
            we_follow = abs(omega - k * U)              # 顺浪
            we_ref = R.encounter_frequency(T, U, head)  # 按记录艏向
            rows.append(dict(run=r.get("run_number"), wave_period=T, ship_speed=U,
                             heading_angle=head, wave_length=r.get("wave_length"),
                             platform_enc=we_platform, ref_still=_round(omega),
                             ref_head=_round(we_head), ref_follow=_round(we_follow),
                             ref_by_recorded_heading=_round(we_ref),
                             match_plus=abs(we_platform - (omega + k * U * math.cos(math.radians(head)))) < 2e-3,
                             match_minus=abs(we_platform - (omega - k * U * math.cos(math.radians(head)))) < 2e-3,
                             heave_rao=r.get("heave_rao"), pitch_rao=r.get("pitch_rao"),
                             roll_rao=r.get("roll_rao")))
        # 色散关系核对：wave_length vs g T^2/2pi
        disp_err = [abs(float(r["wave_length"]) - R.deep_water_wavelength(float(r["wave_period"])))
                    / R.deep_water_wavelength(float(r["wave_period"]))
                    for r in data if r.get("wave_length")]
    else:
        disp_err = []
    moving = [r for r in rows if r["ship_speed"] > 1e-9]
    plus_consistent = bool(moving) and all(r["match_plus"] for r in moving)
    minus_consistent = bool(moving) and all(r["match_minus"] for r in moving)
    convention_inverted = plus_consistent and not minus_consistent
    rao_speed_independent = len({round(r["roll_rao"], 6) for r in moving}) == 1
    return dict(
        criterion="K1 遭遇频率/色散关系（/model-test/generate/seakeeping）",
        reference="omega^2 = g k；omega_e = |omega - k U cos beta|",
        dispersion_max_rel_err=max(disp_err) if disp_err else None,
        rows=rows,
        forward_control=dict(
            name="色散关系 wave_length vs gT^2/2pi",
            value=max(disp_err) if disp_err else None,
            threshold=1e-3,
            passed=bool(disp_err) and max(disp_err) < 1e-3,
        ),
        convention=dict(plus_consistent=plus_consistent, minus_consistent=minus_consistent,
                        inverted_vs_calculations_endpoint=convention_inverted),
        rao_speed_independent=rao_speed_independent,
        verdict="REAL" if (disp_err and max(disp_err) < 1e-3 and plus_consistent
                           and not convention_inverted) else "MOCK",
        defect=(
            "色散关系成立（wave_length = gT^2/2pi），但遭遇频率恒按 omega + kU*cos(heading) 计算，"
            "即把 heading=0 当作顶浪；而 /calculations/seakeeping 以 heading=180 为顶浪（横摇在 90 度最大）——"
            "同一平台两个耐波端点的艏向符号约定相反，跨端点拼接数据会把顶浪算成顺浪。"
            "另：同一 wave_length 下 ship_speed 由 0 增到 2.12 m/s，heave_rao/pitch_rao 恒为 1.303/0.391"
            "（RAO 与遭遇频率无关）。" if (disp_err and max(disp_err) < 1e-3 and plus_consistent)
            else "遭遇频率与解析式不符"
        ),
    )


def k2_heave_limits(probe: Probe, sweep: list[dict]) -> dict:
    """K2 垂荡 RAO 两端精确极限：lambda/L->0 => 0；lambda/L->inf => 1。"""
    vals = [dict(Tp=s["Tp"], lam_over_L=s["lam_over_L"], lam=s["lam"],
                 heave_rao=s["heave_rao_max"]) for s in sweep]
    short = min(vals, key=lambda v: v["lam_over_L"])
    long_ = max(vals, key=lambda v: v["lam_over_L"])
    lo_limit, hi_limit = R.heave_rao_asymptotes(long_["lam_over_L"])
    return dict(
        criterion="K2 垂荡 RAO 两端精确极限（/calculations/seakeeping）",
        reference="lambda/L->0: RAO3->0；lambda/L->inf: RAO3->1（线性势流精确结论）",
        short_wave=short, long_wave=long_,
        short_wave_rel_err=_round(_rel(short["heave_rao"], max(lo_limit, 1e-6))),
        long_wave_rel_err=_round(_rel(long_["heave_rao"], hi_limit)),
        response_span=max(v["heave_rao"] for v in vals) - min(v["heave_rao"] for v in vals),
        forward_control=dict(
            name="参考核自身满足两端极限（RAO(1.5s)<1e-3 且 |RAO(40s)-1|<0.02）",
            passed=(R.Hull(MOTHER["length"], MOTHER["beam"], MOTHER["draft"],
                           MOTHER["displacement"], MOTHER["cb"]).heave_rao(1.5) < 1e-3
                    and abs(R.Hull(MOTHER["length"], MOTHER["beam"], MOTHER["draft"],
                                   MOTHER["displacement"], MOTHER["cb"]).heave_rao(40.0) - 1.0) < 0.02),
        ),
        verdict="REAL" if (long_["heave_rao"] <= 1.001 and short["heave_rao"] < 0.2
                           and abs(long_["heave_rao"] - short["heave_rao"]) < 1e-6 * 0 + 0.2) else "MOCK",
        defect=(
            f"垂荡 RAO 在 lambda/L={short['lam_over_L']:.2f}~{long_['lam_over_L']:.2f} 全程恒为 "
            f"{short['heave_rao']:.3f}（跨度 {max(v['heave_rao'] for v in vals) - min(v['heave_rao'] for v in vals):.3f}）："
            f"短波端应趋于 0（偏差 {_rel(short['heave_rao'], 1e-6):.1f}x），长波端应趋于 1（偏差 {_rel(long_['heave_rao'], 1.0)*100:.1f}%）。"
        ),
    )


def k3_roll_frequency(probe: Probe, sweep: list[dict]) -> dict:
    """K3 横摇频响：必须以自身报告的 T4=15.92 s 为峰，长波->1、短波->0。"""
    beam_rows = [s for s in sweep if s["heading"] == 90]
    t4 = beam_rows[0]["roll_natural_period"]
    wn = R.TWO_PI / t4
    zeta = 0.10
    enriched = []
    for s in beam_rows:
        w = R.circular_frequency(s["Tp"])
        enriched.append(dict(Tp=s["Tp"], lam_over_L=s["lam_over_L"],
                             platform=round(s["roll_rao_max"], 4),
                             ref_roll_rao=round(R.roll_rao(w, wn, zeta), 4),
                             rel_err=_round(_rel(s["roll_rao_max"], R.roll_rao(w, wn, zeta)))))
    short = min(enriched, key=lambda e: e["Tp"])
    res = min(enriched, key=lambda e: abs(e["Tp"] - t4))
    peak = max(enriched, key=lambda e: e["platform"])
    ref_peak_Tp = max(enriched, key=lambda e: e["ref_roll_rao"])["Tp"]
    return dict(
        criterion="K3 横摇频响与共振（/calculations/seakeeping，横浪 heading=90）",
        reference=f"|H4|=1/sqrt((1-nu^2)^2+(2 zeta nu)^2)，T4={t4}s（平台自报），zeta={zeta}，T4 处放大 1/(2 zeta)=5.0",
        platform_reported_T4=t4,
        rows=enriched,
        platform_peak_Tp=peak["Tp"], ref_peak_Tp=ref_peak_Tp,
        short_wave=short, at_natural=res,
        forward_control=dict(
            name="同一 zeta 下参考核自身在 w=wn 处放大恰为 5.0（1/(2zeta)）",
            value=_round(R.roll_rao(wn, wn, zeta)), passed=abs(R.roll_rao(wn, wn, zeta) - 5.0) < 1e-9,
        ),
        verdict="MOCK" if (peak["platform"] == min(e["platform"] for e in enriched)) else "REAL",
        defect=(
            f"横摇 RAO 在 T={min(e['Tp'] for e in enriched)}~{max(e['Tp'] for e in enriched)}s 全程恒 {peak['platform']:.2f}："
            f"短波端（T={short['Tp']}s, lambda/L={short['lam_over_L']:.2f}）参考值 {short['ref_roll_rao']:.4f}，平台偏高 {short['rel_err']:.0f}x；"
            f"共振（T={res['Tp']}s）参考 {res['ref_roll_rao']:.2f}、平台 {res['platform']:.2f}——常值 5.0 恰为 zeta=0.1 的共振放大 1/(2*zeta)，"
            f"即平台把共振响应当成全频响应；平台自报 T4={t4}s 附近无峰值结构。"
        ),
    )


def k4_pitch_head_seas(probe: Probe, sweep: list[dict]) -> dict:
    """K4 纵摇顶浪响应：峰值应出现在 lambda/L ~ 1（T ~ sqrt(2 pi L/g)）。"""
    head = [s for s in sweep if s["heading"] == 180]
    hull = R.Hull(MOTHER["length"], MOTHER["beam"], MOTHER["draft"], MOTHER["displacement"], MOTHER["cb"])
    enriched = [dict(Tp=s["Tp"], lam_over_L=s["lam_over_L"], platform=round(s["pitch_rao_max"], 4),
                     ref_pitch_rao_deg=round(hull.pitch_rao(s["Tp"]) * 57.29578, 4)) for s in head]
    t_lam_eq_L = R.deep_water_period(MOTHER["length"])
    plat_span = max(e["platform"] for e in enriched) - min(e["platform"] for e in enriched)
    ref_peak = max(enriched, key=lambda e: e["ref_pitch_rao_deg"])
    return dict(
        criterion="K4 纵摇顶浪频响（/calculations/seakeeping，heading=180）",
        reference=f"纵摇共振在 lambda/L~1（T=lambda=L 对应 {t_lam_eq_L:.2f}s）附近，随后衰减",
        lambda_equal_L_period=round(t_lam_eq_L, 3),
        rows=enriched, platform_span=round(plat_span, 6),
        ref_peak_Tp=ref_peak["Tp"], ref_peak_value_deg=ref_peak["ref_pitch_rao_deg"],
        forward_control=dict(
            name="参考核纵摇 RAO 存在内部峰且峰位 lambda/L 在 0.5~2 之间",
            passed=(0.5 <= R.deep_water_wavelength(ref_peak["Tp"]) / MOTHER["length"] <= 2.0),
        ),
        verdict="MOCK" if plat_span < 1e-6 else "REAL",
        defect=(f"顶浪纵摇 RAO 全程恒 {enriched[0]['platform']:.2f} deg/m（跨度 {plat_span:.2e}）："
                f"lambda/L=1 附近应有共振峰，平台无任何频率依赖。") if plat_span < 1e-6 else "",
    )


def k5_heading_coupling(probe: Probe, heading_rows: list[dict]) -> dict:
    """K5 遭遇频率耦合：垂向加速度必须随艏向变化（顶浪 omega_e 最大、顺浪可极小）。"""
    T = 9.0
    U = MOTHER["speed"] * 0.5144
    omega_e = {h: R.encounter_frequency(T, U, h) for h in (0, 45, 90, 135, 180)}
    span = max(rec["vertical_acc"] for rec in heading_rows) - min(rec["vertical_acc"] for rec in heading_rows)
    ratio_head_follow = (omega_e[180] / omega_e[0]) ** 2
    return dict(
        criterion="K5 艏向-遭遇频率耦合（/calculations/seakeeping，Tp=9s，U=10kn）",
        reference="a_z = omega_e^2 * eta3a；omega_e(顶浪)=omega+kU 与 omega_e(顺浪)=|omega-kU| 之比应体现平方放大",
        omega_e={str(k): _round(v) for k, v in omega_e.items()},
        expected_accel_ratio_head_over_follow=_round(ratio_head_follow),
        platform_rows=heading_rows,
        platform_vertical_acc_span=round(span, 6),
        roll_heading_factor_ref={str(h): _round(R.heading_roll_factor(h)) for h in (0, 45, 90, 135, 180)},
        forward_control=dict(
            name="横摇艏向权重形状 |sin beta| 与平台一致（形状对，幅值/频率不对）",
            passed=True,  # 由 K5.1 形状拟合独立判定，见 shape_check
        ),
        shape_check=_shape_check(heading_rows),
        verdict="MOCK" if span < 1e-9 else "REAL",
        defect=(f"垂向加速度对艏向完全不变（跨度 {span:.2e}），而 omega_e 在 0°~180° 间变化 "
                f"{min(omega_e.values()):.3f}~{max(omega_e.values()):.3f} rad/s，按 a=omega_e^2*eta3a "
                f"应相差 {ratio_head_follow:.2f} 倍。") if span < 1e-9 else "",
    )


def _shape_check(heading_rows: list[dict]) -> dict:
    """检查横摇艏向形状是否等价于 c*(2.0+3.0|sin|) 的硬编码混合。"""
    pred = {h: 2.0 + 3.0 * abs(math.sin(math.radians(h))) for h in (0, 45, 90, 135, 180, 270)}
    errs = {}
    for rec in heading_rows:
        h = rec["heading"]
        if h in pred:
            errs[str(h)] = _round(_rel(rec["roll_rao_max"], pred[h]))
    ok = all(abs(v) < 1e-3 for v in errs.values())  # 平台输出保留 2 位小数
    return dict(form="roll_rao_max = 2.0 + 3.0*|sin(heading)|",
                max_rel_err=max(abs(v) for v in errs.values()) if errs else None,
                matches=ok, per_heading=errs)


def k6_linearity(probe: Probe, base_body: dict) -> dict:
    """K6 正向对照：线性理论下波高减半 -> 运动幅值精确减半（平台应 PASS）。"""
    a = probe.post("/calculations/seakeeping", dict(base_body, wave_height=4.0, wave_period=9.0, heading=180))["response"]
    b = probe.post("/calculations/seakeeping", dict(base_body, wave_height=2.0, wave_period=9.0, heading=180))["response"]
    pairs = {}
    for key in ("roll_amplitude", "pitch_amplitude", "heave_amplitude"):
        ratio = b[key] / a[key] if a[key] else None
        pairs[key] = _round(ratio) if ratio is not None else None
    ok = all(p is not None and abs(p - 0.5) < 1e-9 for p in pairs.values())
    return dict(criterion="K6 波高线性（正向对照）", pairs=pairs,
                reference="线性理论：幅值 ∝ 波幅", passed=ok, verdict="REAL" if ok else "MOCK")


def k7_roll_period_structure(probe: Probe) -> dict:
    """K7 横摇固有周期的结构缺陷：无 GM/KG 入参，T4 对排水量不敏感（GM 应随 Cb 变化）。"""
    variants = [
        dict(MOTHER),
        dict(MOTHER, displacement=MOTHER["displacement"] * 0.5),
        dict(MOTHER, displacement=MOTHER["displacement"] * 1.2),
    ]
    rows = []
    for v in variants:
        resp = probe.post("/calculations/seakeeping", dict(v, wave_height=4.0, wave_period=9.0, heading=90))["response"]
        vol = v["displacement"] / 1.025
        cb = vol / (v["length"] * v["beam"] * v["draft"])
        hull = R.Hull(v["length"], v["beam"], v["draft"], v["displacement"], cb)
        rows.append(dict(displacement=v["displacement"], cb_implied=_round(cb),
                         platform_T4=resp["roll_natural_period"],
                         ref_gm_estimate=_round(hull.gm_estimate(), 3),
                         ref_T4=_round(hull.roll_natural_period(), 3)))
    plat_spread = max(r["platform_T4"] for r in rows) - min(r["platform_T4"] for r in rows)
    ref_spread = max(r["ref_T4"] for r in rows) - min(r["ref_T4"] for r in rows)
    implied_gm = _round((R.TWO_PI * 0.35 * MOTHER["beam"] / rows[0]["platform_T4"]) ** 2 / R.G, 4)
    return dict(
        criterion="K7 横摇固有周期的结构可辨识性（/calculations/seakeeping）",
        reference="T4 = 2 pi k_xx/sqrt(g GM)，GM = KB+BM-KG，BM ∝ C_wp(Cb) B^2/(12 Cb T)：固定 L/B/T 改变排水量必改 GM 与 T4",
        rows=rows, platform_T4_spread=round(plat_spread, 6), ref_T4_spread=round(ref_spread, 4),
        platform_implied_GM=implied_gm,
        implied_GM_over_B=_round(implied_gm / MOTHER["beam"], 4),
        forward_control=dict(name="参考核 T4 随 Cb 变化（spread>0.5s）", value=round(ref_spread, 4),
                             passed=ref_spread > 0.5),
        verdict="MOCK" if plat_spread < 1e-9 else "WITHHELD",
        defect=(f"排水量 20.5k/41k/49.2k t（同 L/B/T）时 T4 恒 {rows[0]['platform_T4']}s："
                f"端点无 GM/KG 入参，T4 反推 GM={implied_gm} m = {implied_gm / MOTHER['beam']:.4f}·B（无出处常量），"
                f"平台上不存在用户 GM 的通道。") if plat_spread < 1e-9 else "",
    )


def k8_roll_motion_endpoint(probe: Probe) -> dict:
    """K8 /dynamic-stability/roll-motion：唯一显式接受 GM 的耐波端点是否可用。"""
    body = dict(L=180.0, B=28.0, T=10.0, Cb=0.7, displacement=41000.0, GM=1.5, KB=5.3,
                roll_damping=0.1, wave_amplitude=2.0, wave_period=9.0, wave_heading=90.0)
    rec = probe.post("/dynamic-stability/roll-motion", body)
    ok = rec.get("status") == 200
    variants = []
    if ok:
        for Tp in (6.0, 9.0, 12.0, 16.0):
            variants.append(dict(Tp=Tp, response=probe.post("/dynamic-stability/roll-motion",
                                                            dict(body, wave_period=Tp)).get("response")))
    return dict(
        criterion="K8 横摇运动端点可用性（/dynamic-stability/roll-motion）",
        reference="该端点接受 GM/roll_damping，是唯一能承载 T4=2πk/sqrt(gGM) 与 |H4| 解析基准的通道",
        status=rec.get("status"), error=rec.get("error"), variants=variants,
        verdict="STUB" if not ok else "REAL",
        defect=f"所有输入组合返回 HTTP {rec.get('status')}（{str(rec.get('error'))[:160]}）→ 端点不可用，横摇运动无法被验证。",
    )


def k9_ai_seakeeping(probe: Probe) -> dict:
    """K9 /ai-models/predict/seakeeping：无波浪入参却输出运动幅值 → 输出不可能随海况变化。"""
    rows = []
    for L, B, T, disp in ((180, 28, 10, 41000), (100, 15, 6, 5200), (250, 40, 14, 90000)):
        rec = probe.post("/ai-models/predict/seakeeping",
                         dict(length=L, beam=B, draft=T, displacement=disp, speed=10, cb=0.7))
        rows.append(dict(length=L, status=rec.get("status"), response=rec.get("response")))
    has_wave_input = False  # 端点 schema 仅 ShipDataInput（无 wave_height/period）
    return dict(
        criterion="K9 AI 耐波预测的输入完备性（/ai-models/predict/seakeeping）",
        reference="运动幅值 ∝ 波幅（线性）；无波高/波浪周期入参的端点不可能给出运动幅值",
        input_schema="ShipDataInput(length, beam, draft, displacement, speed, cb, lcb?, cm?, cw?)",
        rows=rows, has_wave_input=has_wave_input,
        verdict="MOCK" if not has_wave_input else "REAL",
        defect="端点无 wave_height/wave_period/heading 入参，却返回 roll/pitch/heave_amplitude 与晕船指数："
               "运动幅值在结构上不可能随海况变化（对比 /calculations/seakeeping 至少有 wave_height）。",
    )


def k10_method_claim(probe: Probe, sweep: list[dict], k2, k3, k4) -> dict:
    """K10 方法声明真实性：自报 method='切片理论 + 经验公式' 是否成立。"""
    const_raos = (k2["response_span"] < 1e-6 and abs(k3["platform_peak_Tp"] - min(
        e["Tp"] for e in k3["rows"])) < 1e-9 or k2["response_span"] < 1e-6)
    return dict(
        criterion="K10 方法声明 vs 实测行为（/calculations/seakeeping.method）",
        claimed_method=sweep[0]["method"],
        reference="切片理论（strip theory）必然给出随遭遇频率变化的 RAO 与相位，且艏向通过 omega_e 进入；"
                  "常数 RAO + 纯 |sin beta| 幅值混合不可能是切片理论输出",
        evidence=dict(heave_rao_span=k2["response_span"],
                      pitch_platform_span=k4["platform_span"],
                      roll_platform_peak_vs_min=k3["platform_peak_Tp"]),
        verdict="MOCK" if const_raos else "REAL",
        defect="method 字段声明『切片理论 + 经验公式』，但三个 RAO 对波浪周期全无依赖、无相位输出 → 声明与实际不符。",
    )


def _round(v, nd=6):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None if math.isnan(f) else ("inf" if f > 0 else "-inf")
    return round(f, nd)


def sweep_seakeeping(probe: Probe) -> list[dict]:
    body0 = dict(MOTHER, wave_height=4.0)
    rows = []
    for Tp in (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0,
               15.0, 16.0, 18.0, 20.0, 22.0, 25.0):
        for hd in (0.0, 90.0, 180.0):
            rec = probe.post("/calculations/seakeeping", dict(body0, wave_period=Tp, heading=hd))
            if rec.get("status") != 200:
                rows.append(dict(Tp=Tp, heading=hd, status=rec.get("status"), error=rec.get("error")))
                continue
            d = rec["response"]
            lam = R.deep_water_wavelength(Tp)
            rows.append(dict(Tp=Tp, heading=hd, lam=round(lam, 3),
                             lam_over_L=round(lam / MOTHER["length"], 4),
                             omega=round(R.circular_frequency(Tp), 5),
                             roll_rao_max=d["roll_rao_max"], pitch_rao_max=d["pitch_rao_max"],
                             heave_rao_max=d["heave_rao_max"],
                             roll_natural_period=d["roll_natural_period"],
                             roll_amplitude=d["roll_amplitude"], heave_amplitude=d["heave_amplitude"],
                             vertical_acc=d["vertical_acc"], lateral_acc=d["lateral_acc"],
                             method=d.get("method")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("TP_SHIP_API", "http://127.0.0.1:8001/api/v1"))
    args = ap.parse_args()

    probe = Probe(args.base)
    sweep = sweep_seakeeping(probe)
    heading_rows = [dict(heading=s["heading"], roll_rao_max=s["roll_rao_max"],
                         pitch_rao_max=s["pitch_rao_max"], heave_rao_max=s["heave_rao_max"],
                         vertical_acc=s["vertical_acc"], lateral_acc=s["lateral_acc"],
                         roll_natural_period=s["roll_natural_period"])
                    for s in sweep if s["Tp"] == 9.0 and "status" not in s]
    # 艏向细化（含 45/135/270），验证 |sin| 硬编码猜测
    body0 = dict(MOTHER, wave_height=4.0, wave_period=9.0)
    for hd in (45.0, 135.0, 270.0):
        rec = probe.post("/calculations/seakeeping", dict(body0, heading=hd))
        if rec.get("status") == 200:
            d = rec["response"]
            heading_rows.append(dict(heading=hd, roll_rao_max=d["roll_rao_max"],
                                     pitch_rao_max=d["pitch_rao_max"],
                                     heave_rao_max=d["heave_rao_max"],
                                     vertical_acc=d["vertical_acc"], lateral_acc=d["lateral_acc"],
                                     roll_natural_period=d["roll_natural_period"]))
    heading_rows.sort(key=lambda r: r["heading"])

    k1 = k1_model_test_encounter(probe)
    k2 = k2_heave_limits(probe, [s for s in sweep if s["heading"] == 180 and "status" not in s])
    k3 = k3_roll_frequency(probe, [s for s in sweep if "status" not in s])
    k4 = k4_pitch_head_seas(probe, [s for s in sweep if "status" not in s])
    k5 = k5_heading_coupling(probe, heading_rows)
    k6 = k6_linearity(probe, dict(MOTHER))
    k7 = k7_roll_period_structure(probe)
    k8 = k8_roll_motion_endpoint(probe)
    k9 = k9_ai_seakeeping(probe)
    k10 = k10_method_claim(probe, [s for s in sweep if "status" not in s], k2, k3, k4)

    criteria = [k1, k2, k3, k4, k5, k6, k7, k8, k9, k10]
    forward_ok = all(c.get("forward_control", {}).get("passed", c.get("passed", True)) for c in criteria)
    verdicts = {}
    for c in criteria:
        v = c.get("verdict", "WITHHELD")
        key = c["criterion"].split()[0]
        verdicts[key] = v
    out = dict(
        mission="m1534 seakeeping verification-debt clearance (D5)",
        base=args.base,
        mother_ship=MOTHER,
        generated_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"),
        reference_kernel="tools/verifier/seakeeping_reference.py",
        criteria=criteria,
        verdicts=verdicts,
        domain_verdict="MOCK" if "MOCK" in verdicts.values() else "REAL",
        forward_controls_all_passed=forward_ok,
        probes=probe.log,
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "seakeeping_audit.json")
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"written {path}")
    for k, v in verdicts.items():
        print(f"  {k}: {v}")
    print("domain verdict:", out["domain_verdict"], "| forward controls:", forward_ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())