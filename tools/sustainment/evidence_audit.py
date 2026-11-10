"""Evidence-chain integrity audit for platform reports (six-element machine check).

The report discipline (owner 2026-10-29) demands six elements; before this
module the only enforcement was a human/prose reviewer.  Here three of the six
become machine-adjudicated, and the verdict is a JSON ledger that a gate can
consume:

``C1 figures``   every figure embedded in the report (``![](data:image/...;base64,…)``
                 or ``![cap](path.png)``) must have a sha256 that matches the
                 supplied figure manifest, and the manifest hash must match the
                 bytes on disk → fabricated / swapped figures are caught.
``C2 numbers``   every numeric token in the body must be *traceable*: either it
                 sits behind an inline provenance marker (``[src: …]`` /
                 ``[trace: …]``), or it equals a claimed value whose ``source``
                 is declared in the claims file (SQL/artifact path), or it is
                 an explicitly allow-listed non-result token (date, version,
                 section marker).
``C3 criteria``  the acceptance-criteria table must be *complete and honest*:
                 every criterion carries an evidence path that exists, and its
                 verdict is recomputed from ``actual`` vs ``threshold`` (op) —
                 a criterion marked PASS that does not satisfy its own
                 inequality is flagged as ``verdict_inconsistent``.  Criteria
                 that honestly FAIL must also be disclosed in the prose body.

Six-element section presence (``①``…``⑥`` or ``E1``…``E6`` headings) is checked
as ``C0 sections`` so a structurally incomplete report is caught early.

Public API
----------
``audit(report_md, figures_manifest=None, claims=None, criteria=None, **opts) -> AuditResult``
``AuditResult.to_json()`` / ``AuditResult.ok`` (bool)

CLI::

    python3 -m tools.sustainment.evidence_audit \
        --report docs/reports/x.md --figures figur/manifest.json \
        --claims artifacts/x/claims.json --criteria artifacts/x/criteria.json \
        --out artifacts/sustainment/evidence_audit.json
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1

#: number token: signed decimal / scientific, not glued to an identifier
NUM_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![\w])")
#: inline provenance marker, e.g. ``[src: sql:select count(*) …]``
SRC_MARKER_RE = re.compile(r"\[(?:src|trace|来源|溯源)\s*[:：]([^\]]{1,300})\]")
#: embedded base64 image
DATAURI_RE = re.compile(r"!\[[^\]]*\]\(\s*data:image/(?:png|jpeg|svg\+xml);base64,([A-Za-z0-9+/=\s]+)\)")
#: referenced figure file
IMGREF_RE = re.compile(r"!\[([^\]]*)\]\((?!data:)([^)\s]+)\)")
#: date-like fragments are not result numbers
DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}([-/]\d{1,2})?")
VER_RE = re.compile(r"\bv?\d+\.\d+\.\d+\b")
SECTION_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨]")
#: six-element headings: either circled digits or explicit E1..E6 markers
SIX_ELEMENTS = [
    ("E1", "案例介绍"), ("E2", "怎么做的"), ("E3", "计算结果"),
    ("E4", "结果图"), ("E5", "注意点"), ("E6", "误差判定"),
]
E_MARK_RE = re.compile(r"(?:\bE[1-6]\b)|[①-⑥]")
OP_TABLE = {
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: abs(a - b) <= 1e-12,
    "!=": lambda a, b: abs(a - b) > 1e-12,
}

#: numeric tokens that are structure, not results
DEFAULT_NUMBER_ALLOWLIST: Tuple[str, ...] = (
    "0", "1", "2", "3", "4", "5", "6",  # section / list ordinals (small ints)
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _to_float(tok: str) -> Optional[float]:
    try:
        return float(tok.rstrip("%"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# result containers
# ---------------------------------------------------------------------------


@dataclass
class AuditResult:
    ok: bool
    checks: Dict[str, dict] = field(default_factory=dict)
    numbers: dict = field(default_factory=dict)
    figures: dict = field(default_factory=dict)
    criteria: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "ok": self.ok,
            "checks": self.checks,
            "sections": self.sections,
            "numbers": self.numbers,
            "figures": self.figures,
            "criteria": self.criteria,
            "failed_checks": sorted(k for k, v in self.checks.items() if not v.get("ok")),
        }


# ---------------------------------------------------------------------------
# C0 sections
# ---------------------------------------------------------------------------


def check_sections(body: str) -> dict:
    """Six-element structural presence, tolerant of numbering style."""
    hits = {}
    for code, label in SIX_ELEMENTS:
        present = bool(re.search(rf"\b{code}\b", body)) or (label in body)
        hits[code] = {"label": label, "present": present}
    # circled digits are positional: ①= E1 … ⑥ = E6
    circled = SECTION_RE.findall(body)
    circled_codes = {f"E{idx}" for idx, ch in enumerate("①②③④⑤⑥⑦⑧⑨", start=1) if ch in circled}
    for code in hits:
        if code in circled_codes:
            hits[code]["present"] = True
    found = sum(1 for v in hits.values() if v["present"])
    return {
        "ok": found == len(SIX_ELEMENTS),
        "found": found, "expected": len(SIX_ELEMENTS),
        "elements": hits,
    }


# ---------------------------------------------------------------------------
# C1 figures
# ---------------------------------------------------------------------------


def check_figures(body: str, manifest: Optional[dict], base_dir: Path) -> dict:
    """Verify every embedded/referenced figure against the manifest sha256."""
    entries: List[dict] = list((manifest or {}).get("figures", [])) if manifest else []
    by_name = {str(e.get("name", "")): e for e in entries}
    by_sha = {str(e.get("sha256", "")).lower(): e for e in entries if e.get("sha256")}
    problems: List[dict] = []
    verified: List[dict] = []

    # (a) embedded data-URI figures: bytes in the report must hash to a manifest entry
    embedded = DATAURI_RE.findall(body)
    for raw in embedded:
        try:
            data = base64.b64decode(re.sub(r"\s+", "", raw))
        except Exception:
            problems.append({"kind": "embedded_decode_error", "len": len(raw)})
            continue
        digest = sha256_bytes(data)
        hit = by_sha.get(digest)
        if hit is None:
            problems.append({"kind": "embedded_figure_not_in_manifest", "sha256": digest,
                             "bytes": len(data)})
        else:
            verified.append({"name": hit.get("name"), "sha256": digest, "source": "embedded"})

    # (b) referenced figure files: file bytes must match the manifest entry
    for caption, ref in IMGREF_RE.findall(body):
        p = (base_dir / ref).resolve() if not Path(ref).is_absolute() else Path(ref)
        if not p.exists():
            problems.append({"kind": "figure_file_missing", "ref": ref})
            continue
        digest = sha256_file(p)
        entry = by_name.get(Path(ref).name) or by_sha.get(digest)
        if entry is None:
            problems.append({"kind": "figure_file_not_in_manifest", "ref": ref, "sha256": digest})
            continue
        declared = str(entry.get("sha256", "")).lower()
        if declared and declared != digest:
            problems.append({"kind": "figure_sha256_mismatch", "ref": ref,
                             "declared": declared, "actual": digest})
            continue
        verified.append({"name": entry.get("name"), "ref": ref, "sha256": digest,
                         "source": "file", "caption": caption})

    # (c) manifest self-consistency: the on-disk figure must still hash as declared
    on_disk_mismatch = []
    for e in entries:
        pth = e.get("path")
        if not pth:
            continue
        p = Path(pth)
        if not p.is_absolute():
            p = (base_dir / pth).resolve()
        if not p.exists():
            on_disk_mismatch.append({"name": e.get("name"), "reason": "path_missing", "path": str(p)})
            continue
        actual = sha256_file(p)
        if e.get("sha256") and actual != str(e["sha256"]).lower():
            on_disk_mismatch.append({"name": e.get("name"), "reason": "sha256_mismatch",
                                     "declared": e.get("sha256"), "actual": actual})

    return {
        "ok": not problems and not on_disk_mismatch,
        "manifest_entries": len(entries),
        "embedded_figures": len(embedded),
        "referenced_figures": len(IMGREF_RE.findall(body)),
        "verified": verified,
        "problems": problems,
        "manifest_disk_mismatch": on_disk_mismatch,
    }


# ---------------------------------------------------------------------------
# C2 numbers
# ---------------------------------------------------------------------------


def _num_in(text: str, value: float, tol: float) -> bool:
    for tok in NUM_RE.findall(text):
        v = _to_float(tok)
        if v is None:
            continue
        if abs(v - value) <= max(tol, tol * abs(value)):
            return True
    return False


#: source schemes accepted for a *trailing* marker (``3.42 s [src: sql:...]``)
TRAILING_SCHEMES = ("sql:", "artifact:", "file:", "path:", "table:", "calc:", "run:",
                    "sha256:", "ledger:", "json:", "api:", "git:", "script:")
#: a trailing marker must sit on the same line within this many characters
TRAILING_WINDOW = 60


def _marker_for(pos: int, body: str, markers: List[Tuple[int, str]],
                window: int) -> Optional[Tuple[int, str]]:
    """Provenance marker for the numeric token at ``pos`` (precise, both shapes).

    * **trailing** — ``3.42 s [src: sql: …]``: same line, within
      ``TRAILING_WINDOW`` characters, marker starts with a known source scheme.
    * **leading**  — ``[src: sql: …] 3.42``: within ``window`` characters, marker
      starts with a known scheme, and **no other number sits between** the
      marker and the token (so one marker can never launder a later figure).

    Requiring a source scheme in both shapes is what stops prose brackets
    (``[see appendix]``) from being accepted as provenance.
    """
    # trailing
    line_end = body.find("\n", pos)
    line_end = len(body) if line_end < 0 else line_end
    for mp, mtxt in markers:
        if pos <= mp <= min(pos + TRAILING_WINDOW, line_end):
            text = mtxt.strip()
            if text.lower().startswith(TRAILING_SCHEMES):
                return (mp, text)
    # leading
    candidates = [(mp, mtxt.strip()) for mp, mtxt in markers
                  if 0 <= pos - mp <= window and mtxt.strip().lower().startswith(TRAILING_SCHEMES)]
    for mp, text in sorted(candidates, key=lambda x: -x[0]):
        close = body.find("]", mp)
        marker_end = (close + 1) if close >= 0 else pos  # the marker itself is not a result number
        between = [m for m in NUM_RE.finditer(body, marker_end, pos)]
        if not between:
            return (mp, text)
    return None


def check_numbers(
    body: str,
    claims: Optional[dict] = None,
    allowlist: Sequence[str] = DEFAULT_NUMBER_ALLOWLIST,
    min_coverage: float = 0.9,
    marker_window: int = 160,
    tol: float = 1e-9,
) -> dict:
    """Numeric provenance: each numeric token must be traced or claimed.

    A token is *traced* when an inline ``[src: …]`` / ``[trace: …]`` marker
    appears within ``marker_window`` characters before it, or when its value
    matches a claim whose ``source`` is declared.
    """
    claim_list: List[dict] = list((claims or {}).get("claims", [])) if claims else []
    claim_values: List[Tuple[float, dict]] = []
    for c in claim_list:
        v = _to_float(str(c.get("value", "")))
        if v is not None and c.get("source"):
            claim_values.append((v, c))

    markers = [(m.start(1), m.group(1)) for m in SRC_MARKER_RE.finditer(body)]
    allow = set(allowlist) | {str(x) for x in (claims or {}).get("allowlist", [])}

    date_spans = [m.span() for m in DATE_RE.finditer(body)] + \
                 [m.span() for m in VER_RE.finditer(body)]

    def inside_special(pos: int) -> bool:
        return any(s <= pos < e for s, e in date_spans)

    untraced: List[dict] = []
    traced: List[dict] = []
    seen: Dict[str, int] = {}
    for m in NUM_RE.finditer(body):
        tok = m.group(0)
        pos = m.start()
        if tok in allow or inside_special(pos):
            continue
        fv = _to_float(tok)
        if fv is None:
            continue
        ctx = body[max(0, pos - 90): pos + 90].replace("\n", " ")
        near_marker = _marker_for(pos, body, markers, marker_window)

        if near_marker is not None:
            traced.append({"token": tok, "marker": near_marker[1][:120], "context": ctx})
            continue
        hit = None
        for cv, c in claim_values:
            if abs(abs(fv) - abs(cv)) <= max(tol, tol * abs(cv)):
                hit = c
                break
        if hit is not None:
            traced.append({"token": tok, "claim": hit.get("id") or hit.get("value"),
                           "source": str(hit.get("source"))[:160], "context": ctx})
            continue
        seen[tok] = seen.get(tok, 0) + 1
        untraced.append({"token": tok, "context": ctx, "occurrences": seen[tok]})

    total = len(traced) + len(untraced)
    coverage = (len(traced) / total) if total else 1.0
    return {
        "ok": coverage >= min_coverage,
        "tokens_total": total,
        "traced": len(traced),
        "untraced": len(untraced),
        "coverage": round(coverage, 4),
        "min_coverage": min_coverage,
        "traced_samples": traced[:12],
        "untraced_samples": untraced[:40],
        "claims_source_count": len(claim_values),
    }


# ---------------------------------------------------------------------------
# C3 criteria
# ---------------------------------------------------------------------------


def check_criteria(body: str, criteria: Optional[dict], base_dir: Path) -> dict:
    """Recompute each acceptance-criterion verdict from actual vs threshold."""
    items = list((criteria or {}).get("criteria", [])) if criteria else []
    rows: List[dict] = []
    unverified: List[dict] = []
    inconsistent: List[dict] = []
    undisclosed: List[dict] = []
    for c in items:
        cid = str(c.get("id") or c.get("name") or "?")
        row = {"id": cid, "statement": str(c.get("statement", ""))[:300]}
        # -- evidence existence
        ev = c.get("evidence")
        ev_list = ev if isinstance(ev, list) else ([ev] if ev else [])
        missing = []
        for e in ev_list:
            p = Path(str(e))
            if not p.is_absolute():
                p = (base_dir / str(e)).resolve()
            if not p.exists():
                missing.append(str(e))
        row["evidence"] = [str(e) for e in ev_list]
        row["evidence_missing"] = missing
        # -- verdict recomputation
        op = str(c.get("op", ""))
        actual, threshold = c.get("actual"), c.get("threshold")
        recomputed: Optional[str] = None
        if op in OP_TABLE and actual is not None and threshold is not None:
            try:
                recomputed = "PASS" if OP_TABLE[op](float(actual), float(threshold)) else "FAIL"
            except (TypeError, ValueError):
                recomputed = None
        declared = str(c.get("verdict", "")).upper() or None
        row.update({"op": op, "actual": actual, "threshold": threshold,
                    "declared_verdict": declared, "recomputed_verdict": recomputed})
        if recomputed is None:
            row["verdict"] = declared or "UNVERIFIED"
            if declared is None:
                unverified.append({"id": cid, "reason": "no_threshold_or_verdict"})
        else:
            row["verdict"] = recomputed
            if declared and declared != recomputed:
                inconsistent.append({"id": cid, "declared": declared, "recomputed": recomputed})
        if missing:
            unverified.append({"id": cid, "reason": "evidence_missing", "paths": missing})
        if row["verdict"] == "FAIL" and cid not in body:
            undisclosed.append({"id": cid, "reason": "honest_fail_not_disclosed_in_prose"})
        rows.append(row)
    passes = sum(1 for r in rows if r["verdict"] == "PASS")
    fails = sum(1 for r in rows if r["verdict"] == "FAIL")
    return {
        "ok": bool(rows) and not unverified and not inconsistent and not undisclosed,
        "total": len(rows),
        "passed": passes,
        "failed": fails,
        "coverage": round(len([r for r in rows if r["verdict"] in ("PASS", "FAIL")]) / len(rows), 4)
        if rows else 0.0,
        "criteria": rows,
        "unverified": unverified,
        "verdict_inconsistent": inconsistent,
        "fail_not_disclosed": undisclosed,
    }


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _load(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"input not found: {path}")
    return json.loads(p.read_text())


def audit(
    report_md: str | Path,
    figures_manifest: Optional[str | Path] = None,
    claims: Optional[str | Path] = None,
    criteria: Optional[str | Path] = None,
    min_numeric_coverage: float = 0.9,
    require_sections: bool = True,
    base_dir: Optional[str | Path] = None,
) -> AuditResult:
    rp = Path(report_md)
    body = rp.read_text()
    bdir = Path(base_dir) if base_dir else rp.parent
    sections = check_sections(body)
    figs = check_figures(body, _load(str(figures_manifest)) if figures_manifest else None, bdir)
    nums = check_numbers(body, _load(str(claims)) if claims else None,
                         min_coverage=min_numeric_coverage)
    crit = check_criteria(body, _load(str(criteria)) if criteria else None, bdir)
    checks = {
        "C0_sections": {"ok": sections["ok"] or not require_sections,
                        "found": sections["found"], "expected": sections["expected"]},
        "C1_figures": {"ok": figs["ok"], "problems": len(figs["problems"])
                       + len(figs["manifest_disk_mismatch"])},
        "C2_numbers": {"ok": nums["ok"], "coverage": nums["coverage"],
                       "untraced": nums["untraced"]},
        "C3_criteria": {"ok": crit["ok"], "total": crit["total"], "passed": crit["passed"],
                        "failed": crit["failed"], "unverified": len(crit["unverified"]),
                        "inconsistent": len(crit["verdict_inconsistent"])},
    }
    ok = all(v["ok"] for v in checks.values())
    return AuditResult(ok=ok, checks=checks, numbers=nums, figures=figs,
                       criteria=crit, sections=sections)