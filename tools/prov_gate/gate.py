#!/usr/bin/env python3
"""Number-level provenance gate for reports (P251-W2-N04).

The question this gate answers is narrow and checkable: *can every result
number in this report be re-computed from a recorded source?*  It answers it
with four independent layers, each able to say NO on its own:

L1 render fidelity    the published bytes must equal the canonical render of
                      the template against the sidecar (``RENDER_DRIFT``), and
                      if a render manifest is supplied its digest must match
                      the published bytes (``DOC_SHA_MISMATCH``).  A number
                      typed by hand where a placeholder stood cannot survive
                      this layer.
L2 key coverage       every placeholder in the template must have a sidecar
                      record (``MISSING_KEY``); no placeholder may survive
                      unrendered into the published body (``UNRENDERED_
                      PLACEHOLDER``).
L3 source identity    every number record's ``query_hash`` must equal the
                      fingerprint recomputed from its own ``source``
                      (``QUERY_HASH_MISMATCH``); when a database is supplied
                      the query is re-executed and the value compared
                      (``SOURCE_DRIFT`` / ``SOURCE_ERROR``).  Every artifact
                      record's bytes are re-hashed (``ARTIFACT_HASH_MISMATCH``).
L4 bare-number ratio  every numeric token in the published body is either
                      bound (it sits inside a rendered substitution span, or
                      -- weaker -- it is byte-identical to a recorded value) or
                      it counts as *bare*.  ``bare_number_ratio = bare /
                      in_scope``; above the threshold the report is rejected or
                      flagged per policy.  Structural numerals (dates, versions,
                      ``表 2``-style labels, heading numbering, code) are
                      excluded by **declared scope** and counted in the report
                      under ``excluded`` so the exclusion is auditable rather
                      than silent.

Declared scope that is deliberately NOT enforced
------------------------------------------------
* Numbers inside fenced/inline code are machine content (paths, snippets) --
  censused as ``code_context``, never bound.
* Numbers inside a region marked ``<!-- prov:scope:declared -->`` or
  ``<!-- prov:scope:literature -->`` (or a line carrying ``<!-- declared -->``
  / ``<!-- literature -->``) are *by construction* declared values or cited
  values, not computed results.  They are counted, never silently dropped.
* Headings' section numbering (``## 3.2``) is structural.

Honest limits
-------------
* ``value_equality`` binding (no template, no manifest) is a *weaker* mode: a
  hand-typed literal that happens to equal a recorded value is bound.  The
  report says which mode was used; ``config.require_manifest=True`` forbids the
  weak mode outright.
* The gate cannot judge whether a query is the *right* query.  It proves
  "this number came from that query", never "that query is the intended
  measurement" -- that is the reviewer's job.
* A sidecar can be forged consistently (value, hash, timestamp, and data all
  rewritten).  hmac signing is out of scope here; see ``sidecar.py``.

FAIL codes
----------
SIDECAR_SCHEMA_INVALID  QUERY_HASH_MISMATCH  MISSING_KEY  UNRENDERED_PLACEHOLDER
RENDER_DRIFT  DOC_SHA_MISMATCH  MANIFEST_REQUIRED  MANIFEST_SPAN_MISMATCH
SIDECAR_VALUE_DRIFT  SOURCE_DRIFT  SOURCE_ERROR  ARTIFACT_MISSING
ARTIFACT_HASH_MISMATCH  UNEXTRACTED_DIGIT  SCOPE_REGION_UNTERMINATED
BARE_NUMBER_RATIO_EXCEEDED (FAIL under reject policy, WARN under flag policy)

Exit code 0 = PASS, 1 = FAIL or FLAG, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Sequence

from tools.chronicle.provenance.tokens import (
    FENCE_RE,
    extract_numeric_tokens,
    extraction_coverage,
)

from .render import load_manifest, render
from .sidecar import (
    PLACEHOLDER_RE,
    ArtifactRecord,
    NumberRecord,
    Sidecar,
    render_number_text,
    rounding_budget,
    sha256_file,
    sha256_text,
    validate_sidecar,
)

SCHEMA_VERSION = "report-prov-gate/1"

FAIL = "FAIL"
WARN = "WARN"

REGION_BEGIN_RE = re.compile(r"<!--\s*prov:scope:(declared|literature)\s*-->")
REGION_END_RE = re.compile(r"<!--\s*prov:scope:end\s*-->")
LINE_DECLARED_RE = re.compile(r"<!--\s*(declared|literature)\s*-->")
LABEL_TAIL_RE = re.compile(
    r"(?:表|图|式|第|附录|章节|Figure|Fig\.?|Table|Section|§|Eq\.?|Chapter|Appendix)\s*$",
    re.I,
)
HEADING_PREFIX_RE = re.compile(r"^\s{0,3}#{1,6}\s+(\d+(?:\.\d+)*)")
LIST_MARKER_RE = re.compile(r"^(\s{0,3})(\d{1,3})([.)])(\s|$)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
MAX_LISTED_TOKENS = 50


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@dataclass
class GateConfig:
    #: hard/soft ceiling for the share of in-scope numbers with no binding
    max_bare_ratio: float = 0.0
    #: "reject" -> status FAIL when the ratio is exceeded; "flag" -> status FLAG
    on_exceed: str = "reject"
    #: refuse the weak (value-equality) binding mode
    require_manifest: bool = False
    #: allow byte-identical-to-recorded-value binding when no manifest is given
    value_equality_binding: bool = True
    #: require extraction totality (every manuscript digit inside a token)
    require_extraction_totality: bool = True
    #: cap on the share of numerals that hide in code spans/fences (an escape
    #: hatch if unbounded: a result wrapped in backticks leaves binding scope)
    max_code_ratio: float = 0.10
    #: cap on the share of numerals excluded as declared/literature values
    #: (an escape hatch if unbounded: mark the results section "declared")
    max_region_excluded_ratio: float = 0.75

    def validate(self) -> None:
        if self.on_exceed not in ("reject", "flag"):
            raise ValueError(f"on_exceed must be 'reject' or 'flag', got {self.on_exceed!r}")
        for name in ("max_bare_ratio", "max_code_ratio", "max_region_excluded_ratio"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1], got {value}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _finding(code: str, severity: str, detail: str, **extra: Any) -> dict:
    out = {"code": code, "severity": severity, "detail": detail}
    out.update(extra)
    return out


def _resolve(path: str, root: str | Path | None) -> Path:
    p = Path(path)
    if p.is_absolute() or root is None:
        return p
    return Path(root) / p


def sqlite_scalar(db_path: str | Path, sql: str, params: Sequence[Any] = ()) -> float:
    """Re-execute a recorded query against its snapshot and return the scalar.
    Anything other than exactly one numeric cell is an error: an unverifiable
    record is never a PASS."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(sql, list(params)).fetchone()
    finally:
        conn.close()
    if row is None or len(row) != 1:
        raise ValueError(f"query did not yield a single scalar: {row!r}")
    return float(row[0])


