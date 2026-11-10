#!/usr/bin/env python3
"""physics_integration_verify —— 多物理面**集成验证与交叉互裁**层（域无关引擎）。

为什么需要这一层
----------------
``inheritance_gate`` 管单节点数值互裁，``campaign_integration_verify`` 管 artifact
schema × DAG 拓扑 × 验收门四类结局。两者都**不做物理面自洽**：一个多域战役里，
每个节点各自"数对"并不等于整套物理面"能拼在一起"。本层补的正是这个缺口，
提供四类**域无关**的机械判定：

  (1) **能量/量级自洽**（``check_balance`` / ``check_magnitude`` / ``check_ordering``）
      —— 每条守恒式 ``total vs Σ signed parts`` 的相对残差 ≤ 注入容差；每个量值落在
      注入的 ``log10`` 量级带内；跨量级序关系（``a<b`` / ``a>b`` / ``same_magnitude``）
      带注入最小间隔。
  (2) **耦合接口契约**（``check_interface``）—— 生产方端口与消费方端口的
      **量纲向量 / 帧(坐标系)等价类 / 形状 / 数据类型 / 符号约定**逐项比对；
      量纲不一致时允许一条**声明式桥接**（``bridge``：增益 + 增益单位 + 依据），
      要求 ``dim(bridge.gain_unit) ⊗ dim(producer) == dim(consumer)``。
  (3) **基准数据库**（``build_benchmark_suite``）—— 每条解析锚记录独立 ``sha256``，
      套件级 ``suite_digest`` 由全部条目摘要派生；摘要可重算、条目可复现。
  (4) **误差传播/灵敏度**（``propagate`` / ``rank_sensitivities``）—— 一阶（含协方差项）
      ``σ_y = sqrt(Σ(d_i σ_i)² + 2Σ_{i<j} ρ_ij d_i d_j σ_i σ_j)``，给出弹性
      ``S_i = d_i x_i / y``、方差份额、以及**两种排序口径**：
        * ``elasticity_rank``：按 ``|S|`` 降序（纯物理结构，与注入 σ 无关）；
        * ``variance_rank``：按 ``|S|·σ_rel`` 降序（工程不确定度预算）。
      两种口径的 Top-K 都与注入的**预声明期望**比对，排序平局规则固定
      （``|S|`` ↓，``|S|·σ_rel`` ↓，``id`` ↑），保证跨机器逐字节可复现。

零领域语义（TensorPlatform 铁律）
--------------------------------
本模块**不含任何领域词条 / 域判据 / 域算例**：量值、单位表、量级带、守恒式、
端口、帧等价类、传播项与期望排序**全部**来自注入的 manifest；单位表由调用方给出
（7 个 SI 基本量指数向量 + 到 SI 的标度）。测试夹具用合成域名（``N-A`` / ``alpha`` /
``unit_x``），故任何物理域可直接复用。

裁决判据（全部机算，落 ``criteria[]``）
--------------------------------------
  ``a_energy_self_consistent``  守恒残差全部 ≤ 容差 且 量级带全部命中 且 序关系全部成立
  ``b_interface_contracts``     全部耦合边契约通过（未声明桥接的量纲不一致=不通过），通过率=100%
  ``c_benchmark_db``            解析锚条目数 ≥ ``min_entries``，每条 ``sha256`` 可重算且唯一
  ``d_error_propagation``       每个 target 的域内排序命中预声明；全局两种口径 Top-K 命中预声明；
                                全部弹性符号/量级命中预声明；``σ_y`` 有限
  ``e_report``                  报告含六要素段（非空）+ 判据数字 + 审计链 + 局限

``verdict = "pass"`` 当且仅当五条全过。**未就绪绝不返回 0**（退出码 0/2/3：
0=pass，2=not_ready，3=输入非法）。

确定性：报告不取 wall clock（``generated_at`` 仅在 manifest 显式给出时写入），
``input_digest`` 由 manifest 规范化 JSON 派生 → 同一输入连跑两次逐字节相同。

CLI
---
    python3 ops/taskplane/physics_integration_verify.py \
        --manifest artifacts/p135w2/physics_manifest.json \
        --json-out artifacts/p135w2/integration_validation.json \
        --bench-out artifacts/p135w2/benchmark_suite.json \
        --md-out artifacts/p135w2/integration_validation.md
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

BASEDIMS = ("M", "L", "T", "I", "TH", "N", "J")
REL_TOL_DEFAULT = 0.05
SORT_DECIMALS = 6   # 排序键小数位（吸收中心差分噪声）


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def canonical(payload: Any) -> str:
    """规范化 JSON（key 排序、无空白）—— 摘要与逐字节复现的基础。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, payload: Any) -> None:
    import os

    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 单位/量纲
