#!/usr/bin/env python3
"""Full-text numeric token extraction and 100% binding coverage (W18-N10).

Why this exists
---------------
The chronicle report line (``tools/chronicle/report/``) enforces zero
hand-copy only inside ``<!-- prov:begin -->`` blocks.  A number written
outside those blocks is invisible to the gate, so "no bare number" was never
machine-provable for the whole document.  This module closes that hole:

1. **Total extraction.**  Every *manuscript digit* is inside exactly one
   extracted token.  Extraction coverage is
   ``covered manuscript digits / total manuscript digits`` and must be 1.0.
   Manuscript digits exclude, by declared scope (not by hole): digits inside
   ``{{prov:...}}`` binding tokens, digits inside HTML comments (machine
   metadata), digits inside inline code spans and fenced code blocks
   (machine content, same convention as the block-level gate), and digits
   that are the tail of an identifier (``m5298``, ``sha256:ab12``) -- an
   identifier digit is not a number.  Tokens in code context are censused
   but out of binding scope (declared scope, not a hole).
2. **Eight composite forms.**  table cell / figure caption / formula /
   error bar / range / ratio / percent / scientific notation are all parsed
   to values (see :data:`EIGHT_FORMS`); ``check_coverage`` reports per-form
   counts so "all eight supported" is a machine-checkable statement.
3. **Binding or exemption.**  Every token is either bound to a provenance
   record via an adjacent ``{{prov:pid}}`` token (printed value must equal
   the recorded value within the rounding budget) or exempted by a
   hash-anchored rule from ``exemptions.yaml``.  PASS requires unbound == 0
   and exemption rate < 5%.

Token contract
--------------
Each token is a five-tuple ``(line, col, raw, value, context)`` plus
bookkeeping fields:

* ``line`` / ``col``  -- 1-based line, 0-based column of the token start;
* ``raw``            -- the matched literal (sign, thousands separators,
  e/E exponent, %/‰, unit suffix, range/ratio/± body included);
* ``value``          -- primary numeric value (range: lower bound; ratio:
  quotient; error bar: central value; structural forms such as dates: None);
* ``context``        -- where the token sits: ``prose`` / ``table_cell`` /
  ``figure_caption`` / ``formula`` / ``code`` / ``heading``;
* ``kind``           -- the numeric form: ``integer`` / ``decimal`` /
  ``signed`` / ``thousands`` / ``percent`` / ``permille`` / ``scientific`` /
  ``range`` / ``ratio`` / ``error_bar`` / ``unit`` / ``date`` / ``version``
  (dotted identifiers such as ``3.11.6`` are structural, value None);
* ``value2``         -- secondary value (range upper bound, ratio denominator,
  error-bar half-width);
* ``unit``           -- trailing unit suffix when glued to the digits.

Declared scope decisions (documented, not silent)
-------------------------------------------------
* A hyphen range ``a-b`` is recognised only when tight (no spaces); spaced
  ``a - b`` stays two signed numbers (subtraction is not a range).  En dash
  ``a–b`` and em dash ``a—b`` are ranges regardless of spacing.
* ``a:b`` is a ratio (quotient as ``value``); times like ``12:30`` parse as
  ratios too, which is the same numeric reading.
* A unit suffix is captured only when glued to the digits (``25m/s``);
  ``25 m/s`` keeps the unit out of the token (the space breaks the literal).

Usage
-----
    python3 -m tools.chronicle.provenance.tokens manuscript.md \
        --exemptions tools/chronicle/provenance/exemptions.yaml \
        --provenance provenance.json --json-out coverage.json

Exit code 0 = PASS (100% extraction, 0 unbound, exemption rate < 5%).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "chronicle-provenance-tokens/1"

#: The eight composite numeric forms that must all be supported.  The first
#: three are *contexts* (where the number sits), the last five are *kinds*
#: (what the number is).
EIGHT_FORMS: tuple[str, ...] = (
    "table",           # context == "table_cell"
    "figure_caption",  # context == "figure_caption"
    "formula",         # context == "formula"
    "error_bar",       # kind == "error_bar"
    "range",           # kind == "range"
    "ratio",           # kind == "ratio"
    "percent",         # kind == "percent"
    "scientific",      # kind == "scientific"
)

CONTEXTS: tuple[str, ...] = ("prose", "table_cell", "figure_caption", "formula", "code", "heading")
KINDS: tuple[str, ...] = (
    "integer", "decimal", "signed", "thousands", "percent", "permille",
    "scientific", "range", "ratio", "error_bar", "unit", "date", "version",
)

PROV_TOKEN_RE = re.compile(r"\{\{prov:([A-Za-z0-9_.:-]+)\}\}")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]")
INLINE_FORMULA_RE = re.compile(r"\$[^$\n]+\$")
LABEL_WORD_RE = re.compile(
    r"(?:表|图|式|附录|章节|第|Figure|Fig\.|Table|Section|§|Eq\.?)\s*$"
)

# ---------------------------------------------------------------------------
# numeric core
# ---------------------------------------------------------------------------
# integer part: thousands-grouped (1,234,567) or plain digits
_INT = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
# full core: optional sign + integer + optional fraction
_CORE = rf"(?:[-+])?(?:{_INT})(?:\.\d+)?"
# core without sign (second operand of range/ratio: the separator already
# consumed the boundary, a second sign would be a different reading)
_CORE_NS = rf"(?:{_INT})(?:\.\d+)?"
# scientific: core + mandatory exponent
_SCI = rf"(?:[-+])?(?:{_INT})(?:\.\d+)?[eE][-+]?\d+"
# unit suffix: glued letters/symbols (25m/s, 100MPa, 3.14rad, 100°C);
# a unit never abuts a digit on its right (374f9f572 is a hex id, not 374f)
_UNIT = r"(?:[A-Za-z°µμΩ/·^()]+(?![0-9]))?"
# calendar date: 2026-11-07 / 2026/11/7 (listed before range so it wins)
_DATE = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"

MASTER_RE = re.compile(
    rf"""
    (?P<errbar>      {_CORE}\s*±\s*{_CORE})
  | (?P<date>        {_DATE})
  | (?P<range_tight> {_CORE}[-–—]{_CORE_NS})
  | (?P<range_dash>  {_CORE}\s*[–—]\s*{_CORE_NS})
  | (?P<ratio>       {_CORE}:{_CORE_NS})
  | (?P<sci>         {_SCI})
  | (?P<version>     \d+(?:\.\d+){{2,}})
  | (?P<permille>    {_CORE}‰)
  | (?P<percent>     {_CORE}%)
  | (?P<plain>       {_CORE}{_UNIT})
    """,
    re.X,
)

#: a digit may start a token only if not preceded by a word char / dot /
#: comma (identifier tail, decimal tail, thousands tail)
START_GUARD_RE = re.compile(r"(?<![\w.,])")

UNIT_CHARS = r"A-Za-z°µμΩ/·^()"


def _parse_core(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class NumericToken:
    """One extracted numeric token.  The public contract is the five fields
    ``(line, col, raw, value, context)``; the rest is bookkeeping."""

    line: int
    col: int
    raw: str
    value: float | None
    context: str
    kind: str = "integer"
    unit: str = ""
    value2: float | None = None
    span: tuple[int, int] = (0, 0)
    bound: bool = False
    exemption_rule: str | None = None

    def five_tuple(self) -> tuple[int, int, str, float | None, str]:
        return (self.line, self.col, self.raw, self.value, self.context)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = list(self.span)
        return d


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------
def _mask_spans(text: str, pattern: re.Pattern) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _in_spans(pos: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _line_col(text: str, pos: int) -> tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    col = pos - (text.rfind("\n", 0, pos) + 1)
    return line, col


def _context_of(line: str, in_fence: bool) -> str:
    if in_fence:
        return "code"
    s = line.strip()
    if s.startswith("#"):
        return "heading"
    if TABLE_LINE_RE.match(line):
        return "table_cell"
    if IMAGE_LINE_RE.search(line):
        return "figure_caption"
    if "$$" in line or INLINE_FORMULA_RE.search(line):
        return "formula"
    return "prose"


def _fence_lines(text: str) -> set[int]:
    fence: set[int] = set()
    in_fence = False
    for i, ln in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            fence.add(i)
    return fence


def _build_token(raw: str, alt: str, line: int, col: int, context: str,
                 start: int, end: int) -> NumericToken:
    value: float | None = None
    value2: float | None = None
    unit = ""
    if alt == "errbar":
        central, err = _split_around(raw, "±")
        value, value2 = _parse_core(central), _parse_core(err)
        kind = "error_bar"
    elif alt == "date":
        value = None  # structural: not a measurement
        kind = "date"
    elif alt in ("range_tight", "range_dash"):
        i = re.search(r"[-–—]", raw).start()
        value, value2 = _parse_core(raw[:i]), _parse_core(raw[i + 1:])
        kind = "range"
    elif alt == "ratio":
        num, den = raw.split(":", 1)
        n, d = _parse_core(num), _parse_core(den)
        value, value2 = (n / d if d else None), d
        kind = "ratio"
    elif alt in ("percent", "permille"):
        unit = "%" if alt == "percent" else "‰"
        value = _parse_core(raw.rstrip("‰%"))
        kind = alt
    elif alt == "version":
        value = None  # structural: dotted identifier, not a measurement
        kind = "version"
    elif alt == "sci":
        value = _parse_core(raw)
        kind = "scientific"
    else:  # plain
        m = re.fullmatch(rf"({_CORE})([{UNIT_CHARS}]+)?", raw)
        if m:
            unit = m.group(2) or ""
            value = _parse_core(m.group(1))
        else:  # pragma: no cover - defensive
            value = _parse_core(re.sub(rf"[{UNIT_CHARS}]+$", "", raw))
        body = m.group(1) if m else raw
        if unit:
            kind = "unit"
        elif "," in body:
            kind = "thousands"
        elif body.startswith(("+", "-")):
            kind = "signed"
        elif "." in body:
            kind = "decimal"
        else:
            kind = "integer"
    return NumericToken(
        line=line, col=col, raw=raw, value=value, context=context,
        kind=kind, unit=unit, value2=value2, span=(start, end),
    )


def _split_around(raw: str, sep: str) -> tuple[str, str]:
    i = raw.index(sep)
    return raw[:i].strip(), raw[i + len(sep):].strip()


INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in INLINE_CODE_RE.finditer(text)]


def extract_numeric_tokens(text: str) -> list[NumericToken]:
    """Extract every numeric token in ``text``.  Total over manuscript
    digits: every digit outside prov tokens / HTML comments / inline code /
    fenced code / identifier tails is covered by exactly one token span."""
    prov_spans = _mask_spans(text, PROV_TOKEN_RE)
    comment_spans = _mask_spans(text, HTML_COMMENT_RE)
    code_spans = _inline_code_spans(text)
    fences = _fence_lines(text)
    lines = text.splitlines()

    tokens: list[NumericToken] = []
    pos = 0
    while True:
        m = MASTER_RE.search(text, pos)
        if m is None:
            break
        s = m.start()
        rejected = (
            _in_spans(s, prov_spans) or _in_spans(s, comment_spans)
            or not START_GUARD_RE.match(text, s)
            or (s >= 2 and text[s - 1] == "-"
                and (text[s - 2].isalpha() or text[s - 2] == "_"))
        )
        if rejected:
            # resume one char past the start so digits inside a rejected
            # match (e.g. the 50 of T00:50) are still reachable
            pos = s + 1
            continue
        alt = next(k for k, v in m.groupdict().items() if v is not None)
        line, col = _line_col(text, s)
        if _in_spans(s, code_spans):
            context = "code"
        else:
            context = _context_of(lines[line - 1], line in fences)
        tokens.append(_build_token(m.group(alt), alt, line, col, context, s, m.end()))
        pos = m.end()
    return tokens


def manuscript_digits(text: str) -> list[int]:
    """Absolute offsets of every manuscript digit (see module docstring for
    the exclusions).  This is the denominator of extraction coverage."""
    prov_spans = _mask_spans(text, PROV_TOKEN_RE)
    comment_spans = _mask_spans(text, HTML_COMMENT_RE)
    code_spans = _inline_code_spans(text)
    fences = _fence_lines(text)
    out: list[int] = []
    for m in re.finditer(r"\d", text):
        p = m.start()
        if (_in_spans(p, prov_spans) or _in_spans(p, comment_spans)
                or _in_spans(p, code_spans)):
            continue
        if _line_col(text, p)[0] in fences:
            continue
        if p > 0 and (text[p - 1].isalnum() or text[p - 1] == "_"):
            continue  # identifier tail (m5298, ab12)
        if p >= 2 and text[p - 1] == "-" and (text[p - 2].isalpha() or text[p - 2] == "_"):
            continue  # hyphenated identifier tail (n-007)
        out.append(p)
    return out


def extraction_coverage(text: str) -> tuple[float, int, int]:
    """(coverage, total manuscript digits, covered manuscript digits)."""
    digits = manuscript_digits(text)
    spans = [t.span for t in extract_numeric_tokens(text)]
    covered = sum(1 for p in digits if any(a <= p < b for a, b in spans))
    cov = (covered / len(digits)) if digits else 1.0
    return cov, len(digits), covered


# ---------------------------------------------------------------------------
# exemptions (machine-auditable)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExemptionRule:
    rule_id: str
    exact_regex: str
    reason: str
    hash: str
    valid: bool = True
    detail: str = ""
    _re: "re.Pattern | None" = field(default=None, compare=False, repr=False)

    def matches(self, contextual_raw: str) -> bool:
        return bool(self.valid and self._re is not None
                    and self._re.fullmatch(contextual_raw))


def rule_hash(rule_id: str, exact_regex: str, reason: str) -> str:
    """Self-hash: sha256 over the canonical (rule_id|regex|reason) triple.
    Any edit to a rule invalidates its hash, so a rule cannot be widened
    silently -- the loader rejects it and the gate reports the finding."""
    canon = f"{rule_id}|{exact_regex}|{reason}"
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_exemptions(path: str | Path) -> tuple[list[ExemptionRule], list[dict]]:
    """Load and audit exemption rules.  A rule whose hash does not verify, or
    whose regex does not compile, is rejected (never applied) and reported.
    Returns (rules, findings)."""
    import yaml  # local import: core module stays stdlib-only at import time

    findings: list[dict] = []
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rules: list[ExemptionRule] = []
    for i, raw in enumerate(payload.get("rules") or []):
        rid = str(raw.get("rule_id") or f"EX-{i:03d}")
        regex = str(raw.get("exact_regex") or "")
        reason = str(raw.get("reason") or "")
        declared = str(raw.get("hash") or "")
        expected = rule_hash(rid, regex, reason)
        if declared != expected:
            findings.append({
                "code": "EXEMPTION_HASH_MISMATCH", "severity": "FAIL",
                "detail": f"{rid}: declared {declared[:24]}… != recomputed "
                          f"{expected[:24]}… (rule rejected, not applied)",
                "rule_id": rid,
            })
            rules.append(ExemptionRule(rid, regex, reason, declared, valid=False,
                                      detail="hash mismatch"))
            continue
        try:
            cre = re.compile(regex)
        except re.error as exc:
            findings.append({
                "code": "EXEMPTION_BAD_REGEX", "severity": "FAIL",
                "detail": f"{rid}: {exc} (rule rejected, not applied)",
                "rule_id": rid,
            })
            rules.append(ExemptionRule(rid, regex, reason, declared, valid=False,
                                       detail=f"bad regex: {exc}"))
            continue
        rule = ExemptionRule(rid, regex, reason, declared, valid=True)
        object.__setattr__(rule, "_re", cre)
        rules.append(rule)
    return rules, findings


def _contextual_raw(text: str, tok: NumericToken) -> str:
    """raw plus an immediately preceding label word (表/图/第/Fig./...), so
    label rules match the label+number pair without matching bare integers."""
    line_start = text.rfind("\n", 0, tok.span[0]) + 1
    prefix = text[line_start:tok.span[0]]
    m = LABEL_WORD_RE.search(prefix)
    return (prefix[m.start():] if m else "") + tok.raw


# ---------------------------------------------------------------------------
# coverage gate
# ---------------------------------------------------------------------------
@dataclass
class CoverageReport:
    file: str
    total_tokens: int = 0
    bound: int = 0
    exempted: int = 0
    unbound: int = 0
    extraction_coverage: float = 0.0
    binding_coverage: float = 0.0
    exemption_rate: float = 0.0
    total_manuscript_digits: int = 0
    covered_manuscript_digits: int = 0
    unbound_tokens: list = field(default_factory=list)
    by_kind: dict = field(default_factory=dict)
    by_context: dict = field(default_factory=dict)
    by_form: dict = field(default_factory=dict)
    invalid_rules: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    status: str = "FAIL"

    def to_dict(self) -> dict:
        return asdict(self)


def _load_records(provenance_path: str | Path | None) -> dict[str, dict]:
    if not provenance_path:
        return {}
    payload = json.loads(Path(provenance_path).read_text(encoding="utf-8"))
    return {r["pid"]: r for r in payload.get("records", [])}


def _rounding_budget(record: dict) -> float:
    tol = record.get("tolerance") or {}
    base = abs(float(record.get("value") or 0.0)) * float(tol.get("rtol", 0.0)) \
        + float(tol.get("atol", 0.0))
    decimals = record.get("decimals")
    if decimals is not None:
        base = max(base, 0.5 * 10 ** (-int(decimals)))
    return base


def _adjacent_prov(text: str, tok: NumericToken) -> str | None:
    """pid of a {{prov:pid}} token immediately following the number (whitespace
    allowed in between), else None."""
    m = re.match(r"\s*\{\{prov:([A-Za-z0-9_.:-]+)\}\}", text[tok.span[1]:])
    return m.group(1) if m else None


def check_coverage(
    text: str,
    records: dict[str, dict] | None = None,
    rules: Sequence[ExemptionRule] | None = None,
    file: str = "<text>",
    max_exemption_rate: float = 0.05,
) -> CoverageReport:
    """The full-text gate.  PASS requires: extraction coverage 1.0, zero
    unbound tokens, exemption rate < ``max_exemption_rate``, and no invalid
    exemption rules."""
    records = records or {}
    rules = list(rules or [])
    rep = CoverageReport(file=file)
    for r in rules:
        if not r.valid:
            rep.invalid_rules.append(r.rule_id)
            rep.findings.append({
                "code": "EXEMPTION_RULE_INVALID", "severity": "FAIL",
                "detail": f"{r.rule_id}: {r.detail} (rule rejected, not applied)",
            })

    tokens = extract_numeric_tokens(text)
    rep.total_tokens = len(tokens)
    for t in tokens:
        rep.by_kind[t.kind] = rep.by_kind.get(t.kind, 0) + 1
        rep.by_context[t.context] = rep.by_context.get(t.context, 0) + 1

    # V1 extraction totality
    cov, total_d, covered_d = extraction_coverage(text)
    rep.extraction_coverage = cov
    rep.total_manuscript_digits = total_d
    rep.covered_manuscript_digits = covered_d
    if cov < 1.0:
        rep.findings.append({
            "code": "UNEXTRACTED_DIGIT", "severity": "FAIL",
            "detail": f"{total_d - covered_d} manuscript digit(s) not inside any token",
        })

    # V2 binding / exemption per token (code-context tokens are machine
    # content, out of binding scope by declared scope -- census only)
    for tok in tokens:
        if tok.context == "code":
            continue
        pid = _adjacent_prov(text, tok)
        if pid is not None:
            rec = records.get(pid)
            if rec is None:
                rep.findings.append({
                    "code": "DANGLING_TOKEN", "severity": "FAIL",
                    "detail": f"line {tok.line}: no record for {pid}",
                })
                rep.unbound += 1
                tok.bound = False
                rep.unbound_tokens.append(tok.to_dict())
                continue
            if rec.get("kind") != "number" or rec.get("value") is None:
                rep.findings.append({
                    "code": "TOKEN_KIND_MISMATCH", "severity": "FAIL",
                    "detail": f"line {tok.line}: {pid} is not a number record",
                })
                rep.unbound += 1
                rep.unbound_tokens.append(tok.to_dict())
                continue
            if tok.value is None or abs(tok.value - float(rec["value"])) \
                    > _rounding_budget(rec) + 1e-12:
                rep.findings.append({
                    "code": "FABRICATED_NUMBER", "severity": "FAIL",
                    "detail": f"line {tok.line}: printed {tok.value!r} != recorded "
                              f"{rec['value']!r} (budget {_rounding_budget(rec):g})",
                })
                rep.unbound += 1
                rep.unbound_tokens.append(tok.to_dict())
                continue
            tok.bound = True
            rep.bound += 1
            continue
        ctx_raw = _contextual_raw(text, tok)
        hit = next((r for r in rules if r.matches(ctx_raw)), None)
        if hit is not None:
            tok.exemption_rule = hit.rule_id
            rep.exempted += 1
            continue
        rep.unbound += 1
        rep.unbound_tokens.append(tok.to_dict())

    # V3 rates (over in-scope tokens: everything except code context)
    in_scope = rep.bound + rep.exempted + rep.unbound
    if in_scope:
        rep.binding_coverage = (rep.bound + rep.exempted) / in_scope
        rep.exemption_rate = rep.exempted / in_scope
    if rep.unbound:
        rep.findings.append({
            "code": "UNBOUND_NUMBER", "severity": "FAIL",
            "detail": f"{rep.unbound} token(s) neither bound nor exempted",
        })
    if in_scope and rep.exemption_rate >= max_exemption_rate:
        rep.findings.append({
            "code": "EXEMPTION_RATE_HIGH", "severity": "FAIL",
            "detail": f"exemption rate {rep.exemption_rate:.4f} >= {max_exemption_rate}",
        })

    # V4 per-form census (the eight composite forms)
    for form in EIGHT_FORMS:
        if form == "table":
            n = rep.by_context.get("table_cell", 0)
        elif form in ("figure_caption", "formula"):
            n = rep.by_context.get(form, 0)
        else:
            n = rep.by_kind.get(form, 0)
        rep.by_form[form] = n

    rep.status = "FAIL" if any(f["severity"] == "FAIL" for f in rep.findings) else "PASS"
    return rep


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="full-text numeric provenance gate")
    ap.add_argument("manuscript")
    ap.add_argument("--exemptions", default=None)
    ap.add_argument("--provenance", default=None)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    text = Path(args.manuscript).read_text(encoding="utf-8")
    rules: list[ExemptionRule] = []
    if args.exemptions:
        rules, _ = load_exemptions(args.exemptions)
    rep = check_coverage(text, _load_records(args.provenance), rules,
                        file=str(args.manuscript))
    out = rep.to_dict()
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if rep.status == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
