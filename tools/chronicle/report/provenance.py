#!/usr/bin/env python3
"""Number/figure provenance markers for chronicle reports (IN18-T3).

A manuscript produced on this line never contains a bare number: every figure
and table value inside an enforced block carries an inline provenance token

    ... 覆盖率 85.7 % {{prov:n-007}}

and each token resolves to a record in ``provenance.json``:

    {"pid": "n-007", "kind": "number", "value": 85.71, "decimals": 1,
     "locator": {"section": "Results", "table": "table1", "row": "..."},
     "source": {"type": "sql", "sql": "SELECT ...", "params": [],
                "snapshot_hash": "<sha256 of report.sqlite>",
                "snapshot_ts": "2026-..."}}

Why a token and not a footnote: the token is *in the same paragraph as the
digit*, so a later edit that rewrites the digit without re-running the query
leaves a token whose printed value no longer equals the recorded value. That is
exactly the W2 failure mode (a reviewer could not resolve a cited report, and a
dashboard number had drifted from its table), and it is machine-detectable.

Enforcement region
------------------
Only numbers inside ``<!-- prov:begin -->`` ... ``<!-- prov:end -->`` blocks are
required to be marked. Narrative sections (Introduction, Limitations...) discuss
numbers qualitatively and would otherwise generate noise; claiming full-document
coverage we do not have would be the kind of overclaim this line is built to
reject. The verifier reports the enforced-block coverage explicitly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

PROV_FILE = "provenance.json"
BLOCK_BEGIN = "<!-- prov:begin -->"
BLOCK_END = "<!-- prov:end -->"
TOKEN_FMT = "{{prov:%s}}"
TOKEN_RE = re.compile(r"(?P<num>[-+]?\d+(?:\.\d+)?)\s*\{\{prov:(?P<pid>[A-Za-z0-9_.:-]+)\}\}")
TOKEN_ONLY_RE = re.compile(r"\{\{prov:(?P<pid>[A-Za-z0-9_.:-]+)\}\}")
# a numeric literal that a reader would read as a result (excludes list markers,
# heading hashes, html comments, inline code and already-tokenised numbers)
# structural numerals are identifiers, not measurements: "表 1", "图 1–3",
# "Fig. 2", "Section 4". Exempting them is a declared scope decision (see the
# verifier's UNMARKED_NUMBER contract), not a hole: an exempted numeral can
# never stand where a measured value belongs, because the exemption only fires
# when the numeral is glued to a label word.
LABEL_NUM_RE = re.compile(
    r"(?:表|图|第|式|附录|章节|Figure|Fig\.|Table|Section|§)\s*\d+(?:\s*[\u2013\u2014\-]\s*\d+)?"
)
BARE_NUM_RE = re.compile(r"(?<![\w.{/-])(?P<num>[-+]?\d+(?:\.\d+)?)(?![\w.}%/-])")


def render_number(value: float, pid: str, decimals: int | None = None) -> str:
    """Format a number and attach its provenance token. The only sanctioned way
    to put a measured value into a manuscript."""
    if decimals is None:
        text = f"{value:g}"
    else:
        text = f"{value:.{decimals}f}"
    return f"{text} {TOKEN_FMT % pid}"


def render_figure_token(pid: str) -> str:
    return TOKEN_FMT % pid


@dataclass
class Record:
    pid: str
    kind: str  # "number" | "figure"
    source: dict
    value: float | None = None
    decimals: int | None = None
    locator: dict = field(default_factory=dict)
    tolerance: dict = field(default_factory=dict)
    caption: str = ""
    artifacts: list = field(default_factory=list)  # [{"path":..., "sha256":...}]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def allowed_abs_error(self) -> float:
        """Printed-value budget: half a unit in the last printed digit, so a
        legitimately rounded display is not punished, while a changed digit is."""
        rtol = float(self.tolerance.get("rtol", 0.0))
        atol = float(self.tolerance.get("atol", 0.0))
        base = abs(float(self.value or 0.0)) * rtol + atol
        if self.decimals is not None:
            base = max(base, 0.5 * 10 ** (-self.decimals))
        return base

    def matches(self, other: float) -> bool:
        if self.value is None:
            return False
        return abs(float(other) - float(self.value)) <= self.allowed_abs_error + 1e-12


class ProvenanceStore:
    """Collects provenance records while a report is generated."""

    def __init__(self) -> None:
        self.records: dict[str, Record] = {}
        self._seq = 0

    # -- creation ----------------------------------------------------------
    def _next(self, prefix: str) -> str:
        self._seq += 1
        pid = f"{prefix}-{self._seq:03d}"
        while pid in self.records:  # pragma: no cover - defensive
            self._seq += 1
            pid = f"{prefix}-{self._seq:03d}"
        return pid

    def add_number(
        self,
        value: float,
        sql: str,
        params: Sequence[Any],
        snapshot_hash: str,
        snapshot_ts: str,
        decimals: int | None = None,
        locator: dict | None = None,
        tolerance: dict | None = None,
        db: str = "report.sqlite",
    ) -> str:
        pid = self._next("n")
        self.records[pid] = Record(
            pid=pid,
            kind="number",
            value=float(value),
            decimals=decimals,
            locator=dict(locator or {}),
            tolerance=dict(tolerance or {}),
            source={
                "type": "sql",
                "sql": sql.strip(),
                "params": list(params),
                "snapshot_hash": snapshot_hash,
                "snapshot_ts": snapshot_ts,
                "db": db,
            },
        )
        return pid

    def add_figure(
        self,
        caption: str,
        sql: list[str],
        params: list[Sequence[Any]],
        snapshot_hash: str,
        snapshot_ts: str,
        artifacts: Iterable[tuple[str, str]],
        locator: dict | None = None,
    ) -> str:
        pid = self._next("fig")
        self.records[pid] = Record(
            pid=pid,
            kind="figure",
            caption=caption,
            locator=dict(locator or {}),
            artifacts=[{"path": str(p), "sha256": str(h)} for p, h in artifacts],
            source={
                "type": "sql",
                "sql": list(sql),
                "params": [list(p) for p in params],
                "snapshot_hash": snapshot_hash,
                "snapshot_ts": snapshot_ts,
                "db": "report.sqlite",
            },
        )
        return pid

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema_version": "chronicle-provenance/1",
            "token_format": TOKEN_FMT,
            "enforcement_block": [BLOCK_BEGIN, BLOCK_END],
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records.values()],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "ProvenanceStore":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        store = cls()
        for raw in payload.get("records", []):
            rec = Record(**raw)
            store.records[rec.pid] = rec
        return store


# ---------------------------------------------------------------------------
# manuscript helpers
# ---------------------------------------------------------------------------
def enforced_blocks(markdown: str) -> list[str]:
    """Return the text of every prov:begin..prov:end block, in order. Unterminated
    blocks are returned to the end of the document and reported by the verifier."""
    out: list[str] = []
    pos = 0
    while True:
        start = markdown.find(BLOCK_BEGIN, pos)
        if start < 0:
            return out
        body_start = start + len(BLOCK_BEGIN)
        end = markdown.find(BLOCK_END, body_start)
        if end < 0:
            out.append(markdown[body_start:])
            return out
        out.append(markdown[body_start:end])
        pos = end + len(BLOCK_END)


def extract_tokens(markdown: str) -> list[dict]:
    """Every number+token pair in the document, with a line number for review."""
    found: list[dict] = []
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        for m in TOKEN_RE.finditer(line):
            found.append(
                {"pid": m.group("pid"), "printed": float(m.group("num")), "lineno": lineno, "line": line.strip()}
            )
    return found


def extract_figure_tokens(markdown: str) -> list[dict]:
    found: list[dict] = []
    for lineno, line in enumerate(markdown.splitlines(), start=1):
        if "](" not in line:  # only image lines carry figure tokens
            continue
        for m in TOKEN_ONLY_RE.finditer(line):
            found.append({"pid": m.group("pid"), "lineno": lineno, "line": line.strip()})
    return found


def bare_numbers(block_text: str) -> list[dict]:
    """Numeric literals inside an enforced block that carry no token."""
    stripped = TOKEN_RE.sub(" ", block_text)
    stripped = LABEL_NUM_RE.sub(" ", stripped)
    stripped = re.sub(r"<!--.*?-->", " ", stripped, flags=re.S)
    stripped = re.sub(r"`[^`]*`", " ", stripped)  # inline code (dates, paths)
    out: list[dict] = []
    for lineno, line in enumerate(stripped.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith(("|--", "```")):
            continue
        for m in BARE_NUM_RE.finditer(line):
            out.append({"literal": m.group("num"), "lineno": lineno, "line": line.strip()[:160]})
    return out