#!/usr/bin/env python3
"""验收阈值口径注册表 + 派生一致性门 + 漂移门（平台共性机制，零领域语义）。

问题（为什么阈值必须变成"函数"）
--------------------------------
平台的每一次"验收通过"都挂在一个数上："误差 ≤ 15%""kill rate ≥ 0.90"
"退化解 ≤ 3%"。这些数是**判据**，但历史上它们以字面量形式散落在代码与配置里，
没人能回答三个问题：

1. **出处**：这个数从哪来？（解析界 / 文献 / 规范条款 / 实测标定 / 拍脑袋）
2. **依赖量**：它随什么变？网格分辨率 h、阶数 p、统计样本数 N、相对容差 ε 变了，
   同一个数还成立吗？
3. **可重推导**：给定 h，能否算出阈值应当是多少，并拿实测误差去对照？

没有这三问，阈值就退化成"当年恰好能过的那条线"——它会在某个分辨率、某个样本量
上悄悄失效，而门禁照旧变绿。

本模块把这三问变成机器判据（八件套）
------------------------------------
``schemas/threshold_registry.schema.json``
    注册表契约。每条判据必须带 ``source_type``（五类）、``source_ref``、
    ``depends_on``（依赖量与指数）、``derivation``（C·h^p 显式表达式）、
    ``review_date`` 与**显式的** ``decidable`` 标注。
``load_registry`` / ``validate``
    结构 + 语义校验（含「表达式指数 == depends_on 指数」「threshold == 表达式在
    reference_h 处的取值」两条交叉一致性）。
``source_ref_status``
    来源指针不腐烂：``<path>#sha256:<hex>#<pointer>`` 的文件必须存在且 sha256 相符；
    改了被引文件却没更新引用 → ``SHA_DRIFT``（这条判据的"有来源"资格当场失效）。
``classify_entry``
    可裁/不可裁判定。``heuristic`` 与 ``empirical`` 一律不可裁（显式剔除）；
    阈值恰好等于实测误差的**同义反复**判据被判 ``empirical``（负控 C7）。
``derivation_check``
    派生一致性：对声明 O(h^p) 的判据，跑 h→h/2→h/4… 扫描，用 Richardson 式
    对数线性拟合反演实测衰减指数，``|实测-p| ≤ ORDER_TOL(0.3)``。
``rederivation_closure``
    重推导闭环：把 ``threshold(h)=C·h^p`` 的预测值投到 h/2、h/4 两档，与该档实测
    误差比 ``ratio`` 必须落在 ``RATIO_BAND=[0.5,2]`` 且同向（两边同步下降）。
``build_lock`` / ``drift_check``
    漂移门：注册表的**语义指纹**入锁文件；``threshold`` 被改而 ``source_ref``
    未变 → RED；仅注释/格式化/键序变化 → 指纹不变（CLEAR，C4 假阳性 0）。
``coverage_ledger``
    两个覆盖率数字：「有来源阈值占比」（来源可验：文件在 + sha 符）与
    「可派生阈值占比」（有表达式且指数可用）。

零领域语义纪律
--------------
本模块与 ``tools/criteria/probes.py``、schema、测试夹具里**不出现任何领域词条**：
所有"实测"来自纯数学合成算子（初等函数定积分 / 一阶初值问题的已知阶离散误差）；
领域语义只能作为 ``source_ref`` 字符串（路径+sha+指针）注入。域词表若需过滤，
从 ``config/domain_word_denylist.json`` 以数据形式读入，绝不硬编码。

用法
----
    python3 -m tools.criteria.threshold_registry validate  --registry data/criteria/threshold_registry.json
    python3 -m tools.criteria.threshold_registry lock      --registry data/criteria/threshold_registry.json
    python3 -m tools.criteria.threshold_registry drift     --registry ... --lock ...
    python3 -m tools.criteria.threshold_registry derive    --registry ...
    python3 -m tools.criteria.threshold_registry coverage  --registry ...
    python3 -m tools.criteria.threshold_registry gate      --registry ... --lock ...

退出码：0 = 绿；1 = 红（有 blocking finding）；2 = 输入缺失/解析失败。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]

SCHEMA_VERSION = "threshold-registry/1.0"
LOCK_SCHEMA_VERSION = "threshold-registry-lock/1.0"
DEFAULT_REGISTRY = ROOT / "data" / "criteria" / "threshold_registry.json"
DEFAULT_LOCK = ROOT / "data" / "criteria" / "threshold_registry.lock.json"
DEFAULT_SCHEMA = ROOT / "schemas" / "threshold_registry.schema.json"

SOURCE_TYPES: Tuple[str, ...] = ("analytic", "literature", "standard", "empirical", "heuristic")
COMPARATORS: Tuple[str, ...] = ("<=", ">=", "<", ">", "==", "!=")
#: 只有这三类是「合格判据集合」的候选：有先验依据才能拿来裁人。
DECIDABLE_SOURCE_TYPES: Tuple[str, ...] = ("analytic", "literature", "standard")

#: 语义指纹忽略的键（注释/格式类，改了不许判红 —— 判据 C4）。
COMMENT_KEYS = frozenset(
    {
        "_comment",
        "comment",
        "comments",
        "note",
        "notes",
        "formatting_note",
        "_format",
        "purpose",
        "comparator_note",
    }
)

ORDER_TOL = 0.3  # 判据 C2：实测衰减指数与声明 p 的允许偏差
RATIO_BAND = (0.5, 2.0)  # 判据 C5：预测阈值 / 实测误差 的允许带
TAUTOLOGY_REL_TOL = 0.01  # 判据 C7：阈值 ≈ 实测误差（1% 内）即判同义反复
#: 判据未声明 reference_h 时，取实测误差的默认分辨率（同义反复检测用）
DEFAULT_MEASURE_H = 0.125
FLOAT_SIG = 12  # 数值归一化的有效位数（0.15 / 0.150 / 1.5e-1 同指纹）


# --------------------------------------------------------------------------- #
# 1. JSONC 读取（注册表允许注释，注释不进语义指纹）
# --------------------------------------------------------------------------- #
def strip_jsonc(text: str) -> str:
    """去掉 ``//`` 行注释、``/* */`` 块注释与尾随逗号；字符串内不动。"""
    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    stripped = "".join(out)
    # 尾随逗号：,}  ,]
    return re.sub(r",(\s*[}\]])", r"\1", stripped)


