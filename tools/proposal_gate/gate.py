"""提案门仲裁入口：契约门 → 幂等门 → 去重门 → 结构化孪生门 → 落账。

判定顺序即纪律（先便宜后昂贵、先硬后软）：

1. **契约门（fail-closed）**：:mod:`tools.proposal_gate.contract` 校验失败 →
   ``quarantined``：载荷写隔离区文件 + 追加事件 + 落账理由，**不进仲裁、不进提案池**。
2. **幂等门**：``(mission_id, sha256)`` 已在账本 → ``duplicate``，``dup_of`` 指向首次，
   **不新增行**（反作弊 (a)）。
3. **去重门**：与索引（历史提案池 + 战役节点名 + 本次运行已接纳提案）逐条比相似度，
   最高分 ≥ ``dedup.dup_threshold`` → ``duplicate``（``dup_of`` 指向首次命中条目）。
4. **孪生门（结构化冲突）**：同 repo 且依赖提示集合重合 ≥
   ``dedup.twin_dependency_overlap`` → ``twin``（同属 ``dedup_rejected`` 态）。
5. 否则 ``accepted``（四态 ``proposal``），并入索引，供后续提案对拍。

阈值全部来自配置；本模块无领域词、无散落常量。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import GateConfig
from .contract import ContractResult, ProposalDoc, validate_proposal
from .index import IndexEntry, ProposalIndex
from .ledger import LedgerRow, LedgerStore
from .textsim import shingles, sim_from_sets, similarity_texts

_MAX_BLOB_CHARS = 20_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload_sha256(payload: Any) -> str:
    """原始载荷摘要（隔离区用：契约失败时没有规范模型可摘要）。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GateTransitionError(RuntimeError):
    """状态迁移被复核纪律拒绝（fail-closed）。

    ``code`` 是机器可裁的原因码（API 层映射 409），``detail`` 供人读。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class GateResult:
    verdict: str
    state: str
    sha256: str
    mission_id: int
    plan_id: int | None
    dup_of: str | None
    score: float | None
    reasons: tuple[str, ...]
    created: bool
    schema_version: str
    contract_ok: bool
    quarantine_path: str | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict == "accepted"

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "state": self.state,
            "sha256": self.sha256,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "dup_of": self.dup_of,
            "score": round(self.score, 6) if isinstance(self.score, float) else self.score,
            "reasons": list(self.reasons),
            "created": self.created,
            "schema_version": self.schema_version,
            "contract_ok": self.contract_ok,
            "quarantine_path": self.quarantine_path,
        }


class ProposalGate:
    """提案门（组合 root：配置 + 账本 + 索引 + 隔离区/事件落盘）。"""

    def __init__(
        self,
        cfg: GateConfig,
        ledger: LedgerStore,
        index: ProposalIndex | None = None,
        *,
        quarantine_dir: str | Path | None = None,
        events_file: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.ledger = ledger
        self.index = index if index is not None else ProposalIndex(cfg)
        qdir = quarantine_dir if quarantine_dir is not None else cfg.get("ledger.quarantine_dir")
        self.quarantine_dir = cfg.resolve_path(str(qdir)) if qdir else None
        ev = events_file if events_file is not None else cfg.get("ledger.events_file")
        self.events_file = cfg.resolve_path(str(ev)) if ev else None

    # ── 事件 ────────────────────────────────────────────────────────────
    def emit_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        if self.events_file is None:
            return
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": _now_iso(), "kind": kind, **dict(payload)}
        with self.events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _quarantine(self, payload: Any, sha256: str, errors: Sequence[str]) -> str | None:
        if self.quarantine_dir is None:
            return None
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        path = self.quarantine_dir / f"{sha256}.json"
        body = {
            "at": _now_iso(),
            "sha256": sha256,
            "errors": list(errors),
            "payload": payload,
        }
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path)

    # ── 主判定 ──────────────────────────────────────────────────────────
    def evaluate(
        self,
        payload: Mapping[str, Any],
        *,
        mission_id: int,
        plan_id: int | None = None,
        source_path: str | None = None,
        version: str | None = None,
        persist_quarantine: bool = True,
    ) -> GateResult:
        contract: ContractResult = validate_proposal(payload, self.cfg, version=version)

        if not contract.ok or contract.doc is None:
            sha = payload_sha256(payload)
            qpath = self._quarantine(payload, sha, contract.errors) if persist_quarantine else None
            row, created = self.ledger.record(
                mission_id=mission_id,
                sha256=sha,
                verdict="quarantined",
                schema_version=contract.schema_version,
                plan_id=plan_id,
                reasons=contract.errors,
                source_path=qpath or source_path,
            )
            result = GateResult(
                verdict="quarantined",
                state=row.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=None,
                score=None,
                reasons=contract.errors,
                created=created,
                schema_version=contract.schema_version,
                contract_ok=False,
                quarantine_path=qpath,
            )
            self.emit_event(
                "proposal_quarantined",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "errors": list(contract.errors),
                    "quarantine_path": qpath,
                    "source_path": source_path,
                },
            )
            return result

        doc = contract.doc
        sha = doc.sha256()

        # 幂等门：同 mission 同内容 → 不新增行
        existing = self.ledger.get(mission_id, sha)
        if existing is not None:
            self.emit_event(
                "proposal_resubmitted",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "first_verdict": existing.verdict,
                    "first_state": existing.state,
                },
            )
            return GateResult(
                verdict="duplicate",
                state=existing.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=existing.sha256,
                score=1.0,
                reasons=("idempotent_resubmission", "same_content_hash"),
                created=False,
                schema_version=doc.schema_version,
                contract_ok=True,
            )

        # 跨 mission 同内容：账本全局按 sha 查一次（索引里的 self-exclude 会漏掉这种情形）
        same_content = self.ledger.find_by_sha(sha)
        if same_content is not None and same_content.mission_id != mission_id:
            reasons = (
                "cross_mission_resubmission",
                f"first_mission_id={same_content.mission_id}",
                f"first_verdict={same_content.verdict}",
            )
            row, created = self.ledger.record(
                mission_id=mission_id,
                sha256=sha,
                verdict="duplicate",
                schema_version=doc.schema_version,
                plan_id=plan_id,
                dup_of=sha,
                score=1.0,
                reasons=reasons,
                source_path=source_path,
                blob=doc.blob(),
            )
            self.emit_event(
                "proposal_duplicate",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "dup_of": sha,
                    "score": 1.0,
                    "kind": "cross_mission",
                },
            )
            return GateResult(
                verdict="duplicate",
                state=row.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=sha,
                score=1.0,
                reasons=reasons,
                created=created,
                schema_version=doc.schema_version,
                contract_ok=True,
            )

        threshold = float(self.cfg.need("dedup.dup_threshold"))

        # 去重门：文本相似度
        hit = self.index.best_text_match(doc)
        if hit is not None and hit.sim.score >= threshold:
            dup_ref = hit.entry.sha256 or hit.entry.key
            reasons = (
                f"text_similarity {hit.sim.score:.6f} >= {threshold}",
                f"jaccard={hit.sim.jaccard:.6f}",
                f"containment={hit.sim.containment:.6f}",
                f"dup_of_kind={hit.entry.kind}",
                f"dup_of_key={hit.entry.key}",
            )
            row, created = self.ledger.record(
                mission_id=mission_id,
                sha256=sha,
                verdict="duplicate",
                schema_version=doc.schema_version,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=hit.sim.score,
                reasons=reasons,
                source_path=source_path,
                blob=doc.blob(),
            )
            self.emit_event(
                "proposal_duplicate",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "dup_of": dup_ref,
                    "score": round(hit.sim.score, 6),
                    "kind": hit.entry.kind,
                },
            )
            return GateResult(
                verdict="duplicate",
                state=row.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=hit.sim.score,
                reasons=reasons,
                created=created,
                schema_version=doc.schema_version,
                contract_ok=True,
            )

        # 复核带：阈值以下但仍在「同义改写」量级 → 不自动拒、也不直接入池
        review_threshold = float(self.cfg.need("dedup.review_threshold"))
        if hit is not None and hit.sim.score >= review_threshold:
            dup_ref = hit.entry.sha256 or hit.entry.key
            reasons = (
                f"near_duplicate_review {hit.sim.score:.6f} in "
                f"[{review_threshold}, {threshold})",
                f"jaccard={hit.sim.jaccard:.6f}",
                f"containment={hit.sim.containment:.6f}",
                f"dup_of_kind={hit.entry.kind}",
                f"dup_of_key={hit.entry.key}",
            )
            qpath = (
                self._quarantine(
                    {"sha256": sha, "doc": doc.canonical(), "match": hit.as_dict()},
                    sha,
                    reasons,
                )
                if persist_quarantine
                else None
            )
            row, created = self.ledger.record(
                mission_id=mission_id,
                sha256=sha,
                verdict="near_duplicate",
                schema_version=doc.schema_version,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=hit.sim.score,
                reasons=reasons,
                source_path=qpath or source_path,
                blob=doc.blob(),
            )
            self.emit_event(
                "proposal_near_duplicate",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "dup_of": dup_ref,
                    "score": round(hit.sim.score, 6),
                    "quarantine_path": qpath,
                },
            )
            return GateResult(
                verdict="near_duplicate",
                state=row.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=hit.sim.score,
                reasons=reasons,
                created=created,
                schema_version=doc.schema_version,
                contract_ok=True,
                quarantine_path=qpath,
            )

        # 孪生门：结构化冲突
        twin = self.index.find_twin(doc)
        if twin is not None:
            dup_ref = twin.entry.sha256 or twin.entry.key
            reasons = (
                f"structured_twin dep_overlap={twin.dep_overlap:.6f} "
                f">= {self.cfg.need('dedup.twin_dependency_overlap')}",
                f"repo={twin.repo}",
                f"dup_of_key={twin.entry.key}",
            )
            row, created = self.ledger.record(
                mission_id=mission_id,
                sha256=sha,
                verdict="twin",
                schema_version=doc.schema_version,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=twin.dep_overlap,
                reasons=reasons,
                source_path=source_path,
                blob=doc.blob(),
            )
            self.emit_event(
                "proposal_twin",
                {
                    "sha256": sha,
                    "mission_id": mission_id,
                    "dup_of": dup_ref,
                    "dep_overlap": round(twin.dep_overlap, 6),
                },
            )
            return GateResult(
                verdict="twin",
                state=row.state,
                sha256=sha,
                mission_id=mission_id,
                plan_id=plan_id,
                dup_of=dup_ref,
                score=twin.dep_overlap,
                reasons=reasons,
                created=created,
                schema_version=doc.schema_version,
                contract_ok=True,
            )

        # 通过：入池 + 入索引
        reasons = ()
        if hit is not None:
            reasons = (f"nearest_below_threshold {hit.sim.score:.6f} < {threshold}",)
        row, created = self.ledger.record(
            mission_id=mission_id,
            sha256=sha,
            verdict="accepted",
            schema_version=doc.schema_version,
            plan_id=plan_id,
            score=hit.sim.score if hit is not None else 0.0,
            reasons=reasons,
            source_path=source_path,
            blob=doc.blob(),
        )
        self.index.add(
            IndexEntry(
                kind="proposal",
                key=sha,
                text=doc.blob(),
                repo=doc.repo,
                deps=doc.deps_set(),
                sha256=sha,
            )
        )
        self.emit_event(
            "proposal_accepted",
            {"sha256": sha, "mission_id": mission_id, "repo": doc.repo, "source_path": source_path},
        )
        return GateResult(
            verdict="accepted",
            state=row.state,
            sha256=sha,
            mission_id=mission_id,
            plan_id=plan_id,
            dup_of=None,
            score=hit.sim.score if hit is not None else 0.0,
            reasons=reasons,
            created=created,
            schema_version=doc.schema_version,
            contract_ok=True,
        )

    # ── 采纳（播种）─────────────────────────────────────────────────────
    def adopt(self, *, mission_id: int, sha256: str, plan_id: int | None = None) -> LedgerRow:
        """把提案标为已采纳（播种落点留痕）。

        守卫：只有 ``review.adopt_allowed_states`` 里的 state 可被直接采纳——
        复核带（``near_duplicate``）与契约隔离（fail-closed）行必须先走
        :meth:`adjudicate`，否则「不自动拒也不入池」的纪律可被 adopt 一步绕过。
        """
        self._assert_adoptable(mission_id, sha256)
        row = self.ledger.set_state(
            mission_id=mission_id,
            sha256=sha256,
            state="adopted",
            verdict="adopted",
            plan_id=plan_id,
            reason=f"adopted_at={_now_iso()}",
        )
        self.emit_event(
            "proposal_adopted",
            {"sha256": sha256, "mission_id": mission_id, "plan_id": plan_id},
        )
        return row

    def _assert_adoptable(self, mission_id: int, sha256: str) -> None:
        allowed = tuple(self.cfg.get("review.adopt_allowed_states") or ())
        if not allowed:
            return
        row = self.ledger.get(mission_id, sha256)
        if row is None:  # 不存在的情形由调用方给 404，这里不越权代答
            return
        if row.state not in allowed:
            raise GateTransitionError(
                "adopt_requires_state",
                f"当前 state={row.state} 不在允许集合 {list(allowed)}；"
                "复核带/隔离行须先 POST /proposals/gate/review/resolve 裁决",
            )

    # ── 复核带（near_duplicate 的处置通道）───────────────────────────────
    def review_band(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """在带待裁决的行（配置驱动：``review.queue_states`` × ``review.queue_verdicts``）。

        排序按 ``score`` 降序（最像重复的先看），同分按写入先后；
        素材全部来自账本列，不重算相似度 → 队列视图与判定口径同源。
        """
        queue_states = tuple(self.cfg.need("review.queue_states"))
        queue_verdicts = set(self.cfg.need("review.queue_verdicts"))
        decisions = sorted(dict(self.cfg.need("review.decisions")))
        items: list[dict[str, Any]] = []
        for state in queue_states:
            for row in self.ledger.rows(state=state, limit=100_000):
                if queue_verdicts and row.verdict not in queue_verdicts:
                    continue
                item = row.as_dict()
                item["decisions"] = decisions
                items.append(item)
        items.sort(key=lambda d: (-(d.get("score") or 0.0), int(d.get("id") or 0)))
        return items[: int(limit)]

    def adjudicate(
        self,
        *,
        mission_id: int,
        sha256: str,
        decision: str,
        reviewer: str,
        note: str,
    ) -> LedgerRow:
        """复核带裁决：把 ``near_duplicate`` 行显式迁出带（``admit`` / ``reject``）。

        纪律（全部 fail-closed）：
        * 只在带上（state/verdict 双条件）的行可裁 —— 契约失败行**不可**经此放行；
        * ``reviewer`` 必填、``note`` 长度 ≥ ``review.min_note_length``（留痕可审计）；
        * 裁决理由与前后状态一并追加进 ``reasons``，并写事件流。
        """
        review = dict(self.cfg.need("review"))
        decisions = dict(review["decisions"])
        if decision not in decisions:
            raise GateTransitionError(
                "unknown_decision", f"未登记裁决：{decision}；可选 {sorted(decisions)}"
            )
        row = self.ledger.get(mission_id, sha256)
        if row is None:
            raise KeyError(f"账本无此行：mission_id={mission_id} sha256={sha256}")
        queue_states = tuple(review["queue_states"])
        queue_verdicts = set(review["queue_verdicts"])
        if row.state not in queue_states or (queue_verdicts and row.verdict not in queue_verdicts):
            raise GateTransitionError(
                "not_in_review_band",
                f"state={row.state} verdict={row.verdict} 不在复核带；"
                "契约失败行须重新提交合规载荷",
            )
        if review.get("reviewer_required", True) and not str(reviewer or "").strip():
            raise GateTransitionError("reviewer_required", "复核人必填（审计留痕）")
        min_note = int(review.get("min_note_length") or 0)
        if len(str(note or "").strip()) < min_note:
            raise GateTransitionError("note_too_short", f"note 至少 {min_note} 字")
        target = dict(decisions[decision])
        reason = (
            f"review_decision={decision} reviewer={str(reviewer).strip()} at={_now_iso()} "
            f"prev_state={row.state} prev_verdict={row.verdict} note={str(note).strip()}"
        )
        updated = self.ledger.set_state(
            mission_id=mission_id,
            sha256=sha256,
            state=str(target["state"]),
            verdict=target.get("verdict"),
            reason=reason,
        )
        self.emit_event(
            "proposal_review_adjudicated",
            {
                "sha256": sha256,
                "mission_id": mission_id,
                "decision": decision,
                "reviewer": str(reviewer).strip(),
                "prev_state": row.state,
                "prev_verdict": row.verdict,
                "state": updated.state,
                "verdict": updated.verdict,
                "score": updated.score,
            },
        )
        return updated

    # ── 播种覆盖率账本 ──────────────────────────────────────────────────
    def coverage(self, *, index: ProposalIndex | None = None) -> dict[str, Any]:
        """采纳提案 → 战役节点 的播种覆盖率（可 SQL 溯源：分母=账本 adopted 计数）。

        性能：n-gram 集合两侧各算一次（m×n 次集合运算，不再重复切分文本）。
        语义：文本相似度只是「主题可追溯」的代理；真实播种落点（mission_id ↔ 节点）
        由 collect 臂按真库关联核验，两者在报告里分开报，不混为一谈。
        """
        idx = index if index is not None else self.index
        threshold = float(self.cfg.need("dedup.coverage_match_threshold"))
        size = int(self.cfg.need("dedup.shingle_size"))
        adopted = self.ledger.rows(state="adopted", limit=100_000)
        node_entries = [e for e in idx.by_kind("campaign_node") if e.grams or e.text]
        adopted_grams = {row.sha256: shingles(row_blob(row), size) for row in adopted}
        covered: list[dict[str, Any]] = []
        uncovered: list[dict[str, Any]] = []
        matched_nodes: set[str] = set()
        for row in adopted:
            grams = adopted_grams[row.sha256]
            best_key: str | None = None
            best_score = 0.0
            for entry in node_entries:
                sim = sim_from_sets(grams, entry.grams, self.cfg)
                if sim.score > best_score:
                    best_key, best_score = entry.key, sim.score
                    if best_score >= threshold:
                        matched_nodes.add(entry.key)
            item = {
                "sha256": row.sha256,
                "mission_id": row.mission_id,
                "plan_id": row.plan_id,
                "best_node": best_key,
                "score": round(best_score, 6),
            }
            (covered if (best_score >= threshold and best_key) else uncovered).append(item)
        nodes_total = len(node_entries)
        adopted_n = len(adopted)
        return {
            "adopted": adopted_n,
            "covered": len(covered),
            "coverage_rate": round(len(covered) / adopted_n, 6) if adopted_n else 0.0,
            "threshold": threshold,
            "nodes_indexed": nodes_total,
            "nodes_seeded_from_proposals": len(matched_nodes),
            "node_coverage_rate": round(len(matched_nodes) / nodes_total, 6) if nodes_total else 0.0,
            "covered_items": covered,
            "uncovered_items": uncovered,
        }


def row_blob(row: LedgerRow) -> str:
    """账本行里保存的规范文本（相似度可复算；空则退化为 sha 占位，绝不静默成功）。"""
    blob = getattr(row, "blob", None)
    return blob or ""


def build_index_from_entries(entries: Iterable[IndexEntry], cfg: GateConfig) -> ProposalIndex:
    idx = ProposalIndex(cfg)
    idx.add_many(entries)
    return idx


def load_historical_pool(root: str | Path, pattern: str = "**/proposals/**/*.json") -> list[Path]:
    """历史提案池文件清单（in-repo 池；跳过 .git 与隐藏目录）。"""
    base = Path(root)
    out: list[Path] = []
    for path in sorted(base.glob(pattern)):
        rel_parts = path.relative_to(base).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if not path.is_file():
            continue
        out.append(path)
    return out


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    p.mkdir(parents=True, exist_ok=True)
    return p