"""阻力基准数据集闭式判据审计器 (v1.2).

背景
----
`backend/api/domain/ship_benchmark.py` 用 `backend/data/benchmark_reference.json`
里的「实验」Ct 对平台阻力计算做 pass/fail 判决。数据集长期未经复核，v1.0 存在
三类静默缺陷（本审计器可复现，见 docs/verification/audit_before.json）：

1. **尺度混用**：几何是模型尺度（DTMB-5415 L=5.72 m），速度却是实船尺度
   （5~30 kn），喂给求解器的 Froude 数高达 2.06；
2. **自洽性破坏**：声明的 `froude_number` 与 V/√(gL) 不符；`experimental_rt_n`
   与 `experimental_ct` 互相矛盾达 3~9 倍；排水体积不等于 Cb·L·B·T；
3. **出处不可追溯**：只有一句 citation 字符串，没有表号/页码，无法判定
   「实验值」是否真是实验值。

本审计器只用**闭式可计算量**判定（不依赖任何外部数值），因此可离线、可复现、
可进 CI。判定三态对齐平台证据分级：PASS / FAIL / WITHHELD（证据不足，不计入
pass 统计也不计入 fail）。

判据集
------
J1 FROUDE_CONSISTENT    Fr 与 V/√(g·L_ref) 一致（L_ref=喂给求解器的长度）
J2 VOLUME_CONSISTENT    排水体积 ∇ 与 Cb·L·B·T（回转体 π/4·D²·L）一致
J3 RT_CT_CONSISTENT     Rt 与 0.5·ρ·S·Ct·V² 一致（两者不可互相矛盾）
J4 FRICTION_FLOOR       Cf_ITTC57(Re) ≤ Ct ≤ 3·Cf_ITTC57(Re)
J5 FR_BAND              排水型阻力基准 Fr ≤ 0.6，超出须显式声明 out_of_range+理由
J6 PROVENANCE           出处/复核状态字段齐备且类型合法
J7 SCALE_DECLARED       尺度声明与几何长度自洽（model/full）

用法
----
    python3 tools/benchmark_audit/audit_resistance_benchmark.py \
        [--dataset backend/data/benchmark_reference.json] [--json out.json]

退出码：0 = 无 FAIL；1 = 存在 FAIL（CI 门禁用）。WITHHELD 不算 FAIL 但会单列。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

G = 9.81
RHO_DEFAULT = 998.0          # kg/m³
NU_DEFAULT = 1.004e-6        # m²/s（20 °C 淡水，ITTC-57 参考温度）

TOL_FROUDE = 0.01
TOL_VOLUME = 0.01
TOL_RT = 0.02
FR_BAND_MAX = 0.60
CT_OVER_CF_MAX = 3.0

# 实船代表长度：仅用于诊断「声明值疑似用实船长度算出」
FULL_SCALE_LENGTH = {"DTMB-5415": 142.0, "KVLCC2": 320.0, "KCS": 230.0}

PASS, FAIL, NA = "PASS", "FAIL", "WITHHELD"


def cf_ittc57(re: float) -> float:
    """ITTC-1957 平板摩擦阻力系数。"""
    if re <= 0:
        return float("nan")
    denom = math.log10(re) - 2.0
    if denom <= 0:
        return float("nan")
    return 0.075 / denom ** 2


@dataclass
class JudgeResult:
    case_id: str
    judge: str
    status: str
    measured: Any = None
    expected: Any = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != FAIL

    def as_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "judge": self.judge,
            "status": self.status,
            "measured": self.measured,
            "expected": self.expected,
            "detail": self.detail,
        }


@dataclass
class AuditReport:
    dataset_version: str = ""
    n_cases: int = 0
    results: List[JudgeResult] = field(default_factory=list)

    @property
    def failures(self) -> List[JudgeResult]:
        return [r for r in self.results if r.status == FAIL]

    @property
    def withheld(self) -> List[JudgeResult]:
        return [r for r in self.results if r.status == NA]

    @property
    def failed_cases(self) -> List[str]:
        return sorted({r.case_id for r in self.failures})

    def judge_counts(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            slot = out.setdefault(r.judge, {"pass": 0, "fail": 0, "withheld": 0})
            slot[r.status.lower()] += 1
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "n_cases": self.n_cases,
            "n_judgements": len(self.results),
            "n_failures": len(self.failures),
            "n_withheld": len(self.withheld),
            "n_failed_cases": len(self.failed_cases),
            "failed_cases": self.failed_cases,
            "judge_counts": self.judge_counts(),
            "failures": [r.as_dict() for r in self.failures],
            "withheld": [r.as_dict() for r in self.withheld],
        }


# ---------------------------------------------------------------------------
# 几何辅助
# ---------------------------------------------------------------------------

def is_axisymmetric(case: Dict[str, Any]) -> bool:
    """SUBOFF 这类回转体：几何只给直径，没有 beam/draft。"""
    g = case.get("geometry", {})
    if g.get("solid_type") == "body_of_revolution":
        return True
    return "diameter_m" in g and "beam_m" not in g


def reference_length(case: Dict[str, Any]) -> Optional[float]:
    g = case.get("geometry", {})
    for key in ("length_m", "length_pp_m", "length"):
        if g.get(key):
            return float(g[key])
    if case.get("length_m"):
        return float(case["length_m"])
    return None


def displaced_volume(case: Dict[str, Any], rho: float) -> Optional[float]:
    """从排水量字段反算体积（m³）。"""
    g = case.get("geometry", {})
    if g.get("displacement_m3"):
        return float(g["displacement_m3"])
    if g.get("displacement_kg"):
        return float(g["displacement_kg"]) / rho
    if g.get("displacement_t"):
        return float(g["displacement_t"]) * 1000.0 / rho
    if case.get("displacement_t"):
        return float(case["displacement_t"]) * 1000.0 / rho
    return None


def cb_box_volume(case: Dict[str, Any]) -> Optional[float]:
    """Cb·L·B·T（排水型）或 Cb·π/4·D²·L（回转体）。"""
    g = case.get("geometry", {})
    L = reference_length(case)
    cb = g.get("cb") or case.get("cb")
    if not L or not cb:
        return None
    if is_axisymmetric(case):
        d = float(g["diameter_m"])
        return float(cb) * math.pi / 4.0 * d * d * L
    b = g.get("beam_m") or case.get("beam_m")
    t = g.get("draft_m") or case.get("draft_m")
    if not b or not t:
        return None
    return float(cb) * L * float(b) * float(t)


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------

def judge_j1_froude(case: Dict[str, Any]) -> JudgeResult:
    cid = case.get("case_id", "?")
    L = reference_length(case)
    V = case.get("speed_ms")
    fr_decl = case.get("froude_number")
    if not L or not V or fr_decl is None:
        return JudgeResult(cid, "J1_FROUDE_CONSISTENT", FAIL, None, None,
                           "缺少 speed_ms / froude_number / length_m")
    fr_calc = float(V) / math.sqrt(G * L)
    err = abs(fr_calc - float(fr_decl)) / fr_calc
    status = PASS if err <= TOL_FROUDE else FAIL
    detail = f"Fr_decl={fr_decl:.4f} vs Fr(L_ref={L:g}m)={fr_calc:.4f}, rel={err*100:.2f}%"
    if status == FAIL:
        fs = FULL_SCALE_LENGTH.get(case.get("ship_type", ""))
        if fs:
            fr_full = float(V) / math.sqrt(G * fs)
            detail += f"; 若用实船长度 {fs:g} m 得 Fr={fr_full:.4f} → 声明值疑似混用尺度"
    return JudgeResult(cid, "J1_FROUDE_CONSISTENT", status, round(float(fr_decl), 4),
                       round(fr_calc, 4), detail)


def judge_j2_volume(case: Dict[str, Any], rho: float) -> JudgeResult:
    cid = case.get("case_id", "?")
    vol = displaced_volume(case, rho)
    box = cb_box_volume(case)
    if vol is None or box is None:
        return JudgeResult(cid, "J2_VOLUME_CONSISTENT", FAIL, vol, box,
                           "缺少排水量或 Cb/主尺度，几何无法闭合")
    err = abs(vol - box) / box
    status = PASS if err <= TOL_VOLUME else FAIL
    return JudgeResult(cid, "J2_VOLUME_CONSISTENT", status, round(vol, 5),
                       round(box, 5), f"∇={vol:.5f} m³ vs Cb·L·B·T={box:.5f} m³, rel={err*100:.2f}%")


def judge_j3_rt_ct(case: Dict[str, Any], rho: float) -> JudgeResult:
    cid = case.get("case_id", "?")
    ct = case.get("experimental_ct")
    if ct is None:
        return JudgeResult(cid, "J3_RT_CT_CONSISTENT", NA, None, None,
                           f"无参考 Ct（reference_ct_status={case.get('reference_ct_status')!r}）→ 不参与互校")
    rt = case.get("experimental_rt_n")
    g = case.get("geometry", {})
    S = g.get("wetted_surface_m2")
    V = case.get("speed_ms")
    if rt is None or not S or not V:
        return JudgeResult(cid, "J3_RT_CT_CONSISTENT", FAIL, rt, None,
                           "声明了参考 Ct 但缺 Rt/wetted_surface/speed，无法互校")
    rt_from_ct = 0.5 * rho * float(S) * float(ct) * float(V) ** 2
    err = abs(float(rt) - rt_from_ct) / rt_from_ct
    status = PASS if err <= TOL_RT else FAIL
    return JudgeResult(cid, "J3_RT_CT_CONSISTENT", status, float(rt), round(rt_from_ct, 2),
                       f"Rt_decl={float(rt):.2f} N vs 0.5ρS·Ct·V²={rt_from_ct:.2f} N, rel={err*100:.1f}%")


def judge_j4_friction_floor(case: Dict[str, Any], rho: float, nu: float) -> JudgeResult:
    cid = case.get("case_id", "?")
    ct = case.get("experimental_ct")
    if ct is None:
        return JudgeResult(cid, "J4_FRICTION_FLOOR", NA, None, None, "无参考 Ct → 不参与")
    L = reference_length(case)
    V = case.get("speed_ms")
    if not L or not V:
        return JudgeResult(cid, "J4_FRICTION_FLOOR", FAIL, ct, None, "缺 L/V")
    re = float(V) * L / nu
    cf = cf_ittc57(re)
    ok = cf <= float(ct) <= CT_OVER_CF_MAX * cf
    return JudgeResult(cid, "J4_FRICTION_FLOOR", PASS if ok else FAIL, round(float(ct), 6),
                       [round(cf, 6), round(CT_OVER_CF_MAX * cf, 6)],
                       f"Re={re:.3e}, Cf_ITTC57={cf:.6f}, Ct/Cf={float(ct)/cf:.2f} "
                       f"(允许 1.0~{CT_OVER_CF_MAX})")


def judge_j5_fr_band(case: Dict[str, Any]) -> JudgeResult:
    cid = case.get("case_id", "?")
    L = reference_length(case)
    V = case.get("speed_ms")
    if not L or not V:
        return JudgeResult(cid, "J5_FR_BAND", FAIL, None, FR_BAND_MAX, "缺 L/V")
    fr = float(V) / math.sqrt(G * L)
    if fr <= FR_BAND_MAX:
        return JudgeResult(cid, "J5_FR_BAND", PASS, round(fr, 4), FR_BAND_MAX,
                           f"Fr(L_ref={L:g}m)={fr:.3f} ≤ {FR_BAND_MAX}")
    if case.get("validity") == "out_of_range" and case.get("validity_reason"):
        return JudgeResult(cid, "J5_FR_BAND", PASS, round(fr, 4), FR_BAND_MAX,
                           f"Fr={fr:.3f} 超band，已显式声明 out_of_range：{case['validity_reason']}")
    return JudgeResult(cid, "J5_FR_BAND", FAIL, round(fr, 4), FR_BAND_MAX,
                       f"Fr(L_ref={L:g}m)={fr:.3f} 超排水型阻力基准物理带且未声明豁免")


def judge_j6_provenance(case: Dict[str, Any]) -> JudgeResult:
    cid = case.get("case_id", "?")
    prov = case.get("provenance") or {}
    have_cite = bool(case.get("source_citation"))
    have_type = isinstance(prov.get("type"), str) and bool(prov.get("type"))
    have_locator = isinstance(prov.get("locator"), str) and bool(prov.get("locator"))
    verified = case.get("reference_verified")
    status_field = case.get("reference_ct_status")
    ok = (have_cite and have_type and have_locator
          and isinstance(verified, bool) and status_field in ("verified", "withheld", "retired"))
    detail = (f"citation={have_cite}, provenance.type={have_type}, locator={have_locator}, "
              f"reference_verified={verified!r}, reference_ct_status={status_field!r}")
    return JudgeResult(cid, "J6_PROVENANCE", PASS if ok else FAIL,
                       {"citation": have_cite, "type": have_type, "locator": have_locator,
                        "verified": verified, "ct_status": status_field}, None, detail)


def judge_j7_scale(case: Dict[str, Any]) -> JudgeResult:
    cid = case.get("case_id", "?")
    scale = case.get("scale")
    L = reference_length(case)
    ok = scale in ("model", "full") and bool(L)
    detail = f"scale={scale!r}, L_ref={L}"
    if ok and scale == "model" and L > 30.0:
        ok, detail = False, f"scale='model' 但 L_ref={L:g} m > 30 m，尺度声明可疑"
    if ok and scale == "full" and L < 30.0:
        ok, detail = False, f"scale='full' 但 L_ref={L:g} m < 30 m，尺度声明可疑"
    return JudgeResult(cid, "J7_SCALE_DECLARED", PASS if ok else FAIL, scale, L, detail)


def audit_dataset(data: Dict[str, Any]) -> AuditReport:
    rep = AuditReport(dataset_version=str(data.get("version", "unknown")))
    cases = data.get("cases", [])
    rep.n_cases = len(cases)
    for case in cases:
        rho = float(case.get("water_density", data.get("water_density", RHO_DEFAULT)))
        nu = float(case.get("kinematic_viscosity", data.get("kinematic_viscosity", NU_DEFAULT)))
        rep.results.extend([
            judge_j1_froude(case),
            judge_j2_volume(case, rho),
            judge_j3_rt_ct(case, rho),
            judge_j4_friction_floor(case, rho, nu),
            judge_j5_fr_band(case),
            judge_j6_provenance(case),
            judge_j7_scale(case),
        ])
    return rep


def audit_file(path: str | Path) -> AuditReport:
    with open(path, "r", encoding="utf-8") as f:
        return audit_dataset(json.load(f))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="阻力基准数据集闭式判据审计 (J1~J7)")
    ap.add_argument("--dataset", default="backend/data/benchmark_reference.json")
    ap.add_argument("--json", default=None, help="把完整审计结果写到该路径")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    rep = audit_file(args.dataset)
    payload = rep.as_dict()
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.quiet:
        print(f"dataset={args.dataset} version={rep.dataset_version} cases={rep.n_cases}")
        for judge, cnt in payload["judge_counts"].items():
            print(f"  {judge:24s} pass={cnt['pass']:3d} fail={cnt['fail']:3d} withheld={cnt['withheld']:3d}")
        print(f"FAIL {payload['n_failures']} 条（案例 {payload['n_failed_cases']}/{rep.n_cases}）；"
              f"WITHHELD {payload['n_withheld']} 条")
    return 0 if payload["n_failures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())