def load_registry(path: Path | str = DEFAULT_REGISTRY) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(strip_jsonc(text))
    except json.JSONDecodeError as exc:  # pragma: no cover - 输入错误路径
        raise ValueError(f"注册表不是合法 JSON/JSONC: {path}: {exc}") from exc
    if not isinstance(doc, dict) or "criteria" not in doc:
        raise ValueError(f"注册表缺 criteria 数组: {path}")
    return doc


def load_schema(path: Path | str = DEFAULT_SCHEMA) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 2. 结构 + 语义校验
# --------------------------------------------------------------------------- #
def _validate_with_jsonschema(doc: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    try:
        import jsonschema
    except Exception:  # pragma: no cover - 无 jsonschema 时退化为手写校验
        return []
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in sorted(validator.iter_errors(doc), key=lambda e: str(e.absolute_path))
    ]


def _expr_identifiers(expr: str) -> List[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"derivation 表达式不可解析: {expr!r}: {exc}") from exc
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    return sorted(set(names))


def eval_derivation(derivation: Dict[str, Any], h: float) -> float:
    """安全求值 C·h^p：只允许 + - * / ** 与括号、常量 C、变量 h。"""
    expr = str(derivation["expression"])
    coeff = float(derivation["coefficient"])
    tree = ast.parse(expr, mode="eval")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id == "C":
                return coeff
            if node.id == "h":
                return float(h)
            raise ValueError(f"表达式含非法变量 {node.id!r}")
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
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
            raise ValueError(f"表达式含非法运算 {type(node.op).__name__}")
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return v
            raise ValueError("表达式含非法一元运算")
        raise ValueError(f"表达式含非法结点 {type(node).__name__}")

    return float(ev(tree))


