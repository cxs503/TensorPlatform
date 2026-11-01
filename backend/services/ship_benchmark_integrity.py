"""基准参考数据集的机器完整性裁定（reference-value integrity adjudication）。

背景：``backend/data/benchmark_reference.json`` 是平台精度闸门的唯一参考证据源，
但其数值从未被机器校验过。本模块用**恒等式与物理下界**（不引入任何新文献数值，
也不修改任何已公布数字）逐条裁定每个算例可否进入精度比较：

通道 ``ct``（总阻力系数比较用）与通道 ``rt``（总阻力比较用）分别裁定；
判据分 ``critical``（不通过则整条算例/通道不可用）与 ``warn``（记警告仍可用）。

恒等式与下界：

* ``fn_identity``         Fn = V/√(g·L)         —— 几何/速度/弗劳德数自洽（tol 2%）
* ``cb_identity``         Cb = ∇/(L·B·T)        —— 方形系数与排水量自洽（tol 2%）
* ``ct_rt_identity``      Ct = Rt/(½ρSV²)       —— Ct 与 Rt 自洽（tol 2%）
* ``ct_friction_floor``   Ct ≥ Cf_ITTC57(Re)    —— 无推力全湿船体总阻力不可能低于摩擦阻力
* ``rt_friction_floor``   Rt ≥ ½ρS·Cf·V²        —— 同上，作用在 Rt 通道
* ``provenance``          source_citation 含年份与出处标识

依据：这些恒等式只依赖被裁定数据自身携带的量（V, L, Ct, Rt, S），
故任何"数字抄错/量级丢失/尺度混用"都会被机器捕获，无需外部表格。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services import resistance_model as rm

G = rm.G
RHO_REF = rm.RHO_SEA_WATER
NU_REF = rm.NU_SEA_WATER_15C

DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmark_reference.json"

TOL_IDENTITY = 0.02      # 恒等式相对偏差容差 2%
FLOOR_MARGIN = 0.90      # 摩擦下界留 10% 余量（形状因子 1+k 亦包含在内）
SEVERITY_CRITICAL = "critical"
SEVERITY_WARN = "warn"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_WITHHELD = "WITHHELD"

CHANNEL_CT = "ct"
CHANNEL_RT = "rt"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _rel_dev(value: float, reference: float) -> float:
    """相对偏差 |value-reference|/|reference|。"""
    if reference == 0:
        return float("inf")
    return abs(value - reference) / abs(reference)


def _check(name: str, severity: str, status: str, detail: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "severity": severity, "status": status, "detail": detail, "metrics": metrics}


def _geometry(case: Dict[str, Any]) -> Dict[str, Any]:
    return case.get("geometry") or {}


def _displacement_tonnes(geometry: Dict[str, Any]) -> Optional[float]:
    """从几何块读取排水量（吨）；字段名可能是 displacement_t 或 displacement_kg。"""
    if "displacement_t" in geometry:
        return float(geometry["displacement_t"])
    if "displacement_kg" in geometry:
        return float(geometry["displacement_kg"]) / 1000.0
    return None


def _wetted_surface(geometry: Dict[str, Any]) -> Optional[float]:
    for key in ("wetted_surface_m2", "wetted_surface_area_m2"):
        if key in geometry:
            return float(geometry[key])
    return None


def asset_sha256(path: Path = DATASET_PATH) -> str:
    """参考资产的 sha256 指纹（防静默改动）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 单项判据
# ---------------------------------------------------------------------------

def check_provenance(case: Dict[str, Any]) -> Dict[str, Any]:
    cite = str(case.get("source_citation") or "")
    has_year = re.search(r"(?:19|20)\d{2}", cite) is not None
    ok = len(cite) >= 20 and has_year
    return _check(
        "provenance",
        SEVERITY_CRITICAL,
        STATUS_PASS if ok else STATUS_FAIL,
        "参考值须带可追溯出处（≥20 字符且含 4 位年份）" if not ok else "出处字段合规",
        {"citation_length": len(cite), "has_year": has_year},
    )


