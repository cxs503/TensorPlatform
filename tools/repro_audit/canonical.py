"""纯判定层：历史验收声明的重跑复证（P70-N08）。

设计约束（本模块是全审计唯一「判据」所在，故必须是纯函数、零 IO、stdlib-only）：

* **两类计量口径**（对齐 ``docs/REPRODUCIBILITY_CROSS_ISA.md`` 的 observable/diagnostic 二分）：
  - ``observed``：物理/交付量（阻力、效率、Cb、R² …），判据=相对容差 ``REL_TOL``（1e-6，
    任务书口径；报告自带更宽容差时由调用方覆盖）。
  - ``diagnostic``：近零抵消量（残差、守恒误差、对称性偏差），判据=绝对容差 ``ABS_TOL``
    （1e-9）。近零量的相对误差无意义（分母趋零），故必须另一套口径。
* **ULP/量化协议并联**：除阈值判据外，每个量还记录 ``ulp_distance`` 与 ``quantize`` 摘要，
  与 ``tools/repro_digest/ulp.py`` 同一算法（该模块可 import 时直接复用，否则用本文件内的
  等价实现；``tests/test_repro_audit.py`` 断言两者逐位一致）。
* **KPI 可加总**：``repro_rate`` 只由 pass / (pass + drift) 得出，blocked 单列不折算
  （任务书判据 (b)(d)）。``aggregate`` 同时输出「重算值」，供落库前做 |KPI-重算| ≤ 1e-9 对账。

漂移归因四分类（``attribute_drift``）：
  ``source_change`` / ``dependency_version`` / ``float_path`` / ``environment``。
判定只用**实测证据**（ablation 结果位），不做猜测：证据不足时落到 ``environment`` 并标
``note="unresolved"``，如实暴露未归因部分。
"""

from __future__ import annotations

import math
import struct
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ─────────────────────────── 判据常数（任务书口径） ───────────────────────────
REL_TOL = 1e-6                 # observed：相对容差
ABS_TOL = 1e-9                 # diagnostic：绝对容差
NEAR_ZERO = 1e-8               # |ref| ≤ NEAR_ZERO → 判为近零量（走绝对容差）
ULP_GATE = 65536               # 跨 ISA 协议声明的 ULP 门（REPRODUCIBILITY_CROSS_ISA.md §3.1）
QUANT_DIGITS = 12              # 协议声明的量化位数
MAX_DEPTH = 12                 # JSON 下钻深度上限（防病态嵌套）
DRIFT_DETAIL_CAP = 40          # 单节点上报的 drift 明细上限（集群 stdout 只有 ~8 KB）

ATTRIBUTION_CLASSES = ("source_change", "dependency_version", "float_path", "environment")

# ─────────────────────── ULP / 量化（与 protocol 同算法） ───────────────────────


def _bits(x: float) -> int:
    return struct.unpack("<q", struct.pack("<d", float(x)))[0]


def monotone_key(x: float) -> int:
    """把 double 映射到保序整数键线（含 ±0、跨零、负号翻转）。"""
    b = _bits(x)
    return b if b >= 0 else -(b & 0x7FFFFFFFFFFFFFFF)


def ulp_distance(a: float, b: float) -> int:
    """两 double 之间相隔的可表示数个数（NaN 对 NaN 记 0；一 NaN 记 ULP_GATE*10**6）。"""
    fa, fb = float(a), float(b)
    if math.isnan(fa) and math.isnan(fb):
        return 0
    if math.isnan(fa) or math.isnan(fb):
        return ULP_GATE * 10 ** 6
    if math.isinf(fa) or math.isinf(fb):
        return 0 if fa == fb else ULP_GATE * 10 ** 6
    return abs(monotone_key(fa) - monotone_key(fb))


def quantize(x: float, digits: int = QUANT_DIGITS) -> str:
    """规范十进制串：NaN/±Inf/±0 归一，其余 ``format(x, '.Ng')``。"""
    v = float(x)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if v == 0.0:
        return "0"
    return format(v, ".%dg" % int(digits))


def quantize_matches(a: float, b: float, digits: int = QUANT_DIGITS) -> bool:
    return quantize(a, digits) == quantize(b, digits)


