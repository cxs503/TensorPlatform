"""机器判据：船舶总体性能物理一致性 conformance（m1490 / 承接 m1473 审计）。

判据分两组
----------
由 FAIL 转 PASS（7 项，对应审计 D1/D2/D3/D5/D6）::

    A-CT         水下回转体总阻力系数 Ct（SUBOFF AFF-8，V=5 m/s）
    S-KB-BOX     矩形浮箱 KB 必须回退解析值 T/2
    S-GZMAX      大倾角 GZ_max 必须与自述 wall-sided 一致（有效域 θ≤40°）
    S-AREA-3040  30–40° GZ 面积同上
    P-ETA-BOUND  敞水效率 η0 ≤ min(0.998, η_致动盘)
    P-KT         隐含推力系数 KT 必须落在 B 系列 O(0.10–0.35)
    P-THRUST     推力平衡 T(1-t)=Rt（跨端点一致性）

不得回归（5 项）::

    R-CF   ITTC-57 摩擦线（双速度比，免湿面积）
    R-RE   雷诺数 Re = V·L/ν
    R-FN   傅汝德数 Fn = V/√(gL)
    S-GM   GM 对 KG 的敏感度 dGM/dKG = -1 且 GM>0.05 m
    P-PD   几何螺距比在 B 系列域内 + 轴功率/扭矩自洽 P_D=2πnQ

附加（不计入 12 项门，报告单列）::

    A-CR-SURF    水面船 Cr 回归合理性（Holtrop 参考 ±30%）
    S-VANISH     消失角必须是 GZ=0 的真实根，且随 KG 变化（D3 后半）

被测对象
--------
``RefTarget``  本仓 ``src/ship_perf`` 的正确物理（判据自检 / 服务侧修复规格）
``LiveTarget`` 在线 :8001 服务（承接前应至少 7 项 FAIL）
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from . import hydrostatics as hs
from . import propeller as pr
from . import resistance as rs
from .constants import ETA0_HARD_CAP, NU_FRESH, NU_SEA, RHO_FRESH, RHO_SEA

KN = 0.5144444  # 1 kn -> m/s


# ---------------------------------------------------------------- 算例


@dataclass
class Case:
    key: str
    name: str
    payload: Dict[str, float]
    note: str = ""


CASE_SUBOFF = Case(
    key="suboff_aff8",
    name="SUBOFF AFF-8 水下回转体 L=4.356 m，V=5.0 m/s（Fn_L=0.765）",
    payload={
        "lwl": 4.356,
        "lpp": 4.356,
        "b": 0.508,
        "t": 0.254,
        "displacement": 0.53,
        "cb": 0.60,
        "cm": 1.0,
        "cwp": 0.80,
        "speed_ms": 5.0,
        "speed_kn": 5.0 / KN,
    },
    note="圆剖面 Cm=1、B/T=2；无自由面兴波 ⇒ Cr_兴波≡0",
)

CASE_BOX_A = Case(
    key="box_a",
    name="矩形浮箱 L=100 B=16 T=16 D=26 Cb=Cm=1，KG=8",
    payload={
        "length": 100.0,
        "beam": 16.0,
        "draft": 16.0,
        "depth": 26.0,
        "cb": 1.0,
        "cm": 1.0,
        "displacement": 100 * 16 * 16 * 1.0 * 1.025,
        "kg": 8.0,
    },
    note="tan45°=T/B ⇒ 舭部未出水；甲板入水角 51.3° ⇒ 0–40° 内 wall-sided 精确",
)

CASE_BOX_B = Case(
    key="box_b",
    name="矩形浮箱 L=100 B=16 T=6 D=10 Cb=Cm=1，KG=6.0 / 5.6",
    payload={
        "length": 100.0,
        "beam": 16.0,
        "draft": 6.0,
        "depth": 10.0,
        "cb": 1.0,
        "cm": 1.0,
        "displacement": 100 * 16 * 6 * 1.0 * 1.025,
        "kg": 6.0,
    },
    note="KG>D/2 ⇒ GZ(90°)<0，消失角为有限根；KG=6.0 与 5.6 应给出不同根",
)

CASE_SHIP = Case(
    key="ship100",
    name="100 m 单桨货船 L=100 B=16 T=6 Cb=0.70 ∇=6720 m³，V=14 kn，D_p=4.0 m P/D=1.0",
    payload={
        "length": 100.0,
        "beam": 16.0,
        "draft": 6.0,
        "depth": 12.0,
        "cb": 0.70,
        "cm": 0.98,
        "displacement": 100 * 16 * 6 * 0.70 * 1.025,
        "kg": 6.0,
        "speed_kn": 14.0,
        "speed_ms": 14.0 * KN,
        "prop_diameter": 4.0,
        "prop_pitch": 4.0,
        "prop_rpm": 127.0,
    },
)


def ship_hull() -> rs.HullForm:
    p = CASE_SHIP.payload
    return rs.HullForm(
        lwl=p["length"],
        lpp=p["length"],
        b=p["beam"],
        t=p["draft"],
        displacement=p["length"] * p["beam"] * p["draft"] * p["cb"],
        cb=p["cb"],
        cm=p["cm"],
    )


def suboff_hull() -> rs.HullForm:
    p = CASE_SUBOFF.payload
    return rs.HullForm(
        lwl=p["lwl"],
        lpp=p["lpp"],
        b=p["b"],
        t=p["t"],
        displacement=p["displacement"],
        cb=p["cb"],
        cm=p["cm"],
    )


# ---------------------------------------------------------------- 结果容器


@dataclass
class CheckResult:
    name: str
    required: bool
    status: str
    title: str
    value: Optional[float] = None
    reference: Optional[float] = None
    deviation_pct: Optional[float] = None
    tolerance_pct: Optional[float] = None
    detail: str = ""
    evidence: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _pct(v: float, ref: float) -> float:
    return 100.0 * (v - ref) / ref if ref else float("nan")


def _mk(
    name: str,
    required: bool,
    title: str,
    ok: bool,
    value: Optional[float],
    reference: Optional[float],
    tol: Optional[float],
    detail: str,
    evidence: Optional[Dict[str, object]] = None,
) -> CheckResult:
    dev = _pct(value, reference) if (value is not None and reference) else None
    return CheckResult(
        name=name,
        required=required,
        status="PASS" if ok else "FAIL",
        title=title,
        value=value,
        reference=reference,
        deviation_pct=dev,
        tolerance_pct=tol,
        detail=detail,
        evidence=evidence or {},
    )


# ---------------------------------------------------------------- Target 抽象


class Target:
    """被测对象适配器。"""

    key = "base"
    label = "base"

    def resistance(self, case: Case, speed_kn: Optional[float] = None) -> Dict[str, object]:
        raise NotImplementedError

    def calculations_resistance(self, case: Case) -> Dict[str, object]:
        raise NotImplementedError

    def stability(self, case: Case, kg: Optional[float] = None) -> Dict[str, object]:
        raise NotImplementedError

    def propulsion(self, case: Case) -> Dict[str, object]:
        raise NotImplementedError


# ---------------------------------------------------------------- Ref 实现


class RefTarget(Target):
    """以 src/ship_perf 的正确物理为被测对象（判据自检 / 修复规格）。"""

    key = "ref"
    label = "src/ship_perf（本仓正确物理实现）"

    def resistance(self, case: Case, speed_kn: Optional[float] = None) -> Dict[str, object]:
        if case is CASE_SUBOFF:
            try:
                p = rs.submerged_body_resistance(
                    suboff_hull(), case.payload["speed_ms"], appendages=rs.SUBOFF_APPENDAGES
                )
            except rs.OutOfDomainError as e:
                return {"rejected": str(e)}
            return {
                "rt": p.rt / 1000.0,
                "rf": p.rf / 1000.0,
                "ct": p.ct,
                "cf": p.cf,
                "cr": p.ct - p.form_factor_1pk * p.cf,
                "re": p.re,
                "fn": p.fn,
            }
        v = (speed_kn * KN) if speed_kn else CASE_SHIP.payload["speed_ms"]
        p = rs.holtrop_surface_resistance(ship_hull(), v)
        return {
            "rt": p.rt / 1000.0,
            "rf": p.rf / 1000.0,
            "ct": p.ct,
            "cf": p.cf,
            "cr": p.ct - p.form_factor_1pk * p.cf - rs.roughness_allowance(ship_hull().lwl),
            "re": p.re,
            "fn": p.fn,
        }

    def calculations_resistance(self, case: Case) -> Dict[str, object]:
        return self.resistance(case)

    def stability(self, case: Case, kg: Optional[float] = None) -> Dict[str, object]:
        p = case.payload
        kgv = p["kg"] if kg is None else kg
        r = hs.evaluate_stability(p["beam"], p["draft"], p["depth"], p["cb"], kgv, p["cm"])
        return {
            "kb": r.kb,
            "bm": r.bm,
            "gm": r.gm,
            "gz_max": r.gz_max,
            "gz_max_angle": r.gz_max_angle,
            "vanishing_angle": r.vanishing_angle,
            "area_0_30": r.area_0_30,
            "area_30_40": r.area_30_40,
            "area_0_40": r.area_0_40,
            "angles": r.angles,
            "gz": r.gz,
        }

    def propulsion(self, case: Case) -> Dict[str, object]:
        p = case.payload
        rp = rs.holtrop_surface_resistance(ship_hull(), p["speed_ms"])
        pt = pr.solve_n_for_thrust(
            rp.rt,
            p["speed_ms"],
            p["prop_diameter"],
            p["cb"],
            z=4,
            pd=p["prop_pitch"] / p["prop_diameter"],
            ae_ao=0.55,
        )
        torque_knm = pt.kq * RHO_SEA * pt.n_rps ** 2 * pt.d ** 5 / 1000.0
        return {
            "thrust": pt.thrust_N / 1000.0,
            "torque": torque_knm,
            "delivered_power": pt.delivered_power_kw,
            "open_water_eff": pt.eta0,
            "n_rps": pt.n_rps,
            "rpm": pt.rpm,
            "d": pt.d,
            "pitch_diameter": pt.pd,
            "thrust_deduction": pt.t,
            "wake_fraction": pt.w,
            "kt": pt.kt,
            "kq": pt.kq,
            "j": pt.j,
            "rt": rp.rt / 1000.0,
        }


# ---------------------------------------------------------------- Live 实现


class LiveTarget(Target):
    """在线 :8001 服务适配器（HTTP，仅标准库）。"""

    key = "live"
    label = "在线 :8001 服务"

    def __init__(self, base: str = "http://127.0.0.1:8001", timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: Dict[str, object]) -> Tuple[int, object]:
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {"detail": str(e)}
            return e.code, payload
        except Exception as e:
            return 0, {"detail": f"transport error: {e}"}

    def _body_resistance(self, case: Case, v_kn: float) -> Dict[str, object]:
        if case is CASE_SUBOFF:
            p = case.payload
            return {
                "Lwl": p["lwl"],
                "Lpp": p["lpp"],
                "B": p["b"],
                "T": p["t"],
                "T_f": p["t"],
                "T_a": p["t"],
                "displacement": p["displacement"],
                "Cb": p["cb"],
                "Cm": p["cm"],
                "Cwp": p["cwp"],
                "speeds": [round(v_kn, 4)],
            }
        p = CASE_SHIP.payload
        return {
            "Lwl": p["length"],
            "Lpp": p["length"],
            "B": p["beam"],
            "T": p["draft"],
            "T_f": p["draft"],
            "T_a": p["draft"],
            "displacement": p["length"] * p["beam"] * p["draft"] * p["cb"],
            "Cb": p["cb"],
            "Cm": p["cm"],
            "Cwp": (1.0 + 2.0 * p["cb"]) / 3.0,
            "speeds": [round(v_kn, 4)],
        }

    def resistance(self, case: Case, speed_kn: Optional[float] = None) -> Dict[str, object]:
        v_kn = speed_kn if speed_kn is not None else (
            CASE_SUBOFF.payload["speed_kn"] if case is CASE_SUBOFF else CASE_SHIP.payload["speed_kn"]
        )
        st, resp = self._post("resistance/predict", self._body_resistance(case, v_kn))
        if st != 200 or isinstance(resp, dict):
            return {"rejected": f"HTTP {st}: {str(resp)[:200]}"}
        pt = resp[0]  # type: ignore[index]
        return {
            "rt": pt.get("total_resistance"),
            "rf": pt.get("friction_resistance"),
            "re": pt.get("reynolds_number"),
            "fn": pt.get("froude_number"),
            "ct": None,
            "cf": None,
        }

    def calculations_resistance(self, case: Case) -> Dict[str, object]:
        if case is CASE_SUBOFF:
            q = CASE_SUBOFF.payload
            body = {
                "length": q["lwl"],
                "beam": q["b"],
                "draft": q["t"],
                "displacement": q["displacement"],
                "speed": round(q["speed_kn"], 4),
                "cb": q["cb"],
                "water_density": RHO_FRESH,
                "water_viscosity": NU_FRESH,
            }
        else:
            p = CASE_SHIP.payload
            body = {
                "length": p["length"],
                "beam": p["beam"],
                "draft": p["draft"],
                "displacement": p["length"] * p["beam"] * p["draft"] * p["cb"],
                "speed": p["speed_kn"],
                "cb": p["cb"],
            }
        st, resp = self._post("calculations/resistance", body)
        if st != 200 or not isinstance(resp, dict) or resp.get("detail"):
            return {"rejected": f"HTTP {st}: {str(resp)[:200]}"}
        return {
            "rt": resp.get("rt"),
            "rf": resp.get("rf"),
            "ct": resp.get("ct"),
            "cf": resp.get("cf"),
            "cr": resp.get("cr"),
            "re": resp.get("reynolds_number"),
            "fn": resp.get("froude_number"),
        }

    def stability(self, case: Case, kg: Optional[float] = None) -> Dict[str, object]:
        p = case.payload
        body = {
            "length": p["length"],
            "beam": p["beam"],
            "draft": p["draft"],
            "depth": p["depth"],
            "displacement": p["displacement"],
            "cb": p["cb"],
            "kg": p["kg"] if kg is None else kg,
        }
        st, resp = self._post("calculations/stability", body)
        if st != 200 or not isinstance(resp, dict) or resp.get("detail"):
            return {"error": f"HTTP {st}: {str(resp)[:200]}"}
        return {
            "kb": resp.get("kb"),
            "bm": resp.get("bm"),
            "gm": resp.get("gm"),
            "gz_max": resp.get("gz_max"),
            "gz_max_angle": resp.get("gz_max_angle"),
            "vanishing_angle": resp.get("vanishing_angle"),
            "area_0_30": resp.get("area_0_30"),
            "area_30_40": resp.get("area_30_40"),
            "area_0_40": resp.get("area_0_40"),
        }

    def gz_curve(self, case: Case, kg: Optional[float] = None) -> Dict[str, object]:
        p = case.payload
        body = {
            "L": p["length"],
            "B": p["beam"],
            "T": p["draft"],
            "D": p["depth"],
            "displacement": p["displacement"],
            "Cb": p["cb"],
            "Cm": p["cm"],
            "KG": p["kg"] if kg is None else kg,
        }
        st, resp = self._post("stability/gz-curve", body)
        if st != 200 or not isinstance(resp, dict):
            return {"angles": [], "gz": [], "error": f"HTTP {st}"}
        return {"angles": resp.get("angles", []), "gz": resp.get("gz_values", [])}

    def propulsion(self, case: Case) -> Dict[str, object]:
        p = CASE_SHIP.payload
        body = {
            "length": p["length"],
            "beam": p["beam"],
            "draft": p["draft"],
            "displacement": p["displacement"],
            "speed": p["speed_kn"],
            "cb": p["cb"],
            "prop_diameter": p["prop_diameter"],
            "prop_pitch": p["prop_pitch"],
            "prop_rpm": p["prop_rpm"],
            "num_blades": 4,
            "blade_area_ratio": 0.55,
        }
        st, resp = self._post("calculations/propulsion", body)
        if st != 200 or not isinstance(resp, dict) or resp.get("detail"):
            return {"error": f"HTTP {st}: {str(resp)[:200]}"}
        return {
            "thrust": resp.get("thrust"),
            "torque": resp.get("torque"),
            "delivered_power": resp.get("delivered_power"),
            "open_water_eff": resp.get("open_water_eff"),
            "n_rps": p["prop_rpm"] / 60.0,
            "rpm": p["prop_rpm"],
            "d": p["prop_diameter"],
            "pitch_diameter": p["prop_pitch"] / p["prop_diameter"],
            "thrust_deduction": resp.get("thrust_deduction"),
            "wake_fraction": resp.get("wake_fraction"),
            "kt": resp.get("kt"),
            "kq": resp.get("kq"),
            "j": resp.get("j"),
            "rt": None,
        }


# ---------------------------------------------------------------- 判据


def _angles_0_40() -> List[float]:
    return [float(a) for a in range(0, 41, 5)]


def chk_a_ct(t: Target) -> CheckResult:
    ref = RefTarget().resistance(CASE_SUBOFF)
    ct_ref = float(ref["ct"])
    tol = 15.0
    ev: Dict[str, object] = {"case": CASE_SUBOFF.name, "ct_ref": ct_ref, "endpoints": {}}
    verdicts: List[bool] = []
    eps = ("resistance/predict",)
    if not isinstance(t, RefTarget):
        eps = ("resistance/predict", "calculations/resistance")
    for ep in eps:
        got = (
            t.resistance(CASE_SUBOFF)
            if ep == "resistance/predict"
            else t.calculations_resistance(CASE_SUBOFF)
        )
        if got.get("rejected"):
            verdicts.append(True)
            ev["endpoints"][ep] = {"explicit_reject": str(got["rejected"])[:160]}
            continue
        try:
            if got.get("ct") is not None:
                ct = float(got["ct"])
            else:
                cf = float(got["cf"]) if got.get("cf") is not None else rs.ittc57_cf(float(got["re"]))
                ct = float(got["rt"]) / float(got["rf"]) * cf
        except (TypeError, ValueError, ZeroDivisionError):
            verdicts.append(False)
            ev["endpoints"][ep] = {"error": "响应字段缺失"}
            continue
        dev = _pct(ct, ct_ref)
        ok = abs(dev) <= tol
        verdicts.append(ok)
        ev["endpoints"][ep] = {"ct": ct, "dev_pct": dev, "pass": ok}
    worst = max(
        (abs(float(v["dev_pct"])) for v in ev["endpoints"].values() if isinstance(v, dict) and "dev_pct" in v),
        default=0.0,
    )
    return _mk(
        "A-CT",
        True,
        "水下回转体 Ct 不得套用越域 Holtrop Cr；(1+k)Cf+Ca+附体 或显式拒绝",
        all(verdicts),
        None,
        None,
        tol,
        f"Ct_ref={ct_ref:.5f}（Ct/Cf={ct_ref / float(ref['cf']):.3f}, Cf={float(ref['cf']):.5f}）；"
        f"实测最大偏差 {worst:.1f}%",
        ev,
    )


def chk_a_cr_surf(t: Target) -> CheckResult:
    ref = RefTarget().resistance(CASE_SHIP)
    cr_ref = float(ref["cr"])
    got = t.resistance(CASE_SHIP) if isinstance(t, RefTarget) else t.calculations_resistance(CASE_SHIP)
    if got.get("rejected"):
        return _mk("A-CR-SURF", False, "水面船 Cr 回归", False, None, cr_ref, 30.0, str(got["rejected"])[:120])
    cr = float(got["cr"]) if got.get("cr") is not None else float("nan")
    dev = _pct(cr, cr_ref)
    return _mk(
        "A-CR-SURF",
        False,
        "水面船 Cr 回归合理性（Fn=0.23, Cb=0.70）",
        abs(dev) <= 30.0,
        cr,
        cr_ref,
        30.0,
        f"Cr_服务={cr:.6f} vs Holtrop 参考 {cr_ref:.6f}",
        {},
    )


def chk_s_kb_box(t: Target) -> CheckResult:
    p = CASE_BOX_A.payload
    kb_ref = p["draft"] / 2.0
    st = t.stability(CASE_BOX_A)
    if st.get("error"):
        return _mk("S-KB-BOX", True, "浮箱 KB=T/2", False, None, kb_ref, 3.0, str(st["error"])[:160])
    kb = st.get("kb")
    if kb is None:
        bm = p["beam"] ** 2 / (12.0 * p["draft"] * p["cb"])
        kb = float(st["gm"]) + p["kg"] - bm
    dev = _pct(float(kb), kb_ref)
    return _mk(
        "S-KB-BOX",
        True,
        "矩形浮箱 KB 回退解析 T/2",
        abs(dev) <= 3.0,
        float(kb),
        kb_ref,
        3.0,
        f"KB_被测={float(kb):.4f} m vs 解析 T/2={kb_ref:.4f} m（偏差 {dev:+.1f}%）",
        {"case": CASE_BOX_A.name, "raw": {k: st.get(k) for k in ("kb", "bm", "gm")}},
    )


def chk_s_gm(t: Target) -> CheckResult:
    kg = CASE_BOX_A.payload["kg"]
    a = t.stability(CASE_BOX_A, kg)
    b = t.stability(CASE_BOX_A, kg + 1.0)
    if a.get("error") or b.get("error"):
        return _mk("S-GM", True, "dGM/dKG=-1", False, None, -1.0, 2.0, "端点错误")
    d = float(b["gm"]) - float(a["gm"])
    dev = _pct(d, -1.0)
    ok = abs(dev) <= 2.0 and float(a["gm"]) > 0.05
    return _mk(
        "S-GM",
        True,
        "GM=KM-KG 结构完整（dGM/dKG=-1 且 GM>0.05 m）",
        ok,
        d,
        -1.0,
        2.0,
        f"GM(KG={kg:.1f})={float(a['gm']):.4f} m，GM(KG={kg + 1:.1f})={float(b['gm']):.4f} m，Δ={d:+.4f} m",
        {},
    )


def chk_s_gzmax(t: Target) -> CheckResult:
    p = CASE_BOX_A.payload
    bm = p["beam"] ** 2 / (12.0 * p["draft"] * p["cb"])
    st = t.stability(CASE_BOX_A)
    if st.get("error"):
        return _mk("S-GZMAX", True, "GZ_max vs wall-sided", False, None, None, 3.0, str(st["error"])[:160])
    gm_ref = hs.kb_from_cb(p["draft"], p["cb"]) + bm - p["kg"]  # 解析静水力，不用被测自报 GM
    gm_api = float(st["gm"])
    ang = _angles_0_40()
    ws = hs.wall_sided_gz(ang, gm_ref, bm)
    gz_ref, a_ref = hs.max_in_band(ang, ws, 40.0)
    gz_api = float(st["gz_max"])
    dev = _pct(gz_api, gz_ref)
    return _mk(
        "S-GZMAX",
        True,
        "GZ_max 与 wall-sided 一致（0–40°，wall-sided 精确域；基准用解析 GM）",
        abs(dev) <= 3.0,
        gz_api,
        gz_ref,
        3.0,
        f"GZ_max_被测={gz_api:.4f} m vs wall-sided {gz_ref:.4f} m@{a_ref:.0f}°（解析 GM={gm_ref:.4f} m，被测自报 {gm_api:.4f} m）",
        {"case": CASE_BOX_A.name, "gz_max_angle": st.get("gz_max_angle"), "gm_api": gm_api, "gm_ref": gm_ref},
    )


def chk_s_area_3040(t: Target) -> CheckResult:
    p = CASE_BOX_A.payload
    bm = p["beam"] ** 2 / (12.0 * p["draft"] * p["cb"])
    st = t.stability(CASE_BOX_A)
    if st.get("error"):
        return _mk("S-AREA-3040", True, "30–40° 面积", False, None, None, 3.0, str(st["error"])[:160])
    gm_ref = hs.kb_from_cb(p["draft"], p["cb"]) + bm - p["kg"]
    ang = _angles_0_40()
    ws = hs.wall_sided_gz(ang, gm_ref, bm)
    area_ref = hs.integrate_area(ang, ws, 30.0, 40.0)
    area_api = float(st["area_30_40"])
    dev = _pct(area_api, area_ref)
    return _mk(
        "S-AREA-3040",
        True,
        "30–40° GZ 面积与 wall-sided 一致（基准用解析 GM）",
        abs(dev) <= 3.0,
        area_api,
        area_ref,
        3.0,
        f"area_30_40_被测={area_api:.5f} m·rad vs wall-sided {area_ref:.5f} m·rad（{dev:+.1f}%，解析 GM={gm_ref:.4f} m）",
        {"gm_api": float(st["gm"]), "gm_ref": gm_ref},
    )


def chk_s_vanish(t: Target) -> CheckResult:
    kg = CASE_BOX_B.payload["kg"]
    a = t.stability(CASE_BOX_B, kg)
    b = t.stability(CASE_BOX_B, kg - 0.4)
    ra = RefTarget().stability(CASE_BOX_B, kg).get("vanishing_angle")
    rb = RefTarget().stability(CASE_BOX_B, kg - 0.4).get("vanishing_angle")
    va, vb = a.get("vanishing_angle"), b.get("vanishing_angle")
    try:
        ok = (
            va is not None
            and vb is not None
            and ra is not None
            and rb is not None
            and abs(float(va) - float(ra)) <= 3.0
            and abs(float(vb) - float(rb)) <= 3.0
            and abs(float(vb) - float(va)) >= 5.0
        )
    except (TypeError, ValueError):
        ok = False
    return _mk(
        "S-VANISH",
        False,
        "消失角为 GZ=0 真实根且随 KG 变化（D3 后半）",
        ok,
        float(va) if va is not None else None,
        float(ra) if ra is not None else None,
        3.0,
        f"φ_v(KG={kg:.1f})={va}°（ref {ra:.2f}°），φ_v(KG={kg - 0.4:.1f})={vb}°（ref {rb:.2f}°）",
        {"case": CASE_BOX_B.name},
    )


def chk_p_eta_bound(t: Target) -> CheckResult:
    pp = t.propulsion(CASE_SHIP)
    if pp.get("error"):
        return _mk("P-ETA-BOUND", True, "η0 ≤ 致动盘上界", False, None, None, None, str(pp["error"])[:160])
    eta = float(pp["open_water_eff"])
    d = float(pp["d"])
    thrust = float(pp["thrust"]) * 1000.0
    w = pp.get("wake_fraction")
    w = pr.wake_fraction(CASE_SHIP.payload["cb"]) if w is None else float(w)
    va = CASE_SHIP.payload["speed_ms"] * (1.0 - w)
    a0 = math.pi * d * d / 4.0
    ct = thrust / (0.5 * RHO_SEA * va * va * a0)
    eta_i = pr.ideal_efficiency(ct)
    bound = min(ETA0_HARD_CAP, eta_i)
    return _mk(
        "P-ETA-BOUND",
        True,
        "敞水效率 η0 ≤ min(0.998, η_致动盘)",
        eta <= bound,
        eta,
        bound,
        None,
        "η0=%.4f vs 上界 %.4f（η_致动盘 C_T=%.3f ⇒ %.4f）" % (eta, bound, ct, eta_i),
        {"J": pp.get("j"), "KT": pp.get("kt")},
    )


def chk_p_kt(t: Target) -> CheckResult:
    pp = t.propulsion(CASE_SHIP)
    if pp.get("error"):
        return _mk("P-KT", True, "KT 落在 B 系列域", False, None, None, None, str(pp["error"])[:160])
    d = float(pp["d"])
    n = float(pp["n_rps"])
    thrust = float(pp["thrust"]) * 1000.0
    kt = thrust / (RHO_SEA * n * n * d ** 4)
    ok = 0.10 <= kt <= 0.35
    return _mk(
        "P-KT",
        True,
        "隐含 KT=thrust/(ρn²D⁴) 落在 B 系列 O(0.10–0.35)",
        ok,
        kt,
        None,
        None,
        f"KT={kt:.4f}（T={thrust / 1000:.2f} kN, n={n:.3f} rps, D={d:.2f} m）"
        + ("" if ok else f"；越域 {kt / 0.35 if kt > 0.35 else 0.10 / max(kt, 1e-9):.1f}×"),
        {"J": pp.get("j")},
    )


def chk_p_thrust(t: Target) -> CheckResult:
    pp = t.propulsion(CASE_SHIP)
    rp = t.resistance(CASE_SHIP)
    if pp.get("error") or rp.get("rejected"):
        return _mk("P-THRUST", True, "T(1-t)=Rt", False, None, None, 5.0, "端点错误")
    rt_kn = float(rp["rt"])
    t_ = pp.get("thrust_deduction")
    t_ = pr.thrust_deduction(CASE_SHIP.payload["cb"]) if t_ is None else float(t_)
    thrust_kn = float(pp["thrust"])
    need = rt_kn / (1.0 - t_)
    dev = _pct(thrust_kn, need)
    return _mk(
        "P-THRUST",
        True,
        "跨端点推力平衡 T(1-t)=Rt",
        abs(dev) <= 5.0,
        thrust_kn,
        need,
        5.0,
        f"T_被测={thrust_kn:.3f} kN vs 需求 Rt/(1-t)={need:.3f} kN（Rt={rt_kn:.3f} kN, t={t_:.3f}）",
        {"Rt_kN": rt_kn, "t": t_},
    )


def chk_p_pd(t: Target) -> CheckResult:
    pp = t.propulsion(CASE_SHIP)
    if pp.get("error"):
        return _mk("P-PD", True, "P/D 与轴功率自洽", False, None, None, 1.0, str(pp["error"])[:160])
    pd = float(pp["pitch_diameter"])
    n = float(pp["n_rps"])
    q = float(pp["torque"])
    p_del = float(pp["delivered_power"])
    p_calc = 2.0 * math.pi * n * q
    dev = _pct(p_del, p_calc)
    ok = (0.6 <= pd <= 1.4) and abs(dev) <= 1.0
    return _mk(
        "P-PD",
        True,
        "P/D∈[0.6,1.4] 且 P_D=2πnQ",
        ok,
        p_del,
        p_calc,
        1.0,
        f"P/D={pd:.3f}；P_D_被测={p_del:.2f} kW vs 2πnQ={p_calc:.2f} kW（{dev:+.2f}%）",
        {},
    )


def chk_r_cf(t: Target) -> CheckResult:
    v1, v2 = 10.0, 14.0
    if isinstance(t, RefTarget):
        rf1 = float(t.resistance(CASE_SHIP, v1)["rf"])
        rf2 = float(t.resistance(CASE_SHIP, v2)["rf"])
        a = t.resistance(CASE_SHIP, v1)
        b = t.resistance(CASE_SHIP, v2)
    else:
        a = t.resistance(CASE_SHIP, v1)
        b = t.resistance(CASE_SHIP, v2)
        if a.get("rejected") or b.get("rejected"):
            return _mk("R-CF", True, "ITTC-57 摩擦线", False, None, None, 2.0, "端点拒绝")
        rf1, rf2 = float(a["rf"]), float(b["rf"])
    cf1 = rs.ittc57_cf(float(a["re"]))
    cf2 = rs.ittc57_cf(float(b["re"]))
    ratio = rf2 / rf1
    ref = (v2 / v1) ** 2 * cf2 / cf1
    dev = _pct(ratio, ref)
    return _mk(
        "R-CF",
        True,
        "ITTC-57 摩擦线（双速度比，免湿面积）",
        abs(dev) <= 2.0,
        ratio,
        ref,
        2.0,
        f"Rf(14kn)/Rf(10kn)={ratio:.4f} vs ITTC-57 预测 {ref:.4f}（{dev:+.2f}%）",
        {},
    )


def chk_r_re(t: Target) -> CheckResult:
    r = t.resistance(CASE_SHIP)
    if r.get("rejected"):
        return _mk("R-RE", True, "Re=V·L/ν", False, None, None, 2.0, "端点拒绝")
    ref = CASE_SHIP.payload["speed_ms"] * CASE_SHIP.payload["length"] / NU_SEA
    dev = _pct(float(r["re"]), ref)
    return _mk(
        "R-RE",
        True,
        "Re = V·L/ν",
        abs(dev) <= 2.0,
        float(r["re"]),
        ref,
        2.0,
        f"Re_被测={float(r['re']):.4e} vs {ref:.4e}（{dev:+.2f}%）",
        {},
    )


def chk_r_fn(t: Target) -> CheckResult:
    r = t.resistance(CASE_SHIP)
    if r.get("rejected"):
        return _mk("R-FN", True, "Fn=V/√(gL)", False, None, None, 2.0, "端点拒绝")
    ref = CASE_SHIP.payload["speed_ms"] / math.sqrt(9.80665 * CASE_SHIP.payload["length"])
    dev = _pct(float(r["fn"]), ref)
    return _mk(
        "R-FN",
        True,
        "Fn = V/√(gL)",
        abs(dev) <= 2.0,
        float(r["fn"]),
        ref,
        2.0,
        f"Fn_被测={float(r['fn']):.5f} vs {ref:.5f}（{dev:+.2f}%）",
        {},
    )


#: 12 项门（7 项须 FAIL→PASS + 5 项回归守护），附加项放最后
CHECKS = [
    chk_a_ct,
    chk_s_kb_box,
    chk_s_gzmax,
    chk_s_area_3040,
    chk_p_eta_bound,
    chk_p_kt,
    chk_p_thrust,
    chk_r_cf,
    chk_r_re,
    chk_r_fn,
    chk_s_gm,
    chk_p_pd,
    chk_s_vanish,
    chk_a_cr_surf,
]

FLIP = {"A-CT", "S-KB-BOX", "S-GZMAX", "S-AREA-3040", "P-ETA-BOUND", "P-KT", "P-THRUST"}
REGRESSION = {"R-CF", "R-RE", "R-FN", "S-GM", "P-PD"}


def run_all(target: Target) -> List[CheckResult]:
    out: List[CheckResult] = []
    for fn in CHECKS:
        try:
            out.append(fn(target))
        except Exception as e:  # 判据自身异常不应静默
            out.append(
                CheckResult(
                    name=getattr(fn, "__name__", "?"),
                    required=True,
                    status="ERROR",
                    title="判据执行异常",
                    detail=f"{type(e).__name__}: {e}",
                )
            )
    return out


def summarize(results: List[CheckResult]) -> Dict[str, object]:
    req = [r for r in results if r.required]
    extra = [r for r in results if not r.required]
    return {
        "required_total": len(req),
        "required_pass": sum(1 for r in req if r.status == "PASS"),
        "required_fail": sum(1 for r in req if r.status != "PASS"),
        "extra_total": len(extra),
        "extra_pass": sum(1 for r in extra if r.status == "PASS"),
        "gate_pass": all(r.status == "PASS" for r in req),
        "flip_pass": {
            r.name: r.status == "PASS" for r in req if r.name in FLIP
        },
        "regression_pass": {
            r.name: r.status == "PASS" for r in req if r.name in REGRESSION
        },
    }


def format_table(results: List[CheckResult]) -> str:
    lines = [
        f"{'判据':<14}{'组':<8}{'结果':<6}{'实测':>14}{'参考':>14}{'偏差%':>9}  说明",
        "-" * 118,
    ]
    for r in results:
        grp = "翻转" if r.name in FLIP else ("回归" if r.name in REGRESSION else "附加")
        v = f"{r.value:.5g}" if isinstance(r.value, (int, float)) else "-"
        ref = f"{r.reference:.5g}" if isinstance(r.reference, (int, float)) else "-"
        dev = f"{r.deviation_pct:+.2f}" if isinstance(r.deviation_pct, (int, float)) else "-"
        lines.append(f"{r.name:<14}{grp:<8}{r.status:<6}{v:>14}{ref:>14}{dev:>9}  {r.detail}")
    return "\n".join(lines)