def expression_order(expr: str) -> Optional[float]:
    """从 ``C*h**p`` / ``C*h*h`` 里抽出 h 的幂次；无法判定返回 None。"""
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None

    def walk(node: ast.AST) -> Optional[float]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return _sum_orders(walk(node.left), walk(node.right))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            base, expo = node.left, node.right
            if isinstance(base, ast.Name) and base.id == "h" and isinstance(expo, ast.Constant):
                if isinstance(expo.value, (int, float)):
                    return float(expo.value)
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return _sum_orders(walk(node.left), walk(node.right))
        if isinstance(node, ast.Name) and node.id == "h":
            return 1.0
        if isinstance(node, ast.Name) and node.id == "C":
            return 0.0  # 常数因子不改变 h 的幂次
        if isinstance(node, ast.Constant):
            return 0.0
        return None

    def _sum_orders(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return a + b

    return walk(tree)


def validate(registry: Dict[str, Any], schema_path: Path | str = DEFAULT_SCHEMA) -> Dict[str, Any]:
    """返回 {ok, errors, n_criteria}。errors 每条都带 criterion_id 便于定位。"""
    errors: List[str] = []
    schema = load_schema(schema_path)
    errors.extend(_validate_with_jsonschema(registry, schema))

    seen = set()
    measured = measured_map(registry)
    for entry in registry.get("criteria", []):
        cid = entry.get("criterion_id", "<missing>")
        if cid in seen:
            errors.append(f"{cid}: criterion_id 重复")
        seen.add(cid)
        # ---- 交叉一致性 1：表达式指数 == depends_on[h].exponent
        deriv = entry.get("derivation")
        if deriv:
            try:
                ids = _expr_identifiers(deriv["expression"])
            except ValueError as exc:
                errors.append(f"{cid}: {exc}")
                ids = []
            dep_params = {d.get("param"): d.get("exponent") for d in entry.get("depends_on", [])}
            for name in ids:
                if name not in ("C", "h"):
                    errors.append(f"{cid}: 表达式变量 {name!r} 未在 depends_on 声明")
            if "h" in ids:
                if "h" not in dep_params:
                    errors.append(f"{cid}: 表达式含 h 但 depends_on 无 h 依赖")
                else:
                    decl = expression_order(str(deriv["expression"]))
                    if decl is None:
                        errors.append(f"{cid}: 无法从表达式判定 h 的幂次")
                    elif abs(float(dep_params["h"]) - decl) > 1e-12:
                        errors.append(
                            f"{cid}: 表达式 h 幂次 {decl} != depends_on[h].exponent {dep_params['h']}"
                        )
            # ---- 交叉一致性 2：threshold == 表达式在 reference_h 处的值
            try:
                pred = eval_derivation(deriv, float(deriv["reference_h"]))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cid}: 表达式求值失败: {exc}")
                pred = None
            if pred is not None:
                thr = entry.get("threshold")
                if not isinstance(thr, (int, float)) or isinstance(thr, bool):
                    errors.append(f"{cid}: threshold 非数值")
                else:
                    rel = abs(float(thr) - pred) / max(abs(pred), 1e-300)
                    if rel > 1e-6:
                        errors.append(
                            f"{cid}: threshold={thr} 与表达式在 h={deriv['reference_h']} 处的预测 {pred:.6g} 不符（相对差 {rel:.3g}）"
                        )
        # ---- 交叉一致性 3：可裁标注不许说谎（C1/C7 的结构前提）
        verdict = classify_entry(entry, measured.get(entry.get("criterion_id")))
        if bool(entry.get("decidable")) is not bool(verdict["decidable"]):
            errors.append(
                f"{cid}: decidable={entry.get('decidable')} 与判定 {verdict['decidable']}"
                f"（{verdict['reason']}）不一致"
            )
    return {"ok": not errors, "errors": errors, "n_criteria": len(registry.get("criteria", []))}


