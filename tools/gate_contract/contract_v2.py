"""gate.json v2 契约：支持三类非标量判据族 + 聚合口径显式化。

v2 新增判据类型（``criterion.type``）：

==================  ============================================================
type                语义
==================  ============================================================
scalar              v1 兼容：单标量 + 算子 + 阈值（默认，可省略 type 字段）
pointwise_band      曲线逐点容差 + pass_ratio 聚合
spectral_envelope   谱包络 + 峰值/谱统计量容差
field_fingerprint   场量多阶统计量 + 网格哈希
==================  ============================================================

所有判据的聚合口径（aggregation）与单位（unit）必须显式写入契约。
判据只能引用工件字段（禁止内联数字）——阈值/容差可内联（它们是契约参数，不是测量值）。

零领域语义：本模块不认识任何域词，所有名称来自数据注入。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .contract import (ContractError, canonical_json, canonical_sha256,
                       env_fingerprint, resolve, sha256_bytes,
                       sha256_file, same_value, _contained_path)

SCHEMA_VERSION_V2 = "gate-contract/2.0"

#: v2 判据类型白名单
CRITERION_TYPES = ("scalar", "pointwise_band", "spectral_envelope", "field_fingerprint")

#: 聚合算子白名单
AGGREGATION_OPS = ("pass_ratio", "Linf", "weighted_mean", "absmax", "max", "min", "mean")

#: 容差模式
TOLERANCE_MODES = ("absolute", "relative")


class V2ContractError(ContractError):
    """v2 特有错误码。"""
    pass


# ── v2 结构校验 ─────────────────────────────────────────────────

_REQUIRED_V2_KEYS = {"schema_version", "gate_id", "mission_id", "criteria"}


def validate_v2_structure(gate: Any) -> list[str]:
    """返回结构问题列表（空=合法）。"""
    if not isinstance(gate, dict):
        return ["not_an_object"]
    problems = [f"missing:{k}" for k in sorted(_REQUIRED_V2_KEYS) if k not in gate]
    sv = gate.get("schema_version")
    if sv not in (SCHEMA_VERSION_V2, "gate-contract/1.0"):
        problems.append(f"schema_version:{sv!r}")
    if not isinstance(gate.get("gate_id"), str) or not gate.get("gate_id", "").strip():
        problems.append("gate_id_not_string")
    if isinstance(gate.get("mission_id"), bool) or not isinstance(gate.get("mission_id"), int):
        problems.append("mission_id_not_int")
    if not isinstance(gate.get("criteria"), list):
        problems.append("criteria_not_list")
    return problems


def validate_criterion_v2(entry: dict, idx: int) -> list[str]:
    """校验单个 v2 判据条目的结构。"""
    problems = []
    cid = entry.get("id", f"#{idx}")
    ctype = entry.get("type", "scalar")
    if ctype not in CRITERION_TYPES:
        problems.append(f"{cid}:invalid_type:{ctype}")
        return problems

    # 公共字段
    unit = entry.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        problems.append(f"{cid}:unit_missing")

    agg = entry.get("aggregation")
    if ctype != "scalar":
        if not isinstance(agg, dict):
            problems.append(f"{cid}:aggregation_missing")
        else:
            op = agg.get("op")
            if op not in AGGREGATION_OPS:
                problems.append(f"{cid}:aggregation_op_invalid:{op}")

    # 工件引用
    art = entry.get("artifact")
    if not isinstance(art, dict):
        problems.append(f"{cid}:artifact_missing")
    else:
        if not isinstance(art.get("path"), str) or not art.get("path"):
            problems.append(f"{cid}:artifact_path_missing")
        if not isinstance(art.get("sha256"), str) or len(art.get("sha256", "")) != 64:
            problems.append(f"{cid}:artifact_sha_invalid")

    # 类型特有字段
    if ctype == "pointwise_band":
        problems.extend(_validate_pointwise_band(entry, cid))
    elif ctype == "spectral_envelope":
        problems.extend(_validate_spectral_envelope(entry, cid))
    elif ctype == "field_fingerprint":
        problems.extend(_validate_field_fingerprint(entry, cid))
    else:  # scalar
        if entry.get("op") not in ("le", "lt", "ge", "gt", "abs_le", "eq",
                                    "in_range", "all_in_range"):
            problems.append(f"{cid}:op_invalid")
        if "threshold" not in entry:
            problems.append(f"{cid}:threshold_missing")
        if "pointer" not in entry or not entry.get("pointer"):
            problems.append(f"{cid}:pointer_missing")

    return problems


def _validate_pointwise_band(entry: dict, cid: str) -> list[str]:
    problems = []
    tol = entry.get("tolerance")
    if not isinstance(tol, dict):
        problems.append(f"{cid}:tolerance_missing")
    else:
        mode = tol.get("mode")
        if mode not in TOLERANCE_MODES:
            problems.append(f"{cid}:tolerance_mode_invalid:{mode}")
        if "value" not in tol:
            problems.append(f"{cid}:tolerance_value_missing")
    if "pass_ratio_threshold" not in entry:
        problems.append(f"{cid}:pass_ratio_threshold_missing")
    else:
        pr = entry.get("pass_ratio_threshold")
        if not isinstance(pr, (int, float)) or not (0 <= float(pr) <= 1):
            problems.append(f"{cid}:pass_ratio_threshold_out_of_range")
    for key in ("ref_pointer", "meas_pointer"):
        if key not in entry or not entry.get(key):
            problems.append(f"{cid}:{key}_missing")
    return problems


def _validate_spectral_envelope(entry: dict, cid: str) -> list[str]:
    problems = []
    for key in ("lower_pointer", "upper_pointer", "meas_pointer"):
        if key not in entry or not entry.get(key):
            problems.append(f"{cid}:{key}_missing")
    peak = entry.get("peak_tolerance")
    if not isinstance(peak, dict):
        problems.append(f"{cid}:peak_tolerance_missing")
    else:
        if "freq_abs_tol" not in peak and "freq_rel_tol" not in peak:
            problems.append(f"{cid}:peak_freq_tol_missing")
        if "amp_abs_tol" not in peak and "amp_rel_tol" not in peak:
            problems.append(f"{cid}:peak_amp_tol_missing")
    return problems


def _validate_field_fingerprint(entry: dict, cid: str) -> list[str]:
    problems = []
    stats = entry.get("stats")
    if not isinstance(stats, dict):
        problems.append(f"{cid}:stats_missing")
    else:
        if "ref_pointer" not in stats or "meas_pointer" not in stats:
            problems.append(f"{cid}:stats_pointers_missing")
        orders = stats.get("orders")
        if not isinstance(orders, list) or not orders:
            problems.append(f"{cid}:stats_orders_missing")
    grid = entry.get("grid_hash")
    if not isinstance(grid, dict):
        problems.append(f"{cid}:grid_hash_missing")
    else:
        if "ref_pointer" not in grid or "meas_pointer" not in grid:
            problems.append(f"{cid}:grid_hash_pointers_missing")
    return problems


# ── v2 判据求值 ─────────────────────────────────────────────────

def _measure_v2(entry: dict, evidence_root: Path) -> tuple[Any, str]:
    """v2 版工件测量：支持 select 字段（v1 兼容）和 aggregation 字段。"""
    from .contract import apply_op
    art = entry.get("artifact")
    if not isinstance(art, dict):
        raise ContractError("artifact_missing", str(entry.get("id")))
    rel = art.get("path")
    if not isinstance(rel, str) or not rel:
        raise ContractError("artifact_missing", str(entry.get("id")))
    path = _contained_path(evidence_root, rel)
    try:
        data = path.read_bytes()
    except OSError:
        raise ContractError("artifact_missing", rel) from None
    digest = sha256_bytes(data)
    want = art.get("sha256")
    if not isinstance(want, str) or not want:
        raise ContractError("artifact_sha_missing", rel)
    if digest != want:
        raise ContractError("artifact_sha_mismatch", f"{Path(rel).name}:{digest[:12]}!={want[:12]}")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractError("artifact_unparsable", rel) from None
    
    # 解析指针
    pointer = entry.get("pointer")
    select = entry.get("select")  # v1 兼容
    field = entry.get("field")
    
    value = resolve(doc, pointer, select, field)
    
    # 单位换算
    try:
        scale = float(entry.get("scale", 1.0) if entry.get("scale") is not None else 1.0)
    except (TypeError, ValueError):
        raise ContractError("scale_invalid", str(entry.get("scale"))) from None
    
    if isinstance(value, list):
        scaled = [float(v) * scale for v in value]
        if not all(math.isfinite(v) for v in scaled):
            raise ContractError("value_not_finite", rel)
        return scaled, digest
    
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("pointer_unresolved", type(value).__name__)
    out = float(value) * scale
    if not math.isfinite(out):
        raise ContractError("value_not_finite", rel)
    return out, digest


def evaluate_scalar(entry: dict, artifact_doc: dict,
                    evidence_root: Path) -> dict:
    """v1 兼容标量判据求值。"""
    from .contract import apply_op
    value, digest = _measure_v2(entry, evidence_root)
    ok = apply_op(entry["op"], value, entry["threshold"])
    select = entry.get("select", "absmax")
    return {
        "measured": value,
        "sha256": digest,
        "status": "pass" if ok else "fail",
        "code": None if ok else "threshold_violated",
        "aggregation": {"op": select, "detail": f"select={select}"},
    }


def _load_curve(artifact_doc: dict, pointer: str) -> list[dict]:
    """从工件文档中按指针取曲线数据（期望 [{x, y}, ...] 或 {x: [...], y: [...]}）。"""
    data = resolve(artifact_doc, pointer)
    if isinstance(data, list):
        result = []
        for item in data:
            if isinstance(item, dict) and "x" in item and "y" in item:
                result.append({"x": float(item["x"]), "y": float(item["y"])})
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                result.append({"x": float(item[0]), "y": float(item[1])})
        return result
    if isinstance(data, dict) and "x" in data and "y" in data:
        xs = data["x"]
        ys = data["y"]
        if isinstance(xs, list) and isinstance(ys, list) and len(xs) == len(ys):
            return [{"x": float(x), "y": float(y)} for x, y in zip(xs, ys)]
    raise ContractError("pointer_unresolved", f"curve:{pointer}")


def evaluate_pointwise_band(entry: dict, artifact_doc: dict,
                            evidence_root: Path) -> dict:
    """逐点容差曲线比对。"""
    ref_curve = _load_curve(artifact_doc, entry["ref_pointer"])
    meas_curve = _load_curve(artifact_doc, entry["meas_pointer"])

    if len(ref_curve) != len(meas_curve):
        raise ContractError("curve_length_mismatch",
                            f"ref={len(ref_curve)} meas={len(meas_curve)}")

    tol = entry["tolerance"]
    tol_mode = tol["mode"]
    tol_value = float(tol["value"])
    pr_threshold = float(entry["pass_ratio_threshold"])

    n = len(ref_curve)
    if n == 0:
        raise ContractError("curve_empty", "")

    pass_count = 0
    fail_intervals = []
    current_fail = None

    for i, (ref_pt, meas_pt) in enumerate(zip(ref_curve, meas_curve)):
        ref_y = ref_pt["y"]
        meas_y = meas_pt["y"]
        x = ref_pt["x"]

        if tol_mode == "absolute":
            deviation = abs(meas_y - ref_y)
            passed = deviation <= tol_value
        else:
            denom = abs(ref_y) if abs(ref_y) > 1e-300 else 1e-300
            deviation = abs(meas_y - ref_y) / denom
            passed = deviation <= tol_value

        if passed:
            pass_count += 1
            if current_fail is not None:
                fail_intervals.append(current_fail)
                current_fail = None
        else:
            if current_fail is None:
                current_fail = {"x_start": x, "x_end": x,
                                "max_deviation": deviation}
            else:
                current_fail["x_end"] = x
                current_fail["max_deviation"] = max(current_fail["max_deviation"],
                                                     deviation)
    if current_fail is not None:
        fail_intervals.append(current_fail)

    pass_ratio = pass_count / n
    ok = pass_ratio >= pr_threshold

    art_path = _contained_path(evidence_root, entry["artifact"]["path"])
    digest = sha256_file(art_path)

    return {
        "measured": {"pass_ratio": pass_ratio, "n_points": n,
                     "pass_count": pass_count},
        "fail_intervals": fail_intervals,
        "sha256": digest,
        "status": "pass" if ok else "fail",
        "code": None if ok else "pass_ratio_below_threshold",
        "aggregation": {
            "op": "pass_ratio",
            "detail": f"pass_ratio={pass_ratio:.4f}>={pr_threshold}",
            "tolerance_mode": tol_mode,
            "tolerance_value": tol_value,
            "unit": entry.get("unit", ""),
        },
    }


def _load_spectrum(artifact_doc: dict, pointer: str) -> list[dict]:
    """取谱数据 [{omega, amplitude}, ...] 或 {omega: [...], amplitude: [...]}。"""
    data = resolve(artifact_doc, pointer)
    if isinstance(data, list):
        result = []
        for item in data:
            if isinstance(item, dict) and "omega" in item and "amplitude" in item:
                result.append({"omega": float(item["omega"]),
                               "amplitude": float(item["amplitude"])})
        return result
    if isinstance(data, dict) and "omega" in data and "amplitude" in data:
        omegas = data["omega"]
        amps = data["amplitude"]
        if isinstance(omegas, list) and isinstance(amps, list):
            return [{"omega": float(o), "amplitude": float(a)}
                    for o, a in zip(omegas, amps)]
    raise ContractError("pointer_unresolved", f"spectrum:{pointer}")


def _find_peak(spectrum: list[dict]) -> dict:
    """找谱的峰值频率和幅值。"""
    if not spectrum:
        return {"peak_omega": 0.0, "peak_amplitude": 0.0}
    peak = max(spectrum, key=lambda s: s["amplitude"])
    return {"peak_omega": peak["omega"], "peak_amplitude": peak["amplitude"]}


def _spectral_stat(spectrum: list[dict], order: int) -> float:
    """计算谱统计量 m_n = sum(omega^n * amplitude) / sum(amplitude)。"""
    if not spectrum:
        return 0.0
    total_amp = sum(s["amplitude"] for s in spectrum)
    if total_amp < 1e-300:
        return 0.0
    return sum(s["omega"] ** order * s["amplitude"] for s in spectrum) / total_amp


def evaluate_spectral_envelope(entry: dict, artifact_doc: dict,
                               evidence_root: Path) -> dict:
    """谱包络比对。"""
    lower_spec = _load_spectrum(artifact_doc, entry["lower_pointer"])
    upper_spec = _load_spectrum(artifact_doc, entry["upper_pointer"])
    meas_spec = _load_spectrum(artifact_doc, entry["meas_pointer"])

    peak_tol = entry["peak_tolerance"]

    lower_map = {s["omega"]: s["amplitude"] for s in lower_spec}
    upper_map = {s["omega"]: s["amplitude"] for s in upper_spec}

    envelope_pass = 0
    envelope_total = 0
    fail_bands = []

    for pt in meas_spec:
        omega = pt["omega"]
        amp = pt["amplitude"]
        lo = lower_map.get(omega)
        hi = upper_map.get(omega)
        if lo is not None and hi is not None:
            envelope_total += 1
            if lo <= amp <= hi:
                envelope_pass += 1
            else:
                fail_bands.append({"omega": omega, "amplitude": amp,
                                   "lower": lo, "upper": hi})

    meas_peak = _find_peak(meas_spec)
    ref_peak = _find_peak(upper_spec)

    peak_freq_ok = True
    peak_amp_ok = True
    freq_dev = abs(meas_peak["peak_omega"] - ref_peak["peak_omega"])
    amp_dev = abs(meas_peak["peak_amplitude"] - ref_peak["peak_amplitude"])

    if "freq_abs_tol" in peak_tol:
        peak_freq_ok = freq_dev <= float(peak_tol["freq_abs_tol"])
    elif "freq_rel_tol" in peak_tol:
        denom = abs(ref_peak["peak_omega"]) if abs(ref_peak["peak_omega"]) > 1e-300 else 1e-300
        peak_freq_ok = (freq_dev / denom) <= float(peak_tol["freq_rel_tol"])

    if "amp_abs_tol" in peak_tol:
        peak_amp_ok = amp_dev <= float(peak_tol["amp_abs_tol"])
    elif "amp_rel_tol" in peak_tol:
        denom = abs(ref_peak["peak_amplitude"]) if abs(ref_peak["peak_amplitude"]) > 1e-300 else 1e-300
        peak_amp_ok = (amp_dev / denom) <= float(peak_tol["amp_rel_tol"])

    stat_ok = True
    stat_details = []
    stat_tols = entry.get("stat_tolerance")
    if stat_tols and isinstance(stat_tols, list):
        for mt in stat_tols:
            order = int(mt["order"])
            meas_m = _spectral_stat(meas_spec, order)
            ref_m = _spectral_stat(upper_spec, order)
            if mt.get("mode") == "relative":
                denom = abs(ref_m) if abs(ref_m) > 1e-300 else 1e-300
                ok_i = abs(meas_m - ref_m) / denom <= float(mt.get("tol", 0.05))
            else:
                ok_i = abs(meas_m - ref_m) <= float(mt.get("tol", 0.01))
            stat_ok = stat_ok and ok_i
            stat_details.append({"order": order, "measured": meas_m,
                                   "reference": ref_m, "pass": ok_i})

    envelope_ratio = envelope_pass / envelope_total if envelope_total > 0 else 0.0
    all_ok = (envelope_ratio >= 1.0 - 1e-9) and peak_freq_ok and peak_amp_ok and stat_ok

    art_path = _contained_path(evidence_root, entry["artifact"]["path"])
    digest = sha256_file(art_path)

    return {
        "measured": {
            "envelope_pass_ratio": envelope_ratio,
            "peak_omega": meas_peak["peak_omega"],
            "peak_amplitude": meas_peak["peak_amplitude"],
            "ref_peak_omega": ref_peak["peak_omega"],
            "ref_peak_amplitude": ref_peak["peak_amplitude"],
        },
        "fail_bands": fail_bands,
        "stat_details": stat_details,
        "sha256": digest,
        "status": "pass" if all_ok else "fail",
        "code": None if all_ok else "spectral_envelope_violated",
        "aggregation": {
            "op": "envelope_containment",
            "detail": f"envelope_ratio={envelope_ratio:.4f}",
            "peak_freq_dev": freq_dev,
            "peak_amp_dev": amp_dev,
            "unit": entry.get("unit", ""),
        },
    }


def _load_field(artifact_doc: dict, pointer: str) -> list[float]:
    """取场量数据（扁平数组或多维展平）。"""
    data = resolve(artifact_doc, pointer)
    if isinstance(data, list):
        flat = []
        for item in data:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                flat.append(float(item))
            elif isinstance(item, list):
                for v in item:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        flat.append(float(v))
        return flat
    raise ContractError("pointer_unresolved", f"field:{pointer}")


def _field_stats(field: list[float], orders: list[int]) -> dict:
    """计算场量的多阶统计量。"""
    n = len(field)
    if n == 0:
        return {str(o): 0.0 for o in orders}
    result = {}
    for order in orders:
        if order == 0:
            result[str(order)] = float(n)
        elif order == 1:
            result[str(order)] = sum(field) / n
        else:
            mean = sum(field) / n
            result[str(order)] = sum((v - mean) ** order for v in field) / n
    return result


def _grid_hash(field: list[float]) -> str:
    """场量的网格哈希（分桶统计，不存整场）。"""
    if not field:
        return sha256_bytes(b"")
    vmin, vmax = min(field), max(field)
    rng = vmax - vmin if vmax > vmin else 1.0
    buckets = [0] * 16
    for v in field:
        idx = min(int((v - vmin) / rng * 16), 15)
        buckets[idx] += 1
    return sha256_bytes(json.dumps(buckets).encode())


def evaluate_field_fingerprint(entry: dict, artifact_doc: dict,
                               evidence_root: Path) -> dict:
    """场量指纹比对：多阶统计量 + 网格哈希。"""
    stats_cfg = entry["stats"]
    orders = [int(o) for o in stats_cfg["orders"]]

    ref_field = _load_field(artifact_doc, stats_cfg["ref_pointer"])
    meas_field = _load_field(artifact_doc, stats_cfg["meas_pointer"])

    ref_stats = _field_stats(ref_field, orders)
    meas_stats = _field_stats(meas_field, orders)

    stat_tol = entry.get("stat_tolerance", {})
    stat_pass = True
    stat_details = []
    for order in orders:
        key = str(order)
        ref_v = ref_stats[key]
        meas_v = meas_stats[key]
        tol = float(stat_tol.get(key, stat_tol.get("default", 0.05)))
        mode = stat_tol.get("mode", "relative")
        if mode == "relative":
            denom = abs(ref_v) if abs(ref_v) > 1e-10 else 1.0
            ok = abs(meas_v - ref_v) / denom <= tol
        else:
            ok = abs(meas_v - ref_v) <= tol
        stat_pass = stat_pass and ok
        stat_details.append({"order": order, "ref": ref_v, "measured": meas_v,
                               "tolerance": tol, "pass": ok})

    grid_cfg = entry["grid_hash"]
    ref_grid_field = _load_field(artifact_doc, grid_cfg["ref_pointer"])
    meas_grid_field = _load_field(artifact_doc, grid_cfg["meas_pointer"])
    ref_hash = _grid_hash(ref_grid_field)
    meas_hash = _grid_hash(meas_grid_field)
    grid_ok = ref_hash == meas_hash

    all_ok = stat_pass and grid_ok

    art_path = _contained_path(evidence_root, entry["artifact"]["path"])
    digest = sha256_file(art_path)

    return {
        "measured": {
            "ref_stats": ref_stats,
            "meas_stats": meas_stats,
            "ref_grid_hash": ref_hash,
            "meas_grid_hash": meas_hash,
        },
        "stat_details": stat_details,
        "sha256": digest,
        "status": "pass" if all_ok else "fail",
        "code": None if all_ok else "field_fingerprint_mismatch",
        "aggregation": {
            "op": "stat_hash",
            "detail": f"stats_ok={stat_pass} grid_ok={grid_ok}",
            "unit": entry.get("unit", ""),
        },
    }


# ── v2 引擎入口 ─────────────────────────────────────────────────

def verify_gate_v2(gate_path: str | Path | None = None, *,
                   gate_doc: dict | None = None,
                   registry: dict | None = None,
                   evidence_root: str | Path | None = None) -> dict:
    """v2 引擎：复算一份 gate.json（v2 schema）。"""
    from .contract import apply_op, OPS

    issues: list[dict] = []

    def issue(code: str, criterion_id: str | None = None, detail: str = "") -> None:
        issues.append({"code": code, "criterion_id": criterion_id, "detail": detail})

    gate = gate_doc
    if gate is None and gate_path is not None:
        p = Path(gate_path)
        if not p.is_file():
            issue("gate_missing", None, str(gate_path))
            return _finish_v2(None, None, issues, [], False, gate_path)
        try:
            gate = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            issue("schema_invalid", None, "json_decode")
            return _finish_v2(None, None, issues, [], False, gate_path)

    if gate is None:
        issue("gate_missing", None, str(gate_path))
        return _finish_v2(None, None, issues, [], False, gate_path)

    problems = validate_v2_structure(gate)
    if problems:
        for prob in problems:
            issue("schema_invalid", None, prob)
        return _finish_v2(gate, None, issues, [], registry is not None, gate_path)

    base = Path(gate_path).parent if gate_path is not None else Path(evidence_root or ".")
    if evidence_root is not None:
        base = Path(evidence_root)

    reg_entry = None
    if registry is not None:
        gates = registry.get("gates", {})
        reg_entry = gates.get(gate.get("gate_id"))
        if reg_entry is None:
            issue("registry_missing", None, str(gate.get("gate_id")))

    criteria = gate.get("criteria", [])
    if not criteria:
        issue("criteria_missing", None, "empty")

    seen_ids: set[str] = set()
    for idx, entry in enumerate(criteria):
        if isinstance(entry, dict):
            cid = entry.get("id", f"#{idx}")
            if cid in seen_ids:
                issue("criterion_duplicate", cid, "repeated_in_gate")
            seen_ids.add(cid)

    rows: list[dict] = []
    for idx, entry in enumerate(criteria):
        if not isinstance(entry, dict):
            issue("schema_invalid", f"#{idx}", "not_an_object")
            rows.append({"id": f"#{idx}", "status": "withheld", "code": "schema_invalid"})
            continue

        cid = entry.get("id", f"#{idx}")
        ctype = entry.get("type", "scalar")
        row: dict[str, Any] = {
            "id": cid, "type": ctype,
            "op": entry.get("op"), "threshold": entry.get("threshold"),
            "unit": entry.get("unit"), "aggregation": None,
            "measured": None, "artifact": None, "sha256": None,
            "status": "withheld", "code": None,
        }

        try:
            unit = entry.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                raise ContractError("unit_missing", cid)

            art = entry.get("artifact")
            if not isinstance(art, dict):
                raise ContractError("artifact_missing", cid)
            row["artifact"] = art.get("path")

            rel = art.get("path")
            path = _contained_path(base, rel)
            try:
                data = path.read_bytes()
            except OSError:
                raise ContractError("artifact_missing", rel) from None
            digest = sha256_bytes(data)
            want = art.get("sha256")
            if digest != want:
                raise ContractError("artifact_sha_mismatch",
                                    f"{Path(rel).name}:{digest[:12]}!={want[:12]}")
            try:
                artifact_doc = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ContractError("artifact_unparsable", rel) from None
            row["sha256"] = digest

            if reg_entry is not None:
                ref_crit = (reg_entry.get("criteria") or {}).get(str(cid))
                if not isinstance(ref_crit, dict):
                    raise ContractError("threshold_mismatch", f"{cid}:not_in_registry")
                if ref_crit.get("unit") != entry.get("unit"):
                    raise ContractError("operator_mismatch",
                                        f"{cid}:unit:{ref_crit.get('unit')}!={entry.get('unit')}")
                if ctype == "scalar":
                    if ref_crit.get("op") != entry.get("op"):
                        raise ContractError("operator_mismatch",
                                            f"{cid}:op:{ref_crit.get('op')}!={entry.get('op')}")
                    if canonical_json(ref_crit.get("threshold")) != canonical_json(entry.get("threshold")):
                        raise ContractError("threshold_mismatch",
                                            f"{cid}:threshold_mismatch")
                else:
                    # 非标量判据：比对容差参数
                    if ctype == "pointwise_band":
                        ref_tol = ref_crit.get("tolerance", {})
                        entry_tol = entry.get("tolerance", {})
                        if canonical_json(ref_tol) != canonical_json(entry_tol):
                            raise ContractError("threshold_mismatch",
                                                f"{cid}:tolerance_mismatch")
                        if ref_crit.get("pass_ratio_threshold") != entry.get("pass_ratio_threshold"):
                            raise ContractError("threshold_mismatch",
                                                f"{cid}:pass_ratio_threshold_mismatch")
                    elif ctype == "spectral_envelope":
                        ref_peak = ref_crit.get("peak_tolerance", {})
                        entry_peak = entry.get("peak_tolerance", {})
                        if canonical_json(ref_peak) != canonical_json(entry_peak):
                            raise ContractError("threshold_mismatch",
                                                f"{cid}:peak_tolerance_mismatch")
                    elif ctype == "field_fingerprint":
                        ref_stat_tol = ref_crit.get("stat_tolerance", {})
                        entry_stat_tol = entry.get("stat_tolerance", {})
                        if canonical_json(ref_stat_tol) != canonical_json(entry_stat_tol):
                            raise ContractError("threshold_mismatch",
                                                f"{cid}:stat_tolerance_mismatch")

            if ctype == "scalar":
                result = evaluate_scalar(entry, artifact_doc, base)
            elif ctype == "pointwise_band":
                result = evaluate_pointwise_band(entry, artifact_doc, base)
            elif ctype == "spectral_envelope":
                result = evaluate_spectral_envelope(entry, artifact_doc, base)
            elif ctype == "field_fingerprint":
                result = evaluate_field_fingerprint(entry, artifact_doc, base)
            else:
                raise ContractError("type_invalid", ctype)

            row["measured"] = result["measured"]
            row["sha256"] = result["sha256"]
            row["status"] = result["status"]
            row["code"] = result.get("code")
            row["aggregation"] = result.get("aggregation")
            if ctype == "pointwise_band":
                row["fail_intervals"] = result.get("fail_intervals", [])
            elif ctype == "spectral_envelope":
                row["fail_bands"] = result.get("fail_bands", [])

        except ContractError as exc:
            row["status"] = "withheld"
            row["code"] = exc.code
            issue(exc.code, cid, exc.detail)

        rows.append(row)

    if reg_entry is not None:
        registered = {str(k) for k in (reg_entry.get("criteria") or {})}
        declared = {str(e.get("id")) for e in criteria
                    if isinstance(e, dict) and e.get("id") is not None}
        dropped = sorted(registered - declared)
        if dropped:
            issue("criterion_dropped", None, ",".join(dropped))

    return _finish_v2(gate, gate.get("gate_id"), issues, rows,
                      registry is not None and reg_entry is not None, gate_path)


def _finish_v2(gate: dict | None, gate_id: str | None, issues: list[dict],
               rows: list[dict], registry_checked: bool,
               gate_path: str | Path | None) -> dict:
    """汇总判定。"""
    from .verify import PASS, FAIL, WITHHELD, INTEGRITY_CODES

    if any(i["code"] in INTEGRITY_CODES for i in issues):
        grade = WITHHELD
    elif any(r["status"] == "fail" for r in rows):
        grade = FAIL
    elif rows and all(r["status"] == "pass" for r in rows):
        grade = PASS
    else:
        grade = WITHHELD

    mission_id = gate.get("mission_id") if isinstance(gate, dict) else None
    env_fp = env_fingerprint()

    payload = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "grade": grade,
        "criteria_summary": [{"id": r["id"], "type": r.get("type", "scalar"),
                              "status": r["status"], "aggregation": r.get("aggregation")}
                             for r in rows],
    }

    return {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "grade": grade,
        "issues": issues,
        "criteria": rows,
        "gate_sha256": canonical_sha256({k: v for k, v in (gate or {}).items()
                                          if k != "env_fingerprint"}),
        "verdict_digest": canonical_sha256(payload),
        "env_fingerprint": env_fp,
        "registry_checked": registry_checked,
        "gate_path": str(gate_path) if gate_path is not None else None,
    }