# ---------------------------------------------------------------------------
def load_units(table: Mapping[str, Any]) -> Dict[str, Tuple[List[int], float]]:
    """``{unit: {"dim":[7 ints], "scale": float}}`` → ``{unit: (dim, scale)}``。"""
    out: Dict[str, Tuple[List[int], float]] = {}
    for name, spec in table.items():
        dim = spec.get("dim")
        if not isinstance(dim, Sequence) or len(dim) != len(BASEDIMS):
            raise ValueError(f"unit {name!r}: dim 必须是 {len(BASEDIMS)} 个整数指数")
        dims = [int(x) for x in dim]
        scale = float(spec.get("scale", 1.0))
        if not (scale > 0.0 and math.isfinite(scale)):
            raise ValueError(f"unit {name!r}: scale 必须为有限正数")
        out[str(name)] = (dims, scale)
    return out


def unit_dim(units: Mapping[str, Tuple[List[int], float]], unit: str) -> List[int]:
    if unit not in units:
        raise KeyError(f"单位 {unit!r} 未在 manifest.units 中声明")
    return list(units[unit][0])


def to_si(units: Mapping[str, Tuple[List[int], float]], value: float, unit: str) -> float:
    if unit not in units:
        raise KeyError(f"单位 {unit!r} 未在 manifest.units 中声明")
    return float(value) * units[unit][1]


def dim_mul(a: Sequence[int], b: Sequence[int]) -> List[int]:
    return [int(x) + int(y) for x, y in zip(a, b)]


def dim_equal(a: Sequence[int], b: Sequence[int]) -> bool:
    return list(a) == list(b)


def dim_label(dim: Sequence[int]) -> str:
    parts = [f"{d}^{e}" if e != 1 else f"{d}" for d, e in zip(BASEDIMS, dim) if e]
    return "·".join(parts) or "1"


# ---------------------------------------------------------------------------
# (1) 能量/量级自洽
# ---------------------------------------------------------------------------
def check_magnitude(entry: Mapping[str, Any], units) -> Dict[str, Any]:
    """量值落在注入的 ``band_log10=[lo,hi]`` 内（比较 SI 标度后的 |value|）。"""
    value_si = to_si(units, entry["value"], entry["unit"])
    band = entry.get("band_log10")
    rec: Dict[str, Any] = {
        "entry_id": entry["entry_id"],
        "node_id": entry.get("node_id"),
        "quantity": entry.get("quantity"),
        "value": entry["value"],
        "unit": entry["unit"],
        "value_si": value_si,
        "log10_si": (math.log10(abs(value_si)) if value_si != 0 else None),
        "dim": unit_dim(units, entry["unit"]),
        "band_log10": list(band) if band else None,
        "ok": True,
        "reason": "",
    }
    if band is None:
        rec["ok"] = False
        rec["reason"] = "未声明 band_log10（量级自洽不可裁）"
        return rec
    if value_si == 0 or not math.isfinite(value_si):
        rec["ok"] = False
        rec["reason"] = "value 非有限或为零，量级不可判"
        return rec
    lo, hi = float(band[0]), float(band[1])
    logv = math.log10(abs(value_si))
    if not (lo <= logv <= hi):
        rec["ok"] = False
        rec["reason"] = f"log10={logv:.3f} 越出带 [{lo},{hi}]"
    return rec


def check_ordering(edges: Sequence[Mapping[str, Any]],
                   magnitudes: Mapping[str, Mapping[str, Any]], units) -> List[Dict[str, Any]]:
    """跨量级序关系：``a<b`` / ``a>b`` / ``same_magnitude``。"""
    out = []
    for edge in edges:
        a, b = edge["a"], edge["b"]
        rel = edge["relation"]
        gap_min = float(edge.get("min_gap_log10", 0.0))
        rec: Dict[str, Any] = {"edge_id": edge.get("edge_id", f"{a}~{b}"), "a": a, "b": b,
                               "relation": rel, "min_gap_log10": gap_min, "ok": True, "reason": ""}
        if a not in magnitudes or b not in magnitudes:
            rec["ok"] = False
            rec["reason"] = "端点量值未声明"
            out.append(rec)
            continue
        la = magnitudes[a]["log10_si"]
        lb = magnitudes[b]["log10_si"]
        if la is None or lb is None:
            rec["ok"] = False
            rec["reason"] = "端点量级不可判（零值/非有限）"
            out.append(rec)
            continue
        rec.update({"log10_a": la, "log10_b": lb})
        if rel == "a<b":
            ok = (lb - la) >= gap_min and lb > la
        elif rel == "a>b":
            ok = (la - lb) >= gap_min and la > lb
        elif rel == "same_magnitude":
            ok = abs(la - lb) <= gap_min
        else:
            ok = False
            rec["reason"] = f"未知 relation={rel!r}"
        rec["ok"] = bool(ok)
        if not ok and not rec["reason"]:
            rec["reason"] = f"序关系 {rel} 不成立（Δlog10={la - lb:+.3f}, 需 ≥{gap_min:g}）"
        out.append(rec)
    return out


