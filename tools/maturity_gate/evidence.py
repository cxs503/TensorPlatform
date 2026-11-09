"""证据解析与通用比较器：把「声明判据」落到「可复算数字」上。

三条硬规矩（anti-fake-pass）：

1. **没有证据就没有 PASS**。证据文档找不到 / 路径不存在 / 值非数值 → ``WITHHELD``，
   既不降级成 PASS，也不当成 FAIL 掩盖（"不知道"≠"不过"≠"过"）。
2. **值可复算**。每个检查读取的数字都带 ``doc`` + ``pointer``（+ 可选 ``sha256`` 钉桩）；
   文档被改过而钉桩未同步 → ``FAIL(evidence_integrity_mismatch)``。
3. **上游自述与复算互裁**。检查里可声明 ``claim``（上游自述布尔）；本引擎按自己的
   谓词重算一遍，两者不一致 → 记 ``claim_mismatch``（不改判，但一票入账，报告必须显形）。

判据 = 检查的合取：任一检查 FAIL 则判据 FAIL；否则任一检查 WITHHELD 则判据 WITHHELD；
全部 PASS 才 PASS。**判据数是分母，检查数不是**——拆句刷分无效。

本模块不含任何领域语义：一切路径/阈值来自规格工件，比较器是纯数学谓词。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .spec import Check, Criterion, NodeSpec

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"

#: 具名原因（报告必须逐条引用，禁止「未知原因」）
REASON_DOC_MISSING = "evidence_doc_missing"
REASON_DOC_UNREADABLE = "evidence_doc_unreadable"
REASON_POINTER_MISSING = "pointer_missing"
REASON_NOT_NUMERIC = "value_not_numeric"
REASON_BAD_COMPARATOR = "comparator_unusable"
REASON_INTEGRITY = "evidence_integrity_mismatch"
REASON_NO_CHECKS = "no_checks_declared"

_INDEX_RE = re.compile(r"^(.*?)\[(-?\d+)\]$")


class PointerError(KeyError):
    """点号路径在文档中不存在。"""


def get_pointer(doc: Any, pointer: str) -> Any:
    """按 ``a.b[0].c`` 取值；缺失抛 :class:`PointerError`（空 pointer 返回文档本身）。"""
    if pointer in ("", "."):
        return doc
    cur = doc
    for part in pointer.split("."):
        if part == "":
            raise PointerError(pointer)
        m = _INDEX_RE.match(part)
        idx: Optional[int] = None
        if m:
            part, idx = m.group(1), int(m.group(2))
        if part:
            if not isinstance(cur, Mapping) or part not in cur:
                raise PointerError(f"{pointer}: 缺键 {part!r}")
            cur = cur[part]
        if idx is not None:
            if not isinstance(cur, (list, tuple)) or not cur:
                raise PointerError(f"{pointer}: 非序列或空，无法取 [{idx}]")
            try:
                cur = cur[idx]
            except IndexError as exc:  # pragma: no cover - 防御
                raise PointerError(f"{pointer}: 下标越界 [{idx}]") from exc
    return cur


def payload_digest(payload: Any) -> str:
    """证据载荷规范化指纹：键排序 + 无冗余空白 + UTF-8。磁盘与内存两条路径同一口径。"""
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(value: Any, comparator: str, threshold: Any = None,
            tolerance: float = 0.0) -> bool:
    """通用谓词。``eq`` 支持布尔阈值（身份判定）；``tolerance`` 仅对数值 ``eq`` 生效。"""
    if comparator == "nonempty":
        if isinstance(value, (str, list, tuple, dict)):
            return len(value) > 0
        return value is not None
    if comparator == "eq" and isinstance(threshold, bool):
        return value is threshold or value == threshold
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"非数值 {value!r} 不能用于比较器 {comparator}")
    v = float(value)
    if comparator == "le":
        return v <= float(threshold)
    if comparator == "lt":
        return v < float(threshold)
    if comparator == "ge":
        return v >= float(threshold)
    if comparator == "gt":
        return v > float(threshold)
    if comparator == "eq":
        t = float(threshold)
        return abs(v - t) <= abs(t) * tolerance if t else v == t
    if comparator == "between":
        lo, hi = float(threshold[0]), float(threshold[1])
        return lo <= v <= hi
    raise ValueError(f"未知比较器 {comparator}")


@dataclass
class CheckResult:
    pointer: str
    verdict: str
    comparator: str
    threshold: Any
    unit: str = "-"
    measured: Any = None
    reason: Optional[str] = None
    evidence_doc: Optional[str] = None
    evidence_sha256: Optional[str] = None
    claim: Any = None
    claim_mismatch: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pointer": self.pointer, "verdict": self.verdict,
            "comparator": self.comparator, "threshold": self.threshold,
            "unit": self.unit, "measured": self.measured, "reason": self.reason,
            "evidence": {"doc": self.evidence_doc, "sha256": self.evidence_sha256},
            "claim": self.claim, "claim_mismatch": self.claim_mismatch,
        }


@dataclass
class CriterionResult:
    criterion_id: str
    statement: str
    verdict: str
    checks: List[CheckResult] = field(default_factory=list)
    reason: Optional[str] = None

    @property
    def claim_mismatch(self) -> bool:
        return any(c.claim_mismatch for c in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {"criterion_id": self.criterion_id, "statement": self.statement,
                "verdict": self.verdict, "reason": self.reason,
                "claim_mismatch": self.claim_mismatch,
                "checks": [c.to_dict() for c in self.checks]}


class EvidenceResolver:
    """证据文档定位器（磁盘默认实现；API 无状态裁判另有内存实现）。

    解析顺序（先具体后一般，均为平台约定，无领域含义）：

    1. 绝对路径直接使用；
    2. ``repo_roots[repo] / doc``（调用方显式给定的仓库工作树）；
    3. ``work_root / f"m{mission_id}" / doc``（mission → 工作树目录的命名约定）。

    子类只需覆写 :meth:`locate` / :meth:`digest` / :meth:`payload` 三件套。
    """

    def __init__(self, repo_roots: Optional[Mapping[str, str]] = None,
                 work_root: Optional[str] = None) -> None:
        self.repo_roots = {k: Path(v) for k, v in (repo_roots or {}).items()}
        self.work_root = Path(work_root) if work_root else None
        self._doc_cache: Dict[Any, Any] = {}
        self._digest_cache: Dict[Any, str] = {}

    def candidates(self, node: NodeSpec, doc: str) -> List[Path]:
        p = Path(doc)
        if p.is_absolute():
            return [p]
        out: List[Path] = []
        root = self.repo_roots.get(node.repo)
        if root is not None:
            out.append(root / p)
        if self.work_root is not None and node.mission_id is not None:
            out.append(self.work_root / f"m{node.mission_id}" / p)
        return out

    def locate(self, node: NodeSpec, doc: str) -> Optional[Path]:
        """返回存在的证据文档路径（报告里的溯源标签），不存在返回 None。"""
        for cand in self.candidates(node, doc):
            if cand.is_file():
                return cand
        return None

    def digest(self, node: NodeSpec, doc: str) -> Optional[str]:
        """证据指纹 = sha256(canonical_json(载荷))。

        刻意**不用文件字节**：字节指纹会被格式化/换行改动打翻，也会让「磁盘路径」与
        「内存裁判路径」算出不同指纹（线上绿、离线红的经典来源）。载荷规范化后两条
        实现路径必然同值。
        """
        path = self.locate(node, doc)
        if path is None:
            return None
        if path not in self._digest_cache:
            try:
                payload = self.payload(node, doc)
            except (OSError, json.JSONDecodeError):
                return None
            self._digest_cache[path] = payload_digest(payload)
        return self._digest_cache[path]

    def payload(self, node: NodeSpec, doc: str) -> Any:
        path = self.locate(node, doc)
        if path is None:
            raise FileNotFoundError(doc)
        if path not in self._doc_cache:
            self._doc_cache[path] = json.loads(path.read_text(encoding="utf-8"))
        return self._doc_cache[path]

    def resolve_existing_root(self, node: NodeSpec) -> Optional[Path]:
        """候选仓库根中第一个真实存在的目录（报告溯源用）。"""
        root = self.repo_roots.get(node.repo)
        if root is not None and root.is_dir():
            return root
        if self.work_root is not None and node.mission_id is not None:
            cand = self.work_root / f"m{node.mission_id}"
            if cand.is_dir():
                return cand
        return None


def _load_doc(path: Path, cache: Dict[Path, Any]) -> Any:
    if path not in cache:
        cache[path] = json.loads(path.read_text(encoding="utf-8"))
    return cache[path]


def evaluate_check(check: Check, node: NodeSpec, resolver: EvidenceResolver) -> CheckResult:
    """判定单个检查 → CheckResult（PASS / FAIL / WITHHELD + 具名 reason）。"""
    base = dict(pointer=check.pointer, comparator=check.comparator,
                threshold=check.threshold, unit=check.unit)
    located = resolver.locate(node, check.doc)
    if located is None:
        return CheckResult(verdict=WITHHELD, reason=REASON_DOC_MISSING,
                           evidence_doc=check.doc, **base)
    digest = resolver.digest(node, check.doc)
    if check.sha256 is not None and digest != check.sha256:
        return CheckResult(verdict=FAIL, reason=REASON_INTEGRITY, evidence_doc=str(located),
                           evidence_sha256=digest, **base)
    try:
        doc = resolver.payload(node, check.doc)
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        return CheckResult(verdict=WITHHELD, reason=REASON_DOC_UNREADABLE,
                           evidence_doc=str(located), evidence_sha256=digest, **base)
    try:
        value = get_pointer(doc, check.pointer)
    except PointerError:
        return CheckResult(verdict=WITHHELD, reason=REASON_POINTER_MISSING,
                           evidence_doc=str(located), evidence_sha256=digest, **base)
    claim = None
    if check.claim:
        try:
            claim = get_pointer(doc, check.claim)
        except PointerError:
            claim = None
    try:
        ok = compare(value, check.comparator, check.threshold, check.tolerance)
    except TypeError:
        return CheckResult(verdict=WITHHELD, reason=REASON_NOT_NUMERIC, measured=value,
                           evidence_doc=str(located), evidence_sha256=digest, claim=claim, **base)
    except ValueError:
        return CheckResult(verdict=WITHHELD, reason=REASON_BAD_COMPARATOR, measured=value,
                           evidence_doc=str(located), evidence_sha256=digest, claim=claim, **base)
    mismatch = bool(isinstance(claim, bool) and claim != ok)
    return CheckResult(verdict=PASS if ok else FAIL, measured=value,
                       evidence_doc=str(located), evidence_sha256=digest, claim=claim,
                       claim_mismatch=mismatch, **base)


def combine(criterion: Criterion, checks: List[CheckResult]) -> CriterionResult:
    """检查合取成判据：FAIL 优先，其次 WITHHELD，全 PASS 才 PASS。"""
    if not checks:
        return CriterionResult(criterion.criterion_id, criterion.statement, WITHHELD,
                               reason=REASON_NO_CHECKS)
    if any(c.verdict == FAIL for c in checks):
        verdict, reason = FAIL, next(c.reason for c in checks if c.verdict == FAIL)
    elif any(c.verdict == WITHHELD for c in checks):
        verdict, reason = WITHHELD, next(c.reason for c in checks if c.verdict == WITHHELD)
    else:
        verdict, reason = PASS, None
    return CriterionResult(criterion.criterion_id, criterion.statement, verdict,
                           checks=list(checks), reason=reason)


def evaluate_criterion(crit: Criterion, node: NodeSpec,
                       resolver: EvidenceResolver) -> CriterionResult:
    return combine(crit, [evaluate_check(c, node, resolver) for c in crit.checks])


def summarize_node(node: NodeSpec, results: List[CriterionResult]) -> Dict[str, Any]:
    """节点汇总。分母恒为**声明判据数**：缺证据只会压低通过率，不会因少声明而虚高。"""
    n = len(results)
    passed = sum(1 for r in results if r.verdict == PASS)
    failed = sum(1 for r in results if r.verdict == FAIL)
    withheld = sum(1 for r in results if r.verdict == WITHHELD)
    return {
        "node_key": node.node_key,
        "title": node.title,
        "repo": node.repo,
        "mission_id": node.mission_id,
        "capability": node.capability or node.title,
        "criteria_total": n,
        "passed": passed,
        "failed": failed,
        "withheld": withheld,
        "pass_ratio": passed / n if n else 0.0,
        "evaluable_ratio": (passed + failed) / n if n else 0.0,
        "claim_mismatches": [r.criterion_id for r in results if r.claim_mismatch],
        "withheld_reasons": sorted({r.reason for r in results if r.reason}),
    }


def evaluate_node(node: NodeSpec, resolver: EvidenceResolver
                  ) -> Tuple[List[CriterionResult], Dict[str, Any]]:
    results = [evaluate_criterion(c, node, resolver) for c in node.criteria]
    return results, summarize_node(node, results)
