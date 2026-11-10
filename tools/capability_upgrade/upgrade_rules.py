"""Capability upgrade rules table: declarative, digest-addressed, SQL-mirrored.

Why a table (P348-W3-N01)
-------------------------
The existing re-adjudication engine (``engine.py``) maps criteria straight onto a
target level: a passing criterion of ``target_level=REAL`` moves a slot from
``WITHHELD`` to ``REAL`` in one jump.  That is enough to *state* a level but not
to *audit a ladder*: it cannot say which intermediate rung was skipped, which
single requirement blocked the move, or what a downgrade should land on.

This module adds the missing half — a **rules table** that owns, as data, the
question "what evidence does one rung of the ladder require?".  Nothing here
names a capability, a metric family or a discipline: the ladder, the requirement
keys, the operators and every threshold arrive from configuration
(``config/capability_upgrade/upgrade_rules.json``) or from the mirrored SQLite
table :data:`TABLE_NAME`.

Ladder
------
``NOT_SUPPORTED -> STUB -> MOCK -> WITHHELD -> REAL`` (rank order is
:data:`tools.capability_upgrade.model.LEVELS`).  Rung semantics used by the
shipped configuration:

==================  ==========================================================
``NOT_SUPPORTED``   no implementation
``STUB``            code path exists, nothing has run
``MOCK``            runs and responds to its input, nothing is verified
``WITHHELD``        at least one anchored verification inside tolerance
``REAL``            evidence chain complete: >= N independent anchors,
                    worst error inside tolerance, an independent review, no open
                    defect
==================  ==========================================================

Two tables are written to SQLite (same shape as the JSON document):

``capability_upgrade_rules``
    one row per requirement: ``rule_id, from_level, to_level, requirement_key,
    op, value, required, label, position`` plus the rule ``description``.
``capability_rollback_rules``
    ``defect_kind, target_level, description`` — where a defect lands.

``capability_upgrade_rules_meta`` holds the schema tag and the ladder, so a rule
set read back from SQLite can be re-digested and compared with the configured
one (drift detection).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import LEVELS, LEVEL_RANK, canonical_json, digest

RULES_SCHEMA = "capability-upgrade-rules/1.0"
RULES_TABLE = "capability_upgrade_rules"
ROLLBACK_TABLE = "capability_rollback_rules"
META_TABLE = "capability_upgrade_rules_meta"

#: operators a requirement may use; ``is_true``/``is_false`` are for boolean facts
OPS: Tuple[str, ...] = ("is_true", "is_false", ">=", "<=", "==", ">", "<")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_op(op: str) -> str:
    op = str(op)
    if op not in OPS:
        raise ValueError(f"unknown requirement operator {op!r}; allowed={OPS}")
    return op


# ---------------------------------------------------------------------------
# requirement / rule records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One machine-checkable condition on a fact of the evidence bundle."""

    key: str
    op: str
    value: Any
    required: bool = True
    label: str = ""

    def __post_init__(self) -> None:
        _require_op(self.op)

    def check(self, observed: Any) -> bool:
        """Evaluate the requirement against an observed fact value.

        A missing fact (``None``) never satisfies a requirement — "we did not
        measure it" must not read as "it passed".
        """
        if observed is None:
            return False
        op = self.op
        if op == "is_true":
            return bool(observed) is True
        if op == "is_false":
            return bool(observed) is False
        try:
            lhs = float(observed)
            rhs = float(self.value)
        except (TypeError, ValueError):
            return False
        if op == ">=":
            return lhs >= rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == "<":
            return lhs < rhs
        return lhs == rhs

    def as_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "op": self.op, "value": self.value,
                "required": bool(self.required), "label": self.label}

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "Requirement":
        return cls(key=str(doc["key"]), op=str(doc["op"]), value=doc.get("value"),
                   required=bool(doc.get("required", True)), label=str(doc.get("label", "")))


@dataclass(frozen=True)
class UpgradeRule:
    """One rung of the ladder: ``from_level -> to_level`` and its requirements."""

    rule_id: str
    from_level: str
    to_level: str
    requirements: Tuple[Requirement, ...]
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "from_level": self.from_level,
            "to_level": self.to_level,
            "description": self.description,
            "requirements": [r.as_dict() for r in self.requirements],
        }

    @property
    def digest(self) -> str:
        return digest(self.as_dict())

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "UpgradeRule":
        reqs = tuple(Requirement.from_dict(r) for r in doc.get("requirements") or [])
        if not reqs:
            raise ValueError(f"rule {doc.get('rule_id')!r} has no requirements")
        return cls(rule_id=str(doc["rule_id"]), from_level=str(doc["from_level"]).upper(),
                   to_level=str(doc["to_level"]).upper(), requirements=reqs,
                   description=str(doc.get("description", "")))