# --------------------------------------------------------------------------- #
# 3. 来源指针（不腐烂检查）
# --------------------------------------------------------------------------- #
REF_RE = re.compile(r"^(?P<path>[^#]+)#sha256:(?P<sha>[0-9a-f]{8,64})#(?P<pointer>.*)$")


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_ref_parts(ref: str) -> Optional[Dict[str, str]]:
    m = REF_RE.match(ref or "")
    if not m:
        return None
    return {"path": m.group("path"), "sha256": m.group("sha"), "pointer": m.group("pointer")}


def source_ref_status(ref: str, root: Optional[Path | str] = None) -> Dict[str, Any]:
    """OK / MALFORMED / MISSING_FILE / SHA_DRIFT。"""
    root = ROOT if root is None else root
    parts = source_ref_parts(ref)
    if parts is None:
        return {"status": "MALFORMED", "detail": "需要 <path>#sha256:<hex>#<pointer>"}
    target = Path(root) / parts["path"]
    if not target.is_file():
        return {"status": "MISSING_FILE", "detail": str(parts["path"])}
    actual = sha256_file(target)
    if not actual.startswith(parts["sha256"].lower()):
        return {
            "status": "SHA_DRIFT",
            "detail": f"记录 {parts['sha256'][:12]}… 实际 {actual[:12]}…",
        }
    return {"status": "OK", "detail": parts["path"]}


def make_source_ref(path: Path | str, pointer: str, root: Optional[Path | str] = None) -> str:
    root = ROOT if root is None else root
    rel = str(Path(path).resolve().relative_to(Path(root).resolve()))
    return f"{rel}#sha256:{sha256_file(Path(root) / rel)}#{pointer}"


# --------------------------------------------------------------------------- #
# 4. 可裁/不可裁判定（含负控 C7）
# --------------------------------------------------------------------------- #
def classify_entry(entry: Dict[str, Any], measured: Optional[float] = None) -> Dict[str, Any]:
    """判定一条判据是否进入「合格判据集合」。

    顺序很重要：**同义反复优先**。把阈值写死成恰好等于实测误差的判据，无论它自称
    什么来源，都只能判 ``empirical`` 并剔除——它裁的不是模型，是它自己。
    """
    st = entry.get("source_type")
    thr = entry.get("threshold")
    if measured is not None and isinstance(thr, (int, float)) and not isinstance(thr, bool):
        denom = max(abs(float(measured)), 1e-300)
        if abs(float(thr) - float(measured)) <= TAUTOLOGY_REL_TOL * denom:
            return {
                "decidable": False,
                "effective_source_type": "empirical",
                "reason": "tautology: threshold==measured error（阈值写死成实测误差，不可裁）",
                "forced": True,
            }
    if st == "heuristic":
        return {
            "decidable": False,
            "effective_source_type": st,
            "reason": "source_type=heuristic（拍脑袋/调参得来，不可裁）",
            "forced": False,
        }
    if st == "empirical":
        return {
            "decidable": False,
            "effective_source_type": st,
            "reason": "source_type=empirical（仅拟合实测，无先验依据，不可裁）",
            "forced": False,
        }
    if st not in DECIDABLE_SOURCE_TYPES:
        return {
            "decidable": False,
            "effective_source_type": st,
            "reason": f"source_type={st!r} 不在可裁集合 {DECIDABLE_SOURCE_TYPES}",
            "forced": False,
        }
    if not entry.get("source_ref"):
        return {
            "decidable": False,
            "effective_source_type": st,
            "reason": "缺 source_ref（无出处）",
            "forced": False,
        }
    return {
        "decidable": True,
        "effective_source_type": st,
        "reason": f"source_type={st} 且有 source_ref",
        "forced": False,
    }


def measured_map(registry: Dict[str, Any]) -> Dict[str, float]:
    """给每条绑定了探针的判据取**该判据参考分辨率处**的实测误差。

    同义反复检测（C7）问的是"阈值是否恰好等于它要裁的那个量在自身参考点的实测值"，
    所以取样分辨率取 ``derivation.reference_h``；不可派生的判据用 ``DEFAULT_MEASURE_H``。
    """
    from tools.criteria.probes import get_probe

    out: Dict[str, float] = {}
    for entry in registry.get("criteria", []):
        pid = entry.get("probe")
        if not pid:
            continue
        deriv = entry.get("derivation") or {}
        h = float(deriv.get("reference_h", DEFAULT_MEASURE_H))
        if h <= 0:  # 参考分辨率非法 → 没有可信实测点（结构校验会另行判红）
            continue
        try:
            out[entry["criterion_id"]] = get_probe(str(pid)).error(h)
        except (KeyError, ValueError, ZeroDivisionError):
            continue
    return out


