"""双索引：``campaign_nodes.name``（真库）+ 历史提案池（``**/proposals/**/*.json``）。

索引里只放**文本与结构化键**，不存任何域词表：文本相似度由
:mod:`tools.proposal_gate.textsim` 的字符 n-gram 计算，因此索引对任意学科域都同构。

条目类型（``kind``）来自配置 ``dedup.index_kinds``：
* ``campaign_node`` —— 在飞/已交付战役节点（真库读出的 name [+ metadata.goal]）
* ``proposal``      —— 提案（历史池 + 本进程内已接纳提案）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .config import GateConfig
from .contract import ProposalDoc
from .textsim import SimResult, shingles, sim_from_sets, similarity_texts


@dataclass(frozen=True)
class IndexEntry:
    kind: str
    key: str
    text: str
    repo: str | None = None
    deps: frozenset[str] = frozenset()
    sha256: str | None = None
    meta: tuple[tuple[str, str], ...] = ()
    grams: frozenset[str] = frozenset()

    def with_grams(self, size: int) -> "IndexEntry":
        """预切分 n-gram 集合（索引构建期一次算好，查询期只做集合运算）。"""
        if self.grams:
            return self
        return IndexEntry(
            kind=self.kind,
            key=self.key,
            text=self.text,
            repo=self.repo,
            deps=self.deps,
            sha256=self.sha256,
            meta=self.meta,
            grams=shingles(self.text, size),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "key": self.key,
            "repo": self.repo,
            "text_len": len(self.text),
            "deps": sorted(self.deps),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DedupHit:
    """一次去重命中（或最接近的一次未命中）的完整证据。"""

    entry: IndexEntry
    sim: SimResult

    def as_dict(self) -> dict[str, object]:
        return {"entry": self.entry.as_dict(), **self.sim.as_dict()}


@dataclass(frozen=True)
class TwinHit:
    entry: IndexEntry
    dep_overlap: float
    repo: str

    def as_dict(self) -> dict[str, object]:
        return {
            "entry": self.entry.as_dict(),
            "dep_overlap": round(self.dep_overlap, 6),
            "repo": self.repo,
        }


def dependency_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """结构化冲突度量：依赖提示集合的 Jaccard（空集 → 0，不构成孪生证据）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ProposalIndex:
    cfg: GateConfig
    entries: list[IndexEntry] = field(default_factory=list)

    # ── 构建 ────────────────────────────────────────────────────────────
    def add(self, entry: IndexEntry) -> None:
        self.entries.append(entry.with_grams(int(self.cfg.need("dedup.shingle_size"))))

    def add_many(self, entries: Iterable[IndexEntry]) -> int:
        n = 0
        for e in entries:
            self.add(e)
            n += 1
        return n

    def add_node(self, key: str, text: str, *, repo: str | None = None, sha256: str | None = None) -> None:
        self.add(IndexEntry(kind="campaign_node", key=key, text=text, repo=repo, sha256=sha256))

    def add_doc(self, doc: ProposalDoc, *, key: str) -> IndexEntry:
        entry = IndexEntry(
            kind="proposal",
            key=key,
            text=doc.blob(),
            repo=doc.repo,
            deps=doc.deps_set(),
            sha256=doc.sha256(),
        )
        self.add(entry)
        return entry

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[IndexEntry]:
        return iter(self.entries)

    def by_kind(self, kind: str) -> list[IndexEntry]:
        return [e for e in self.entries if e.kind == kind]

    def kinds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    # ── 查询 ────────────────────────────────────────────────────────────
    def _candidates(
        self,
        kinds: Sequence[str] | None = None,
        *,
        exclude_sha: str | None = None,
    ) -> list[IndexEntry]:
        wanted = set(kinds) if kinds else None
        out = []
        for e in self.entries:
            if wanted is not None and e.kind not in wanted:
                continue
            if exclude_sha is not None and e.sha256 is not None and e.sha256 == exclude_sha:
                continue  # 幂等重提：自我比较不算重复证据
            out.append(e)
        return out

    def best_text_match(
        self,
        doc: ProposalDoc,
        *,
        kinds: Sequence[str] | None = None,
    ) -> DedupHit | None:
        """最相似的候选条目（不做阈值裁剪，阈值判定在 gate 层做，便于落审计数字）。"""
        size = int(self.cfg.need("dedup.shingle_size"))
        probe = shingles(doc.blob(), size)
        best: DedupHit | None = None
        for entry in self._candidates(kinds, exclude_sha=doc.sha256()):
            sim = (
                sim_from_sets(probe, entry.grams, self.cfg)
                if entry.grams
                else similarity_texts(doc.blob(), entry.text, self.cfg)
            )
            if best is None or sim.score > best.sim.score:
                best = DedupHit(entry=entry, sim=sim)
        return best

    def best_node_match(self, doc: ProposalDoc) -> DedupHit | None:
        return self.best_text_match(doc, kinds=("campaign_node",))

    def find_twin(
        self,
        doc: ProposalDoc,
        *,
        kinds: Sequence[str] | None = None,
    ) -> TwinHit | None:
        """结构化冲突：同 repo 且依赖提示集合重合 ≥ ``dedup.twin_dependency_overlap``。"""
        threshold = float(self.cfg.need("dedup.twin_dependency_overlap"))
        deps = doc.deps_set()
        if not deps:
            return None
        best: TwinHit | None = None
        for entry in self._candidates(kinds, exclude_sha=doc.sha256()):
            if not entry.repo or entry.repo != doc.repo:
                continue
            overlap = dependency_overlap(deps, entry.deps)
            if overlap >= threshold and (best is None or overlap > best.dep_overlap):
                best = TwinHit(entry=entry, dep_overlap=overlap, repo=doc.repo)
        return best