@dataclass(frozen=True)
class RollbackRule:
    """Where a defect of a given kind lands the capability (highest honest rung)."""

    defect_kind: str
    target_level: str
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"defect_kind": self.defect_kind, "target_level": self.target_level,
                "description": self.description}

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "RollbackRule":
        return cls(defect_kind=str(doc["defect_kind"]),
                   target_level=str(doc["target_level"]).upper(),
                   description=str(doc.get("description", "")))


# ---------------------------------------------------------------------------
# default table (identical to config/capability_upgrade/upgrade_rules.json)
# ---------------------------------------------------------------------------


def _default_document() -> Dict[str, Any]:
    return {
        "schema": RULES_SCHEMA,
        "ladder": list(LEVELS),
        "rules": [
            {
                "rule_id": "r.not_supported->stub",
                "from_level": "NOT_SUPPORTED",
                "to_level": "STUB",
                "description": "an implementation exists (callable code path present)",
                "requirements": [
                    {"key": "implementation_present", "op": "is_true", "value": True,
                     "label": "implementation present"},
                ],
            },
            {
                "rule_id": "r.stub->mock",
                "from_level": "STUB",
                "to_level": "MOCK",
                "description": "implementation runs, its smoke test passes and the output "
                               "responds to its input",
                "requirements": [
                    {"key": "implementation_present", "op": "is_true", "value": True,
                     "label": "implementation present"},
                    {"key": "smoke_test_passed", "op": "is_true", "value": True,
                     "label": "smoke test passed"},
                    {"key": "input_response", "op": "is_true", "value": True,
                     "label": "output responds to input"},
                ],
            },
            {
                "rule_id": "r.mock->withheld",
                "from_level": "MOCK",
                "to_level": "WITHHELD",
                "description": "at least one anchored verification inside tolerance",
                "requirements": [
                    {"key": "min_anchors", "op": ">=", "value": 1,
                     "label": "independent verification anchors"},
                    {"key": "max_error_pct", "op": "<=", "value": 15.0,
                     "label": "worst relative error [%]"},
                ],
            },
            {
                "rule_id": "r.withheld->real",
                "from_level": "WITHHELD",
                "to_level": "REAL",
                "description": "evidence chain complete: anchors + tolerance + "
                               "independent review + no open defect",
                "requirements": [
                    {"key": "min_anchors", "op": ">=", "value": 3,
                     "label": "independent verification anchors"},
                    {"key": "max_error_pct", "op": "<=", "value": 15.0,
                     "label": "worst relative error [%]"},
                    {"key": "independent_reviews", "op": ">=", "value": 1,
                     "label": "independent reviews passed"},
                    {"key": "open_defects", "op": "==", "value": 0,
                     "label": "open defects"},
                ],
            },
        ],
        "rollback_rules": [
            {"defect_kind": "implementation_absent", "target_level": "NOT_SUPPORTED",
             "description": "the code path does not exist any more"},
            {"defect_kind": "smoke_test_failed", "target_level": "STUB",
             "description": "the code path exists but no longer runs"},
            {"defect_kind": "no_response_to_input", "target_level": "MOCK",
             "description": "output does not move with its input (hardcoded value fingerprint)"},
            {"defect_kind": "error_above_threshold", "target_level": "WITHHELD",
             "description": "verification error exceeds the configured tolerance"},
            {"defect_kind": "insufficient_anchors", "target_level": "WITHHELD",
             "description": "too few independent anchors to support the level"},
            {"defect_kind": "provenance_broken", "target_level": "WITHHELD",
             "description": "evidence lost its content address and is not admissible"},
            {"defect_kind": "review_revoked", "target_level": "WITHHELD",
             "description": "the independent review was withdrawn or contradicted"},
            {"defect_kind": "evidence_regression", "target_level": "WITHHELD",
             "description": "a later evidence bundle no longer satisfies the rung"},
            {"defect_kind": "unspecified", "target_level": "WITHHELD",
             "description": "fallback: unproven until re-adjudicated"},
        ],
    }


# ---------------------------------------------------------------------------
# the table object
# ---------------------------------------------------------------------------