def decidable_ids(registry: Dict[str, Any], measured: Optional[Dict[str, float]] = None) -> List[str]:
    measured = measured_map(registry) if measured is None else measured
    return [
        e["criterion_id"]
        for e in registry.get("criteria", [])
        if classify_entry(e, measured.get(e["criterion_id"]))["decidable"]
    ]


# --------------------------------------------------------------------------- #
# 5. 派生一致性引擎（C2）与重推导闭环（C5）
# --------------------------------------------------------------------------- #
def estimate_order(hs: Sequence[float], errors: Sequence[float]) -> Dict[str, float]:
    """log|e| ~ p·log h 的最小二乘斜率 + R²。"""
    if len(hs) < 2 or len(hs) != len(errors):
        raise ValueError("需要 ≥2 档且长度一致")
    xs = [math.log(h) for h in hs]
    ys = [math.log(e) for e in errors]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return {"order": slope, "r2": r2, "n_levels": n}


def derivation_check(
    registry: Dict[str, Any],
    h0: float = 0.25,
    levels: int = 6,
) -> Dict[str, Any]:
    """C2：对声明 O(h^p) 的判据实测衰减指数，逐条给偏差与判定。"""
    from tools.criteria.probes import get_probe

    rows: List[Dict[str, Any]] = []
    for entry in registry.get("criteria", []):
        if entry.get("source_type") != "analytic":
            continue
        deriv = entry.get("derivation") or {}
        deps = {d["param"]: float(d["exponent"]) for d in entry.get("depends_on", [])}
        if "h" not in deps:
            continue
        declared = deps["h"]
        pid = entry.get("probe")
        if not pid:
            rows.append(
                {
                    "criterion_id": entry["criterion_id"],
                    "declared_order": declared,
                    "probe": None,
                    "status": "UNCHECKED_NO_PROBE",
                }
            )
            continue
        try:
            probe = get_probe(str(pid))
        except KeyError as exc:
            rows.append(
                {
                    "criterion_id": entry["criterion_id"],
                    "declared_order": declared,
                    "probe": pid,
                    "status": "UNCHECKED_UNKNOWN_PROBE",
                    "detail": str(exc),
                }
            )
            continue
        hs, es = probe.error_series(h0, levels)
        fit = estimate_order(hs, es)
        deviation = abs(fit["order"] - declared)
        rows.append(
            {
                "criterion_id": entry["criterion_id"],
                "declared_order": declared,
                "probe": pid,
                "observed_order": round(fit["order"], 6),
                "deviation": round(deviation, 6),
                "r2": round(fit["r2"], 9),
                "h_series": [f"{h:.8g}" for h in hs],
                "error_series": [f"{e:.6e}" for e in es],
                "status": "PASS" if deviation <= ORDER_TOL else "FAIL",
            }
        )
    checked = [r for r in rows if r["status"] in ("PASS", "FAIL")]
    failed = [r for r in checked if r["status"] == "FAIL"]
    unchecked = [r for r in rows if r["status"].startswith("UNCHECKED")]
    return {
        "ok": not failed and not unchecked,
        "order_tol": ORDER_TOL,
        "n_checked": len(checked),
        "n_failed": len(failed),
        "n_unchecked": len(unchecked),
        "max_deviation": max((r["deviation"] for r in checked), default=None),
        "rows": rows,
    }