# ─────────────────────────── 数值展平 / 判类 ───────────────────────────


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def flatten_numbers(obj: Any, prefix: str = "", depth: int = 0,
                    out: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """把嵌套 JSON 展平成 {点路径: float}。bool 不算数值（避免 True==1 的假等价）。"""
    if out is None:
        out = {}
    if depth > MAX_DEPTH:
        return out
    if is_number(obj):
        out[prefix or "."] = float(obj)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            key = "%s.%s" % (prefix, k) if prefix else str(k)
            flatten_numbers(v, key, depth + 1, out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            flatten_numbers(v, "%s[%d]" % (prefix, i), depth + 1, out)
    return out


def classify_quantity(ref: float) -> str:
    """近零（含 0）→ diagnostic（绝对容差）；其余 → observed（相对容差）。"""
    r = float(ref)
    if r == 0.0 or math.isnan(r) or abs(r) <= NEAR_ZERO:
        return "diagnostic"
    return "observed"


def compare_value(path: str, ref: float, cur: float,
                  rel_tol: float = REL_TOL, abs_tol: float = ABS_TOL) -> Dict[str, Any]:
    """单项判定：observed 走相对容差，diagnostic 走绝对容差；两种口径都不放宽。"""
    cls = classify_quantity(ref)
    r, c = float(ref), float(cur)
    if math.isnan(r) and math.isnan(c):
        abs_err = rel_err = 0.0
    elif math.isnan(r) or math.isnan(c):
        abs_err = rel_err = float("inf")
    else:
        abs_err = abs(c - r)
        rel_err = abs_err / abs(r) if r != 0.0 else (0.0 if abs_err == 0.0 else float("inf"))
    if cls == "observed":
        ok = rel_err <= rel_tol
        tol_used = {"rel": rel_tol}
    else:
        ok = abs_err <= abs_tol
        tol_used = {"abs": abs_tol}
    return {
        "path": path,
        "cls": cls,
        "ref": r,
        "cur": c,
        "abs_err": abs_err,
        "rel_err": rel_err,
        "ulp": ulp_distance(r, c),
        "quant_ok": quantize_matches(r, c),
        "verdict": "pass" if ok else "drift",
        "tol": tol_used,
    }


def compare_number_maps(ref: Mapping[str, float], cur: Mapping[str, float],
                        rel_tol: float = REL_TOL, abs_tol: float = ABS_TOL) -> Dict[str, Any]:
    """对两份展平数值表逐项比对。

    返回 ``{"items": [...], "counts": {...}, "structural": [...]}``；结构差异
    （ref 有 cur 无 / cur 新增）单独列 ``structural``——它们同样计入 drift 但不参与
    数值容差判定（否则「少算了一个量」会被静默通过）。
    """
    items: List[Dict[str, Any]] = []
    structural: List[Dict[str, Any]] = []
    for path in sorted(ref):
        if path not in cur:
            structural.append({"path": path, "kind": "missing_in_rerun", "ref": ref[path]})
            continue
        items.append(compare_value(path, ref[path], cur[path], rel_tol, abs_tol))
    for path in sorted(set(cur) - set(ref)):
        structural.append({"path": path, "kind": "added_in_rerun", "cur": cur[path]})
    n_pass = sum(1 for it in items if it["verdict"] == "pass")
    return {
        "items": items,
        "structural": structural,
        "counts": {
            "compared": len(items),
            "pass": n_pass,
            "drift": len(items) - n_pass,
            "structural": len(structural),
            "observed": sum(1 for it in items if it["cls"] == "observed"),
            "diagnostic": sum(1 for it in items if it["cls"] == "diagnostic"),
            "max_ulp": max((it["ulp"] for it in items), default=0),
            "max_rel": max((it["rel_err"] for it in items if it["cls"] == "observed"), default=0.0),
        },
    }


def compare_runs(run_a: Mapping[str, float], run_b: Mapping[str, float]) -> Dict[str, Any]:
    """两次重跑的逐项一致性（判据 (c)：drift 必须在两次重跑中一致）。"""
    cmp = compare_number_maps(run_a, run_b, REL_TOL, ABS_TOL)
    return {
        "drift_paths": sorted(it["path"] for it in cmp["items"] if it["verdict"] == "drift")
        + sorted(s["path"] for s in cmp["structural"]),
        "counts": cmp["counts"],
    }


# ─────────────────────────── 漂移归因 ───────────────────────────


def attribute_drift(item: Mapping[str, Any], ev: Mapping[str, Any]) -> Dict[str, str]:
    """给单个 drift 项打四分类标签 + 证据串（只用实测证据位，不猜）。

    证据位（缺省=未做，None 表示未知，不参与判定）：
      ``workdir_differs_from_commit`` / ``workdir_replay_matches_ref``   → 源码变更
      ``local_same_isa_matches_ref`` / ``numeric_stack_differs``          → 依赖版本
      ``threads1_matches_ref``                                            → 环境（线程数）
      ``ulp`` ≤ ULP_GATE                                                  → 浮点路径（bit 级）
    """
    ulp = int(item.get("ulp") or 0)
    if ev.get("workdir_differs_from_commit") and ev.get("workdir_replay_matches_ref"):
        return {"class": "source_change",
                "evidence": "作者工作树文件与提交内容不一致，且用工作树版本重放即命中报告值"}
    if ev.get("source_substitution_matches_ref"):
        return {"class": "source_change",
                "evidence": "把被改写的源文件换成作者工作树版本后重放命中报告值（提交版本≠作者口径）"}
    if ev.get("threads1_matches_ref") and not ev.get("default_threads_matches_ref"):
        return {"class": "environment",
                "evidence": "线程数固定为 1 时命中报告值，默认线程数下漂移 → 归约顺序/并行归因"}
    if ev.get("local_same_isa_matches_ref") and ev.get("numeric_stack_differs"):
        return {"class": "dependency_version",
                "evidence": "同 ISA 控制机（numpy/scipy 版本同作者）重放命中，集群节点（版本不同）漂移"}
    if ulp and ulp <= ULP_GATE:
        return {"class": "float_path",
                "evidence": "漂移 %d ULP ≤ 协议门 %d（bit 级，非物理差异）" % (ulp, ULP_GATE)}
    if ev.get("local_same_isa_matches_ref"):
        return {"class": "environment",
                "evidence": "同 ISA 控制机命中而集群节点漂移，且未定位到版本/线程因子"}
    return {"class": "environment", "evidence": "证据不足（note=unresolved）：未找到可复现的归因因子"}


# ─────────────────────────── 节点判级 / KPI 聚合 ───────────────────────────


def node_verdict(numeric: Optional[Mapping[str, Any]], pytest_info: Optional[Mapping[str, Any]],
                 blocked_reason: Optional[str] = None) -> str:
    """节点级判决：blocked 只在「无任何可执行证据」时给出，绝不折算进通过率。"""
    if blocked_reason:
        return "blocked"
    has_numeric = bool(numeric and numeric.get("counts", {}).get("compared", 0) > 0)
    has_pytest = bool(pytest_info and pytest_info.get("collected", 0) > 0)
    if not has_numeric and not has_pytest:
        return "blocked"
    if numeric and (numeric.get("counts", {}).get("drift", 0) > 0
                    or numeric.get("counts", {}).get("structural", 0) > 0):
        return "drift"
    if pytest_info and pytest_info.get("regressions", 0) > 0:
        return "drift"
    return "pass"


def aggregate(nodes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """把逐节点工件聚成 KPI。repro_rate = pass / (pass + drift)，blocked 分母外单列。"""
    n_pass = sum(1 for n in nodes if n.get("status") == "pass")
    n_drift = sum(1 for n in nodes if n.get("status") == "drift")
    n_blocked = sum(1 for n in nodes if n.get("status") == "blocked")
    denom = n_pass + n_drift
    rate = (n_pass / denom) if denom else 0.0
    items_total = items_pass = items_drift = 0
    for n in nodes:
        c = (n.get("numeric") or {}).get("counts") or {}
        items_total += int(c.get("compared", 0))
        items_pass += int(c.get("pass", 0))
        items_drift += int(c.get("drift", 0)) + int(c.get("structural", 0))
    return {
        "nodes_total": len(nodes),
        "nodes_pass": n_pass,
        "nodes_drift": n_drift,
        "nodes_blocked": n_blocked,
        "rate_denominator": denom,
        "repro_rate": rate,
        # 供落库前对账：用同一 JSON 重算一遍，|KPI - 重算| 必须 ≤ 1e-9
        "repro_rate_recomputed": (n_pass / denom) if denom else 0.0,
        "items_compared": items_total,
        "items_pass": items_pass,
        "items_drift": items_drift,
        "item_pass_rate": (items_pass / items_total) if items_total else 0.0,
    }


def kpi_consistency(kpi: Mapping[str, Any]) -> Dict[str, Any]:
    """落库前自检：KPI 与「从逐节点状态重算」的差。"""
    recomputed = kpi.get("repro_rate_recomputed")
    kpi_val = kpi.get("repro_rate")
    delta = abs(float(kpi_val) - float(recomputed)) if recomputed is not None else None
    return {"kpi": kpi_val, "recomputed": recomputed, "delta": delta,
            "ok": delta is not None and delta <= 1e-9}