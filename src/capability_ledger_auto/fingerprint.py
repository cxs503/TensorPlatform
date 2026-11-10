"""证据指纹（evidence fingerprint）：把一条能力的证据链压成可复算的 sha256 溯源链。

设计目标（对应节点 p399w2-n02 的「sha256 溯源指纹 + 溯源链」要求）：

1. **确定性**：同一份仓库内容两次扫描得到逐字节相同的指纹（叶子排序固定、
   canonical JSON、不含时间戳）。
2. **可复算**：任何人都能用 :func:`verify_leaves` 从叶子逐条复算到根哈希，
   任一叶子被改写即定位到首个坏条目（fail-closed）。
3. **有界**：产物里每类叶子最多落盘 ``MAX_ITEMS_PER_KIND`` 条（控制体积），
   但根哈希始终覆盖**全部**叶子，并显式登记被截断的条数——体积优化不得
   削弱防篡改强度。
4. **零领域语义**：叶子只记录 kind / locator / sha256 / 结构化计数，
   不含任何域词条。

链结构::

    h_0 = sha256("GENESIS:<capability_id>")
    h_i = sha256(h_{i-1} + "|" + canonical(body_i))
    root = h_n

叶子按 ``(kind, locator, digest)`` 排序，保证扫描顺序变化不影响根哈希。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .evidence_chain import EvidenceChain
from .provenance import GENESIS_HASH, canonical_json, sha256_text

FINGERPRINT_SCHEMA = "evidence-fingerprint/v1"
MAX_ITEMS_PER_KIND = 32
MAX_DETAIL_ITEMS = 8


def _truncate(values: Iterable[str]) -> list[str]:
    out = []
    for v in values:
        out.append(str(v))
        if len(out) >= MAX_DETAIL_ITEMS:
            break
    return out


@dataclass(frozen=True)
class EvidenceLeaf:
    """指纹树的一片叶子：一条可独立复核的证据单元。"""

    kind: str  # code / test / report / five_tuple
    locator: str
    digest: str
    detail: dict[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "locator": self.locator, "digest": self.digest}
        if self.detail:
            payload["detail"] = self.detail
        return payload

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.kind, self.locator, self.digest)


def _leaf_hash(prev: str, leaf: EvidenceLeaf) -> str:
    return sha256_text(prev + "|" + canonical_json(leaf.body()))


def chain_root(capability_id: str, leaves: Iterable[EvidenceLeaf]) -> str:
    """自创世哈希逐条链接，返回根哈希（空叶子集合返回创世哈希）。"""
    ordered = sorted(leaves, key=lambda leaf: leaf.sort_key)
    prev = sha256_text(f"GENESIS:{capability_id}")
    for leaf in ordered:
        prev = _leaf_hash(prev, leaf)
    return prev


def verify_leaves(
    capability_id: str, leaves: Iterable[EvidenceLeaf], expected_root: str | None = None,
) -> tuple[bool, int | None]:
    """逐条复算哈希链。

    返回 ``(是否完好, 首个坏条目下标)``；给出 ``expected_root`` 时同时比对根哈希
    （叶子被删/被增也会被抓到）。空集合 + 无 expected_root 视为完好。
    """
    ordered = sorted(leaves, key=lambda leaf: leaf.sort_key)
    prev = sha256_text(f"GENESIS:{capability_id}")
    for idx, leaf in enumerate(ordered):
        prev = _leaf_hash(prev, leaf)
    if expected_root is not None and prev != expected_root:
        # 根哈希不符但逐条链完好 = 叶子被删除或新增，定位到链尾
        return False, len(ordered)
    return True, None


def _code_leaf(ev: Any) -> EvidenceLeaf:
    locator = f"{ev.file_path}:{ev.line_number}"
    digest = sha256_text(
        "|".join([str(ev.file_path), str(ev.matched_text), str(ev.line_sha256)])
    )
    detail = {
        "match_type": getattr(ev, "match_type", "text"),
        "matched_text": str(getattr(ev, "matched_text", ""))[:120],
        "file_sha256": getattr(ev, "file_sha256", ""),
        "is_stub_like": bool(getattr(ev, "is_stub_like", False)),
    }
    markers = _truncate(getattr(ev, "mock_markers", []) or [])
    if markers:
        detail["mock_markers"] = markers
    return EvidenceLeaf("code", locator, digest, detail)


def _test_leaf(ev: Any) -> EvidenceLeaf:
    detail = {
        "source": getattr(ev, "source", "static"),
        "passed": int(getattr(ev, "passed_tests", 0)),
        "failed": int(getattr(ev, "failed_tests", 0)),
        "total": int(getattr(ev, "total_tests", 0)),
    }
    return EvidenceLeaf("test", str(ev.file_path), getattr(ev, "file_sha256", "") or
                        sha256_text(str(ev.file_path)), detail)


def _report_leaf(ev: Any) -> EvidenceLeaf:
    detail = {
        "verdict": str(getattr(ev, "verdict", "")),
        "asserted_level": str(getattr(ev, "asserted_level", "")),
        "as_of": str(getattr(ev, "as_of", "")),
        "source_kind": str(getattr(ev, "source_kind", "")),
    }
    return EvidenceLeaf("report", str(ev.file_path),
                        getattr(ev, "file_sha256", "") or sha256_text(str(ev.file_path)), detail)


def _five_tuple_leaves(chain: EvidenceChain) -> list[EvidenceLeaf]:
    leaves: list[EvidenceLeaf] = []
    five = chain.five_tuple
    for name in ("theory", "code", "config", "data", "report"):
        item = getattr(five, name, None)
        if item is None:
            continue
        locator = f"{name}:{item.source_path or '<missing>'}"
        digest = item.content_hash or sha256_text(f"{name}:{item.source_path}")
        leaves.append(EvidenceLeaf(
            "five_tuple", locator, digest,
            {"present": bool(item.is_present), "quality": round(float(item.quality_score), 4)},
        ))
    return leaves


def build_leaves(chain: EvidenceChain) -> list[EvidenceLeaf]:
    """把证据链展开成叶子集合（顺序无关，内部会排序）。"""
    leaves: list[EvidenceLeaf] = []
    scan = chain.scan_result
    leaves.extend(_code_leaf(ev) for ev in scan.code_evidence)
    leaves.extend(_test_leaf(ev) for ev in scan.test_evidence)
    leaves.extend(_report_leaf(ev) for ev in scan.report_evidence)
    leaves.extend(_five_tuple_leaves(chain))
    return leaves


@dataclass(frozen=True)
class EvidenceFingerprint:
    """单条能力的证据指纹。"""

    capability_id: str
    root: str
    leaf_count: int
    leaves: tuple[EvidenceLeaf, ...]  # 落盘用的有界子集
    truncated_by_kind: dict[str, int] = field(default_factory=dict)
    registry_sha256: str = ""

    def verify(self, all_leaves: Iterable[EvidenceLeaf] | None = None) -> tuple[bool, int | None]:
        """有 ``all_leaves`` 时按全量复核；否则按落盘子集复核（子集自洽，但会
        与 root 不符时如实返回 False，不做静默通过）。"""
        if all_leaves is None:
            return verify_leaves(self.capability_id, self.leaves, expected_root=self.root)
        return verify_leaves(self.capability_id, all_leaves, expected_root=self.root)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FINGERPRINT_SCHEMA,
            "capability_id": self.capability_id,
            "root": self.root,
            "leaf_count": self.leaf_count,
            "leaves_emitted": len(self.leaves),
            "truncated_by_kind": dict(self.truncated_by_kind),
            "registry_sha256": self.registry_sha256,
            "leaves": [leaf.body() for leaf in self.leaves],
        }


def fingerprint_chain(
    chain: EvidenceChain, registry_sha256: str = "",
) -> EvidenceFingerprint:
    """对一条证据链生成指纹（根哈希覆盖全部叶子，落盘子集有界）。"""
    all_leaves = sorted(build_leaves(chain), key=lambda leaf: leaf.sort_key)
    root = chain_root(chain.capability_id, all_leaves)

    by_kind: dict[str, list[EvidenceLeaf]] = {}
    for leaf in all_leaves:
        by_kind.setdefault(leaf.kind, []).append(leaf)

    emitted: list[EvidenceLeaf] = []
    truncated: dict[str, int] = {}
    for kind in sorted(by_kind):
        items = by_kind[kind]
        emitted.extend(items[:MAX_ITEMS_PER_KIND])
        if len(items) > MAX_ITEMS_PER_KIND:
            truncated[kind] = len(items) - MAX_ITEMS_PER_KIND

    return EvidenceFingerprint(
        capability_id=chain.capability_id,
        root=root,
        leaf_count=len(all_leaves),
        leaves=tuple(sorted(emitted, key=lambda leaf: leaf.sort_key)),
        truncated_by_kind=truncated,
        registry_sha256=registry_sha256,
    )


def record_digest(record: dict[str, Any]) -> str:
    """台账记录行摘要（等级 + 置信度 + 证据根 + 注册表摘要的绑定）。"""
    bound = {
        "capability_id": record.get("capability_id"),
        "registry_sha256": record.get("registry_sha256"),
        "grade": record.get("grade"),
        "confidence": record.get("confidence"),
        "evidence_root": record.get("evidence_root"),
        "leaf_count": record.get("leaf_count"),
    }
    return sha256_text(canonical_json(bound))


def build_record(
    chain: EvidenceChain,
    grade_result: Any,
    registry_sha256: str = "",
) -> dict[str, Any]:
    """把「判级 + 置信度 + 证据指纹」合成一行可入库的台账记录。"""
    fp = fingerprint_chain(chain, registry_sha256=registry_sha256)
    record: dict[str, Any] = {
        "capability_id": chain.capability_id,
        "registry_sha256": registry_sha256,
        "grade": getattr(grade_result, "grade", ""),
        "confidence": round(float(getattr(grade_result, "confidence", 0.0)), 4),
        "evidence_root": fp.root,
        "leaf_count": fp.leaf_count,
        "truncated_by_kind": dict(fp.truncated_by_kind),
        "leaves": [leaf.body() for leaf in fp.leaves],
        "chain_quality": round(float(chain.chain_quality), 4),
        "five_tuple_present": chain.five_tuple.present_count,
    }
    record["record_digest"] = record_digest(record)
    return record


__all__ = [
    "FINGERPRINT_SCHEMA",
    "MAX_ITEMS_PER_KIND",
    "EvidenceLeaf",
    "EvidenceFingerprint",
    "build_leaves",
    "chain_root",
    "verify_leaves",
    "fingerprint_chain",
    "record_digest",
    "build_record",
    "GENESIS_HASH",
]