def check_balance(balance: Mapping[str, Any], units) -> Dict[str, Any]:
    """守恒式闭合：``residual_rel = (total_si - Σ signed parts_si)/|total_si|``。"""
    total_unit = balance["total"]["unit"]
    total_si = to_si(units, balance["total"]["value"], total_unit)
    total_dim = unit_dim(units, total_unit)
    tol = float(balance.get("tol_rel", REL_TOL_DEFAULT))
    rec: Dict[str, Any] = {
        "balance_id": balance.get("balance_id"),
        "node_id": balance.get("node_id"),
        "total_si": total_si,
        "total_unit": total_unit,
        "tol_rel": tol,
        "parts": [],
        "ok": True,
        "reason": "",
    }
    if total_si == 0 or not math.isfinite(total_si):
        rec["ok"] = False
        rec["reason"] = "total 为零/非有限，残差无意义"
        return rec
    acc = 0.0
    for part in balance.get("parts", []):
        e = to_si(units, part["value"], part["unit"])
        sign = -1.0 if str(part.get("sign", "+")).strip() in ("-", "−") else 1.0
        pdim = unit_dim(units, part["unit"])
        dim_ok = dim_equal(pdim, total_dim)
        acc += sign * e
        rec["parts"].append({
            "label": part.get("label"),
            "value": part["value"],
            "unit": part["unit"],
            "sign": part.get("sign", "+"),
            "value_si": e,
            "signed_si": sign * e,
            "share": (sign * e) / total_si,
            "dim_matches_total": dim_ok,
        })
        if not dim_ok:
            rec["ok"] = False
            rec["reason"] = f"部件 {part.get('label')!r} 量纲 {dim_label(pdim)} 与守恒量 " \
                            f"{dim_label(total_dim)} 不一致"
    residual = (total_si - acc) / abs(total_si)
    rec["sum_parts_si"] = acc
    rec["residual_rel"] = residual
    rec["closure_ok"] = abs(residual) <= tol
    if rec["ok"] and not rec["closure_ok"]:
        rec["ok"] = False
        rec["reason"] = f"闭合残差 {residual:+.3%} 超出容差 {tol:.1%}"
    return rec