def code_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges that are machine content: inline code spans and fenced
    blocks.  Marker syntax quoted inside them is documentation, not a directive
    (declared scope: code is never a control channel)."""
    spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in INLINE_CODE_RE.finditer(text)]
    pos = 0
    open_at: int | None = None
    for line in text.splitlines(keepends=True):
        end = pos + len(line)
        if FENCE_RE.match(line):
            if open_at is None:
                open_at = pos
            else:
                spans.append((open_at, end))
                open_at = None
        pos = end
    if open_at is not None:
        spans.append((open_at, len(text)))
    return spans


def _in_code(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def scope_regions(text: str) -> tuple[list[tuple[int, int, str]], list[str]]:
    """Spans of ``declared`` / ``literature`` regions, plus unterminated-region
    names.  Where the SAME marker closes a region, the end marker closes it;
    a nested begin reports the outer region as unterminated rather than
    silently swallowing it.  Markers inside code spans/fences are inert."""
    regions: list[tuple[int, int, str]] = []
    open_region: tuple[int, str] | None = None
    unterminated: list[str] = []
    masks = code_spans(text)
    events: list[tuple[int, str, str]] = []
    for m in REGION_BEGIN_RE.finditer(text):
        if _in_code(m.start(), masks):
            continue
        events.append((m.start(), "begin", m.group(1)))
    for m in REGION_END_RE.finditer(text):
        if _in_code(m.start(), masks):
            continue
        events.append((m.start(), "end", "end"))
    events.sort()
    for pos, kind, name in events:
        if kind == "begin":
            if open_region is not None:
                unterminated.append(open_region[1])
                regions.append((open_region[0], pos, open_region[1]))
            open_region = (pos, name)
        else:
            if open_region is None:
                unterminated.append("end-without-begin")
            else:
                regions.append((open_region[0], pos, open_region[1]))
                open_region = None
    if open_region is not None:
        unterminated.append(open_region[1])
        regions.append((open_region[0], len(text), open_region[1]))
    return regions, unterminated


def _line_bounds(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = 0
    for line in text.splitlines(keepends=True):
        out.append((start, start + len(line)))
        start += len(line)
    if start < len(text) or not out:
        out.append((start, len(text)))
    return out


def _line_index(bounds: list[tuple[int, int]], pos: int) -> int:
    for i, (a, b) in enumerate(bounds):
        if a <= pos < b:
            return i
    return len(bounds) - 1


def _exclusion_reason(
    text: str,
    tok: Any,
    regions: list[tuple[int, int, str]],
    bounds: list[tuple[int, int]],
) -> str | None:
    """Why a token is out of binding scope (None = it is in scope)."""
    if tok.context == "code":
        return "code_context"
    for a, b, name in regions:
        if a <= tok.span[0] < b:
            return f"{name}_region"
    li = _line_index(bounds, tok.span[0])
    a, b = bounds[li]
    line = text[a:b]
    m = LINE_DECLARED_RE.search(line)
    if m is not None:  # a line marker declares the whole line
        return f"{m.group(1)}_line"
    if tok.kind in ("date", "version"):
        return "structural_date_or_version"
    lm = LIST_MARKER_RE.match(line)
    if lm is not None and a + lm.start(2) <= tok.span[0] < a + lm.end(2):
        return "list_marker"
    pre = text[max(0, tok.span[0] - 24):tok.span[0]].rstrip(" ([{（【")
    if LABEL_TAIL_RE.search(pre):
        return "label_numeral"
    hm = HEADING_PREFIX_RE.match(line)
    if hm is not None and a + hm.start(1) <= tok.span[0] < a + hm.end(1):
        return "section_number"
    return None


def _render_candidates(rec: NumberRecord) -> set[str]:
    base = render_number_text(rec)
    return {base, base + "%", base + "‰"}


def _bound_by_entry(tok: Any, entry: dict) -> bool:
    text = str(entry.get("text", ""))
    start = int(entry.get("start", 0))
    if not (start <= tok.span[0] < start + max(len(text), 1)):
        return False
    raw = tok.raw
    return text.startswith(raw) or raw.startswith(text)


def _looks_like_path(value: Any) -> bool:
    if isinstance(value, Path):
        return True
    if not isinstance(value, str) or "\n" in value or len(value) > 4096:
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False


def _first_diff_line(a: str, b: str) -> int:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(min(len(la), len(lb))):
        if la[i] != lb[i]:
            return i + 1
    return min(len(la), len(lb)) + 1


def _numeric_mutations(expected: str, actual: str, limit: int = 10) -> list[dict]:
    """Where the actual document's digits differ from the canonical render, so
    the finding names the tampered numbers rather than "documents differ"."""
    out: list[dict] = []
    for tok in extract_numeric_tokens(actual):
        if tok.raw not in expected:
            out.append({"line": tok.line, "literal": tok.raw})
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def gate_report(
    published: str | Path,
    sidecar: Sidecar,
    template: str | Path | None = None,
    manifest: dict | str | Path | None = None,
    db_path: str | Path | None = None,
    artifact_root: str | Path | None = None,
    config: GateConfig | None = None,
    doc_name: str | None = None,
) -> dict:
    cfg = config or GateConfig()
    cfg.validate()

    if _looks_like_path(published):
        doc_path = Path(published)
        md = doc_path.read_text(encoding="utf-8")
        doc_name = doc_name or str(doc_path)
    else:
        md = str(published)
        doc_name = doc_name or "<published>"

    template_text: str | None = None
    if template is not None:
        if _looks_like_path(template):
            template_text = Path(str(template)).read_text(encoding="utf-8")
        else:
            template_text = str(template)

    if manifest is not None and not isinstance(manifest, dict):
        manifest = load_manifest(manifest)

    findings: list[dict] = []

    # ---- L0 sidecar shape + query identity -------------------------------
    for f in validate_sidecar(sidecar, strict_hash=True):
        findings.append(_finding(f["code"], FAIL, f["detail"], key=f.get("key")))
    if cfg.require_manifest and manifest is None:
        findings.append(_finding("MANIFEST_REQUIRED", FAIL,
                                 "policy requires a render manifest; none was supplied"))
    if cfg.require_manifest and template_text is None:
        findings.append(_finding("MANIFEST_REQUIRED", FAIL,
                                 "policy requires a template for canonical re-render; none was supplied"))

    # ---- L1 render fidelity ---------------------------------------------
    if template_text is not None:
        missing = sidecar.missing_keys(PLACEHOLDER_RE.findall(template_text))
        if missing:
            findings.append(_finding("MISSING_KEY", FAIL,
                                     f"template references key(s) with no sidecar record: {', '.join(missing)}",
                                     missing=missing))
        else:
            canonical = render(template_text, sidecar, strict=True).markdown
            if canonical != md:
                findings.append(_finding(
                    "RENDER_DRIFT", FAIL,
                    f"published body differs from canonical render at line "
                    f"{_first_diff_line(canonical, md)} (the published bytes are not what "
                    f"the sidecar produces)",
                    first_diff_line=_first_diff_line(canonical, md),
                    numeric_mutations=_numeric_mutations(canonical, md),
                ))

    leftover = PLACEHOLDER_RE.findall(md)
    if leftover:
        findings.append(_finding("UNRENDERED_PLACEHOLDER", FAIL,
                                 f"{len(leftover)} placeholder(s) survived into the published body: "
                                 f"{', '.join(dict.fromkeys(leftover))}",
                                 keys=list(dict.fromkeys(leftover))))

    # ---- L2 manifest integrity ------------------------------------------
    manifest_ok = False
    if manifest is not None:
        doc_sha = sha256_text(md)
        if manifest.get("rendered_sha256") != doc_sha:
            findings.append(_finding(
                "DOC_SHA_MISMATCH", FAIL,
                f"published sha256 {doc_sha[:16]} != manifest {str(manifest.get('rendered_sha256'))[:16]}: "
                f"the document was edited after rendering",
            ))
        else:
            manifest_ok = True
            for entry in manifest.get("entries", []):
                start, end = int(entry["start"]), int(entry["end"])
                if md[start:end] != entry.get("text"):
                    findings.append(_finding(
                        "MANIFEST_SPAN_MISMATCH", FAIL,
                        f"{entry['key']}: span [{start},{end}) holds {md[start:end]!r}, "
                        f"manifest says {entry.get('text')!r}",
                        key=entry["key"],
                    ))
                    continue
                rec = sidecar.get(entry["key"])
                if rec is None:
                    continue
                current = (render_number_text(rec) if rec.kind == "number"
                           else rec.artifact_sha256)
                if current != entry.get("text"):
                    findings.append(_finding(
                        "SIDECAR_VALUE_DRIFT", FAIL,
                        f"{entry['key']}: sidecar now renders {current!r} but the published "
                        f"document shows {entry.get('text')!r} (record edited after render)",
                        key=entry["key"],
                    ))
    else:
        doc_sha = sha256_text(md)

    # ---- L3 source re-computation ---------------------------------------
    recomputed = 0
    for rec in sidecar.number_records():
        src = rec.source
        if db_path is not None and src.get("type") == "sql":
            try:
                value = sqlite_scalar(db_path, src["sql"], src.get("params") or [])
            except Exception as exc:  # unverifiable is never a PASS
                findings.append(_finding("SOURCE_ERROR", FAIL,
                                         f"{rec.key}: {exc}", key=rec.key))
                continue
            recomputed += 1
            budget = rounding_budget(rec) + abs(float(value)) * 1e-9
            if abs(value - float(rec.value)) > budget:
                findings.append(_finding(
                    "SOURCE_DRIFT", FAIL,
                    f"{rec.key}: recorded {rec.value!r} but the re-executed query returns "
                    f"{value!r} (budget {budget:g})",
                    key=rec.key, recorded=rec.value, recomputed=value,
                ))
    for rec in sidecar.artifact_records():
        art = _resolve(rec.source["path"], artifact_root)
        if not art.exists():
            findings.append(_finding("ARTIFACT_MISSING", FAIL,
                                     f"{rec.key}: {art} is not on disk", key=rec.key))
            continue
        actual = sha256_file(art)
        if actual != rec.artifact_sha256:
            findings.append(_finding(
                "ARTIFACT_HASH_MISMATCH", FAIL,
                f"{rec.key}: {art.name} sha256 {actual[:16]} != recorded "
                f"{rec.artifact_sha256[:16]}",
                key=rec.key, actual=actual, recorded=rec.artifact_sha256,
            ))

    # ---- L4 bare-number ratio -------------------------------------------
    regions, unterminated = scope_regions(md)
    if unterminated:
        findings.append(_finding("SCOPE_REGION_UNTERMINATED", FAIL,
                                 f"declared/literature region(s) not closed: {', '.join(unterminated)}"))
    bounds = _line_bounds(md)
    tokens = extract_numeric_tokens(md)
    excluded: dict[str, int] = {}
    in_scope: list[Any] = []
    for tok in tokens:
        reason = _exclusion_reason(md, tok, regions, bounds)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            in_scope.append(tok)

    entries = [e for e in (manifest.get("entries", []) if manifest_ok else []) if e.get("kind") == "number"]
    candidate_cache: set[str] = set()
    for rec in sidecar.number_records():
        candidate_cache |= _render_candidates(rec)

    if entries:
        binding_mode = "manifest"
    elif cfg.value_equality_binding:
        binding_mode = "value_equality"
    else:
        binding_mode = "none"

    bound = 0
    bare: list[dict] = []
    for tok in in_scope:
        if binding_mode == "manifest" and any(_bound_by_entry(tok, e) for e in entries):
            bound += 1
            continue
        if binding_mode in ("manifest", "value_equality") and candidate_cache:
            raw = tok.raw
            if raw in candidate_cache or raw.rstrip("%‰") in candidate_cache:
                bound += 1
                continue
        bare.append({"line": tok.line, "col": tok.col, "raw": tok.raw,
                     "kind": tok.kind, "context": tok.context})
    n_scope = len(in_scope)
    bare_ratio = (len(bare) / n_scope) if n_scope else 0.0
    n_tokens = len(tokens)
    code_ratio = (excluded.get("code_context", 0) / n_tokens) if n_tokens else 0.0
    region_excluded = sum(v for k, v in excluded.items() if k.endswith("_region") or k.endswith("_line"))
    region_ratio = (region_excluded / n_tokens) if n_tokens else 0.0
    if code_ratio > cfg.max_code_ratio:
        findings.append(_finding(
            "CODE_CONTEXT_RATIO_HIGH", WARN,
            f"{code_ratio:.4f} of numerals sit in code spans/fences (cap "
            f"{cfg.max_code_ratio:.4f}): a numeral wrapped as code leaves binding scope",
        ))
    if region_ratio > cfg.max_region_excluded_ratio:
        findings.append(_finding(
            "REGION_EXCLUDED_RATIO_HIGH", WARN,
            f"{region_ratio:.4f} of numerals are excluded as declared/literature (cap "
            f"{cfg.max_region_excluded_ratio:.4f}): the declared-scope markers are "
            f"carrying too much of the document to be credible",
        ))

    cov, total_digits, covered_digits = extraction_coverage(md)
    if cfg.require_extraction_totality and cov < 1.0:
        findings.append(_finding("UNEXTRACTED_DIGIT", FAIL,
                                 f"{total_digits - covered_digits} manuscript digit(s) outside any token"))

    if bare_ratio > cfg.max_bare_ratio:
        findings.append(_finding(
            "BARE_NUMBER_RATIO_EXCEEDED",
            FAIL if cfg.on_exceed == "reject" else WARN,
            f"bare_number_ratio {bare_ratio:.4f} > threshold {cfg.max_bare_ratio:.4f} "
            f"({len(bare)} of {n_scope} in-scope number(s) unbound); policy={cfg.on_exceed}",
            rejected=cfg.on_exceed == "reject",
        ))

    fails = [f for f in findings if f["severity"] == FAIL]
    warns = [f for f in findings if f["severity"] == WARN]
    if fails:
        status, disposition = FAIL, "reject"
    elif warns:
        status, disposition = "FLAG", "flag"
    else:
        status, disposition = "PASS", "pass"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "disposition": disposition,
        "document": doc_name,
        "document_sha256": sha256_text(md),
        "sidecar": {
            "record_count": len(sidecar),
            "number_records": len(sidecar.number_records()),
            "artifact_records": len(sidecar.artifact_records()),
        },
        "policy": {
            "max_bare_ratio": cfg.max_bare_ratio,
            "on_exceed": cfg.on_exceed,
            "require_manifest": cfg.require_manifest,
            "value_equality_binding": cfg.value_equality_binding,
            "max_code_ratio": cfg.max_code_ratio,
            "max_region_excluded_ratio": cfg.max_region_excluded_ratio,
        },
        "counts": {
            "numeric_tokens": len(tokens),
            "in_scope": n_scope,
            "bound": bound,
            "bare": len(bare),
            "excluded": sum(excluded.values()),
            "queries_reexecuted": recomputed,
            "artifacts_rehashed": len(sidecar.artifact_records()),
        },
        "bare_number_ratio": bare_ratio,
        "code_context_ratio": code_ratio,
        "region_excluded_ratio": region_ratio,
        "binding_mode": binding_mode,
        "manifest_verified": manifest_ok,
        "excluded_by_reason": excluded,
        "extraction": {
            "coverage": cov,
            "manuscript_digits": total_digits,
            "covered_digits": covered_digits,
        },
        "bare_tokens": bare[:MAX_LISTED_TOKENS],
        "fail_count": len(fails),
        "warn_count": len(warns),
        "findings": findings,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="number-level provenance gate for reports")
    ap.add_argument("--published", required=True)
    ap.add_argument("--sidecar", required=True)
    ap.add_argument("--template", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--db", default=None, help="sqlite snapshot to re-execute recorded queries")
    ap.add_argument("--artifact-root", default=None)
    ap.add_argument("--max-bare-ratio", type=float, default=0.0)
    ap.add_argument("--on-exceed", choices=("reject", "flag"), default="reject")
    ap.add_argument("--require-manifest", action="store_true")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    cfg = GateConfig(
        max_bare_ratio=args.max_bare_ratio,
        on_exceed=args.on_exceed,
        require_manifest=args.require_manifest,
    )
    result = gate_report(
        args.published,
        Sidecar.load(args.sidecar),
        template=args.template,
        manifest=args.manifest,
        db_path=args.db,
        artifact_root=args.artifact_root,
        config=cfg,
    )
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    if not args.quiet:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"[{result['status']}] ratio={result['bare_number_ratio']:.4f} "
              f"fails={result['fail_count']} warns={result['warn_count']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())