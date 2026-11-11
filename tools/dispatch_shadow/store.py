"""Partitioned, append-only, hash-chained decision log store.

Storage layout (schema v1)::

    <root>/manifest.json          # counts + head digests + config digest
    <root>/config.json            # injected LogConfig (domain-free)
    <root>/records/part-<k>.jsonl # decision partition, k = decision_seq % parts
    <root>/outcomes/part-<k>.jsonl# outcome partition, k = outcome index % parts

Why two partitions: outcomes arrive *after* the fact (an outcome is only known hours
later).  Keeping them in a separate append-only file means (a) the decision
record's content address is stable the moment it is written, (b) a late label
cannot silently rewrite dispatch-time bytes, and (c) "which decisions are still
unlabelled" is a set difference, not a mutable flag.

Integrity: each record carries ``prev_record_id`` and
``chain_digest = sha256(prev_chain_digest || record_id)`` (likewise for
outcomes).  Verification recomputes both from the rows on disk, so an edited
body, a dropped row, a reordering or a re-addressed row all fail differently.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

from .canonical import content_digest, sha256_hex
from .schema import (
    DecisionRecord,
    LogConfig,
    SchemaError,
    make_outcome,
    make_record,
    validate_outcome,
    validate_record,
)

__all__ = ["DecisionLog", "ChainReport"]


@dataclass
class ChainReport:
    ok: bool
    records: int
    outcomes: int
    problems: list[dict[str, Any]]
    head_record_id: str | None
    head_outcome_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "records": self.records,
            "outcomes": self.outcomes,
            "problems": self.problems,
            "head_record_id": self.head_record_id,
            "head_outcome_id": self.head_outcome_id,
        }


def _read_jsonl(path: str) -> Iterator[dict[str, Any]]:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - corruption path
                raise SchemaError(f"{path}:{lineno} is not valid JSON: {exc}") from exc


class DecisionLog:
    """Append-only decision log with content-addressed, hash-chained rows."""

    def __init__(
        self,
        root: str,
        *,
        config: LogConfig | None = None,
        partitions: int = 4,
        log_id: str | None = None,
    ) -> None:
        self.root = root
        self.partitions = max(1, int(partitions))
        os.makedirs(os.path.join(root, "records"), exist_ok=True)
        os.makedirs(os.path.join(root, "outcomes"), exist_ok=True)
        cfg_path = os.path.join(root, "config.json")
        if config is None:
            if not os.path.exists(cfg_path):
                raise SchemaError(f"no config at {cfg_path} and none injected")
            with open(cfg_path, "r", encoding="utf-8") as fh:
                config = LogConfig.from_dict(json.load(fh))
        else:
            existing = None
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
            if existing is not None and content_digest(existing) != config.sha256:
                raise SchemaError(
                    "log directory was created with a different config; refusing to "
                    "reinterpret existing records under a new feature/outcome schema"
                )
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(config.to_dict(), fh, sort_keys=True, indent=2)
        self.config = config
        self.log_id = log_id or self._discover_log_id()
        self._head: tuple[int, str | None, str | None] | None = None
        self._outcome_head: tuple[int, str | None, str | None] | None = None

    # -- discovery ------------------------------------------------------
    def _record_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for k in range(self.partitions):
            rows.extend(_read_jsonl(os.path.join(self.root, "records", f"part-{k:04d}.jsonl")))
        rows.sort(key=lambda r: int(r["decision_seq"]))
        return rows

    def _outcome_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for k in range(self.partitions):
            rows.extend(_read_jsonl(os.path.join(self.root, "outcomes", f"part-{k:04d}.jsonl")))
        # partitions are a *storage* detail: chain order is the append order
        rows.sort(key=lambda r: int(r.get("outcome_index", -1)))
        return rows

    def _discover_log_id(self) -> str:
        rows = self._record_rows()
        return rows[0]["log_id"] if rows else "synthetic.log"

    # -- writing --------------------------------------------------------
    def _append(self, kind: str, index: int, row: Mapping[str, Any]) -> None:
        path = os.path.join(self.root, kind, f"part-{index % self.partitions:04d}.jsonl")
        line = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _next_seq_and_head(self) -> tuple[int, str | None, str | None]:
        if self._head is None:
            rows = self._record_rows()
            self._head = (
                (int(rows[-1]["decision_seq"]) + 1, rows[-1]["record_id"], rows[-1]["chain_digest"])
                if rows
                else (0, None, None)
            )
        return self._head

    def _advance_head(self, record: Mapping[str, Any]) -> None:
        self._head = (int(record["decision_seq"]) + 1, record["record_id"], record["chain_digest"])

    def append_decision(
        self,
        *,
        dispatch_ts: float,
        policy: Mapping[str, Any],
        candidates: Iterable[Mapping[str, Any]],
        scores: Mapping[str, float],
        ranking: Iterable[str],
        chosen: str,
        propensities: Mapping[str, float],
        exploration_floor: float,
        writer: str = "synthetic.writer",
        decision_seq: int | None = None,
    ) -> dict[str, Any]:
        seq, prev_id, prev_chain = self._next_seq_and_head()
        if decision_seq is not None and int(decision_seq) != seq:
            raise SchemaError(f"out of order append: expected seq {seq}, got {decision_seq}")
        record = make_record(
            config=self.config,
            log_id=self.log_id,
            decision_seq=seq,
            dispatch_ts=dispatch_ts,
            policy=policy,
            candidates=list(candidates),
            scores=scores,
            ranking=list(ranking),
            chosen=chosen,
            propensities=propensities,
            exploration_floor=exploration_floor,
            writer=writer,
        )
        record["prev_record_id"] = prev_id
        record["chain_digest"] = sha256_hex(f"{prev_chain or ''}|{record['record_id']}")
        self._append("records", seq, record)
        self._advance_head(record)
        return record

    def append_outcome(
        self,
        *,
        record_id: str,
        outcome: Mapping[str, Any],
        outcome_ts: float,
        source: str = "synthetic.labeler",
    ) -> dict[str, Any]:
        if self._outcome_head is None:
            rows = self._outcome_rows()
            if rows:
                last = rows[-1]
                self._outcome_head = (len(rows), last["outcome_id"], last["chain_digest"])
            else:
                self._outcome_head = (0, None, None)
        index, prev_id, prev_chain = self._outcome_head
        row = make_outcome(
            outcome_index=index,
            record_id=record_id,
            outcome=outcome,
            outcome_ts=outcome_ts,
            source=source,
            prev_outcome_id=prev_id,
            prev_outcome_chain=prev_chain,
            config=self.config,
        )
        self._append("outcomes", index, row)
        self._outcome_head = (index + 1, row["outcome_id"], row["chain_digest"])
        return row

    # -- reading --------------------------------------------------------
    def verify(self) -> ChainReport:
        problems: list[dict[str, Any]] = []
        rows = self._record_rows()
        prev_id: str | None = None
        prev_chain: str | None = None
        expected_seq = 0
        head_record = None
        for row in rows:
            seq = int(row.get("decision_seq", -1))
            if seq != expected_seq:
                problems.append(
                    {"code": "SEQ_GAP", "expected": expected_seq, "observed": seq}
                )
            expected_seq = seq + 1
            try:
                validate_record(row, config=self.config)
            except (SchemaError, KeyError, TypeError) as exc:
                problems.append({"code": "RECORD_INVALID", "seq": seq, "detail": str(exc)})
                prev_id = row.get("record_id")
                prev_chain = row.get("chain_digest")
                continue
            if row.get("prev_record_id") != prev_id:
                problems.append(
                    {"code": "CHAIN_BREAK", "seq": seq, "expected_prev": prev_id,
                     "observed_prev": row.get("prev_record_id")}
                )
            recomputed = sha256_hex(f"{prev_chain or ''}|{row['record_id']}")
            if recomputed != row.get("chain_digest"):
                problems.append(
                    {"code": "CHAIN_DIGEST_MISMATCH", "seq": seq,
                     "expected": recomputed, "observed": row.get("chain_digest")}
                )
            prev_id = row["record_id"]
            prev_chain = row.get("chain_digest")
            head_record = prev_id

        outcomes = self._outcome_rows()
        known_ids = {r["record_id"] for r in rows}
        prev_out: str | None = None
        prev_out_chain: str | None = None
        head_outcome = None
        for row in outcomes:
            try:
                validate_outcome(row, config=self.config)
            except (SchemaError, KeyError) as exc:
                problems.append({"code": "OUTCOME_INVALID", "detail": str(exc)})
                prev_out = row.get("outcome_id")
                prev_out_chain = row.get("chain_digest")
                continue
            if row["record_id"] not in known_ids:
                problems.append(
                    {"code": "ORPHAN_OUTCOME", "record_id": row["record_id"]}
                )
            if row.get("prev_outcome_id") != prev_out:
                problems.append(
                    {"code": "OUTCOME_CHAIN_BREAK", "record_id": row["record_id"],
                     "expected_prev": prev_out, "observed_prev": row.get("prev_outcome_id")}
                )
            recomputed = sha256_hex(f"{prev_out_chain or ''}|{row['outcome_id']}")
            if recomputed != row.get("chain_digest"):
                problems.append(
                    {"code": "OUTCOME_CHAIN_DIGEST_MISMATCH", "record_id": row["record_id"],
                     "expected": recomputed, "observed": row.get("chain_digest")}
                )
            prev_out = row["outcome_id"]
            prev_out_chain = row.get("chain_digest")
            head_outcome = prev_out
        return ChainReport(
            ok=not problems,
            records=len(rows),
            outcomes=len(outcomes),
            problems=problems,
            head_record_id=head_record,
            head_outcome_id=head_outcome,
        )

    def records(self, *, with_outcomes: bool = True) -> list[DecisionRecord]:
        rows = self._record_rows()
        outcomes: dict[str, dict[str, Any]] = {}
        if with_outcomes:
            for row in self._outcome_rows():
                outcomes[row["record_id"]] = row
        out: list[DecisionRecord] = []
        for row in rows:
            out.append(DecisionRecord(data=row, outcome=outcomes.get(row["record_id"])))
        return out

    def labelled(self) -> list[DecisionRecord]:
        return [r for r in self.records() if r.labelled()]

    def head_digest(self) -> str:
        rows = self._record_rows()
        return rows[-1]["chain_digest"] if rows else sha256_hex("")

    def manifest(self) -> dict[str, Any]:
        report = self.verify()
        return {
            "log_id": self.log_id,
            "schema_version": "dispatch-decision-log/1",
            "config_sha256": self.config.sha256,
            "partitions": self.partitions,
            "records": report.records,
            "outcomes": report.outcomes,
            "unlabelled": report.records - report.outcomes,
            "head_record_id": report.head_record_id,
            "head_outcome_id": report.head_outcome_id,
            "chain_ok": report.ok,
        }

    def write_manifest(self) -> dict[str, Any]:
        m = self.manifest()
        with open(os.path.join(self.root, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh, sort_keys=True, indent=2)
        return m