# ---------------------------------------------------------------------------
# (2) 耦合接口契约
# ---------------------------------------------------------------------------
def _frame_class_map(frame_classes: Sequence[Sequence[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for idx, group in enumerate(frame_classes or []):
        key = f"class{idx}"
        for name in group:
            out[str(name)] = key
    return out


def check_interface(edge: Mapping[str, Any], units,
                    frame_classes: Sequence[Sequence[str]] = ()) -> Dict[str, Any]:
    prod = edge["producer"]
    cons = edge["consumer"]
    fmap = _frame_class_map(frame_classes)
    violations: List[str] = []

    p_dim = unit_dim(units, prod["unit"])
    c_dim = unit_dim(units, cons["unit"])
    bridge = edge.get("bridge")
    if bridge is not None:
        g_dim = unit_dim(units, bridge["gain_unit"])
        gained = dim_mul(p_dim, g_dim)
        dim_ok = dim_equal(gained, c_dim)
        dim_note = (f"桥接 {bridge['gain']}×{bridge['gain_unit']} "
                    f"({dim_label(g_dim)}) 后 {dim_label(gained)} vs 消费方 {dim_label(c_dim)}")
    else:
        dim_ok = dim_equal(p_dim, c_dim)
        dim_note = f"生产 {dim_label(p_dim)} vs 消费 {dim_label(c_dim)}"
    if not dim_ok:
        violations.append(f"量纲不一致：{dim_note}")

    p_frame = str(prod.get("frame", ""))
    c_frame = str(cons.get("frame", ""))
    if p_frame != c_frame:
        pk = fmap.get(p_frame, f"::raw::{p_frame}")
        ck = fmap.get(c_frame, f"::raw::{c_frame}")
        if pk != ck:
            violations.append(f"帧不兼容：{p_frame!r} vs {c_frame!r}（无等价类桥接）")

    p_shape = list(prod.get("shape", []))
    c_shape = list(cons.get("shape", []))
    if bridge is not None and bridge.get("shape_map"):
        p_shape = list(bridge["shape_map"].get(canonical(p_shape), p_shape))
    if p_shape != c_shape:
        violations.append(f"形状不匹配：{p_shape} vs {c_shape}")

    p_dtype = prod.get("dtype")
    c_dtype = cons.get("dtype")
    if p_dtype is not None and c_dtype is not None and p_dtype != c_dtype:
        violations.append(f"数据类型不匹配：{p_dtype!r} vs {c_dtype!r}")

    p_sign = prod.get("sign_convention")
    c_sign = cons.get("sign_convention")
    if p_sign is not None and c_sign is not None and p_sign != c_sign:
        violations.append(f"符号约定不匹配：{p_sign!r} vs {c_sign!r}")

    return {
        "edge_id": edge.get("edge_id"),
        "producer": f"{prod.get('node_id')}.{prod.get('port')}",
        "consumer": f"{cons.get('node_id')}.{cons.get('port')}",
        "producer_unit": prod["unit"],
        "consumer_unit": cons["unit"],
        "dim": dim_label(c_dim),
        "bridge": bridge,
        "violations": violations,
        "ok": not violations,
    }


# ---------------------------------------------------------------------------
# (3) 基准数据库
# ---------------------------------------------------------------------------
def build_benchmark_suite(anchors: Sequence[Mapping[str, Any]], units,
                          suite_id: str = "benchmark-suite") -> Dict[str, Any]:
    """每条解析锚 → 独立 ``sha256``；套件 ``suite_digest`` 由条目摘要链派生。"""
    entries: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for a in anchors:
        body = {
            "entry_id": a["entry_id"],
            "node_id": a.get("node_id"),
            "quantity": a.get("quantity"),
            "value": a["value"],
            "unit": a["unit"],
            "anchor_kind": a.get("anchor_kind", "analytic"),
            "formula": a.get("formula"),
            "reference": a.get("reference"),
            "inputs": a.get("inputs", {}),
        }
        h = digest(body)
        dup = h in seen
        rec = dict(body)
        rec["value_si"] = to_si(units, a["value"], a["unit"])
        rec["dim"] = dim_label(unit_dim(units, a["unit"]))
        rec["sha256"] = h
        rec["duplicate_of"] = seen.get(h)
        rec["unique"] = not dup
        seen.setdefault(h, a["entry_id"])
        entries.append(rec)
    suite_digest = digest({"suite_id": suite_id, "entries": [e["sha256"] for e in entries]})
    return {
        "suite_id": suite_id,
        "n_entries": len(entries),
        "n_unique": sum(1 for e in entries if e["unique"]),
        "entries": entries,
        "suite_digest": suite_digest,
    }


def verify_benchmark_suite(suite: Mapping[str, Any], min_entries: int = 10) -> Dict[str, Any]:
    recomputed = digest({"suite_id": suite["suite_id"],
                         "entries": [e["sha256"] for e in suite["entries"]]})
    bad = [e["entry_id"] for e in suite["entries"]
           if digest({k: e[k] for k in ("entry_id", "node_id", "quantity", "value", "unit",
                                        "anchor_kind", "formula", "reference", "inputs")}) != e["sha256"]]
    dup = [e["entry_id"] for e in suite["entries"] if not e["unique"]]
    problems = []
    if suite["n_entries"] < min_entries:
        problems.append(f"条目数 {suite['n_entries']} < 下限 {min_entries}")
    if bad:
        problems.append(f"摘要不可重算：{bad}")
    if dup:
        problems.append(f"条目重复：{dup}")
    if recomputed != suite["suite_digest"]:
        problems.append("suite_digest 与条目摘要链不符")
    return {
        "n_entries": suite["n_entries"],
        "n_unique": suite["n_unique"],
        "min_entries": min_entries,
        "suite_digest": suite["suite_digest"],
        "recomputed_digest": recomputed,
        "broken_entries": bad,
        "duplicate_entries": dup,
        "ok": not problems,
        "reasons": problems,
    }


# ---------------------------------------------------------------------------
# (4) 误差传播 / 灵敏度
# ---------------------------------------------------------------------------
def propagate(target: Mapping[str, Any], units) -> Dict[str, Any]:
    """一阶误差传播（含协方差项）+ 弹性 + 方差份额。"""
    y = to_si(units, target["value"], target["unit"])
    terms = list(target.get("terms", []))
    var = 0.0
    scaled: List[Dict[str, Any]] = []
    for t in terms:
        d = float(t["derivative"])          # 输出单位 / 输入单位（SI 内）
        s = float(t["sigma"])
        x = float(t["value"])
        c = d * s
        var += c * c
        scaled.append({"input_id": t["input_id"], "symbol": t.get("symbol", t["input_id"]),
                       "value": x, "sigma": s, "sigma_rel": (abs(s / x) if x else None),
                       "derivative": d, "contribution_si": c,
                       "unit": t.get("unit"), "rationale": t.get("rationale")})
    cov = 0.0
    for i in range(len(scaled)):
        for j in range(i + 1, len(scaled)):
            rho = float(target.get("correlations", {}).get(
                f"{scaled[i]['input_id']}|{scaled[j]['input_id']}", 0.0))
            if rho:
                cov += 2.0 * rho * scaled[i]["contribution_si"] * scaled[j]["contribution_si"]
    var_total = var + cov
    sigma_y = math.sqrt(var_total) if var_total > 0 else 0.0
    for t in scaled:
        t["elasticity"] = (t["derivative"] * t["value"] / y) if y else None
        t["variance_share"] = ((t["contribution_si"] ** 2) / var_total) if var_total > 0 else None
        t["uncertainty_weight"] = (abs(t["elasticity"]) * t["sigma_rel"]) if (
            t["elasticity"] is not None and t["sigma_rel"] is not None) else None
        # 排序键取 6 位小数：中心差分噪声量级 1e-9，四舍五入后同弹性才算真平局
        t["abs_elasticity_sorted"] = (round(abs(t["elasticity"]), SORT_DECIMALS)
                                      if t["elasticity"] is not None else None)
        t["weight_sorted"] = (round(t["uncertainty_weight"], SORT_DECIMALS)
                              if t["uncertainty_weight"] is not None else None)
    return {
        "target_id": target["target_id"],
        "node_id": target.get("node_id"),
        "output": target.get("output"),
        "output_unit": target["unit"],
        "value_si": y,
        "sigma_y_si": sigma_y,
        "rel_sigma": (sigma_y / abs(y)) if y else None,
        "diagonal_variance": math.sqrt(var),
        "covariance_contribution": cov,
        "terms": scaled,
    }


def rank_sensitivities(targets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """两种排序口径（平局规则固定：|S| ↓, |S|·σ_rel ↓, id ↑）。"""
    elastic: List[Dict[str, Any]] = []
    weighted: List[Dict[str, Any]] = []
    for t in targets:
        for term in t["terms"]:
            if term["elasticity"] is None:
                continue
            elastic.append({"target_id": t["target_id"], "input_id": term["input_id"],
                            "abs_elasticity": abs(term["elasticity"]),
                            "elasticity": term["elasticity"],
                            "sigma_rel": term["sigma_rel"],
                            "_abs": term["abs_elasticity_sorted"],
                            "_w": term["weight_sorted"],
                            "key": f"{t['target_id']}:{term['input_id']}"})
            weighted.append(dict(elastic[-1]))
    # 平局规则（固定、可复现）：弹性口径 |S| ↓ → |S|·σ_rel ↓ → key ↑；
    # 方差口径 |S|·σ_rel ↓ → |S| ↓ → key ↑
    elastic.sort(key=lambda r: (-r["_abs"], -r["_w"], r["key"]))
    weighted.sort(key=lambda r: (-r["_w"], -r["_abs"], r["key"]))
    return {"elasticity_rank": elastic, "variance_rank": weighted}


# ---------------------------------------------------------------------------
# 表达式守恒交叉检查（白名单 AST，无 eval 注入面）
# ---------------------------------------------------------------------------
_ALLOWED_FUNCS = {"sqrt": math.sqrt, "abs": abs, "exp": math.exp, "log": math.log,
                  "log10": math.log10, "min": min, "max": max}
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
                  ast.Pow, ast.USub, ast.UAdd, ast.Call, ast.Name, ast.Constant, ast.Load)


def safe_eval(expr: str, names: Mapping[str, float]) -> float:
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"表达式含不允许的语法节点 {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ValueError("表达式只允许白名单函数")
    return float(_eval_node(tree.body, names))


def _eval_node(node, names):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("常量只能是数值")
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        if node.id not in names:
            raise KeyError(f"表达式引用了未声明的量 {node.id!r}")
        return float(names[node.id])
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, names)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a = _eval_node(node.left, names)
        b = _eval_node(node.right, names)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.Pow):
            return a ** b
        raise ValueError("不支持的二元运算")
    if isinstance(node, ast.Call):
        fn = _eval_node(node.func, names)
        return fn(*[_eval_node(a, names) for a in node.args])
    raise ValueError(f"不支持的节点 {type(node).__name__}")


def check_cross_checks(checks: Sequence[Mapping[str, Any]],
                       quantities: Mapping[str, Tuple[float, str]]) -> List[Dict[str, Any]]:
    out = []
    for chk in checks:
        expr, rel, rhs = chk["expr"], chk.get("relation", "lte"), float(chk["rhs"])
        tol = float(chk.get("tol_rel", REL_TOL_DEFAULT))
        # 量值 id 可能含点号（``node.port`` 形式），先最长优先替换成合法标识符占位符
        names: Dict[str, float] = {}
        substituted = expr
        for idx, key in enumerate(sorted(quantities, key=len, reverse=True)):
            if key in substituted:
                ph = f"q__{idx}"
                names[ph] = quantities[key][0]
                substituted = substituted.replace(key, ph)
        rec = {"check_id": chk.get("check_id"), "expr": expr, "relation": rel, "rhs": rhs,
               "tol_rel": tol, "note": chk.get("note"), "ok": True, "reason": ""}
        try:
            lhs = safe_eval(substituted, names)
        except Exception as exc:  # noqa: BLE001 - 表达式错误按不通过上报
            rec["ok"] = False
            rec["reason"] = f"表达式求值失败：{exc}"
            out.append(rec)
            continue
        rec["lhs"] = lhs
        if rel in ("eq", "=="):
            ok = abs(lhs - rhs) <= tol * max(abs(rhs), 1e-30)
        elif rel in ("lte", "<="):
            ok = lhs <= rhs * (1.0 + tol)
        elif rel in ("gte", ">="):
            ok = lhs >= rhs * (1.0 - tol)
        elif rel in ("lt", "<"):
            ok = lhs < rhs * (1.0 - tol)
        elif rel in ("gt", ">"):
            ok = lhs > rhs * (1.0 + tol)
        else:
            ok, rec["reason"] = False, f"未知 relation={rel!r}"
        rec["ok"] = bool(ok)
        if not ok and not rec["reason"]:
            rec["reason"] = f"lhs={lhs:.6g} 不满足 {rel} rhs={rhs:.6g} (tol {tol:.1%})"
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 主裁决
# ---------------------------------------------------------------------------
def verify(manifest: Mapping[str, Any], *, min_benchmark_entries: int = 10) -> Dict[str, Any]:
    units = load_units(manifest.get("units", {}))
    anchors = manifest.get("anchors", [])
    magnitudes = {a["entry_id"]: check_magnitude(a, units) for a in anchors}

    balances = [check_balance(b, units) for b in manifest.get("balances", [])]
    ordering = check_ordering(manifest.get("ordering", []), magnitudes, units)
    interfaces = [check_interface(e, units, manifest.get("frame_classes", []))
                  for e in manifest.get("interfaces", [])]

    suite = build_benchmark_suite(anchors, units, manifest.get("suite_id", "benchmark-suite"))
    suite_v = verify_benchmark_suite(suite, min_entries=min_benchmark_entries)

    propagated = [propagate(t, units) for t in manifest.get("propagation", {}).get("targets", [])]
    ranks = rank_sensitivities(propagated)
    exp = manifest.get("propagation", {}).get("expectations", {})
    top_k = int(exp.get("top_k", 5))

    per_target: List[Dict[str, Any]] = []
    for t in propagated:
        order = [x["input_id"] for x in sorted(
            t["terms"], key=lambda r: (-(r["abs_elasticity_sorted"] or 0.0),
                                       -(r["weight_sorted"] or 0.0),
                                       r["input_id"]))]
        want = exp.get("per_target_order", {}).get(t["target_id"])
        per_target.append({"target_id": t["target_id"], "order": order,
                           "expected": want,
                           "ok": (want is None) or (order == list(want))})

    global_sets = {
        "elasticity_rank": [r["key"] for r in ranks["elasticity_rank"][:top_k]],
        "variance_rank": [r["key"] for r in ranks["variance_rank"][:top_k]],
    }
    global_ok = {}
    for name, got in global_sets.items():
        want = exp.get(f"global_{name}_expected")
        global_ok[name] = {"top_k": top_k, "got": got, "expected": want,
                           "ok": (want is None) or (got == list(want))}

    sign_rows: List[Dict[str, Any]] = []
    for spec in exp.get("sign_expectations", []):
        tid, iid = spec["target_id"], spec["input_id"]
        row = {"target_id": tid, "input_id": iid, "want_sign": spec["sign"],
               "want_abs": spec.get("abs_elasticity"), "tol_rel": float(spec.get("tol_rel", 0.05)),
               "ok": False, "reason": ""}
        hit = next((t for t in propagated if t["target_id"] == tid), None)
        term = next((x for x in (hit["terms"] if hit else []) if x["input_id"] == iid), None)
        if term is None or term["elasticity"] is None:
            row["reason"] = "目标/参数未声明或弹性不可算"
        else:
            s = term["elasticity"]
            sign_ok = (s > 0) if spec["sign"] == "+" else (s < 0)
            abs_ok = (row["want_abs"] is None or
                      abs(abs(s) - float(row["want_abs"])) <= row["tol_rel"] * abs(float(row["want_abs"])))
            row["got_elasticity"] = s
            row["ok"] = bool(sign_ok and abs_ok)
            if not row["ok"]:
                row["reason"] = f"S={s:+.4f} 与期望 {spec['sign']}{row['want_abs']} 不符"
        sign_rows.append(row)

    cross = check_cross_checks(manifest.get("cross_checks", []),
                               {a["entry_id"]: (magnitudes[a["entry_id"]]["value_si"], a["unit"])
                                for a in anchors})

    # ---- 判据 ----
    a_ok = (all(m["ok"] for m in magnitudes.values()) and all(b["ok"] for b in balances)
            and all(o["ok"] for o in ordering))
    b_ok = bool(interfaces) and all(i["ok"] for i in interfaces)
    c_ok = suite_v["ok"]
    d_ok = (all(p["ok"] for p in per_target) and all(g["ok"] for g in global_ok.values())
            and all(s["ok"] for s in sign_rows) and all(x["ok"] for x in cross)
            and all(math.isfinite(t["sigma_y_si"]) for t in propagated))
    report = manifest.get("report", {})
    six = {k: bool(report.get(k)) for k in ("case", "method", "results", "figures", "caveats",
                                            "error_judgement")}
    e_ok = all(six.values())
    criteria = [
        {"id": "a_energy_self_consistent", "ok": bool(a_ok),
         "detail": {"n_magnitudes": len(magnitudes),
                    "n_magnitude_fail": sum(1 for m in magnitudes.values() if not m["ok"]),
                    "n_balances": len(balances),
                    "max_abs_residual": max([abs(b["residual_rel"]) for b in balances], default=None),
                    "n_ordering_fail": sum(1 for o in ordering if not o["ok"])}},
        {"id": "b_interface_contracts", "ok": bool(b_ok),
         "detail": {"n_edges": len(interfaces),
                    "pass_rate": (sum(1 for i in interfaces if i["ok"]) / len(interfaces)
                                  if interfaces else 0.0),
                    "failed": [i["edge_id"] for i in interfaces if not i["ok"]]}},
        {"id": "c_benchmark_db", "ok": bool(c_ok),
         "detail": {"n_entries": suite["n_entries"], "n_unique": suite["n_unique"],
                    "min_entries": min_benchmark_entries,
                    "suite_digest": suite["suite_digest"], "reasons": suite_v["reasons"]}},
        {"id": "d_error_propagation", "ok": bool(d_ok),
         "detail": {"top_k": top_k, "global": global_ok,
                    "per_target_fail": [p["target_id"] for p in per_target if not p["ok"]],
                    "sign_fail": [f"{s['target_id']}:{s['input_id']}" for s in sign_rows if not s["ok"]],
                    "cross_check_fail": [c["check_id"] for c in cross if not c["ok"]]}},
        {"id": "e_report", "ok": bool(e_ok),
         "detail": {"sections": six}},
    ]
    verdict = "pass" if all(c["ok"] for c in criteria) else "not_ready"

    report_out: Dict[str, Any] = {
        "module": "ops/taskplane/physics_integration_verify.py",
        "suite_id": manifest.get("suite_id"),
        "input_digest": digest(manifest),
        "unit_system": {"basedims": list(BASEDIMS), "n_units": len(units)},
        "magnitudes": list(magnitudes.values()),
        "balances": balances,
        "ordering": ordering,
        "interfaces": interfaces,
        "benchmark_suite": {k: suite[k] for k in ("suite_id", "n_entries", "n_unique", "suite_digest")},
        "benchmark_verification": suite_v,
        "propagation": propagated,
        "sensitivity": {"per_target": per_target, "global": global_ok,
                        "elasticity_top": ranks["elasticity_rank"][:top_k],
                        "variance_top": ranks["variance_rank"][:top_k],
                        "sign_expectations": sign_rows},
        "cross_checks": cross,
        "criteria": criteria,
        "verdict": verdict,
    }
    if manifest.get("generated_at"):
        report_out["generated_at"] = manifest["generated_at"]
    return report_out


def render_markdown(rep: Mapping[str, Any]) -> str:
    L: List[str] = []
    L.append(f"# 物理面集成验证与交叉互裁 — `{rep.get('suite_id')}`")
    L.append("")
    L.append(f"* 裁决：**{rep['verdict']}**")
    L.append(f"* input_digest：`{rep['input_digest']}`")
    L.append(f"* 基准库 suite_digest：`{rep['benchmark_suite']['suite_digest']}`")
    L.append("")
    L.append("## 判据")
    L.append("")
    L.append("| 判据 | 结果 | 关键数字 |")
    L.append("|---|---|---|")
    for c in rep["criteria"]:
        L.append(f"| `{c['id']}` | {'PASS' if c['ok'] else 'FAIL'} | "
                 f"`{json.dumps(c['detail'], ensure_ascii=False, sort_keys=True)[:300]}` |")
    L.append("")
    L.append("## 守恒闭合（能量/量纲）")
    L.append("")
    L.append("| 守恒式 | 节点 | 总量(SI) | Σ部件(SI) | 相对残差 | 容差 | 结果 |")
    L.append("|---|---|---|---|---|---|---|")
    for b in rep["balances"]:
        L.append(f"| `{b['balance_id']}` | {b['node_id']} | {b['total_si']:.6g} | "
                 f"{b.get('sum_parts_si', float('nan')):.6g} | "
                 f"{b.get('residual_rel', float('nan')):+.3%} | {b['tol_rel']:.1%} | "
                 f"{'PASS' if b['ok'] else 'FAIL'} |")
    L.append("")
    L.append("## 量级带")
    L.append("")
    L.append("| 锚 | log10(SI) | 带 | 结果 |")
    L.append("|---|---|---|---|")
    for m in rep["magnitudes"]:
        L.append(f"| `{m['entry_id']}` | {m['log10_si'] if m['log10_si'] is not None else 'n/a'} | "
                 f"{m['band_log10']} | {'PASS' if m['ok'] else 'FAIL'} |")
    L.append("")
    L.append("## 耦合接口契约")
    L.append("")
    L.append("| 边 | 生产→消费 | 量纲 | 桥接 | 结果 |")
    L.append("|---|---|---|---|---|")
    for i in rep["interfaces"]:
        br = "有" if i["bridge"] else "—"
        L.append(f"| `{i['edge_id']}` | {i['producer']} → {i['consumer']} | {i['dim']} | {br} | "
                 f"{'PASS' if i['ok'] else 'FAIL: ' + '; '.join(i['violations'])} |")
    L.append("")
    L.append("## 灵敏度（两种口径）")
    L.append("")
    L.append("| 排名 | 弹性口径 | \\|S\\| | 方差口径 | \\|S\\|·σ_rel |")
    L.append("|---|---|---|---|---|")
    el = rep["sensitivity"]["elasticity_top"]
    va = rep["sensitivity"]["variance_top"]
    for k in range(max(len(el), len(va))):
        e = el[k] if k < len(el) else {}
        v = va[k] if k < len(va) else {}
        L.append(f"| {k + 1} | `{e.get('key', '')}` | {e.get('abs_elasticity', 0):.4f} | "
                 f"`{v.get('key', '')}` | {v.get('abs_elasticity', 0) * (v.get('sigma_rel') or 0):.4f} |")
    L.append("")
    L.append("## 交叉检查")
    L.append("")
    L.append("| 检查 | 表达式 | 关系 | lhs | 结果 |")
    L.append("|---|---|---|---|---|")
    for c in rep["cross_checks"]:
        L.append(f"| `{c['check_id']}` | `{c['expr']}` | {c['relation']} {c['rhs']:.6g} | "
                 f"{c.get('lhs', float('nan')):.6g} | {'PASS' if c['ok'] else 'FAIL'} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="多物理面集成验证（域无关引擎）")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--json-out")
    ap.add_argument("--bench-out")
    ap.add_argument("--md-out")
    ap.add_argument("--min-benchmark-entries", type=int, default=10)
    args = ap.parse_args(argv)

    try:
        manifest = load_json(args.manifest)
        rep = verify(manifest, min_benchmark_entries=args.min_benchmark_entries)
    except (ValueError, KeyError) as exc:
        print(f"INPUT_INVALID: {exc}", file=sys.stderr)
        return 3

    if args.json_out:
        _write_json(args.json_out, rep)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(rep))
    if args.bench_out:
        units = load_units(manifest.get("units", {}))
        suite = build_benchmark_suite(manifest.get("anchors", []), units,
                                      manifest.get("suite_id", "benchmark-suite"))
        _write_json(args.bench_out, suite)

    print(f"verdict={rep['verdict']} criteria={[ (c['id'], c['ok']) for c in rep['criteria'] ]}")
    return 0 if rep["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())