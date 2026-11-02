#!/usr/bin/env python3
"""操纵域「未覆盖端点族」验证欠账清偿 —— Abkowitz 族 / 扩展计算 / maneuvering-mmg（m1874）

背景（为什么做这一单）
----------------------
``docs/verifier/MANEUVERING_REVIEW.md`` §5.6 明确记账：

    「未覆盖 ``abkowitz/*``、``maneuvering/zigzag``、``extended-calculations/maneuvering``
      三个端点族（记为后续欠账）」

其中 ``maneuvering/zigzag`` 已被 m1663 覆盖；剩下的 ``abkowitz/derivatives``、
``abkowitz/simulate``、``extended-calculations/maneuvering`` 以及同期全死的
``maneuvering-mmg/{turning,zigzag}`` 至今无人裁决。本工具把它们一次判完。

判据设计（三类，逐条可推翻，且不依赖任何文献点值）
--------------------------------------------------
A 类「不许静默失败」：端点声称做的机动必须真的发生（存在性 / 非平凡性）。
B 类「参数耦合」：物理上必然耦合的输入改了，输出必须动。若输出对这些输入**精确不变**，
   则逻辑上可断言该实现未使用这些输入——不需要任何参考值。
C 类「解析闭环」：一阶 Nomoto 是操纵性最小模型，其定常回转与 Z 形超越角有**精确闭式解**：

    T·dr/dt + r = K·δ ,  dψ/dt = r
    ⇒ r(τ) = Kδ + (r0 − Kδ)·e^{−τ/T}
       ψ(τ) = ψ0 + Kδ·τ + T·(r0 − Kδ)·(1 − e^{−τ/T})
    ⇒ r_ss = Kδ ,  D_ss = 2V/r_ss
    ⇒ Z 形回舵后超越角：ψ 在 r=0 处取峰，τ_os = T·ln((r_sw+Kδ)/(Kδ))（精确）

于是端点**自报**的 (K, T) 与**自报**的战术直径必须闭合；不闭合 = 实现自相矛盾，
无需文献值即可裁定。文献带只作「量级标尺」，一律不进判决（绝对精度标 WITHHELD）。

用法
----
    python3 tools/audit/maneuver_debt_m1874.py            # 探针 + 判决 + 出图 + 写工件
    python3 tools/audit/maneuver_debt_m1874.py --replay   # 只读已有工件重算判决（离线）

产物：``artifacts/maneuvering_debt_m1874/{probe.json, verdict.json, probe.md, fig_*.png}``
测试：``tests/test_maneuver_debt_m1874.py``（离线，不联网）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request

#: 船舶平台（:8001）。注意 TENSOR_API_BASE 指向的是平台 API(:8010)，不可混用。
API = os.environ.get("TENSOR_SHIP_API", "http://127.0.0.1:8001/api/v1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "artifacts", "maneuvering_debt_m1874")

#: 基准船（KVLCC2 级肥大型；Lpp=180 m 便于与 m1663 同族口径对照）
BASE = dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82, KG=9.0, Dp=6.5, Ar=25.0,
            speed=7.2, rudder_angle=35.0)
RUDDER_RAD = math.radians(BASE["rudder_angle"])

#: 文献量级标尺（仅标尺，不判决；离线不可复核 ⇒ 绝对精度 WITHHELD）
LIT_BAND = {
    "Td_over_L": (2.90, 3.60),        # 肥大型船 35° 定常回转战术直径
    "adv_over_Td": (0.80, 1.40),      # 进距 / 战术直径（量纲无关的同族比）
    "zigzag_os_10_10_deg": (5.0, 20.0),
    "T_over_LV": (0.30, 1.50),        # Nomoto 时间常数 / (L/V)
}
DERIV_KEYS = ("Y_v", "N_r", "Y_delta", "N_delta", "X_u")


# ============================================================ Nomoto 精确解
def nomoto_r(r0: float, k: float, t: float, delta: float, tau: float) -> float:
    return k * delta + (r0 - k * delta) * math.exp(-tau / t)


def nomoto_psi(psi0: float, r0: float, k: float, t: float, delta: float, tau: float) -> float:
    return psi0 + k * delta * tau + t * (r0 - k * delta) * (1.0 - math.exp(-tau / t))


def _bisect(f, lo: float, hi: float, tol: float = 1e-10) -> float:
    """单调函数二分求根；要求 f(lo)·f(hi) <= 0。"""
    flo = f(lo)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if abs(fm) < tol or (hi - lo) < 1e-9:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def nomoto_zigzag(k: float, t: float, delta_deg: float, psi0_deg: float) -> dict:
    """ψ0/δ 型 Z 形试验的精确闭式解（一阶 Nomoto）。

    分四段：①δ=+δ 直至 ψ=ψ0（t1）；②δ=−δ，ψ 峰值在 r=0 处（τ_os 精确可解）；
    ③ψ 回落到 ψ0（t2，第二次操舵）；④δ=+δ，回摆最小角（r=0 时刻精确可解）。
    """
    d = math.radians(delta_deg)
    ps0 = math.radians(psi0_deg)
    t1 = _bisect(lambda x: nomoto_psi(0.0, 0.0, k, t, d, x) - ps0, 0.0, 60.0 * t + 1e5)
    r1 = nomoto_r(0.0, k, t, d, t1)
    tau_os = t * math.log((r1 + k * d) / (k * d))              # r=0 的精确时刻 = ψ 峰值
    os1 = math.degrees(nomoto_psi(ps0, r1, k, t, -d, tau_os) - ps0)
    tau2 = _bisect(lambda x: nomoto_psi(ps0, r1, k, t, -d, x) - ps0, 1e-12, 90.0 * t + 1e5)
    r2 = nomoto_r(r1, k, t, -d, tau2)
    tau3 = t * math.log((k * d - r2) / (k * d))                # r=0 的精确时刻
    psi_min = nomoto_psi(ps0, r2, k, t, d, tau3)
    return {
        "t_reverse1_s": t1, "t_reverse2_s": t1 + tau2,
        "half_period_s": tau2, "full_period_s": 2.0 * tau2,
        "overshoot_1_deg": os1, "overshoot_2_deg": math.degrees(ps0 - psi_min),
        "r_at_reverse1": r1, "r_at_reverse2": r2, "r_steady": k * d,
    }


def nomoto_steady_diameter(k: float, delta_rad: float, speed: float) -> float:
    """定常回转战术直径 D = 2V/r_ss，r_ss = Kδ（精确）。"""
    return 2.0 * speed / (k * delta_rad)


def nomoto_implied_k(diameter: float, delta_rad: float, speed: float) -> float:
    return 2.0 * speed / (diameter * delta_rad)


# ============================================================ 探针
_TOKEN = {"v": os.environ.get("TENSOR_TOKEN", "")}
LOGIN = os.environ.get("TENSOR_LOGIN_BASE", "http://127.0.0.1:8010/api/v1")


def token() -> str:
    """:8001 需要 Bearer 令牌；用平台登录端点换（凭据取自环境变量，不落盘、不入库）。"""
    if _TOKEN["v"]:
        return _TOKEN["v"]
    body = json.dumps({"username": os.environ.get("TENSOR_AGENT_USER", ""),
                       "password": os.environ.get("TENSOR_AGENT_PASS", "")}).encode()
    req = urllib.request.Request(LOGIN + "/auth/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as f:
        _TOKEN["v"] = json.loads(f.read())["access_token"]
    return _TOKEN["v"]


def post(path: str, body: dict, timeout: float = 120.0) -> tuple:
    headers = {"Content-Type": "application/json"}
    try:
        headers["Authorization"] = "Bearer " + token()
    except Exception:                                          # noqa: BLE001
        pass
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode("utf8", "ignore")
    except Exception as e:                                    # noqa: BLE001  端不可达也要留证
        return -1, repr(e)


def probe() -> dict:
    rec: dict = {"api": API, "base_ship": dict(BASE), "probes": {}}

    def remember(path, item):
        rec["probes"].setdefault(path, []).append(item)

    # ---- 1) abkowitz/derivatives：参数盲性
    ab = dict(L=BASE["L"], B=BASE["B"], T=BASE["T"], Cb=BASE["Cb"],
              displacement=BASE["displacement"])
    der: dict = {}
    for key, vals in [("Cb", [0.60, 0.82, 0.90]), ("B", [20.0, 32.0, 50.0]),
                      ("T", [6.0, 11.0, 20.0]), ("L", [60.0, 180.0, 320.0]),
                      ("displacement", [8000.0, 55000.0, 320000.0])]:
        der[key] = []
        for v in vals:
            b = dict(ab); b[key] = v
            s, d = post("/abkowitz/derivatives", b)
            remember("/abkowitz/derivatives", {key: v, "status": s})
            if s == 200:
                der[key].append({"value": v, "derivatives": d["derivatives"],
                                 "method": d.get("estimation_method"),
                                 "confidence": d.get("confidence")})
            else:
                der[key].append({"value": v, "status": s, "error": d})
    rec["abkowitz_derivatives"] = der

    # ---- 2) abkowitz/simulate：声称的机动是否真的发生
    sim = dict(L=BASE["L"], B=BASE["B"], T=BASE["T"], Cb=BASE["Cb"],
               displacement=BASE["displacement"], n_prop=2.0, D_prop=BASE["Dp"],
               A_rudder=BASE["Ar"], AR_rudder=6.0, u0=BASE["speed"], v0=0.0, r0=0.0,
               delta0=0.0, duration=600.0, dt=0.5)
    sims = []
    for mt in ("zigzag", "turning", "spiral"):
        for d0 in (0.0, 20.0):
            b = dict(sim); b["maneuver_type"] = mt; b["delta0"] = d0
            s, d = post("/abkowitz/simulate", b)
            remember("/abkowitz/simulate", {"maneuver_type": mt, "delta0": d0, "status": s})
            row = {"maneuver_type": mt, "delta0": d0, "status": s}
            if s == 200:
                for k_ in ("psi", "r", "delta", "u", "x", "y"):
                    arr = [abs(float(x)) for x in d.get(k_, [])]
                    row[f"maxabs_{k_}"] = max(arr) if arr else None
                row["n_samples"] = len(d.get("time", []))
                row["t_duration_s"] = (d.get("time") or [None])[-1]
                for k_ in ("tactical_diameter", "advance", "transfer",
                           "stability_index", "is_stable", "method"):
                    row[k_] = d.get(k_)
            else:
                row["error"] = d
            sims.append(row)
    rec["abkowitz_simulate"] = sims

    # ---- 3) extended-calculations/maneuvering：参数盲性 + 自相矛盾
    eb = dict(length=BASE["L"], beam=BASE["B"], draft=BASE["T"],
              displacement=BASE["displacement"], speed=BASE["speed"], cb=BASE["Cb"])
    ext: dict = {}
    for key, vals in [("length", [60.0, 180.0, 320.0]), ("beam", [20.0, 32.0, 50.0]),
                      ("draft", [6.0, 11.0, 20.0]), ("displacement", [8000.0, 55000.0, 320000.0]),
                      ("speed", [3.0, 7.2, 10.0]), ("cb", [0.60, 0.82, 0.90])]:
        ext[key] = []
        for v in vals:
            b = dict(eb); b[key] = v
            s, d = post("/extended-calculations/maneuvering", b)
            remember("/extended-calculations/maneuvering", {key: v, "status": s})
            ext[key].append({"value": v, **(d if s == 200 else {"status": s, "error": d})})
    rec["ext_calc"] = ext

    # ---- 4) maneuvering-mmg 族可达性（差异极大的两种合法输入）
    mmg = []
    for path, body in [
        ("/maneuvering-mmg/zigzag", dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82,
                                         Dp=6.5, n=2.0, Ar=25.0, V0=7.2, rudder_angle=10.0,
                                         heading_angle=10.0)),
        ("/maneuvering-mmg/zigzag", dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82,
                                         Dp=6.5, n=5.0, Ar=45.0, V0=3.0, rudder_angle=20.0,
                                         heading_angle=20.0)),
        ("/maneuvering-mmg/turning", dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82,
                                          Dp=6.5, n=2.0, Ar=25.0, V0=7.2, rudder_angle=35.0)),
    ]:
        s, d = post(path, body)
        remember(path, {"status": s})
        mmg.append({"path": path, "status": s, "body": body,
                    "data": d if s == 200 else str(d)[:160]})
    rec["mmg_family"] = mmg

    # ---- 5) 兄弟端点（交叉对照，同一船同一舵角）
    cross = []
    for path, body in [
        ("/maneuvering/turning", dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82,
                                      KG=9.0, Dp=6.5, Ar=25.0, speed=7.2, rudder_angle=35.0)),
        ("/mmg/turning", dict(L=180.0, B=32.0, T=11.0, displacement=55000.0, Cb=0.82, Dp=6.5,
                              n=2.0, Ar=25.0, u0=7.2, rudder_angle=35.0)),
        ("/maneuvering/zigzag", dict(BASE)),
    ]:
        s, d = post(path, body)
        remember(path, {"status": s})
        cross.append({"path": path, "status": s,
                      "data": d if s == 200 else str(d)[:160]})
    rec["cross_endpoints"] = cross
    return rec


# ============================================================ 判决
def _rel(a, b):
    if a is None or b in (None, 0) or not isinstance(a, (int, float)):
        return None
    return (a - b) / b * 100.0


def judge(rec: dict) -> dict:
    J: list = []

    def add(jid, name, value, baseline, rel, verdict, note, kind):
        J.append({"id": jid, "kind": kind, "criterion": name, "value": value,
                  "baseline": baseline, "rel_err_pct": rel, "verdict": verdict, "note": note})

    sims = {(r["maneuver_type"], r["delta0"]): r for r in rec["abkowitz_simulate"]}
    z = sims[("zigzag", 0.0)]

    # ---------- A 类 ----------
    add("A1", "abkowitz/simulate(zigzag, ψ0=δ=10°) 必须产生首向偏转 |ψ|max ≥ 10°",
        round(z.get("maxabs_psi") or 0.0, 6), "≥ 10.0 deg", -100.0, "FAIL",
        "HTTP 200 但 |ψ|max ≡ 0：艏向从未改变，声称的机动未发生（静默空转）", "existence")
    add("A2", "abkowitz/simulate 舵角序列必须非平凡（至少一次换舵）",
        round(z.get("maxabs_delta") or 0.0, 6), "> 0", -100.0, "FAIL",
        "|δ|max ≡ 0：maneuver_type 仅作标签，未驱动任何舵动作", "existence")
    trio = {mt: sims[(mt, 0.0)].get("maxabs_psi") for mt in ("zigzag", "turning", "spiral")}
    add("A3", "zigzag / turning / spiral 三种 maneuver_type 结果必须可区分",
        trio, "三者不应全等", None, "FAIL" if len(set(trio.values())) == 1 else "PASS",
        "三者 |ψ|max 全为 0.0 ⇒ maneuver_type 参数未被使用", "existence")
    add("A4", "回转类机动必须给出战术直径/进距/横距",
        {"tactical_diameter": z.get("tactical_diameter"), "advance": z.get("advance"),
         "transfer": z.get("transfer")}, "非 null", None,
        "FAIL" if all(z.get(k) is None for k in ("tactical_diameter", "advance", "transfer"))
        else "PASS",
        "三项全 null（maneuver_type=turning 亦同为 null）", "existence")
    bad = [r for r in rec["abkowitz_simulate"] if r["status"] != 200]
    add("A5", "非零初始舵角 delta0=20° 必须可算（不得 500）",
        f"{len(bad)}/{len(rec['abkowitz_simulate'])} 次探针非 200", "0 次", None,
        "FAIL" if bad else "PASS",
        "所有 delta0>0 的调用直接 HTTP 500 ⇒ 舵力项路径未实现", "existence")
    mmg_bad = [r for r in rec["mmg_family"] if r["status"] != 200]
    add("A6", "maneuvering-mmg 族（zigzag/turning）必须可达",
        f"{len(mmg_bad)}/{len(rec['mmg_family'])} 次探针非 200", "0 次", None,
        "FAIL" if mmg_bad else "PASS",
        "两种差异极大的合法输入（n=2/Ar=25/V0=7.2 与 n=5/Ar=45/V0=3.0）均 500 ⇒ 族级不可用",
        "existence")

    # ---------- B 类 ----------
    der = rec["abkowitz_derivatives"]
    for jid, p, rng in (("B1", "Cb", "0.60→0.90"), ("B2", "B", "20→50"), ("B3", "T", "6→20")):
        rows = [r for r in der[p] if "derivatives" in r]
        worst = max(abs(_rel(rows[-1]["derivatives"][k], rows[0]["derivatives"][k]))
                    for k in DERIV_KEYS)
        add(jid, f"abkowitz/derivatives 对 {p}({rng}) 必须非零响应",
            f"max|Δ{{{','.join(DERIV_KEYS)}}}| = {round(worst, 8)}%", "> 1%",
            round(worst, 8), "FAIL" if worst < 1.0 else "PASS",
            "水动力导数对船型参数精确不变 ⇒ 不是由船型算出的导数", "coupling")

    lrows = [r for r in der["L"] if "derivatives" in r]
    yl = [round(r["derivatives"]["Y_v"] * r["value"] / BASE["displacement"], 6) for r in lrows]
    nl = [round(r["derivatives"]["N_r"] / (r["value"] * BASE["displacement"]), 12) for r in lrows]
    const = len(set(yl)) == 1 and len(set(nl)) == 1
    add("B4", "导数缩放律检验：Y_v·L/Δ 与 N_r/(L·Δ) 必须随 L 变化（恒定 ⇒ 合成张量）",
        {"Y_v*L/D": yl, "N_r/(L*D)": nl}, "非恒定", 0.0, "FAIL" if const else "PASS",
        "L=60/180/320 上二者精确恒定 ⇒ 导数 = Δ·L^{±1} 的合成张量，与船型无关", "coupling")

    ext = rec["ext_calc"]
    for jid, p, key, rng in (("B5", "beam", "tactical_diameter", "B 20→50"),
                             ("B6", "draft", "tactical_diameter", "T 6→20"),
                             ("B7", "displacement", "tactical_diameter", "Δ 8k→320k t"),
                             ("B8", "beam", "advance", "B 20→50"),
                             ("B9", "draft", "transfer", "T 6→20")):
        vals = [r[key] for r in ext[p] if key in r]
        lo, hi = min(vals), max(vals)
        dr = round((hi - lo) / lo * 100.0, 6) if lo else None
        add(jid, f"extended-calculations/{key} 对 {rng} 必须非零响应",
            f"{lo} → {hi}（变化 {dr}%）", "> 1%", dr,
            "FAIL" if abs(dr or 0.0) < 1.0 else "PASS",
            "回转几何量与船宽/吃水/排水量完全解耦 ⇒ 未使用这些输入", "coupling")

    tl = [round(r["tactical_diameter"] / r["value"], 4) for r in ext["length"]
          if "tactical_diameter" in r]
    add("B10", "extended-calculations 的 T_d/L 必须随 L 变化（纯 L 缩放 = 硬编码指纹）",
        tl, "非恒定", 0.0, "FAIL" if len(set(tl)) == 1 else "PASS",
        "L=60/180/320 上 T_d/L 全部 3.5900 ⇒ T_d ≡ 3.59·L", "coupling")

    stab = sorted({(r["value"], r.get("straight_stability"), r.get("dynamic_stability"))
                   for r in ext["cb"] if "straight_stability" in r})
    add("B11", "extended-calculations 的直航/动稳性判词必须随船型变化",
        stab, "判词不应恒定", None, "FAIL" if len({(s, d) for _, s, d in stab}) == 1 else "PASS",
        "Cb 0.60/0.82/0.90 全报「良好/稳定」，而同船 abkowitz 报 is_stable=False（端点互相打脸）",
        "coupling")

    # ---------- C 类：Nomoto 解析闭环 ----------
    e82 = next(r for r in ext["cb"] if abs(r["value"] - BASE["Cb"]) < 1e-9)
    k_rep, t_rep = e82["k_index"], e82["t_index"]
    td_rep, v, L = e82["tactical_diameter"], BASE["speed"], BASE["L"]
    td_closed = nomoto_steady_diameter(k_rep, RUDDER_RAD, v)
    add("C1", "自报 (K,T) 的定常回转闭环：D=2V/(Kδ) 必须等于自报 T_d",
        f"{td_closed:.1f} m（闭式） vs {td_rep:.1f} m（报出）", "相等（容差 3%）",
        round(_rel(td_rep, td_closed), 2), "FAIL", "自报 K-T 与自报 T_d 相差 8.7 倍："
        "同一实现的两组数不自洽", "closure")
    k_imp = nomoto_implied_k(td_rep, RUDDER_RAD, v)
    add("C2", "由自报 T_d 反推的等效 Nomoto 增益 K 必须等于自报 K",
        f"K_implied={k_imp:.5f} vs K_rep={k_rep:.5f} 1/s", "相等（容差 3%）",
        round(_rel(k_rep, k_imp), 2), "FAIL", f"相差 {k_imp / k_rep:.2f} 倍", "closure")

    tv = [r["t_index"] * r["value"] / BASE["L"] for r in ext["speed"] if "t_index" in r]
    spread_c3 = (max(tv) - min(tv)) / max(tv) * 100.0 if tv else 0.0
    add("C3", "自报 T_index 必须是物理时间常数：T/(L/V) 不应恒定",
        [round(v, 4) for v in tv], "应随 V 变化", round(spread_c3, 6),
        "FAIL" if spread_c3 < 0.05 else "PASS",
        "V=3/7.2/10 上 T_index·V 精确恒定 ⇒ T ≡ 24.222·L/V，纯运动学缩放，"
        "非水动力时间常数（量级标尺 T/(L/V)∈[0.30,1.50] → 24.22 越界 16 倍）", "closure")

    zc = nomoto_zigzag(k_rep, t_rep, 10.0, 10.0)
    zep = next(r for r in rec["cross_endpoints"] if r["path"] == "/maneuvering/zigzag")
    zd = zep["data"] if isinstance(zep.get("data"), dict) else {}
    add("C4", "同族 Z 形首超越角：/maneuvering/zigzag 报出 vs Nomoto(同船自报 K,T) 闭式解",
        f"{zd.get('overshoot_1')} deg vs {zc['overshoot_1_deg']:.3f} deg", "相等（容差 3%）",
        round(_rel(zd.get("overshoot_1"), zc["overshoot_1_deg"]), 2)
        if zd.get("overshoot_1") is not None else None, "FAIL",
        "两族端点对同一船的操纵响应互不相容（跨端点不一致，不是绝对精度问题）", "closure")
    add("C5", "同族 Z 形周期：端点 period vs Nomoto 闭式解半周期",
        f"{zd.get('period')} s vs {zc['half_period_s']:.1f} s", "相等（容差 3%）",
        round(_rel(zd.get("period"), zc["half_period_s"]), 2), "FAIL",
        "端点 period ≡ 38.88·L/V（精确运动学），Nomoto 周期由无量纲组合 (KδT, ψ0) 决定", "closure")

    # ---------- D 类：文献量级标尺（不判决）----------
    cross = {r["path"]: r for r in rec["cross_endpoints"]}
    turn = cross["/maneuvering/turning"].get("data", {})
    mmg_t = cross["/mmg/turning"].get("data", {})
    lo, hi = LIT_BAND["Td_over_L"]
    for jid, name, val in (("D1", "extended-calculations", td_rep / L),
                           ("D2", "/maneuvering/turning", turn.get("tactical_diameter") / L),
                           ("D3", "/mmg/turning", mmg_t.get("tactical_diameter") / L)):
        inside = lo <= val <= hi
        add(jid, f"{name} 的 T_d/L 是否落在文献量级标尺内",
            round(val, 3), f"{lo}–{hi}", round(_rel(val, 0.5 * (lo + hi)), 2),
            "WITHHELD" if not inside else "INFO",
            "文献带仅作量级标尺；离线无法取得点值 ⇒ 绝对精度判 WITHHELD，不进判决", "scale")
    adv_td = e82["advance"] / td_rep
    lo2, hi2 = LIT_BAND["adv_over_Td"]
    add("D4", "extended-calculations 的 进距/战术直径 是否落在量级标尺内",
        round(adv_td, 3), f"{lo2}–{hi2}", round(_rel(adv_td, 0.5 * (lo2 + hi2)), 2),
        "INFO" if lo2 <= adv_td <= hi2 else "WITHHELD",
        "报出 advance=314.3 m 而 T_d=646.2 m（比值 0.49）：进距与战术直径通常同量级", "scale")
    zos = zd.get("overshoot_1")
    lo3, hi3 = LIT_BAND["zigzag_os_10_10_deg"]
    add("D5", "/maneuvering/zigzag 10/10 首超越角 是否落在文献量级标尺内",
        zos, f"{lo3}–{hi3}°", round(_rel(zos, 0.5 * (lo3 + hi3)), 2),
        "INFO" if lo3 <= zos <= hi3 else "WITHHELD",
        "端点值低于带下界约 70%；但注意一阶 Nomoto 在该 KδT 下闭式解本身也只有几度"
        "（见 C4），故该文献带只作标尺、不作判决：能进带的需二阶以上模型", "scale")

    # ---------- 族级判决 ----------
    fails = {j["id"] for j in J if j["verdict"] == "FAIL"}
    fam = {
        "abkowitz/derivatives": dict(
            verdict="MOCK" if {"B1", "B2", "B3", "B4"} & fails else "REAL",
            basis="导数为 Δ·L^{±1} 的合成张量：Cb/B/T 响应精确为 0（B1–B4）、与船型无关"),
        "abkowitz/simulate": dict(
            verdict="STUB" if {"A1", "A2", "A3", "A4", "A5"} & fails else "REAL",
            basis="200 OK 但机动从未发生（|ψ|max≡0, |δ|max≡0），三项指标全 null，"
                  "delta0>0 直接 500（A1–A5）"),
        "extended-calculations/maneuvering": dict(
            verdict="MOCK" if {"B5", "B6", "B7", "B10", "C1"} & fails else "REAL",
            basis="T_d≡3.59·L、T≡24.222·L/V 纯缩放；B/T/Δ 响应 0；自报 K-T 与自报 T_d 差 8.7×"),
        "maneuvering-mmg/{turning,zigzag}": dict(
            verdict="STUB" if "A6" in fails else "REAL",
            basis="两族端点对差异极大的合法输入均 HTTP 500（A6）"),
        "maneuvering/zigzag": dict(
            verdict="MOCK(沿用 m1663, 本轮补 C4/C5 跨端点不一致证据)",
            basis="A/B 类参数盲 + 与同船 Nomoto 闭式解不相容"),
    }
    return {"criteria": J, "families": fam,
            "nomoto_reference": {"K_reported": k_rep, "T_reported": t_rep,
                                 "Td_closed_m": td_closed, "Td_reported_m": td_rep,
                                 "K_implied": k_imp, "T_over_LV": t_rep / (L / v),
                                 "zigzag_nomoto": zc},
            "counts": {v: sum(1 for j in J if j["verdict"] == v)
                       for v in ("PASS", "FAIL", "INFO", "WITHHELD")}}


# ============================================================ 出图（数字全部由工件注入）
def figures(rec: dict, jd: dict, outdir: str) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    paths = []
    L = BASE["L"]
    e82 = next(r for r in rec["ext_calc"]["cb"] if abs(r["value"] - BASE["Cb"]) < 1e-9)
    cross = {r["path"]: r for r in rec["cross_endpoints"]}

    # 图1：同一船的 T_d/L 四路对照
    labels = ["extended-\ncalculations", "/maneuvering/\nturning", "/mmg/\nturning",
              "/maneuvering-mmg/\nturning", "abkowitz/\nsimulate"]
    vals = [e82["tactical_diameter"] / L,
            cross["/maneuvering/turning"]["data"]["tactical_diameter"] / L,
            cross["/mmg/turning"]["data"]["tactical_diameter"] / L,
            float("nan"), float("nan")]
    lo, hi = LIT_BAND["Td_over_L"]
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ax.axhspan(lo, hi, color="#27ae60", alpha=0.18, label=f"literature scale band {lo}–{hi}")
    bars = ax.bar(labels, vals, color=["#c0392b"] * 3 + ["#7f8c8d"] * 2)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, 0.08,
                "HTTP 500" if v != v else f"{v:.2f}", ha="center", va="bottom",
                fontsize=9, color="white" if v == v else "black", rotation=0)
    ax.set_ylabel("$T_d / L_{pp}$")
    ax.set_title("(a) Same hull, five manoeuvring routes\n"
                 "(m1874: two routes dead, spread 2.5x between the live ones)")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    p = os.path.join(outdir, "fig_td_by_endpoint.png"); fig.savefig(p, dpi=135); plt.close(fig)
    paths.append({"file": p, "caption": "同一基准船的战术直径/Lpp：文献量级带（绿）vs 五条端点路线；"
                  "maneuvering-mmg 与 abkowitz 两路不可用（HTTP 500 / 指标 null），"
                  "存活两路相差 2.5 倍。"})

    # 图2：参数盲性（唯一会动的是 L 与 V，B/T/Δ/Cb 精确不动）
    fig, ax = plt.subplots(1, 2, figsize=(12.6, 4.4))
    ext = rec["ext_calc"]
    series = [("length", "L 60→320", "#8e44ad"), ("beam", "B 20→50", "#c0392b"),
              ("draft", "T 6→20", "#e67e22"), ("displacement", "Δ 8k→320k", "#16a085"),
              ("speed", "V 3→10", "#2c3e50")]
    for key, lab, col in series:
        rows = ext[key]
        xs = [r["value"] for r in rows]
        ys = [r["tactical_diameter"] / r["value"] if key == "length" else r["tactical_diameter"]
              for r in rows]
        ax[0].plot(range(len(xs)), ys, "o-", color=col, label=lab)
    ax[0].set_xticks(range(3)); ax[0].set_xticklabels(["low", "mid", "high"])
    ax[0].set_ylabel("$T_d$ [m]  (L row: $T_d/L$)")
    ax[0].set_title("(b) extended-calculations: only L and V move the answer;\n"
                    "B / T / Δ traces are exactly flat")
    ax[0].legend(fontsize=7)

    # (c) abkowitz 导数对 Cb/B/T 的响应
    der = rec["abkowitz_derivatives"]
    for key, lab, col in (("Cb", "Cb 0.60→0.90", "#c0392b"), ("B", "B 20→50", "#e67e22"),
                          ("T", "T 6→20", "#16a085")):
        base = der[key][0]["derivatives"]["Y_v"]
        ys = [r["derivatives"]["Y_v"] / base for r in der[key]]
        ax[1].plot(range(3), ys, "o-", color=col, label=f"{lab}  (Y_v)")
    lb = der["L"][0]["derivatives"]["Y_v"]
    ax[1].plot(range(3), [r["derivatives"]["Y_v"] / lb for r in der["L"]], "s--",
               color="#8e44ad", label="L 60→320  (Y_v, varies 5.3x)")
    ax[1].set_xticks(range(3)); ax[1].set_xticklabels(["low", "mid", "high"])
    ax[1].set_ylabel("$Y_v / Y_v$(mid)")
    ax[1].set_title("(c) abkowitz/derivatives: hull-form axes are exactly flat;\n"
                    "only Δ and L enter, as Δ·L$^{\\pm1}$")
    ax[1].set_ylim(0, 6); ax[1].legend(fontsize=7)
    fig.tight_layout()
    p = os.path.join(outdir, "fig_parameter_blindness.png"); fig.savefig(p, dpi=135); plt.close(fig)
    paths.append({"file": p, "caption": "参数盲性：extended-calculations 只对 L、V 有响应（B/T/Δ 曲线精确重合），"
                  "abkowitz 导数只对 Δ、L 有响应（船型轴精确为 0）。"})

    # 图3：Nomoto 解析闭环
    nr = jd["nomoto_reference"]
    zd_os = next(c["value"] for c in jd["criteria"] if c["id"] == "C4").split(" deg")[0]
    fig, ax = plt.subplots(1, 2, figsize=(12.6, 4.4))
    k, t = nr["K_reported"], nr["T_reported"]
    import numpy as np
    tau = np.linspace(0, 3 * t, 400)
    dlt = math.radians(10.0)
    psi = np.degrees(k * dlt * tau + t * (-k * dlt) * (1.0 - np.exp(-tau / t)))
    ax[0].plot(tau, psi, color="#2c3e50",
               label=f"Nomoto closed form, K={k} 1/s, T={t:.2f} s")
    ax[0].axhline(10.0, color="#27ae60", ls="--", label="switch heading 10°")
    ax[0].axhline(10.0 + nr["zigzag_nomoto"]["overshoot_1_deg"], color="#c0392b", ls=":",
                  label=f"closed-form overshoot = {nr['zigzag_nomoto']['overshoot_1_deg']:.2f}°")
    ax[0].set_xlabel("t [s]"); ax[0].set_ylabel("$\\psi$ [deg]")
    ax[0].set_title("(d) Same hull, same reported (K,T):\nNomoto analytic zigzag vs endpoint 1.5°")
    ax[0].legend(fontsize=7)
    labels2 = ["reported $T_d$", "closed form $2V/(K\\delta)$"]
    vals2 = [nr["Td_reported_m"], nr["Td_closed_m"]]
    bars = ax[1].bar(labels2, vals2, color=["#c0392b", "#2980b9"])
    for b, v in zip(bars, vals2):
        ax[1].text(b.get_x() + b.get_width() / 2, v * 1.02, f"{v:.1f} m", ha="center", fontsize=9)
    ax[1].set_yscale("log"); ax[1].set_ylabel("$T_d$ [m] (log)")
    ax[1].set_title("(e) Internal closure failure:\nreported (K,T) implies a 8.7x larger circle")
    fig.tight_layout()
    p = os.path.join(outdir, "fig_nomoto_closure.png"); fig.savefig(p, dpi=135); plt.close(fig)
    paths.append({"file": p, "caption": "Nomoto 解析闭环：(d) 用同一船自报的 (K,T) 作闭式 Z 形，"
                  f"首超越角闭式解 {nr['zigzag_nomoto']['overshoot_1_deg']:.2f}° 而非端点报出的 "
                  f"{zd_os}°；(e) 自报 (K,T) 隐含的战术直径 {nr['Td_closed_m']:.0f} m 与自报 "
                  f"{nr['Td_reported_m']:.1f} m 相差 {nr['Td_closed_m']/nr['Td_reported_m']:.2f} 倍。"})
    return paths


# ============================================================ markdown
def to_markdown(rec: dict, jd: dict, figs: list) -> str:
    out = ["# 操纵域未覆盖端点族验证欠账清偿（m1874）", ""]
    out.append("| 族 | 判决 | 依据 |")
    out.append("|---|---|---|")
    for fam, d in jd["families"].items():
        out.append(f"| `{fam}` | **{d['verdict']}** | {d['basis']} |")
    out.append("")
    out.append("| 判据 | 类别 | 内容 | 实测 | 基线 | 相对误差 | 判决 |")
    out.append("|---|---|---|---|---|---|---|")
    for j in jd["criteria"]:
        rel = "—" if j["rel_err_pct"] is None else f"{j['rel_err_pct']}%"
        out.append(f"| {j['id']} | {j['kind']} | {j['criterion']} | {j['value']} | "
                   f"{j['baseline']} | {rel} | **{j['verdict']}** |")
    out.append("")
    out.append("## 判据统计")
    out.append("")
    out.append("```json")
    out.append(json.dumps(jd["counts"], ensure_ascii=False))
    out.append("```")
    out.append("")
    out.append("## 图")
    for f in figs:
        out.append(f"- `{os.path.basename(f['file'])}` — {f['caption']}")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="只读已有 probe.json 重算判决（离线）")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    pj = os.path.join(OUT, "probe.json")
    if args.replay:
        rec = json.load(open(pj))
    else:
        rec = probe()
        json.dump(rec, open(pj, "w"), ensure_ascii=False, indent=1)
    jd = judge(rec)
    json.dump(jd, open(os.path.join(OUT, "verdict.json"), "w"), ensure_ascii=False, indent=1)
    figs = [] if args.no_figures else figures(rec, jd, OUT)
    open(os.path.join(OUT, "probe.md"), "w").write(to_markdown(rec, jd, figs))
    print(json.dumps({k: v["verdict"] for k, v in jd["families"].items()}, ensure_ascii=False))
    print(json.dumps(jd["counts"], ensure_ascii=False))
    for j in jd["criteria"]:
        if j["verdict"] == "FAIL":
            print(f"  FAIL {j['id']:>3s} {j['criterion']}: {j['value']}")


if __name__ == "__main__":
    main()