def rederivation_closure(registry: Dict[str, Any], levels: int = 2) -> Dict[str, Any]:
    """C5：threshold(h)=C·h^p 的预测值 vs h/2、h/4 实测误差。

    两档都要求 ``ratio = 预测/实测 ∈ RATIO_BAND`` 且**同向**：h 减半时预测与实测
    都下降（同一符号的相对变化），否则说明声明的阶数方向都错了。
    """
    from tools.criteria.probes import get_probe

    lo, hi = RATIO_BAND
    rows: List[Dict[str, Any]] = []
    for entry in registry.get("criteria", []):
        deriv = entry.get("derivation") or {}
        pid = entry.get("probe")
        if not deriv or not pid:
            continue
        try:
            probe = get_probe(str(pid))
        except KeyError as exc:
            rows.append(
                {
                    "criterion_id": entry["criterion_id"],
                    "probe": pid,
                    "expression": deriv.get("expression"),
                    "coefficient": deriv.get("coefficient"),
                    "reference_h": deriv.get("reference_h"),
                    "points": [],
                    "status": "UNKNOWN_PROBE",
                    "detail": str(exc),
                }
            )
            continue
        h_ref = float(deriv["reference_h"])
        prev = None
        passed = True
        pts: List[Dict[str, Any]] = []
        for k in range(1, levels + 1):
            h = h_ref / (2.0 ** k)
            pred = eval_derivation(deriv, h)
            meas = probe.error(h)
            ratio = pred / meas
            ok = lo <= ratio <= hi
            passed = passed and ok
            if prev is not None:
                same_dir = (pred - prev["predicted"]) * (meas - prev["measured"]) > 0
                passed = passed and same_dir
            else:
                same_dir = True
            pts.append(
                {
                    "h": h,
                    "predicted": pred,
                    "measured": meas,
                    "ratio": round(ratio, 6),
                    "in_band": ok,
                    "same_direction": same_dir,
                }
            )
            prev = {"predicted": pred, "measured": meas}
        rows.append(
            {
                "criterion_id": entry["criterion_id"],
                "probe": pid,
                "expression": deriv["expression"],
                "coefficient": deriv["coefficient"],
                "reference_h": h_ref,
                "points": pts,
                "status": "PASS" if passed else "FAIL",
            }
        )
    return {
        "ok": all(r["status"] == "PASS" for r in rows) and bool(rows),
        "ratio_band": list(RATIO_BAND),
        "n_checked": len(rows),
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# 6. 覆盖率账本（C6）
# --------------------------------------------------------------------------- #
def coverage_ledger(registry: Dict[str, Any], root: Optional[Path | str] = None) -> Dict[str, Any]:
    criteria = registry.get("criteria", [])
    n = len(criteria)
    measured = measured_map(registry)
    by_type: Dict[str, int] = {k: 0 for k in SOURCE_TYPES}
    by_ref: Dict[str, int] = {}
    sourced = derivable = decidable = 0
    for entry in criteria:
        st = str(entry.get("source_type"))
        by_type[st] = by_type.get(st, 0) + 1
        status = source_ref_status(entry.get("source_ref", ""), root)["status"]
        by_ref[status] = by_ref.get(status, 0) + 1
        if status == "OK":
            sourced += 1
        deriv = entry.get("derivation")
        if deriv and entry.get("depends_on"):
            try:
                eval_derivation(deriv, float(deriv["reference_h"]))
                derivable += 1
            except Exception:  # noqa: BLE001
                pass
        if classify_entry(entry, measured.get(entry["criterion_id"]))["decidable"]:
            decidable += 1
    def _ratio(x: int) -> float:
        return round(x / n, 6) if n else 0.0

    return {
        "n_total": n,
        "n_source_verified": sourced,
        "sourced_ratio": _ratio(sourced),
        "n_derivable": derivable,
        "derivable_ratio": _ratio(derivable),
        "n_decidable": decidable,
        "decidable_ratio": _ratio(decidable),
        "n_heuristic_excluded": by_type.get("heuristic", 0) + by_type.get("empirical", 0),
        "by_source_type": by_type,
        "by_source_ref_status": by_ref,
    }


# --------------------------------------------------------------------------- #
# 7. 语义指纹 + 漂移门（C3 / C4）
# --------------------------------------------------------------------------- #
def _norm_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float) or isinstance(value, int):
        return format(float(value), f".{FLOAT_SIG}g")
    if isinstance(value, dict):
        return {k: _norm_value(v) for k, v in sorted(value.items()) if k not in COMMENT_KEYS}
    if isinstance(value, list):
        return [_norm_value(v) for v in value]
    return value


