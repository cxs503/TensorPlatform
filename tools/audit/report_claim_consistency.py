#!/usr/bin/env python3
"""Cross-report numeric consistency audit for the published evidence base.

Why this module exists
----------------------
The platform publishes 1200+ paper-grade reports, and downstream decisions
(evidence grades, capability ledger, "is this capability REAL?") are made by
reading numbers out of them.  Nothing in the platform checks that the *same
physical quantity of the same hull* is reported with the same value by
different reports.  If report A says ``KCS R_T = 1276.4 kN`` and report B says
``KCS R_T = 1630 kN``, at least one of them is not reproducible, and every
grade derived from either one inherits the error silently.

This tool extracts ``(case, quantity, value, unit)`` tuples from report tables,
groups them by ``(case, quantity)`` and reports the spread.  A group whose
spread exceeds ``SPREAD_FLAG_PCT`` and that has >= 2 distinct reports is an
**inconsistency**: the evidence base cannot be reproduced for that quantity.

Design rules (mirroring `tools/gci/anchors.py` discipline)
---------------------------------------------------------
* Only *plain* quantity headers are used.  Headers carrying ``ref``/``基准``/
  ``plat``/``端点``/``重构`` are reference or side-specific columns and are
  skipped, so a benchmark value is never mixed with a platform value.
* Values keep their unit; kN/kW/m/s groups never mix.
* Extraction never guesses: an ambiguous row is dropped, not coerced.
* The extractor + grouping is validated against a *labelled synthetic corpus*
  where the true answer is known (see ``tests/test_report_claim_consistency.py``),
  so "0 inconsistencies found" cannot be a silent parsing failure.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from dataclasses import dataclass, asdict, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Claim",
    "GroupFinding",
    "parse_markdown_tables",
    "extract_claims",
    "geometry_case",
    "group_claims",
    "audit_texts",
    "QUANTITY_LEXICON",
    "CASE_LEXICON",
    "SPREAD_FLAG_PCT",
    "MIN_DISTINCT_REPORTS",
]

#: relative spread (max-min)/median above which a group counts as inconsistent
SPREAD_FLAG_PCT = 5.0
#: groups whose operating point is unknown are only flagged above this spread.
#: Rationale: without a speed column the same number can legitimately belong to a
#: different design point, so the claim is judged at a coarser magnitude tier
#: (tier B) instead of being silently dropped or silently trusted.
SPREAD_FLAG_PCT_UNKNOWN_SPEED = 50.0
#: a single report may legitimately quote several values (e.g. a sweep); only
#: cross-report disagreement is a reproducibility defect
MIN_DISTINCT_REPORTS = 2

#: canonical quantity -> (header patterns, canonical unit)
QUANTITY_LEXICON: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "R_T": (("r_t", "总阻力", "阻力rt", "resistance r_t"), "kN"),
    "R_F": (("r_f", "摩擦阻力"), "kN"),
    "P_E": (("p_e", "有效功率"), "kW"),
    "P_D": (("p_d", "交付功率"), "kW"),
    "P_S": (("p_s", "轴功率"), "kW"),
    "GM": (("gm",), "m"),
    "KM": (("km",), "m"),
    "BM": (("bm",), "m"),
    "KB": (("kb",), "m"),
    "T33": (("t33", "垂荡周期"), "s"),
    "T55": (("t55", "纵摇周期"), "s"),
    "T44": (("t44", "横摇周期"), "s"),
    "ETA_D": (("η_d", "eta_d", "推进效率"), "-"),
    "K_T": (("k_t",), "-"),
    "J": (("前进比", "advance ratio"), "-"),
}

#: substrings that mark a *side-specific* or *reference* column -- never mixed in
SIDE_MARKERS = ("ref", "基准", "plat", "端点", "重构", "平台", "解析", "理论", "外推",
                "required", "需求", "内核", "标的", "参考")

#: geometry headers used to build a deterministic fallback case key.  A GM table
#: often identifies the hull only by its principal dimensions, so a row without a
#: hull name is keyed by its geometry signature instead of being guessed/dropped.
#: speed column headers; speed-dependent quantities are only compared at the same V
SPEED_HEADERS = ("v[kn]", "v [kn]", "v[kt]", "航速", "速度", "vs[kn]", "u[kn]")
SPEED_DEPENDENT = ("R_T", "R_F", "P_E", "P_D", "P_S", "K_T", "J")

#: headers of a plain value column in key/value result tables
VALUE_HEADERS = ("实测", "实测值", "数值", "结果", "计算值", "value", "measured")

DIM_RE = re.compile(r"^(l|b|t|d)(?=[\[\(\s_]|$)")
GEOM_ALIAS: Dict[str, Tuple[str, ...]] = {
    "L": ("lwl", "lpp", "l_pp", "loa"),
    "B": ("breadth",),
    "T": ("draft", "draught"),
    "D": ("depth",),
}

CASE_LEXICON: Tuple[str, ...] = (
    "KCS", "KVLCC2", "KVLCC1", "KVLCC", "JBC", "Wigley", "DTMB5415", "KRISO",
    "Aframax", "Feeder120", "Ferry", "SR-108", "SR108",
)

_NUM = re.compile(r"^[~\u223c\u2248]?\s*(-?\d+(?:\.\d+)?)")
_SEP_ROW = re.compile(r"^\|?[\s:|-]+\|?$")
_UNIT_ALIAS = {"kn": "kN", "kN": "kN", "kw": "kW", "KW": "kW", "m": "m", "s": "s",
               "deg": "deg", "°": "deg"}


@dataclass(frozen=True)
class Claim:
    report_id: Optional[int]
    case: str
    quantity: str
    value: float
    unit: str
    speed: Optional[float] = None      # operating point [kn], part of the group key
    row: str = ""                      # provenance snippet, never used for a verdict


@dataclass
class GroupFinding:
    case: str
    quantity: str
    unit: str
    speed: Optional[float] = None
    values: List[float] = field(default_factory=list)
    report_ids: List[Optional[int]] = field(default_factory=list)
    spread_pct: float = 0.0
    flagged: bool = False
    tier: str = "A"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["n_claims"] = len(self.values)
        d["speed_kn"] = self.speed
        d["tier"] = self.tier
        d["n_reports"] = len({r for r in self.report_ids if r is not None})
        d["min"] = min(self.values) if self.values else None
        d["max"] = max(self.values) if self.values else None
        d["median"] = statistics.median(self.values) if self.values else None
        return d


def _norm_header(text: str) -> str:
    t = text.strip().lower()
    t = t.replace("*", "").replace("`", "").replace(" ", "")
    return t


def map_quantity(header: str) -> Optional[str]:
    """Header cell -> canonical quantity, or None (unknown / side-specific)."""
    low = header.lower()
    for marker in SIDE_MARKERS:
        if marker in low:
            return None
    t = _norm_header(header)
    for canon, (pats, _unit) in QUANTITY_LEXICON.items():
        for p in pats:
            if p in t:
                return canon
    return None


def _cell_value(cell: str) -> Optional[float]:
    c = cell.strip()
    if not c or "±" in c or "+-" in c:
        return None
    c = c.replace(",", "")
    m = _NUM.match(c)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v


def _cell_unit(cell: str) -> str:
    c = cell.strip().lower()
    for alias, canon in _UNIT_ALIAS.items():
        if alias in c:
            return canon
    return ""


def geometry_case(header: Sequence[str], row: Sequence[str]) -> Optional[str]:
    """Deterministic fallback case key from principal dimensions, e.g. GEOM:100x20x8."""
    dims: Dict[str, float] = {}
    for idx, h in enumerate(header):
        if idx >= len(row):
            continue
        low = h.lower().replace(" ", "").replace("*", "").replace("`", "")
        m = DIM_RE.match(low)
        dim = m.group(1).upper() if m else None
        if dim is None:
            for d, pats in GEOM_ALIAS.items():
                if low.startswith(pats):
                    dim = d
                    break
        if dim and dim not in dims:
            v = _cell_value(row[idx])
            if v is not None:
                dims[dim] = v
    if "L" in dims and "B" in dims and "T" in dims:
        key = "GEOM:%gx%gx%g" % (dims["L"], dims["B"], dims["T"])
        if "D" in dims:
            key += "x%g" % dims["D"]
        return key
    return None


def parse_markdown_tables(md: str) -> List[Tuple[List[str], List[List[str]]]]:
    """Return [(header_cells, rows)] for every markdown table in ``md``."""
    tables: List[Tuple[List[str], List[List[str]]]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and _SEP_ROW.match(lines[i + 1].strip()):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows: List[List[str]] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


def case_from_text(text: str) -> Optional[str]:
    """First hull token appearing in ``text`` (report title / caption)."""
    up = (text or "").upper()
    best = None
    for tok in CASE_LEXICON:
        i = up.find(tok.upper())
        if i >= 0 and (best is None or i < best[0]):
            best = (i, tok)
    return best[1] if best else None


def row_speed(header: Sequence[str], row: Sequence[str]) -> Optional[float]:
    """Operating speed [kn] of a table row, taken from an explicit speed column."""
    for idx, h in enumerate(header):
        if idx >= len(row):
            continue
        low = h.lower().replace("*", "").replace("`", "")
        if any(p in low for p in SPEED_HEADERS):
            return _cell_value(row[idx])
    return None


def extract_claims(md: str, report_id: Optional[int] = None,
                   title: Optional[str] = None) -> List[Claim]:
    """Extract (case, quantity, value) claims from every table in the markdown.

    Two table shapes are supported:

    * wide tables -- quantity in the *header* (``| 船 | R_T [kN] |``);
    * key/value tables -- quantity in the *row label* and a plain value column
      (``| R_T | 1276.4 | ... |``).

    The case is taken from the row, else from the row's principal dimensions,
    else from the report title; if none of them identifies a hull the row is
    dropped rather than guessed.
    """
    claims: List[Claim] = []
    title_case = case_from_text(title or "")
    for header, rows in parse_markdown_tables(md):
        qcols: Dict[int, str] = {}
        for idx, h in enumerate(header):
            q = map_quantity(h)
            if q:
                qcols[idx] = q
        val_cols = [i for i, h in enumerate(header)
                    if any(v in h.lower() for v in VALUE_HEADERS)]
        if not qcols and not val_cols:
            continue
        for row in rows:
            # column alignment guard: a row whose cell count differs from the
            # header's cannot be mapped cell-for-cell, so it is dropped instead
            # of being read at a shifted column index (a measured failure mode).
            if len(row) != len(header):
                continue
            speed = row_speed(header, row)
            snippet = (" | ".join(row))[:160]
            # mode B: quantity named in the first cell, value in a value column
            if row:
                q_row = map_quantity(row[0])
                if q_row and val_cols:
                    case = None
                    for tok in CASE_LEXICON:
                        if tok.upper() in " ".join(row).upper():
                            case = tok
                            break
                    if not case:
                        case = geometry_case(header, row) or title_case
                    for idx in val_cols:
                        if idx >= len(row):
                            continue
                        val = _cell_value(row[idx])
                        if val is None:
                            continue
                        unit = _cell_unit(row[idx]) or QUANTITY_LEXICON[q_row][1]
                        if unit != QUANTITY_LEXICON[q_row][1]:
                            continue
                        if case:
                            claims.append(Claim(report_id, case, q_row, val, unit,
                                                speed, snippet))
                        break
                    continue
            case = None
            for cell in row:
                up = cell.upper()
                for tok in CASE_LEXICON:
                    if tok.upper() in up:
                        case = tok
                        break
                if case:
                    break
            if not case:
                case = geometry_case(header, row) or title_case
            if not case:
                continue
            for idx, q in qcols.items():
                if idx >= len(row):
                    continue
                val = _cell_value(row[idx])
                if val is None:
                    continue
                unit = _cell_unit(row[idx]) or QUANTITY_LEXICON[q][1]
                if unit != QUANTITY_LEXICON[q][1]:
                    # unit disagreeing with the quantity's canonical unit -> drop
                    continue
                if True:
                    claims.append(Claim(report_id, case, q, val, unit, speed, snippet))
    return claims


def group_claims(claims: Iterable[Claim],
                 spread_flag_pct: float = SPREAD_FLAG_PCT) -> List[GroupFinding]:
    buckets: Dict[Tuple[str, str, str, Optional[float]], GroupFinding] = {}
    for c in claims:
        key = (c.case, c.quantity, c.unit, c.speed)
        g = buckets.get(key)
        if g is None:
            g = buckets[key] = GroupFinding(c.case, c.quantity, c.unit, c.speed)
        g.values.append(c.value)
        g.report_ids.append(c.report_id)
    out: List[GroupFinding] = []
    for g in buckets.values():
        med = statistics.median(g.values)
        n_reports = len({r for r in g.report_ids if r is not None})
        if med != 0:
            g.spread_pct = (max(g.values) - min(g.values)) / abs(med) * 100.0
        else:
            g.spread_pct = 0.0 if max(g.values) == min(g.values) else float("inf")
        speed_blind = (g.speed is None and g.quantity in SPEED_DEPENDENT)
        limit = max(spread_flag_pct, SPREAD_FLAG_PCT_UNKNOWN_SPEED) if speed_blind else spread_flag_pct
        g.tier = "B" if speed_blind else "A"
        g.flagged = (g.spread_pct > limit and n_reports >= MIN_DISTINCT_REPORTS)
        out.append(g)
    out.sort(key=lambda x: (-x.spread_pct, x.case, x.quantity))
    return out


def audit_texts(texts: Dict[Optional[int], str],
                spread_flag_pct: float = SPREAD_FLAG_PCT) -> Tuple[List[Claim], List[GroupFinding]]:
    claims: List[Claim] = []
    for rid, md in texts.items():
        title = None
        if md.startswith("TITLE:: "):
            title, _, md = md.partition("\n")
            title = title[len("TITLE:: "):]
        claims.extend(extract_claims(md, rid, title))
    return claims, group_claims(claims, spread_flag_pct)


def _figures(groups: Sequence[GroupFinding], out_dir: str) -> List[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover - optional dependency
        return []
    os.makedirs(out_dir, exist_ok=True)
    paths: List[str] = []
    flagged = [g for g in groups if g.flagged][:12]
    if flagged:
        fig, ax = plt.subplots(figsize=(10, 0.45 * len(flagged) + 2))
        labels = ["%s %s" % (g.case, g.quantity) for g in flagged]
        ax.barh(range(len(flagged)), [g.spread_pct for g in flagged], color="#c0392b")
        ax.axvline(SPREAD_FLAG_PCT, color="k", ls="--", lw=1)
        ax.set_yticks(range(len(flagged)))
        ax.set_yticklabels(labels, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("cross-report spread  (max-min)/median  [%]")
        ax.set_title("Claimed value spread for the same case & quantity")
        p = os.path.join(out_dir, "fig1_spread.png")
        fig.tight_layout()
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    worst = [g for g in groups if g.flagged][:1]
    if worst:
        g = worst[0]
        fig, ax = plt.subplots(figsize=(7, 3.6))
        xs = [r if r is not None else -1 for r in g.report_ids]
        ax.scatter(xs, g.values, s=28, color="#2980b9")
        ax.axhline(statistics.median(g.values), color="k", ls="--", lw=1,
                   label="median %.4g %s" % (statistics.median(g.values), g.unit))
        ax.set_xlabel("report id")
        ax.set_ylabel("%s [%s]" % (g.quantity, g.unit))
        ax.set_title("Worst case: %s %s, spread %.1f%%" % (g.case, g.quantity, g.spread_pct))
        ax.legend(fontsize=8)
        p = os.path.join(out_dir, "fig2_worst_group.png")
        fig.tight_layout()
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


def _fetch_reports(base: str, user: str, password: str, limit: int) -> Dict[int, str]:
    import urllib.request

    def _req(path, payload=None, token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(base.rstrip("/") + path, data=data,
                                   method="POST" if data else "GET")
        r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(r, timeout=90) as resp:
            return json.loads(resp.read().decode())

    tok = _req("/auth/login", {"username": user, "password": password})["access_token"]
    lst = _req("/tensor/reports?limit=%d" % limit, token=tok)
    items = lst if isinstance(lst, list) else (lst.get("items") or lst.get("reports") or [])
    texts: Dict[int, str] = {}
    for it in items:
        rid = it.get("id")
        if rid is None:
            continue
        try:
            full = _req("/tensor/reports/%d" % int(rid), token=tok)
        except Exception:
            continue
        title = full.get("title") or it.get("title") or ""
        texts[int(rid)] = "TITLE:: " + title + "\n" + (full.get("markdown") or "")
    return texts


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-api", action="store_true")
    ap.add_argument("--from-json", help="offline snapshot {report_id: markdown}")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--out", default="artifacts/report_claim_consistency")
    ap.add_argument("--spread", type=float, default=SPREAD_FLAG_PCT)
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    if args.from_json:
        with open(args.from_json) as f:
            raw = json.load(f)
        texts = {int(k): v for k, v in raw.items()}
    elif args.from_api:
        texts = _fetch_reports(os.environ["TENSOR_API_BASE"],
                               os.environ["TENSOR_AGENT_USER"],
                               os.environ["TENSOR_AGENT_PASS"], args.limit)
    else:
        ap.error("need --from-api or --from-json")

    os.makedirs(args.out, exist_ok=True)
    claims, groups = audit_texts(texts, args.spread)
    flagged = [g for g in groups if g.flagged]
    summary = {
        "n_reports": len(texts),
        "n_claims": len(claims),
        "n_groups": len(groups),
        "n_groups_comparable": sum(1 for g in groups
                                   if len({r for r in g.report_ids if r is not None}) >= MIN_DISTINCT_REPORTS),
        "n_flagged": len(flagged),
        "spread_flag_pct": args.spread,
        "flagged": [g.to_dict() for g in flagged],
        "groups": [g.to_dict() for g in groups],
    }
    with open(os.path.join(args.out, "claims.json"), "w") as f:
        json.dump([asdict(c) for c in claims], f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "reports_snapshot.json"), "w") as f:
        json.dump({str(k): v for k, v in texts.items()}, f, ensure_ascii=False)
    if args.figures:
        summary["figures"] = _figures(groups, args.out)
        with open(os.path.join(args.out, "summary.json"), "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: summary[k] for k in
                      ("n_reports", "n_claims", "n_groups", "n_groups_comparable",
                       "n_flagged")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())