def check_froude_identity(case: Dict[str, Any]) -> Dict[str, Any]:
    geom = _geometry(case)
    length = geom.get("length_m")
    speed = case.get("speed_ms")
    claimed = case.get("froude_number")
    if length is None or speed is None or claimed is None:
        return _check("fn_identity", SEVERITY_CRITICAL, STATUS_WITHHELD,
                      "缺 length_m/speed_ms/froude_number，无法校验", {})
    fn = float(speed) / math.sqrt(G * float(length))
    dev = _rel_dev(float(claimed), fn)
    status = STATUS_PASS if dev <= TOL_IDENTITY else STATUS_FAIL
    detail = ("弗劳德数与 V/√(gL) 自洽" if status == STATUS_PASS else
              f"Fn 与 V/√(gL) 不自洽：申报 {claimed}，按 length_m={length} 应为 {fn:.4f}"
              f"（偏差 {dev*100:.1f}%，疑尺度混用：模型尺度几何配实船尺度航速）")
    return _check("fn_identity", SEVERITY_CRITICAL, status, detail,
                  {"fn_claimed": float(claimed), "fn_from_geometry": round(fn, 6),
                   "rel_dev": round(dev, 6), "tolerance": TOL_IDENTITY})


def check_block_coefficient_identity(case: Dict[str, Any]) -> Dict[str, Any]:
    geom = _geometry(case)
    L, B, T, cb = geom.get("length_m"), geom.get("beam_m"), geom.get("draft_m"), geom.get("cb")
    disp_t = _displacement_tonnes(geom)
    if None in (L, B, T, cb) or disp_t is None:
        return _check("cb_identity", SEVERITY_WARN, STATUS_WITHHELD,
                      "缺 L/B/T/Cb 或排水量，无法校验 Cb", {})
    volume = disp_t * 1000.0 / RHO_REF
    cb_derived = volume / (float(L) * float(B) * float(T))
    dev = _rel_dev(float(cb), cb_derived)
    status = STATUS_PASS if dev <= TOL_IDENTITY else STATUS_FAIL
    detail = ("Cb 与 ∇/(L·B·T) 自洽" if status == STATUS_PASS else
              f"Cb 与排水量不自洽：申报 {cb}，按 Δ={disp_t} t 应为 {cb_derived:.4f}"
              f"（偏差 {dev*100:.1f}%）")
    return _check("cb_identity", SEVERITY_WARN, status, detail,
                  {"cb_claimed": float(cb), "cb_from_displacement": round(cb_derived, 6),
                   "volume_m3": round(volume, 3), "rel_dev": round(dev, 6),
                   "displacement_t": disp_t})


def check_ct_rt_identity(case: Dict[str, Any]) -> Dict[str, Any]:
    geom = _geometry(case)
    surface = _wetted_surface(geom)
    speed, ct, rt = case.get("speed_ms"), case.get("experimental_ct"), case.get("experimental_rt_n")
    if surface is None or speed is None or ct is None or rt is None:
        return _check("ct_rt_identity", SEVERITY_WARN, STATUS_WITHHELD,
                      "缺湿表面积，无法核算 Ct 与 Rt 是否同源", {})
    q = 0.5 * RHO_REF * surface * float(speed) ** 2
    ct_from_rt = float(rt) / q
    dev = _rel_dev(ct_from_rt, float(ct))
    status = STATUS_PASS if dev <= TOL_IDENTITY else STATUS_FAIL
    detail = ("Ct 与 Rt 同源" if status == STATUS_PASS else
              f"Ct 与 Rt 不同源：申报 Ct={ct}，由 Rt={rt} N、S={surface} m²、V={speed} m/s"
              f"反算 Ct={ct_from_rt:.3e}（相差 {dev*100:.0f}%）")
    return _check("ct_rt_identity", SEVERITY_WARN, status, detail,
                  {"ct_claimed": float(ct), "ct_from_rt": float(f"{ct_from_rt:.6e}"),
                   "dynamic_pressure_area_n": round(q, 3), "rel_dev": round(dev, 6)})