@dataclass
class UpgradeRuleTable:
    """The upgrade rules table: ladder + rung rules + rollback targets."""

    ladder: Tuple[str, ...]
    rules: Tuple[UpgradeRule, ...]
    rollback_rules: Tuple[RollbackRule, ...] = ()
    schema: str = RULES_SCHEMA
    source: str = "default"

    # -- construction ------------------------------------------------------
    @classmethod
    def default(cls) -> "UpgradeRuleTable":
        return cls.from_dict(_default_document(), source="builtin")

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any], source: str = "dict") -> "UpgradeRuleTable":
        schema = str(doc.get("schema", RULES_SCHEMA))
        if schema != RULES_SCHEMA:
            raise ValueError(f"unsupported rules schema {schema!r} (expected {RULES_SCHEMA!r})")
        ladder = tuple(str(x).upper() for x in doc.get("ladder") or LEVELS)
        rules = tuple(UpgradeRule.from_dict(r) for r in doc.get("rules") or [])
        rollbacks = tuple(RollbackRule.from_dict(r) for r in doc.get("rollback_rules") or [])
        table = cls(ladder=ladder, rules=rules, rollback_rules=rollbacks, schema=schema,
                    source=source)
        problems = table.validate()
        if problems:
            raise ValueError("invalid rules table: " + "; ".join(problems))
        return table

    @classmethod
    def from_json(cls, path: Path | str) -> "UpgradeRuleTable":
        path = Path(path)
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")), source=str(path))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "ladder": list(self.ladder),
            "rules": [r.as_dict() for r in self.rules],
            "rollback_rules": [r.as_dict() for r in self.rollback_rules],
        }

    @property
    def digest(self) -> str:
        """Content address of the whole table (thresholds included)."""
        return digest(self.to_dict())

    # -- validation --------------------------------------------------------
    def validate(self) -> List[str]:
        problems: List[str] = []
        unknown = [lv for lv in self.ladder if lv not in LEVEL_RANK]
        if unknown:
            problems.append(f"unknown level(s) in ladder: {unknown}")
        if len(set(self.ladder)) != len(self.ladder):
            problems.append("ladder contains duplicates")
        seen: Dict[Tuple[str, str], str] = {}
        for rule in self.rules:
            if rule.from_level not in self.ladder or rule.to_level not in self.ladder:
                problems.append(f"{rule.rule_id}: level outside the ladder")
                continue
            if LEVEL_RANK[rule.to_level] != LEVEL_RANK[rule.from_level] + 1:
                problems.append(f"{rule.rule_id}: {rule.from_level}->{rule.to_level} is not "
                                "a single-rung move")
            key = (rule.from_level, rule.to_level)
            if key in seen:
                problems.append(f"duplicate rule for {key[0]}->{key[1]}")
            seen[key] = rule.rule_id
            if not rule.requirements:
                problems.append(f"{rule.rule_id}: no requirements")
        for rung in self.ladder[:-1]:
            if (rung, self.next_level(rung)) not in seen:  # type: ignore[arg-type]
                problems.append(f"missing rule for the {rung} rung")
        for rb in self.rollback_rules:
            if rb.target_level not in self.ladder:
                problems.append(f"rollback rule {rb.defect_kind!r} targets unknown level")
        return problems

    # -- queries -----------------------------------------------------------
    def next_level(self, level: str) -> Optional[str]:
        level = str(level).upper()
        if level not in self.ladder:
            return None
        idx = self.ladder.index(level)
        return self.ladder[idx + 1] if idx + 1 < len(self.ladder) else None

    def rule_between(self, from_level: str, to_level: str) -> Optional[UpgradeRule]:
        for rule in self.rules:
            if rule.from_level == from_level.upper() and rule.to_level == to_level.upper():
                return rule
        return None

    def outgoing(self, level: str) -> Optional[UpgradeRule]:
        nxt = self.next_level(level)
        return self.rule_between(level, nxt) if nxt else None

    def incoming(self, level: str) -> Optional[UpgradeRule]:
        """The rule that a capability currently at ``level`` had to satisfy."""
        level = str(level).upper()
        if level not in self.ladder:
            return None
        idx = self.ladder.index(level)
        if idx == 0:
            return None
        return self.rule_between(self.ladder[idx - 1], level)

    def rollback_target(self, defect_kind: str) -> Optional[str]:
        for rule in self.rollback_rules:
            if rule.defect_kind == defect_kind:
                return rule.target_level
        for rule in self.rollback_rules:
            if rule.defect_kind == "unspecified":
                return rule.target_level
        return None

    def rule(self, rule_id: str) -> Optional[UpgradeRule]:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    # -- SQLite mirror -----------------------------------------------------
    def ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {RULES_TABLE} (
                rule_id         TEXT NOT NULL,
                from_level      TEXT NOT NULL,
                to_level        TEXT NOT NULL,
                rule_description TEXT NOT NULL DEFAULT '',
                position        INTEGER NOT NULL,
                requirement_key TEXT NOT NULL,
                op              TEXT NOT NULL,
                value_json      TEXT NOT NULL,
                required        INTEGER NOT NULL DEFAULT 1,
                label           TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (rule_id, position)
            );
            CREATE TABLE IF NOT EXISTS {ROLLBACK_TABLE} (
                defect_kind     TEXT PRIMARY KEY,
                target_level    TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS {META_TABLE} (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def to_sqlite(self, conn: sqlite3.Connection, replace: bool = True) -> Dict[str, Any]:
        """Mirror the table into SQLite; returns the row counts written."""
        self.ensure_tables(conn)
        with conn:
            if replace:
                conn.execute(f"DELETE FROM {RULES_TABLE}")
                conn.execute(f"DELETE FROM {ROLLBACK_TABLE}")
            rows = 0
            for rule in self.rules:
                for pos, req in enumerate(rule.requirements):
                    conn.execute(
                        f"INSERT OR REPLACE INTO {RULES_TABLE} (rule_id, from_level, to_level,"
                        " rule_description, position, requirement_key, op, value_json, required,"
                        " label) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (rule.rule_id, rule.from_level, rule.to_level, rule.description, pos,
                         req.key, req.op, canonical_json(req.value), int(req.required), req.label),
                    )
                    rows += 1
            for rb in self.rollback_rules:
                conn.execute(
                    f"INSERT OR REPLACE INTO {ROLLBACK_TABLE} (defect_kind, target_level,"
                    " description) VALUES (?,?,?)",
                    (rb.defect_kind, rb.target_level, rb.description),
                )
            conn.execute(f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES ('schema', ?)",
                         (self.schema,))
            conn.execute(f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES ('ladder', ?)",
                         (canonical_json(list(self.ladder)),))
            conn.execute(f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES ('digest', ?)",
                         (self.digest,))
        return {"rule_rows": rows, "n_rules": len(self.rules),
                "n_rollback_rules": len(self.rollback_rules), "table": RULES_TABLE,
                "digest": self.digest}

    @classmethod
    def from_sqlite(cls, conn: sqlite3.Connection) -> "UpgradeRuleTable":
        """Read the table back out of SQLite (round-trip / drift check)."""
        meta = {row[0]: row[1] for row in conn.execute(f"SELECT key, value FROM {META_TABLE}")}
        if "ladder" not in meta:
            raise KeyError(f"{META_TABLE} has no ladder; run to_sqlite() first")
        grouped: Dict[str, Dict[str, Any]] = {}
        for (rule_id, from_level, to_level, description, pos, key, op, value_json,
             required, label) in conn.execute(
                f"SELECT rule_id, from_level, to_level, rule_description, position,"
                f" requirement_key, op, value_json, required, label FROM {RULES_TABLE}"
                " ORDER BY rule_id, position"
        ):
            entry = grouped.setdefault(rule_id, {
                "rule_id": rule_id, "from_level": from_level, "to_level": to_level,
                "description": description, "requirements": [],
            })
            entry["requirements"].append({"key": key, "op": op,
                                          "value": json.loads(value_json),
                                          "required": bool(required), "label": label})
        rollbacks = [
            {"defect_kind": kind, "target_level": target, "description": desc}
            for kind, target, desc in conn.execute(
                f"SELECT defect_kind, target_level, description FROM {ROLLBACK_TABLE}"
            )
        ]
        ladder = json.loads(meta["ladder"])
        ordered = sorted(grouped.values(),
                         key=lambda r: LEVEL_RANK.get(r["from_level"], 99))
        doc = {
            "schema": meta.get("schema", RULES_SCHEMA),
            "ladder": ladder,
            "rules": ordered,
            "rollback_rules": rollbacks,
        }
        table = cls.from_dict(doc, source="sqlite")
        table.sqlite_digest = meta.get("digest")  # type: ignore[attr-defined]
        return table


def open_sqlite(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the SQLite database that mirrors the rules table."""
    conn = sqlite3.connect(":memory:" if path is None else str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


__all__ = [
    "META_TABLE",
    "OPS",
    "ROLLBACK_TABLE",
    "RULES_SCHEMA",
    "RULES_TABLE",
    "Requirement",
    "RollbackRule",
    "UpgradeRule",
    "UpgradeRuleTable",
    "open_sqlite",
]