def semantic_view(entry: Dict[str, Any]) -> Dict[str, Any]:
    """判据的语义视图：丢掉注释/说明类键，数值按有效位数归一。"""
    return {k: _norm_value(v) for k, v in sorted(entry.items()) if k not in COMMENT_KEYS}


def semantic_sha(entry: Dict[str, Any]) -> str:
    payload = json.dumps(semantic_view(entry), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def registry_sha(registry: Dict[str, Any]) -> str:
    payload = json.dumps(
        {e["criterion_id"]: semantic_view(e) for e in registry.get("criteria", []) if "criterion_id" in e},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_lock(registry: Dict[str, Any]) -> Dict[str, Any]:
    criteria = {}
    for entry in registry.get("criteria", []):
        cid = entry["criterion_id"]
        criteria[cid] = {
            "semantic_sha": semantic_sha(entry),
            "threshold": _norm_value(entry.get("threshold")),
            "source_type": entry.get("source_type"),
            "source_ref": entry.get("source_ref"),
            "review_date": entry.get("review_date"),
            "depends_on": _norm_value(entry.get("depends_on", [])),
            "derivation": _norm_value(entry.get("derivation")),
            "decidable": bool(entry.get("decidable")),
        }
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "registry_sha": registry_sha(registry),
        "n_criteria": len(criteria),
        "criteria": criteria,
    }


def _numeric_fields(entry: Dict[str, Any]) -> Dict[str, Any]:
    """判据的「量」字段：这些变了而来源没变，就是漂移。"""
    return {
        "threshold": _norm_value(entry.get("threshold")),
        "comparator": entry.get("comparator"),
        "quantity": entry.get("quantity"),
        "depends_on": _norm_value(entry.get("depends_on", [])),
        "derivation": _norm_value(entry.get("derivation")),
        "probe": entry.get("probe"),
        "decidable": bool(entry.get("decidable")),
    }


def drift_check(
    lock: Dict[str, Any],
    registry: Dict[str, Any],
    root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """比较锁文件与当前注册表。

    红（blocking）：
      * ``threshold_drift_without_source_change``：量的字段变了而 ``source_ref`` 未变；
      * ``missing_source_ref``：新增判据没有出处；
      * ``source_ref_rot``：出处指针失效（MISSING_FILE / SHA_DRIFT）。
    黄（非阻塞）：删除判据、来源更新但未 bump 复核日期。
    """
    findings: List[Dict[str, Any]] = []
    locked = lock.get("criteria", {})
    current = {e["criterion_id"]: e for e in registry.get("criteria", []) if "criterion_id" in e}

    for cid, entry in current.items():
        old = locked.get(cid)
        if not entry.get("source_ref"):
            findings.append({"criterion_id": cid, "code": "missing_source_ref", "level": "RED"})
        ref_status = source_ref_status(entry.get("source_ref", ""), root)["status"]
        if ref_status in ("MISSING_FILE", "SHA_DRIFT", "MALFORMED"):
            findings.append(
                {
                    "criterion_id": cid,
                    "code": "source_ref_rot",
                    "level": "RED",
                    "detail": source_ref_status(entry.get("source_ref", ""), root)["detail"],
                }
            )
        if old is None:
            findings.append({"criterion_id": cid, "code": "added", "level": "CLEAR"})
            continue
        new_sha = semantic_sha(entry)
        if new_sha == old.get("semantic_sha"):
            findings.append({"criterion_id": cid, "code": "unchanged", "level": "CLEAR"})
            continue
        # 语义变了：拆成「量」与「来源」
        old_qty = {
            "threshold": old.get("threshold"),
            "comparator": old.get("comparator"),
            "quantity": old.get("quantity"),
            "depends_on": _norm_value(old.get("depends_on", [])),
            "derivation": _norm_value(old.get("derivation")),
            "probe": old.get("probe"),
            "decidable": old.get("decidable"),
        }
        new_qty = _numeric_fields(entry)
        qty_changed = old_qty != new_qty
        ref_changed = old.get("source_ref") != entry.get("source_ref")
        date_changed = old.get("review_date") != entry.get("review_date")
        if qty_changed and not ref_changed:
            diff = {
                k: {"old": old_qty.get(k), "new": new_qty.get(k)}
                for k in new_qty
                if old_qty.get(k) != new_qty.get(k)
            }
            findings.append(
                {
                    "criterion_id": cid,
                    "code": "threshold_drift_without_source_change",
                    "level": "RED",
                    "detail": diff,
                }
            )
        elif ref_changed and not date_changed:
            findings.append(
                {
                    "criterion_id": cid,
                    "code": "provenance_updated_without_review_date_bump",
                    "level": "YELLOW",
                }
            )
        else:
            findings.append(
                {"criterion_id": cid, "code": "retraced_with_new_source", "level": "CLEAR"}
            )

    for cid in locked:
        if cid not in current:
            findings.append({"criterion_id": cid, "code": "removed", "level": "YELLOW"})

    reds = [f for f in findings if f["level"] == "RED"]
    yellows = [f for f in findings if f["level"] == "YELLOW"]
    return {
        "verdict": "RED" if reds else "CLEAR",
        "n_red": len(reds),
        "n_yellow": len(yellows),
        "locked_registry_sha": lock.get("registry_sha"),
        "current_registry_sha": registry_sha(registry),
        "findings": findings,
    }


# --------------------------------------------------------------------------- #
# 8. CLI
# --------------------------------------------------------------------------- #
def _dump(payload: Any, out: Optional[str]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="验收阈值口径注册表 / 派生一致性门 / 漂移门")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
        p.add_argument("--schema", default=str(DEFAULT_SCHEMA))
        p.add_argument("--out", default=None)

    for name in ("validate", "lock", "drift", "derive", "coverage", "gate"):
        p = sub.add_parser(name)
        _add_common(p)
        if name in ("drift", "gate"):
            p.add_argument("--lock", default=str(DEFAULT_LOCK))
        if name == "derive":
            p.add_argument("--h0", type=float, default=0.25)
            p.add_argument("--levels", type=int, default=6)

    args = ap.parse_args(argv)
    try:
        registry = load_registry(args.registry)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    if args.cmd == "validate":
        res = validate(registry, args.schema)
        _dump(res, args.out)
        return 0 if res["ok"] else 1

    if args.cmd == "lock":
        lock = build_lock(registry)
        _dump(lock, args.out or str(DEFAULT_LOCK))
        return 0

    if args.cmd == "drift":
        try:
            lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"verdict": "RED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        res = drift_check(lock, registry)
        _dump(res, args.out)
        return 0 if res["verdict"] == "CLEAR" else 1

    if args.cmd == "derive":
        res = {"derivation_check": derivation_check(registry, args.h0, args.levels),
               "rederivation_closure": rederivation_closure(registry)}
        _dump(res, args.out)
        return 0 if (res["derivation_check"]["ok"] and res["rederivation_closure"]["ok"]) else 1

    if args.cmd == "coverage":
        _dump(coverage_ledger(registry), args.out)
        return 0

    if args.cmd == "gate":
        payload = {
            "schema_check": validate(registry, args.schema),
            "derivation_check": derivation_check(registry),
            "rederivation_closure": rederivation_closure(registry),
            "coverage": coverage_ledger(registry),
        }
        lock_path = Path(args.lock)
        if lock_path.is_file():
            payload["drift"] = drift_check(
                json.loads(lock_path.read_text(encoding="utf-8")), registry
            )
        else:
            payload["drift"] = {"verdict": "RED", "n_red": 1, "findings": [
                {"code": "lock_missing", "level": "RED", "detail": str(lock_path)}]}
        ok = (
            payload["schema_check"]["ok"]
            and payload["derivation_check"]["ok"]
            and payload["rederivation_closure"]["ok"]
            and payload["drift"]["verdict"] == "CLEAR"
        )
        payload["verdict"] = "CLEAR" if ok else "RED"
        _dump(payload, args.out)
        return 0 if ok else 1

    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())