def _cf_at(case: Dict[str, Any], geometry_length: Optional[float] = None) -> Optional[float]:
    length = geometry_length if geometry_length is not None else _geometry(case).get("length_m")
    speed = case.get("speed_ms")
    if length is None or speed is None:
        return None
    re = float(speed) * float(length) / NU_REF
    try:
        return rm.ittc57_cf(re)
    except rm.ResistanceInputError:
        return None


def check_ct_friction_floor(case: Dict[str, Any]) -> Dict[str, Any]:
    cf = _cf_at(case)
    ct = case.get("experimental_ct")
    if cf is None or ct is None:
        return _check("ct_friction_floor", SEVERITY_CRITICAL, STATUS_WITHHELD,
                      "缺 V/L 或 Ct，无法施加摩擦下界", {})
    floor = FLOOR_MARGIN * cf
    ok = float(ct) >= floor
    return _check(
        "ct_friction_floor", SEVERITY_CRITICAL, STATUS_PASS if ok else STATUS_FAIL,
        ("Ct 高于摩擦下界" if ok else
         f"Ct={ct} 低于摩擦下界 {FLOOR_MARGIN:.2f}·Cf={floor:.3e}：无推力全湿船体不可能如此，"
         "该 Ct 物理上不可接受"),
        {"ct": float(ct), "cf_ittc57": float(f"{cf:.6e}"),
         "floor": float(f"{floor:.6e}"), "margin": FLOOR_MARGIN},
    )


def check_rt_friction_floor(case: Dict[str, Any]) -> Dict[str, Any]:
    geom = _geometry(case)
    surface = _wetted_surface(geom)
    cf = _cf_at(case)
    speed, rt = case.get("speed_ms"), case.get("experimental_rt_n")
    if surface is None or cf is None or speed is None or rt is None:
        return _check("rt_friction_floor", SEVERITY_CRITICAL, STATUS_WITHHELD,
                      "缺湿表面积/速度/Rt，无法施加摩擦下界", {})
    rf = 0.5 * RHO_REF * surface * cf * float(speed) ** 2
    floor = FLOOR_MARGIN * rf
    ok = float(rt) >= floor
    return _check(
        "rt_friction_floor", SEVERITY_CRITICAL, STATUS_PASS if ok else STATUS_FAIL,
        ("Rt 高于摩擦下界" if ok else
         f"Rt={rt} N 低于摩擦下界 {FLOOR_MARGIN:.2f}·Rf={floor:.1f} N"
         f"（Rf 按 ITTC-57 Cf={cf:.3e}、S={surface} m²、V={speed} m/s 计）：量级不可接受"),
        {"rt": float(rt), "rf_ittc57_n": round(rf, 3), "floor_n": round(floor, 3)},
    )


# ---------------------------------------------------------------------------
# 裁定
# ---------------------------------------------------------------------------

_CHANNEL_CHECKS = {
    CHANNEL_CT: ("provenance", "fn_identity", "ct_friction_floor", "cb_identity"),
    CHANNEL_RT: ("provenance", "fn_identity", "rt_friction_floor", "ct_rt_identity"),
}


