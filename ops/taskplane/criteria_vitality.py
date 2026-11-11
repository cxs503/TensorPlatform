#!/usr/bin/env python3
"""判据活力门（criteria vitality gate）——判据自身的变异注入体检。

问题：门的判据可以是「恒真装饰」
-------------------------------
一个门（gate）自称 REAL，靠的是它的判据真能分辨「合规」与「不合规」。但一条判据只要写成
``observed >= threshold`` 就有三种典型的静默失效，且**任何一种都不会让门变红**：

* **阈值悬空**：阈值定在 10，而全部体检样本的观测值都在 100 以上——把阈值改成 11 或 9，
  判决一模一样。阈值不是被证据钉住的，是随手写的；
* **观测式可平移**：观测式整体 +10% 常数偏移，判决不变——判据对该量的绝对水平不敏感；
* **单位错位不受罚**：阈值按 mm 写、按 m 解读（差 1000 倍），判决照样全绿。

三种失效的共同点：**判据对自身参数的扰动不敏感**。判据参数是判据的全部信息量，参数随便改
都不改变判决 ⇒ 参数没被证据约束 ⇒ 判据退化成常量，门退化成装饰。本模块把这件事做成机器
判据：**变异注入 + 杀死率**。

机制（五步，全部可机裁）
------------------------
1. **变异体生成**（``generate_mutants``）：对每条判据生成 6 个变异体，恰好覆盖 6 类算子：

   =========================  ================================================
   算子                        变异语义（只扰动判据参数，不动被测实现）
   =========================  ================================================
   ``THRESHOLD_PLUS_10``       阈值 ×1.10
   ``THRESHOLD_MINUS_10``      阈值 ×0.90
   ``OP_FLIP``                 比较方向翻转（``>=`` → ``<=``，``==`` → ``!=`` …）
   ``OBSERVED_OFFSET_PLUS``    观测式常数偏移 +0.10·scale
   ``OBSERVED_OFFSET_MINUS``   观测式常数偏移 -0.10·scale
   ``UNIT_ERROR``              单位换算错误注入（阈值按邻级单位解读）
   =========================  ================================================

2. **预注册**（``build_library`` / ``write_library``）：变异体在**执行之前**落盘成变异库，
   每个变异体带 ``digest``（规格指纹），库带 ``gate_sha256``（门定义指纹）。执行引擎只吃库
   里的变异体，库与门定义不一致即拒跑——禁止「先看结果再造变异体」。

3. **执行**（``run_gate``）：体检样本（subject）= 输入 case + **预注册的正确判决** PASS/FAIL。
   正确判决由样本构造方式决定（干净实现 → PASS；注入已知缺陷的实现 → FAIL），**不来自被
   体检的判据**。对每个样本分别求原始判据与变异判据的判决。

4. **杀死率**（``MS``）：变异体在样本池上给出与预注册正确判决不一致的判决 ⇒ **被杀
   （KILLED）**——该判据的这个参数被证据咬住（扰动即失真）。若在全部样本上都与正确判决一致
   ⇒ **存活（SURVIVED）**——该扰动在样本池分辨率下不改变判决，判据的决策边界没被证据钉住。
   ``MS = killed / total``；``MS >= 0.5`` 达标；``gate_vitality = MS × 100``。
   前置条件（防「拿坏尺子量」）：原始判据自身必须在全部样本上判对（``accuracy == 1.0``），
   否则记 ``DEFECTIVE``、门直接封（``real_claim_allowed = False``）。

5. **报告与入账**（``render_markdown`` / ``stamp_capability_ledger``）：输出「判据 × 变异体」
   kill 矩阵（无缺失单元格）、survived 清单（每项带机器推导的原因码 + 审查提示）、门级活力
   分数，并把 ``vitality_score`` 写进 capability_ledger 的 ``criteria_vitality`` 段；未体检或
   MS 不达标的能力 ⇒ ``real_claim_allowed = False``（不得自报 REAL）。

判据（``check_run`` 逐条机裁，``tests/test_criteria_vitality.py`` 钉住）
-----------------------------------------------------------------------
A1 每条判据变异体 >=3 且算子类 >=2；A2 ``MS >= 0.5``；A3 kill 矩阵无缺失单元格；A4 每个
survived 变异体有原因码 + 数值明细；A5 ``gate_vitality == MS x 100``；A6 台账写入
``vitality_score`` 且未体检能力不得自报 REAL；A7 零领域语义（变异算子只认数值/算子/单位，
源码经 ``tools/evidence_domain_scan`` 扫描 0 命中）。

用法
----
    python3 -m ops.taskplane.criteria_vitality preregister --gate g.json --out lib.json
    python3 -m ops.taskplane.criteria_vitality run --gate g.json --library lib.json --out run.json
    python3 -m ops.taskplane.criteria_vitality stamp --run run.json \
        --ledger artifacts/capability_ledger/capability_ledger.json --embed-capability-fields
    python3 -m ops.taskplane.criteria_vitality check --run run.json   # 0 全绿且全 VITAL / 2 不达标 / 3 输入错

零领域语义：本模块只认 ``criterion_id`` / 观测式指针 / 比较算子 / 阈值 / 单位字符串，不认
任何具体工程量的含义；样本与实现入口一律由外部 gate 定义注入。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = Path(__file__).resolve()
MODULE_REL = "ops/taskplane/criteria_vitality.py"

SCHEMA_GATE = "criteria-vitality-gate/1.0"
SCHEMA_LIBRARY = "criteria-vitality-mutants/1.0"
SCHEMA_RUN = "criteria-vitality-run/1.0"
SCHEMA_LEDGER_BLOCK = "criteria-vitality/1.0"

#: 达标线：杀死率 MS >= 0.5。
MS_FLOOR = 0.5
#: A1 覆盖下界：每条判据至少 3 个变异体、至少 2 类算子。
MIN_MUTANTS_PER_CRITERION = 3
MIN_OPERATOR_CLASSES = 2
#: 阈值/观测式扰动的相对幅度（规格：阈值偏移 +-10%）。
RELATIVE_PERTURBATION = 0.10
#: 过拟合诊断：MS=1.0 且多数样本距边界 <10% => 疑似「围着样本调参」。
OVERFIT_MARGIN = 0.10
OVERFIT_SHARE = 2.0 / 3.0

COMPARE_OPS: tuple[str, ...] = (">=", ">", "<=", "<", "==", "!=")
FLIP_OP: dict[str, str] = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "==": "!=", "!=": "=="}

OPERATORS: dict[str, str] = {
    "THRESHOLD_PLUS_10": "阈值 x1.10",
    "THRESHOLD_MINUS_10": "阈值 x0.90",
    "OP_FLIP": "比较方向翻转（>= -> <=、== -> != ...）",
    "OBSERVED_OFFSET_PLUS": "观测式常数偏移 +0.10*scale",
    "OBSERVED_OFFSET_MINUS": "观测式常数偏移 -0.10*scale",
    "UNIT_ERROR": "单位换算错误注入（阈值按邻级单位解读）",
}

#: 单位邻级换算（纯量纲阶梯，不含任何工程量语义；未登记单位按 1e3 量级错位）。
UNIT_LADDER: dict[str, float] = {
    "m": 1e-3,
    "s": 1.0 / 3600.0,
    "g": 1e3,
    "kg": 1e-3,
    "N": 1e-3,
    "J": 1e-3,
    "W": 1e-3,
    "Pa": 1e-3,
    "Hz": 1e-3,
    "rad": 180.0 / math.pi,
    "deg": math.pi / 180.0,
    "1": 100.0,
}
DEFAULT_UNIT_FACTOR = 1e3

SURVIVAL_REASONS: dict[str, str] = {
    "SLACK_EXCEEDS_PERTURBATION": "全部样本距边界的最小相对裕度大于扰动幅度，扰动不足以翻转任何判决",
    "BOUNDARY_COINCIDENCE": "样本落在边界容差内，两个比较方向给出同一判决",
    "DEADBAND_ABSORBED": "容差/相等语义把扰动吃掉了（扰动 <= 容差）",
    "DEGENERATE_MUTANT": "变异后判据与原始判据逐字段相同（如阈值为 0 时的相对偏移）",
    "DEFECTIVE_BASE_MASKED": "原始判据自身判错样本，变异体恰好与预注册判决一致（缺陷的副产物，"
    "不能算判据活力）",
}

#: capability_ledger 既有的序列化惯例（缩进 1 空格 + 结尾换行）：入账必须沿用，
#: 否则一条记录的追加会把整本台账改写成另一种格式，diff 噪音淹没真实改动。
LEDGER_JSON_INDENT = 1

VERDICT_VITAL = "VITAL"
VERDICT_INERT = "INERT"
VERDICT_BLOCKED = "BLOCKED"
STATUS_OK = "OK"
STATUS_DEFECTIVE = "DEFECTIVE"
STATUS_COVERAGE_SHORT = "COVERAGE_SHORT"
STATUS_EXAMINED = "EXAMINED"
STATUS_UNEXAMINED = "UNEXAMINED"


class VitalityInputError(RuntimeError):
    """输入不合法（门定义/变异库/样本/实现入口）——绝不当成「判据不合格」处理。"""


# ── 基础设施 ────────────────────────────────────────────────────────────
def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _round(value: float | None, ndigits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def unit_error_factor(unit: str) -> tuple[float, str]:
    """返回 (换算因子, 邻级单位标签)。未登记单位按 1e3 量级错位处理。"""
    key = (unit or "1").strip()
    if key in UNIT_LADDER:
        return UNIT_LADDER[key], f"{key}->neighbour(x{UNIT_LADDER[key]:.6g})"
    return DEFAULT_UNIT_FACTOR, f"{key or '1'}->generic(x{DEFAULT_UNIT_FACTOR:g})"


# ── 判据规格 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CriterionSpec:
    """一条判据 = 观测式（JSON 指针）+ 比较方向 + 阈值 + 单位（+ 容差/量纲尺度/常数偏移）。"""

    cid: str
    observed: str
    op: str
    threshold: float
    unit: str = "1"
    tolerance: float = 0.0
    scale: float | None = None
    offset: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.cid).strip():
            raise VitalityInputError("criterion_id 不能为空")
        if self.op not in COMPARE_OPS:
            raise VitalityInputError(f"比较算子 {self.op!r} 未登记（应为 {COMPARE_OPS}）")
        if not isinstance(self.observed, str) or not self.observed.startswith("/"):
            raise VitalityInputError(f"观测式必须是 JSON 指针（以 / 开头）：{self.observed!r}")
        try:
            thr, tol, off = float(self.threshold), float(self.tolerance), float(self.offset)
        except (TypeError, ValueError) as exc:
            raise VitalityInputError(f"阈值/容差/偏移必须是数值：{exc}") from exc
        for name, value in (("threshold", thr), ("tolerance", tol), ("offset", off)):
            if math.isnan(value) or math.isinf(value):
                raise VitalityInputError(f"{name} 不能是 NaN/Inf")
        object.__setattr__(self, "threshold", thr)
        object.__setattr__(self, "tolerance", tol)
        object.__setattr__(self, "offset", off)
        if self.scale is not None:
            object.__setattr__(self, "scale", float(self.scale))

    @property
    def reference_scale(self) -> float:
        """扰动幅度与相对裕度的分母（量纲尺度，缺省退回 |阈值|）。"""
        if self.scale is not None and abs(float(self.scale)) > 0.0:
            return abs(float(self.scale))
        return abs(self.threshold) if self.threshold != 0.0 else 1.0

    def replace(self, **changes: Any) -> "CriterionSpec":
        data = self.to_dict()
        data.update(changes)
        return CriterionSpec.from_dict(data)

    def to_dict(self) -> dict:
        out = {
            "criterion_id": self.cid,
            "observed": self.observed,
            "op": self.op,
            "threshold": self.threshold,
            "unit": self.unit,
            "tolerance": self.tolerance,
        }
        if self.scale is not None:
            out["scale"] = self.scale
        if self.offset:
            out["offset"] = self.offset
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CriterionSpec":
        if not isinstance(raw, Mapping):
            raise VitalityInputError("判据定义必须是对象")
        missing = [k for k in ("criterion_id", "observed", "op", "threshold") if k not in raw]
        if missing:
            raise VitalityInputError(f"判据定义缺字段 {missing}: {raw!r}")
        return cls(
            cid=str(raw["criterion_id"]),
            observed=str(raw["observed"]),
            op=str(raw["op"]),
            threshold=raw["threshold"],
            unit=str(raw.get("unit", "1")),
            tolerance=raw.get("tolerance") or 0.0,
            scale=raw.get("scale"),
            offset=raw.get("offset") or 0.0,
        )

    def digest(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


def criterion_statement(spec: CriterionSpec) -> str:
    """人读判据串（数字全部来自规格字段，不手抄）。"""
    offset = f" + ({spec.offset:g})" if spec.offset else ""
    tol = f" +-{spec.tolerance:g}" if spec.tolerance else ""
    return f"{spec.cid}: {spec.observed}{offset} {spec.op} {spec.threshold:g}{tol} [{spec.unit}]"


# ── 求值 ────────────────────────────────────────────────────────────────
def resolve_observation(document: Any, pointer: str) -> float:
    """按 JSON 指针取观测值；取不到/非数值 -> VitalityInputError（不当成判据不合格）。"""
    if not pointer.startswith("/"):
        raise VitalityInputError(f"观测式必须是 JSON 指针：{pointer!r}")
    cur = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, Mapping):
            if token not in cur:
                raise VitalityInputError(f"观测式 {pointer} 缺键 {token!r}（现有键 {sorted(cur)[:8]}）")
            cur = cur[token]
        elif isinstance(cur, Sequence) and not isinstance(cur, (str, bytes)):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError) as exc:
                raise VitalityInputError(f"观测式 {pointer} 的索引 {token!r} 非法：{exc}") from exc
        else:
            raise VitalityInputError(f"观测式 {pointer} 在 {type(cur).__name__} 处无法继续下钻")
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        raise VitalityInputError(f"观测式 {pointer} 取到非数值 {cur!r}（判据只能比较数值）")
    value = float(cur)
    if math.isnan(value) or math.isinf(value):
        raise VitalityInputError(f"观测式 {pointer} 取到 NaN/Inf")
    return value


def compare(op: str, value: float, threshold: float, tolerance: float = 0.0) -> bool:
    """比较算子求值（== / != 走容差死区）。"""
    if op not in COMPARE_OPS:
        raise VitalityInputError(f"比较算子 {op!r} 未登记")
    tol = abs(float(tolerance))
    if op == ">=":
        return value >= threshold - tol
    if op == ">":
        return value > threshold - tol
    if op == "<=":
        return value <= threshold + tol
    if op == "<":
        return value < threshold + tol
    if op == "==":
        return abs(value - threshold) <= tol
    return abs(value - threshold) > tol


def evaluate_criterion(spec: CriterionSpec, observation: Any) -> bool:
    """返回 True = 验收条件成立（PASS）；False = 违反（FAIL）。

    判据语义约定：``op``/``threshold`` 描述的是**必须成立的验收条件**
    （如 ``observed >= 0.80`` 表示「该量不得低于 0.80」），而不是违规谓词。
    """
    value = resolve_observation(observation, spec.observed) + spec.offset
    return compare(spec.op, value, spec.threshold, spec.tolerance)


def normalized_margin(spec: CriterionSpec, observation: Any) -> float:
    """样本相对边界的有符号裕度（以 reference_scale 归一）；>0 = 观测值在阈值上方。"""
    value = resolve_observation(observation, spec.observed) + spec.offset
    return (value - spec.threshold) / spec.reference_scale


# ── 变异算子库 ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Mutant:
    mutant_id: str
    cid: str
    operator: str
    spec: CriterionSpec
    rationale: str

    def to_dict(self) -> dict:
        return {
            "mutant_id": self.mutant_id,
            "criterion_id": self.cid,
            "operator": self.operator,
            "operator_semantics": OPERATORS.get(self.operator, ""),
            "mutated": self.spec.to_dict(),
            "digest": self.spec.digest(),
            "rationale": self.rationale,
        }


def generate_mutants(spec: CriterionSpec, operators: Iterable[str] | None = None) -> list[Mutant]:
    """对一条判据生成变异体：默认 6 个，覆盖全部 6 类算子。"""
    delta = RELATIVE_PERTURBATION * spec.reference_scale
    unit_factor, unit_label = unit_error_factor(spec.unit)

    mutators: dict[str, Callable[[], tuple[CriterionSpec, str]]] = {
        "THRESHOLD_PLUS_10": lambda: (
            spec.replace(threshold=spec.threshold * (1.0 + RELATIVE_PERTURBATION)),
            f"阈值 {spec.threshold:g} -> {spec.threshold * (1.0 + RELATIVE_PERTURBATION):g}"
            f"（x1.10，单位 {spec.unit}）",
        ),
        "THRESHOLD_MINUS_10": lambda: (
            spec.replace(threshold=spec.threshold * (1.0 - RELATIVE_PERTURBATION)),
            f"阈值 {spec.threshold:g} -> {spec.threshold * (1.0 - RELATIVE_PERTURBATION):g}"
            f"（x0.90，单位 {spec.unit}）",
        ),
        "OP_FLIP": lambda: (
            spec.replace(op=FLIP_OP[spec.op]),
            f"比较方向 {spec.op} -> {FLIP_OP[spec.op]}",
        ),
        "OBSERVED_OFFSET_PLUS": lambda: (
            spec.replace(offset=spec.offset + delta),
            f"观测式常数偏移 +{delta:g}（= +{RELATIVE_PERTURBATION:g}*scale）",
        ),
        "OBSERVED_OFFSET_MINUS": lambda: (
            spec.replace(offset=spec.offset - delta),
            f"观测式常数偏移 -{delta:g}（= -{RELATIVE_PERTURBATION:g}*scale）",
        ),
        "UNIT_ERROR": lambda: (
            spec.replace(threshold=spec.threshold * unit_factor),
            f"单位换算错误注入：阈值按 {unit_label} 解读"
            f"（{spec.threshold:g} -> {spec.threshold * unit_factor:g}）",
        ),
    }
    wanted = list(operators) if operators is not None else list(OPERATORS)
    unknown = [o for o in wanted if o not in mutators]
    if unknown:
        raise VitalityInputError(f"未登记变异算子 {unknown}")
    out: list[Mutant] = []
    for operator in wanted:
        mutated, rationale = mutators[operator]()
        out.append(
            Mutant(
                mutant_id=f"{spec.cid}::{operator}",
                cid=spec.cid,
                operator=operator,
                spec=mutated,
                rationale=rationale,
            )
        )
    return out


# ── 门定义 / 变异库 ─────────────────────────────────────────────────────
def parse_gate(raw: Mapping[str, Any]) -> dict:
    if not isinstance(raw, Mapping):
        raise VitalityInputError("门定义必须是对象")
    gate = dict(raw)
    gate_id = str(gate.get("gate_id") or "").strip()
    if not gate_id:
        raise VitalityInputError("门定义缺 gate_id")
    criteria_raw = gate.get("criteria")
    if not isinstance(criteria_raw, list) or not criteria_raw:
        raise VitalityInputError(f"{gate_id}: criteria 必须是非空数组")
    specs = [CriterionSpec.from_dict(item) for item in criteria_raw]
    ids = [s.cid for s in specs]
    if len(ids) != len(set(ids)):
        raise VitalityInputError(f"{gate_id}: criterion_id 重复：{ids}")
    subjects_raw = gate.get("subjects")
    if not isinstance(subjects_raw, list) or not subjects_raw:
        raise VitalityInputError(f"{gate_id}: subjects 必须是非空数组")
    subjects: list[dict] = []
    seen: set[str] = set()
    for idx, item in enumerate(subjects_raw):
        if not isinstance(item, Mapping):
            raise VitalityInputError(f"{gate_id}: subjects[{idx}] 必须是对象")
        sid = str(item.get("subject_id") or "").strip()
        if not sid:
            raise VitalityInputError(f"{gate_id}: subjects[{idx}] 缺 subject_id")
        if sid in seen:
            raise VitalityInputError(f"{gate_id}: subject_id 重复：{sid}")
        seen.add(sid)
        expect = str(item.get("expect") or "").strip().upper()
        if expect not in ("PASS", "FAIL"):
            raise VitalityInputError(f"{gate_id}: subject {sid} 的 expect 必须是 PASS/FAIL")
        case = item.get("case")
        if not isinstance(case, Mapping):
            raise VitalityInputError(f"{gate_id}: subject {sid} 的 case 必须是对象")
        subjects.append(
            {
                "subject_id": sid,
                "case": dict(case),
                "expect": expect,
                "provenance": str(item.get("provenance") or ""),
            }
        )
    impl = gate.get("implementation")
    if impl is not None and not (isinstance(impl, Mapping) and impl.get("ref")):
        raise VitalityInputError(f"{gate_id}: implementation 必须是 {{'ref': 'module:function'}}")
    return {
        "schema": str(gate.get("schema") or SCHEMA_GATE),
        "gate_id": gate_id,
        "title": str(gate.get("title") or gate_id),
        "implementation": dict(impl) if impl else None,
        "criteria": specs,
        "subjects": subjects,
    }


def gate_digest(gate: Mapping[str, Any]) -> str:
    """门定义内容指纹（判据 + 样本 + 实现入口）；任一变动都必须重新预注册。"""
    payload = {
        "gate_id": gate["gate_id"],
        "implementation": gate.get("implementation"),
        "criteria": [spec.to_dict() for spec in gate["criteria"]],
        "subjects": [
            {"subject_id": s["subject_id"], "case": s["case"], "expect": s["expect"]}
            for s in gate["subjects"]
        ],
    }
    return sha256_text(canonical_json(payload))


def build_library(gate: Mapping[str, Any], generated_at: str | None = None) -> dict:
    """生成一个门的变异库（落盘即预注册）。"""
    mutants: list[dict] = []
    per_criterion: list[dict] = []
    for spec in gate["criteria"]:
        group = generate_mutants(spec)
        mutants.extend(m.to_dict() for m in group)
        per_criterion.append(
            {
                "criterion_id": spec.cid,
                "statement": criterion_statement(spec),
                "mutant_count": len(group),
                "operator_classes": sorted({m.operator for m in group}),
            }
        )
    operator_classes = sorted({m["operator"] for m in mutants})
    min_per = min(item["mutant_count"] for item in per_criterion)
    min_classes = min(len(item["operator_classes"]) for item in per_criterion)
    return {
        "schema": SCHEMA_LIBRARY,
        "gate_id": gate["gate_id"],
        "gate_sha256": gate_digest(gate),
        "generated_at": generated_at or _utcnow(),
        "operators": OPERATORS,
        "criteria": per_criterion,
        "mutants": mutants,
        "coverage": {
            "mutants_per_criterion_min": min_per,
            "operator_classes_per_criterion_min": min_classes,
            "operator_classes_total": operator_classes,
            "min_mutants_required": MIN_MUTANTS_PER_CRITERION,
            "min_operator_classes_required": MIN_OPERATOR_CLASSES,
            "coverage_ok": min_per >= MIN_MUTANTS_PER_CRITERION
            and min_classes >= MIN_OPERATOR_CLASSES,
        },
    }


def build_library_file(gates: Sequence[Mapping[str, Any]], generated_at: str | None = None) -> dict:
    stamp = generated_at or _utcnow()
    return {
        "schema": SCHEMA_LIBRARY,
        "generated_at": stamp,
        "module": {"path": MODULE_REL, "sha256": sha256_file(MODULE_PATH)},
        "gates": {gate["gate_id"]: build_library(gate, generated_at=stamp) for gate in gates},
    }


def write_library(library: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def load_json(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VitalityInputError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise VitalityInputError(f"JSON 解析失败 {path}: {exc}") from exc


def load_gates(path: Path | str) -> list[dict]:
    """门定义文件：单个门对象、数组，或 {"gates": [...]}。"""
    raw = load_json(path)
    if isinstance(raw, Mapping) and "gates" in raw:
        raw = raw["gates"]
    items = raw if isinstance(raw, list) else [raw]
    return [parse_gate(item) for item in items]


def verify_library(gate: Mapping[str, Any], library: Mapping[str, Any]) -> list[str]:
    """预注册一致性：变异库必须与门定义逐位对应，否则拒跑。"""
    problems: list[str] = []
    if library.get("schema") != SCHEMA_LIBRARY:
        problems.append(f"变异库 schema 不是 {SCHEMA_LIBRARY}: {library.get('schema')!r}")
    if library.get("gate_id") != gate["gate_id"]:
        problems.append(f"变异库 gate_id={library.get('gate_id')!r} 与门 {gate['gate_id']!r} 不符")
    expected_digest = gate_digest(gate)
    if library.get("gate_sha256") != expected_digest:
        problems.append(
            f"门定义在预注册后被改动：library.gate_sha256={library.get('gate_sha256')} "
            f"现值={expected_digest}"
        )
    registered = {m["mutant_id"]: m for m in library.get("mutants", [])}
    expected = {m.mutant_id: m for spec in gate["criteria"] for m in generate_mutants(spec)}
    missing = sorted(set(expected) - set(registered))
    extra = sorted(set(registered) - set(expected))
    if missing:
        problems.append(f"变异库缺注册变异体：{missing}")
    if extra:
        problems.append(f"变异库含未授权变异体：{extra}")
    for mid, item in registered.items():
        try:
            recomputed = CriterionSpec.from_dict(item.get("mutated") or {}).digest()
        except VitalityInputError as exc:
            problems.append(f"变异体 {mid} 的 mutated 载荷非法：{exc}")
            continue
        if item.get("digest") != recomputed:
            problems.append(
                f"变异体 {mid} 的 mutated 载荷与其 digest 不符："
                f"stored={item.get('digest')} recomputed={recomputed}"
            )
    for mid in sorted(set(expected) & set(registered)):
        want = expected[mid].spec.digest()
        got = registered[mid].get("digest")
        if got != want:
            problems.append(f"变异体 {mid} 规格被篡改：registered={got} recomputed={want}")
    return problems


# ── 执行引擎 ────────────────────────────────────────────────────────────
def resolve_implementation(impl: Any) -> Callable[[Mapping[str, Any]], Any]:
    """实现入口：可调用对象，或 module:function 引用（string / mapping）。"""
    if callable(impl):
        return impl
    ref = None
    if isinstance(impl, str):
        ref = impl
    elif isinstance(impl, Mapping):
        ref = impl.get("ref")
    if not ref or ":" not in str(ref):
        raise VitalityInputError(f"实现入口必须可调用或形如 module:function，收到 {impl!r}")
    module_name, func_name = str(ref).split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise VitalityInputError(f"实现模块 {module_name} 导入失败：{exc}") from exc
    func = getattr(module, func_name, None)
    if not callable(func):
        raise VitalityInputError(f"{ref} 不是可调用对象")
    return func


def _verdict_word(holds: bool) -> str:
    """验收条件成立 => PASS；不成立 => FAIL（判据亮红灯）。"""
    return "PASS" if holds else "FAIL"


def _survival_reason(
    base: CriterionSpec,
    mutant: Mutant,
    margins: Mapping[str, float],
    base_defective: bool = False,
    changed_count: int = 0,
) -> tuple[str, str]:
    """存活原因码 + 数值明细（机器推导，不许手写）。"""
    if base_defective:
        return (
            "DEFECTIVE_BASE_MASKED",
            f"原始判据自身已判错预注册样本；变异体在 {changed_count} 个样本上改判后恰好与"
            "预注册判决一致 ⇒ 该「存活」是判据缺陷的副产物，不算活力证据",
        )
    if mutant.spec.to_dict() == base.to_dict():
        return (
            "DEGENERATE_MUTANT",
            f"变异前后判据字段相同（阈值={base.threshold:g}，算子 {mutant.operator}）",
        )
    abs_margins = {sid: abs(m) for sid, m in margins.items()}
    min_sid = min(abs_margins, key=lambda k: abs_margins[k])
    min_abs = abs_margins[min_sid]
    if mutant.operator == "OP_FLIP":
        return (
            "BOUNDARY_COINCIDENCE",
            f"方向翻转未改变判决：最小相对裕度 {min_abs:.6g}（样本 {min_sid}）落在容差死区 "
            f"{base.tolerance:g} 内",
        )
    thr_shift = abs(mutant.spec.threshold - base.threshold) / base.reference_scale
    off_shift = abs(mutant.spec.offset - base.offset) / base.reference_scale
    if mutant.operator in ("THRESHOLD_PLUS_10", "THRESHOLD_MINUS_10", "UNIT_ERROR"):
        required, kind = thr_shift, "阈值"
    else:
        required, kind = off_shift, "观测式"
    if min_abs > required:
        return (
            "SLACK_EXCEEDS_PERTURBATION",
            f"{kind}扰动 {required:.6g}*scale < 全部样本最小相对裕度 {min_abs:.6g}（样本 {min_sid}）",
        )
    return (
        "DEADBAND_ABSORBED",
        f"{kind}扰动 {required:.6g}*scale 被容差 {base.tolerance:g} 吸收；最小相对裕度 {min_abs:.6g}",
    )


def _evaluate_subjects(spec: CriterionSpec, observations: Mapping[str, Any]) -> dict[str, bool]:
    return {sid: evaluate_criterion(spec, obs) for sid, obs in observations.items()}


def run_gate(
    gate: Mapping[str, Any],
    implementation: Any = None,
    library: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict:
    """对一个门做变异体检，返回门级结果（纯计算；不写任何文件）。"""
    impl_ref = implementation if implementation is not None else gate.get("implementation")
    func = resolve_implementation(impl_ref)
    problems = verify_library(gate, library) if library is not None else []
    if problems:
        raise VitalityInputError("变异库与门定义不一致（预注册校验失败）：" + "; ".join(problems))

    subjects = gate["subjects"]
    observations: dict[str, Any] = {}
    for subject in subjects:
        try:
            doc = func(dict(subject["case"]))
        except VitalityInputError:
            raise
        except Exception as exc:
            raise VitalityInputError(
                f"{gate['gate_id']}/{subject['subject_id']}: 实现入口抛异常 "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(doc, Mapping):
            raise VitalityInputError(
                f"{gate['gate_id']}/{subject['subject_id']}: 实现入口必须返回对象，"
                f"收到 {type(doc).__name__}"
            )
        observations[subject["subject_id"]] = dict(doc)
    expect = {s["subject_id"]: s["expect"] for s in subjects}

    lib = library if library is not None else build_library(gate, generated_at=generated_at)
    mutants_by_cid: dict[str, list[Mutant]] = {spec.cid: [] for spec in gate["criteria"]}
    for item in lib["mutants"]:
        spec = CriterionSpec.from_dict(item["mutated"])
        mutants_by_cid.setdefault(item["criterion_id"], []).append(
            Mutant(
                mutant_id=item["mutant_id"],
                cid=item["criterion_id"],
                operator=item["operator"],
                spec=spec,
                rationale=item.get("rationale", ""),
            )
        )

    criteria_rows: list[dict] = []
    matrix: dict[str, dict[str, dict]] = {}
    survived: list[dict] = []
    total_mutants = 0
    total_killed = 0

    for spec in gate["criteria"]:
        base_verdicts = _evaluate_subjects(spec, observations)
        mismatches = [
            {"subject_id": sid, "expected": expect[sid], "observed": _verdict_word(fired)}
            for sid, fired in base_verdicts.items()
            if _verdict_word(fired) != expect[sid]
        ]
        accuracy = (len(subjects) - len(mismatches)) / len(subjects)
        margins = {sid: normalized_margin(spec, obs) for sid, obs in observations.items()}
        group = mutants_by_cid.get(spec.cid, [])
        operator_classes = sorted({m.operator for m in group})
        coverage_ok = (
            len(group) >= MIN_MUTANTS_PER_CRITERION
            and len(operator_classes) >= MIN_OPERATOR_CLASSES
        )

        cells: dict[str, dict] = {}
        killed = 0
        for mutant in group:
            mutant_verdicts = _evaluate_subjects(mutant.spec, observations)
            changed = [
                {
                    "subject_id": sid,
                    "base": _verdict_word(base_verdicts[sid]),
                    "mutant": _verdict_word(mutant_verdicts[sid]),
                }
                for sid in base_verdicts
                if mutant_verdicts[sid] != base_verdicts[sid]
            ]
            wrong = [
                {"subject_id": sid, "expected": expect[sid], "mutant": _verdict_word(fired)}
                for sid, fired in mutant_verdicts.items()
                if _verdict_word(fired) != expect[sid]
            ]
            if wrong:
                killed += 1
                cells[mutant.mutant_id] = {
                    "mutant_id": mutant.mutant_id,
                    "criterion_id": spec.cid,
                    "operator": mutant.operator,
                    "result": "KILLED",
                    "changed_subjects": changed,
                    "wrong_subjects": wrong,
                    "reason_code": None,
                    "detail": (
                        f"变异后 {len(wrong)} 个样本判决与预注册正确判决不符（首个："
                        f"{wrong[0]['subject_id']} 期望 {wrong[0]['expected']} "
                        f"实得 {wrong[0]['mutant']}）"
                    ),
                }
            else:
                reason_code, detail = _survival_reason(
                    spec, mutant, margins, base_defective=bool(mismatches),
                    changed_count=len(changed),
                )
                cell = {
                    "mutant_id": mutant.mutant_id,
                    "criterion_id": spec.cid,
                    "operator": mutant.operator,
                    "result": "SURVIVED",
                    "changed_subjects": changed,
                    "wrong_subjects": [],
                    "reason_code": reason_code,
                    "detail": detail,
                    "requires_review": True,
                    "review_hint": (
                        "该扰动在现有样本池上不改变判决：补一个落在边界附近的样本"
                        f"（相对裕度 < {RELATIVE_PERTURBATION:g}*scale）或把判据参数写实。"
                        f"原因码 {reason_code}：{SURVIVAL_REASONS[reason_code]}"
                    ),
                }
                cells[mutant.mutant_id] = cell
                survived.append(
                    {
                        "gate_id": gate["gate_id"],
                        "criterion_id": spec.cid,
                        "mutant_id": mutant.mutant_id,
                        "operator": mutant.operator,
                        "reason_code": reason_code,
                        "reason_text": SURVIVAL_REASONS[reason_code],
                        "detail": detail,
                        "review_hint": cell["review_hint"],
                    }
                )

        n = len(group)
        ms_raw = (killed / n) if n else 0.0
        if not coverage_ok:
            status = STATUS_COVERAGE_SHORT
        elif mismatches:
            status = STATUS_DEFECTIVE
        else:
            status = STATUS_OK
        criteria_rows.append(
            {
                "criterion_id": spec.cid,
                "statement": criterion_statement(spec),
                "expected_verdicts": dict(expect),
                "base_verdicts": {sid: _verdict_word(f) for sid, f in base_verdicts.items()},
                "base_accuracy": _round(accuracy),
                "base_mismatches": mismatches,
                "margins": {sid: _round(m) for sid, m in margins.items()},
                "min_abs_margin": _round(min(abs(m) for m in margins.values())),
                "mutants_total": n,
                "killed": killed,
                "survived": n - killed,
                "ms": _round(ms_raw, 12),
                "vitality_score": _round(ms_raw * 100.0, 10),
                "operator_classes": operator_classes,
                "coverage_ok": coverage_ok,
                "status": status,
            }
        )
        matrix[spec.cid] = cells
        total_mutants += n
        total_killed += killed

    expected_cells = sum(len(mutants_by_cid.get(spec.cid, [])) for spec in gate["criteria"])
    actual_cells = sum(len(cells) for cells in matrix.values())
    matrix_complete = actual_cells == expected_cells and all(
        cell for cells in matrix.values() for cell in cells.values()
    )

    gate_ms = (total_killed / total_mutants) if total_mutants else 0.0
    gate_ms_stored = _round(gate_ms, 12)
    blockers: list[str] = []
    for row in criteria_rows:
        if row["status"] == STATUS_DEFECTIVE:
            blockers.append(
                f"判据 {row['criterion_id']} 原始形态就判错 {len(row['base_mismatches'])} 个"
                f"预注册样本（accuracy={row['base_accuracy']:.3f}）——判据自身不可用，"
                "活力分数无意义"
            )
        elif row["status"] == STATUS_COVERAGE_SHORT:
            blockers.append(
                f"判据 {row['criterion_id']} 变异覆盖不足（{row['mutants_total']} 个变异体 / "
                f"{len(row['operator_classes'])} 类算子）"
            )
    if not matrix_complete:
        blockers.append(f"kill 矩阵不完整：应有 {expected_cells} 格，实有 {actual_cells} 格")
    if gate_ms < MS_FLOOR:
        blockers.append(f"杀死率 MS={gate_ms:.4f} < 达标线 {MS_FLOOR}")

    hard_block = any(row["status"] != STATUS_OK for row in criteria_rows) or not matrix_complete
    if hard_block:
        verdict = VERDICT_BLOCKED
    elif gate_ms < MS_FLOOR:
        verdict = VERDICT_INERT
    else:
        verdict = VERDICT_VITAL

    all_abs = [abs(m) for row in criteria_rows for m in row["margins"].values()]
    near = sum(1 for m in all_abs if m < OVERFIT_MARGIN)
    overfit_suspect = bool(all_abs) and gate_ms >= 1.0 and (near / len(all_abs)) >= OVERFIT_SHARE

    if callable(impl_ref):
        impl_label = "callable"
    elif isinstance(impl_ref, Mapping):
        impl_label = impl_ref.get("ref")
    else:
        impl_label = impl_ref

    return {
        "gate_id": gate["gate_id"],
        "title": gate["title"],
        "gate_sha256": gate_digest(gate),
        "library_generated_at": lib.get("generated_at"),
        "preregistered": library is not None,
        "implementation_ref": impl_label,
        "verdict": verdict,
        "real_claim_allowed": verdict == VERDICT_VITAL,
        "blockers": blockers,
        "ms": gate_ms_stored,
        "gate_vitality": _round(gate_ms * 100.0, 10),
        "ms_floor": MS_FLOOR,
        "criteria": criteria_rows,
        "kill_matrix": matrix,
        "matrix_stats": {
            "expected_cells": expected_cells,
            "actual_cells": actual_cells,
            "complete": matrix_complete,
        },
        "survived": survived,
        "diagnostics": {
            "subjects_total": len(subjects),
            "within_overfit_margin": near,
            "overfit_margin": OVERFIT_MARGIN,
            "overfit_suspect": overfit_suspect,
            "overfit_note": (
                f"MS={gate_ms:.4f} 且 {near}/{len(all_abs)} 个样本距边界 < "
                f"{OVERFIT_MARGIN:g}*scale（判据可能围着样本调参）"
                if overfit_suspect
                else ""
            ),
            "observation_digest": sha256_text(canonical_json(observations)),
        },
    }


# ── 批量执行 + 判据机裁 + 报告 ──────────────────────────────────────────
def run_bundle(
    gates: Sequence[Mapping[str, Any]],
    library_file: Mapping[str, Any] | None = None,
    implementations: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict:
    """对一批门逐一做变异体检，聚合成运行记录（run.json）。"""
    stamp = generated_at or _utcnow()
    impls = implementations or {}
    results: list[dict] = []
    for gate in gates:
        lib = None
        if library_file is not None:
            lib = (library_file.get("gates") or {}).get(gate["gate_id"])
            if lib is None:
                raise VitalityInputError(f"变异库缺门 {gate['gate_id']} 的预注册记录")
        results.append(run_gate(gate, impls.get(gate["gate_id"]), lib, generated_at=stamp))

    mutants_total = sum(r["matrix_stats"]["expected_cells"] for r in results)
    killed_total = sum(row["killed"] for r in results for row in r["criteria"])
    survived_total = sum(len(r["survived"]) for r in results)
    pooled_ms = (killed_total / mutants_total) if mutants_total else 0.0
    return {
        "schema": SCHEMA_RUN,
        "generated_at": stamp,
        "module": {"path": MODULE_REL, "sha256": sha256_file(MODULE_PATH)},
        "library": {
            "preregistered": library_file is not None,
            "sha256": sha256_text(canonical_json(library_file)) if library_file is not None else None,
            "generated_at": (library_file or {}).get("generated_at"),
        },
        "ms_floor": MS_FLOOR,
        "relative_perturbation": RELATIVE_PERTURBATION,
        "gates": results,
        "summary": {
            "gates_total": len(results),
            "gates_vital": sum(1 for r in results if r["verdict"] == VERDICT_VITAL),
            "gates_inert": sum(1 for r in results if r["verdict"] == VERDICT_INERT),
            "gates_blocked": sum(1 for r in results if r["verdict"] == VERDICT_BLOCKED),
            "mutants_total": mutants_total,
            "killed_total": killed_total,
            "survived_total": survived_total,
            "ms": _round(pooled_ms, 12),
            "gate_vitality": _round(pooled_ms * 100.0, 10),
            "real_claim_allowed_gates": [r["gate_id"] for r in results if r["real_claim_allowed"]],
            "overfit_suspect_gates": [
                r["gate_id"] for r in results if r["diagnostics"]["overfit_suspect"]
            ],
        },
    }


def check_run(run: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """判据 A1~A5 机裁；返回 (是否全绿, 违规列表)。"""
    violations: list[str] = []
    gates = run.get("gates") or []
    if not gates:
        return False, ["A0 运行记录里没有门"]

    for gate in gates:
        gid = gate["gate_id"]
        for row in gate["criteria"]:
            if row["mutants_total"] < MIN_MUTANTS_PER_CRITERION:
                violations.append(
                    f"A1 {gid}/{row['criterion_id']} 变异体 {row['mutants_total']} < "
                    f"{MIN_MUTANTS_PER_CRITERION}"
                )
            if len(row["operator_classes"]) < MIN_OPERATOR_CLASSES:
                violations.append(
                    f"A1 {gid}/{row['criterion_id']} 算子类 {len(row['operator_classes'])} < "
                    f"{MIN_OPERATOR_CLASSES}"
                )
        if gate["ms"] < MS_FLOOR and gate["real_claim_allowed"]:
            violations.append(
                f"A2 {gid} MS={gate['ms']} < {MS_FLOOR} 却 real_claim_allowed=True（违反达标线）"
            )
        if gate["real_claim_allowed"] and gate["verdict"] != VERDICT_VITAL:
            violations.append(f"A2 {gid} verdict={gate['verdict']} 却 real_claim_allowed=True")
        if gate["verdict"] == VERDICT_VITAL and gate["ms"] < MS_FLOOR:
            violations.append(f"A2 {gid} verdict=VITAL 但 MS={gate['ms']} < {MS_FLOOR}")
        stats = gate["matrix_stats"]
        if not stats["complete"] or stats["actual_cells"] != stats["expected_cells"]:
            violations.append(
                f"A3 {gid} kill 矩阵 {stats['actual_cells']}/{stats['expected_cells']} 格不完整"
            )
        for cid, cells in gate["kill_matrix"].items():
            for mid, cell in cells.items():
                if not cell or cell.get("result") not in ("KILLED", "SURVIVED"):
                    violations.append(f"A3 {gid}/{cid}/{mid} 单元格缺失或无裁决")
        for item in gate["survived"]:
            if not item.get("reason_code") or item["reason_code"] not in SURVIVAL_REASONS:
                violations.append(f"A4 {gid}/{item['mutant_id']} 缺合法原因码")
            if not str(item.get("detail") or "").strip():
                violations.append(f"A4 {gid}/{item['mutant_id']} 缺数值明细")
        total = stats["expected_cells"]
        killed = sum(row["killed"] for row in gate["criteria"])
        expected_ms = (killed / total) if total else 0.0
        if abs(expected_ms - gate["ms"]) > 1e-9:
            violations.append(f"A5 {gid} MS={gate['ms']} 与 killed/total={expected_ms:.6f} 不符")
        if abs(gate["gate_vitality"] - gate["ms"] * 100.0) > 1e-6:
            violations.append(
                f"A5 {gid} gate_vitality={gate['gate_vitality']} != MS*100={gate['ms'] * 100.0}"
            )
        for row in gate["criteria"]:
            if abs(row["vitality_score"] - row["ms"] * 100.0) > 1e-6:
                violations.append(
                    f"A5 {gid}/{row['criterion_id']} 判据分数 {row['vitality_score']} != "
                    f"MS*100={row['ms'] * 100.0}"
                )
    return (not violations), violations


def render_markdown(run: Mapping[str, Any], title: str = "判据活力门体检报告") -> str:
    """把运行记录渲染成可贴报告的 markdown（数字全部取自 run，不手抄）。"""
    lines = [
        f"# {title}",
        "",
        f"- schema: `{run['schema']}` / 生成于 `{run['generated_at']}`",
        f"- 机制源码 sha256: `{run['module']['sha256'][:16]}…`（{run['module']['path']}）",
        f"- 变异库预注册: `{run['library']['preregistered']}`"
        f"（library sha256 `{(run['library']['sha256'] or '-')[:16]}`）",
        f"- 达标线 MS >= `{run['ms_floor']}`；阈值/观测式扰动幅度 "
        f"`{run['relative_perturbation']:g}`",
        "",
        "## 1 门级裁决",
        "",
        "| 门 | 变异体 | 杀死 | 存活 | MS | gate_vitality | 裁决 | 可自报 REAL |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for gate in run["gates"]:
        killed = sum(r["killed"] for r in gate["criteria"])
        total = gate["matrix_stats"]["expected_cells"]
        lines.append(
            f"| `{gate['gate_id']}` | {total} | {killed} | {len(gate['survived'])} | "
            f"{gate['ms']:.4f} | **{gate['gate_vitality']:.2f}** | {gate['verdict']} | "
            f"{'是' if gate['real_claim_allowed'] else '否'} |"
        )
    lines += ["", "## 2 判据 × 变异体 kill 矩阵", ""]
    for gate in run["gates"]:
        operators = sorted(
            {cell["operator"] for cells in gate["kill_matrix"].values() for cell in cells.values()}
        )
        lines += [
            f"### {gate['gate_id']}",
            "",
            "| 判据 | " + " | ".join(operators) + " | MS |",
            "|---" * (len(operators) + 2) + "|",
        ]
        for row in gate["criteria"]:
            cells = gate["kill_matrix"][row["criterion_id"]]
            marks = []
            for op in operators:
                cell = next((c for c in cells.values() if c["operator"] == op), None)
                marks.append("KILL" if cell and cell["result"] == "KILLED" else
                             ("live" if cell else "-"))
            lines.append(
                f"| `{row['criterion_id']}` | " + " | ".join(marks) + f" | {row['ms']:.4f} |"
            )
        lines += [
            "",
            f"- 矩阵完整性：{gate['matrix_stats']['actual_cells']}/"
            f"{gate['matrix_stats']['expected_cells']} 格（complete="
            f"{gate['matrix_stats']['complete']}）",
            f"- 过拟合诊断：{gate['diagnostics']['overfit_suspect']}"
            f"（距边界 <{gate['diagnostics']['overfit_margin']:g} 的样本 "
            f"{gate['diagnostics']['within_overfit_margin']}/"
            f"{gate['diagnostics']['subjects_total']}）",
            "",
        ]
        if gate["blockers"]:
            lines += ["- 阻塞项："] + [f"  - {b}" for b in gate["blockers"]] + [""]
    lines += ["## 3 survived 清单（需人工审查）", ""]
    if not any(gate["survived"] for gate in run["gates"]):
        lines += ["（无：全部门全部变异体被杀）", ""]
    else:
        lines += ["| 门 | 判据 | 变异体 | 算子 | 原因码 | 明细 |", "|---|---|---|---|---|---|"]
        for gate in run["gates"]:
            for item in gate["survived"]:
                lines.append(
                    f"| `{gate['gate_id']}` | `{item['criterion_id']}` | {item['mutant_id']} | "
                    f"{item['operator']} | `{item['reason_code']}` | {item['detail']} |"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


# ── capability ledger 入账 ──────────────────────────────────────────────
def build_ledger_block(
    run: Mapping[str, Any],
    ledger: Mapping[str, Any] | None = None,
    capability_map: Mapping[str, Sequence[str]] | None = None,
) -> dict:
    """构造写进 capability_ledger 的 criteria_vitality 段（纯函数）。"""
    gates_by_cap: dict[str, list[str]] = {}
    for gate_id, caps in (capability_map or {}).items():
        for cap in caps:
            gates_by_cap.setdefault(str(cap), []).append(str(gate_id))

    gates_block: dict[str, dict] = {}
    for gate in run["gates"]:
        gates_block[gate["gate_id"]] = {
            "verdict": gate["verdict"],
            "ms": gate["ms"],
            "gate_vitality": gate["gate_vitality"],
            "real_claim_allowed": gate["real_claim_allowed"],
            "blockers": gate["blockers"],
            "matrix_complete": gate["matrix_stats"]["complete"],
            "overfit_suspect": gate["diagnostics"]["overfit_suspect"],
            "criteria": {
                row["criterion_id"]: {
                    "vitality_score": row["vitality_score"],
                    "ms": row["ms"],
                    "killed": row["killed"],
                    "mutants_total": row["mutants_total"],
                    "status": row["status"],
                }
                for row in gate["criteria"]
            },
        }

    cap_ids: list[str] = []
    if ledger is not None:
        cap_ids = [str(cap.get("id")) for cap in ledger.get("capabilities", [])]
    for cap in gates_by_cap:
        if cap not in cap_ids:
            cap_ids.append(cap)

    capabilities_block: dict[str, dict] = {}
    for cap in cap_ids:
        gate_ids = [g for g in gates_by_cap.get(cap, []) if g in gates_block]
        if not gate_ids:
            capabilities_block[cap] = {
                "status": STATUS_UNEXAMINED,
                "vitality_score": None,
                "real_claim_allowed": False,
                "gates": [],
                "reason": "未体检：capability_ledger 里没有该能力的判据活力门体检记录，按机制不得自报 REAL",
            }
            continue
        total = sum(gates_block[g]["criteria"][c]["mutants_total"]
                    for g in gate_ids for c in gates_block[g]["criteria"])
        killed = sum(gates_block[g]["criteria"][c]["killed"]
                     for g in gate_ids for c in gates_block[g]["criteria"])
        ms = (killed / total) if total else 0.0
        allowed = all(gates_block[g]["real_claim_allowed"] for g in gate_ids)
        capabilities_block[cap] = {
            "status": STATUS_EXAMINED,
            "vitality_score": _round(ms * 100.0, 10),
            "ms": _round(ms, 12),
            "killed": killed,
            "mutants_total": total,
            "real_claim_allowed": allowed,
            "gates": gate_ids,
            "reason": "" if allowed else "存在未达标/被封的活力门，按机制不得自报 REAL",
        }

    return {
        "schema": SCHEMA_LEDGER_BLOCK,
        "generated_at": run["generated_at"],
        "module_sha256": run["module"]["sha256"],
        "library_sha256": run["library"]["sha256"],
        "ms_floor": run["ms_floor"],
        "gates": gates_block,
        "capabilities": capabilities_block,
        "summary": {
            "gates_total": len(gates_block),
            "gates_vital": sum(1 for g in gates_block.values() if g["verdict"] == VERDICT_VITAL),
            "gates_inert": sum(1 for g in gates_block.values() if g["verdict"] == VERDICT_INERT),
            "gates_blocked": sum(1 for g in gates_block.values() if g["verdict"] == VERDICT_BLOCKED),
            "capabilities_total": len(capabilities_block),
            "capabilities_examined": sum(
                1 for c in capabilities_block.values() if c["status"] == STATUS_EXAMINED
            ),
            "capabilities_unexamined": sum(
                1 for c in capabilities_block.values() if c["status"] == STATUS_UNEXAMINED
            ),
            "capabilities_real_claim_allowed": sum(
                1 for c in capabilities_block.values() if c["real_claim_allowed"]
            ),
        },
    }


def _ledger_fingerprint(ledger: Mapping[str, Any], exclude: Sequence[str] = ()) -> str:
    payload = {k: v for k, v in ledger.items() if k not in set(exclude)}
    return sha256_text(canonical_json(payload))


VITALITY_FIELDS: tuple[str, ...] = (
    "vitality_score",
    "vitality_status",
    "vitality_real_claim_allowed",
)


def _vitality_stripped_fingerprint(ledger: Mapping[str, Any]) -> str:
    """剔除 criteria_vitality 段与 capabilities[*] 的 vitality_* 字段后的台账指纹。

    用它保证「入账只动体力字段」：其余内容逐字节不动，且重复入账幂等。
    """
    payload = {k: v for k, v in ledger.items() if k != "criteria_vitality"}
    if "capabilities" in payload:
        payload["capabilities"] = [
            {k: v for k, v in cap.items() if k not in VITALITY_FIELDS}
            for cap in payload["capabilities"]
        ]
    return sha256_text(canonical_json(payload))


def stamp_capability_ledger(
    run: Mapping[str, Any],
    ledger_path: Path | str,
    capability_map: Mapping[str, Sequence[str]] | None = None,
    embed_capability_fields: bool = False,
    write: bool = True,
) -> tuple[dict, dict, str]:
    """把活力分数写进 capability_ledger。

    返回 (block, ledger_after, untouched_digest_before)。写入是**附加式**的：
    除 ``criteria_vitality`` 段（以及可选的 ``capabilities[*].vitality_score`` 字段）外，
    台账其余内容逐字节不动（由 untouched 指纹前后比对保证）。
    """
    path = Path(ledger_path)
    ledger = load_json(path)
    if not isinstance(ledger, Mapping):
        raise VitalityInputError(f"{path}: 台账必须是对象")
    ledger = dict(ledger)

    block = build_ledger_block(run, ledger, capability_map)
    before = _vitality_stripped_fingerprint(ledger)

    updated = dict(ledger)
    updated["criteria_vitality"] = block
    if embed_capability_fields:
        caps = []
        for cap in updated.get("capabilities", []):
            cap = dict(cap)
            info = block["capabilities"].get(str(cap.get("id")), {})
            cap["vitality_score"] = info.get("vitality_score")
            cap["vitality_status"] = info.get("status", STATUS_UNEXAMINED)
            cap["vitality_real_claim_allowed"] = bool(info.get("real_claim_allowed", False))
            caps.append(cap)
        updated["capabilities"] = caps

    after = _vitality_stripped_fingerprint(updated)
    if after != before:
        raise VitalityInputError("台账写入越界：除 vitality 字段外内容发生变化，已中止写入")

    if write:
        path.write_text(
            json.dumps(updated, ensure_ascii=False, indent=LEDGER_JSON_INDENT) + "\n",
            encoding="utf-8",
        )
    return block, updated, before


# ── CLI ────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判据活力门：变异注入体检 + 杀死率")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pre = sub.add_parser("preregister", help="生成并落盘变异库（预注册）")
    p_pre.add_argument("--gate", required=True)
    p_pre.add_argument("--out", required=True)

    p_run = sub.add_parser("run", help="执行体检")
    p_run.add_argument("--gate", required=True)
    p_run.add_argument("--library", default=None, help="预注册变异库（缺省=现场生成，不推荐）")
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--markdown", default=None)

    p_stamp = sub.add_parser("stamp", help="写入 capability ledger")
    p_stamp.add_argument("--run", required=True)
    p_stamp.add_argument("--ledger", required=True)
    p_stamp.add_argument("--capability-map", default=None, help="JSON: {gate_id: [capability_id...]}")
    p_stamp.add_argument("--embed-capability-fields", action="store_true")
    p_stamp.add_argument("--block-out", default=None)

    p_check = sub.add_parser("check", help="A1~A5 机裁")
    p_check.add_argument("--run", required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "preregister":
            gates = load_gates(args.gate)
            library = build_library_file(gates)
            write_library(library, args.out)
            total = sum(len(lib["mutants"]) for lib in library["gates"].values())
            print(f"预注册完成：{len(gates)} 个门 / {total} 个变异体 -> {args.out}")
            return 0

        if args.cmd == "run":
            gates = load_gates(args.gate)
            library = load_json(args.library) if args.library else None
            run = run_bundle(gates, library)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.markdown:
                md = Path(args.markdown)
                md.parent.mkdir(parents=True, exist_ok=True)
                md.write_text(render_markdown(run), encoding="utf-8")
            ok, violations = check_run(run)
            not_vital = [g["gate_id"] for g in run["gates"] if g["verdict"] != VERDICT_VITAL]
            print(
                f"体检完成：{run['summary']['gates_total']} 个门 / "
                f"MS={run['summary']['ms']:.4f} / "
                f"VITAL={run['summary']['gates_vital']} INERT={run['summary']['gates_inert']} "
                f"BLOCKED={run['summary']['gates_blocked']} -> {out}"
            )
            for item in violations:
                print(f"  违规: {item}")
            for gid in not_vital:
                print(f"  未达标门（不得自报 REAL）: {gid}")
            return 0 if (ok and not not_vital) else 2

        if args.cmd == "stamp":
            run = load_json(args.run)
            cap_map = load_json(args.capability_map) if args.capability_map else None
            block, _, _ = stamp_capability_ledger(
                run,
                args.ledger,
                capability_map=cap_map,
                embed_capability_fields=args.embed_capability_fields,
            )
            if args.block_out:
                Path(args.block_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.block_out).write_text(
                    json.dumps(block, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            print(
                f"入账完成：capabilities={block['summary']['capabilities_total']} "
                f"examined={block['summary']['capabilities_examined']} "
                f"unexamined={block['summary']['capabilities_unexamined']} "
                f"real_allowed={block['summary']['capabilities_real_claim_allowed']}"
            )
            return 0

        run = load_json(args.run)
        ok, violations = check_run(run)
        not_vital = [g["gate_id"] for g in run.get("gates", []) if g["verdict"] != VERDICT_VITAL]
        if ok and not not_vital:
            print(f"PASS: A1~A5 全绿且全部门 VITAL（MS={run['summary']['ms']:.4f}）")
            return 0
        if ok and not_vital:
            print("FAIL: 机制自洽但存在未达标门（不得自报 REAL）：")
            for gid in not_vital:
                print(f"  - {gid}")
            return 2
        print("FAIL:")
        for item in violations:
            print(f"  - {item}")
        return 2
    except VitalityInputError as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