def adjudicate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """对单个算例做通道级裁定。"""
    checks = [
        check_provenance(case),
        check_froude_identity(case),
        check_block_coefficient_identity(case),
        check_ct_rt_identity(case),
        check_ct_friction_floor(case),
        check_rt_friction_floor(case),
    ]
    by_name = {c["name"]: c for c in checks}

    channels: Dict[str, Dict[str, Any]] = {}
    for channel, names in _CHANNEL_CHECKS.items():
        relevant = [by_name[n] for n in names]
        fails = [c for c in relevant if c["status"] == STATUS_FAIL and c["severity"] == SEVERITY_CRITICAL]
        warns = [c for c in relevant if c["status"] == STATUS_FAIL and c["severity"] == SEVERITY_WARN]
        if fails:
            status = STATUS_FAIL
        elif warns:
            status = "PASS_WITH_WARNINGS"
        elif any(c["status"] == STATUS_WITHHELD for c in relevant):
            status = "PASS_WITH_WARNINGS"
        else:
            status = STATUS_PASS
        channels[channel] = {
            "status": status,
            "admissible": status in (STATUS_PASS, "PASS_WITH_WARNINGS"),
            "blocking": [c["name"] for c in fails],
            "warnings": [c["name"] for c in warns],
            "withheld": [c["name"] for c in relevant if c["status"] == STATUS_WITHHELD],
        }

    return {
        "case_id": case.get("case_id"),
        "ship_type": case.get("ship_type"),
        "speed_ms": case.get("speed_ms"),
        "channels": channels,
        "checks": checks,
    }


def audit_dataset(data: Dict[str, Any], dataset_path: Path = DATASET_PATH) -> Dict[str, Any]:
    """对整个数据集做完整性裁定并给出准入集合。"""
    cases: Iterable[Dict[str, Any]] = data.get("cases", [])
    adjudicated: List[Dict[str, Any]] = [adjudicate_case(c) for c in cases]

    ids = [c["case_id"] for c in adjudicated]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})

    admission: Dict[str, List[str]] = {}
    excluded: Dict[str, Dict[str, Any]] = {}
    for channel in (CHANNEL_CT, CHANNEL_RT):
        admitted, out = [], {}
        for rec in adjudicated:
            ch = rec["channels"][channel]
            if ch["admissible"]:
                admitted.append(rec["case_id"])
            else:
                out[rec["case_id"]] = {"blocking_checks": ch["blocking"]}
        admission[channel] = admitted
        excluded[channel] = out

    per_hull: Dict[str, Dict[str, List[str]]] = {}
    for channel in (CHANNEL_CT, CHANNEL_RT):
        per_hull[channel] = {}
        for rec in adjudicated:
            per_hull[channel].setdefault(rec["ship_type"], [])
            if rec["channels"][channel]["admissible"]:
                per_hull[channel][rec["ship_type"]].append(rec["case_id"])

    return {
        "dataset_path": str(dataset_path),
        "dataset_version": data.get("version"),
        "asset_sha256": asset_sha256(dataset_path),
        "n_cases": len(adjudicated),
        "duplicate_case_ids": duplicates,
        "tolerances": {"identity_rel_dev": TOL_IDENTITY, "friction_floor_margin": FLOOR_MARGIN},
        "reference_fluid": {"rho_kg_m3": RHO_REF, "nu_m2_s": NU_REF},
        "cases": adjudicated,
        "admission": admission,
        "excluded": excluded,
        "admitted_per_hull_type": per_hull,
        "summary": {
            "ct_admitted": len(admission[CHANNEL_CT]),
            "ct_excluded": len(excluded[CHANNEL_CT]),
            "rt_admitted": len(admission[CHANNEL_RT]),
            "rt_excluded": len(excluded[CHANNEL_RT]),
            "critical_failures": sorted({
                f"{c['case_id']}:{chk['name']}"
                for c in adjudicated for chk in c["checks"]
                if chk["status"] == STATUS_FAIL and chk["severity"] == SEVERITY_CRITICAL
            }),
            "warnings": sorted({
                f"{c['case_id']}:{chk['name']}"
                for c in adjudicated for chk in c["checks"]
                if chk["status"] == STATUS_FAIL and chk["severity"] == SEVERITY_WARN
            }),
        },
    }


def load_and_audit(path: Path = DATASET_PATH) -> Dict[str, Any]:
    with open(path, "r") as fh:
        data = json.load(fh)
    return audit_dataset(data, path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="基准参考数据集完整性裁定")
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--out", default=None, help="写出裁定 JSON 的路径")
    args = parser.parse_args(argv)

    report = load_and_audit(Path(args.dataset))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
        print(f"wrote {out}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())