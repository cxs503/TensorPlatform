#!/usr/bin/env python3
"""Capability-card evidence-chain checker (E1-E7) — mission 1819 / infra 席.

Problem it closes (measured, see docs/audits/capability_card_evidence_chain.md):
all five cards in `docs/capability_registry/cards/` carried evidence that could
not be resolved by anybody:

  * `report_id` was a free-text label ("suboff_sail", "e2e1", "wave10_multiphase",
    "dtmb5415_geometry", "report_gate_v1") — not a single one is a platform report id;
  * `report_md5` was a syntactically valid but *invented* 32-hex string, admitted by
    the card's own note ("Placeholder: report not yet frozen");
  * 3 cards still declared `verification_status: PASS` on top of that.

The previous gate only asserted `re.fullmatch("[a-fA-F0-9]{32}", md5)` — a
tautology: any fabricated digest passes.  This module replaces it with checks
that are *falsifiable*: every anchor digest is recomputed from a pinned copy of
the report body, and every claim must be quoted verbatim from that body.

Checks
------
E1 registry_integrity   index.json <-> cards on disk (ids, count, dup, repo/name drift)
E2 anchor_resolvable    anchor_type in {platform_report, repo_file}; report id numeric
                        and present in the pinned snapshot; repo_file path exists
E3 anchor_digest        sha256/md5 recomputed from the pinned bytes must equal the card
E4 placeholder_anchor   no placeholder wording / sentinel digest anywhere in the anchor
E5 overclaim            PASS requires E2+E3 green *and* a quote that does not assert failure
E6 tolerance            |relative_error_pct| <= tolerance_pct whenever status is PASS
E7 quote_hit            quote_match must verbatim-hit the anchored text; line digest must
                        match (transcription / memory / hallucination cannot pass)

Offline and deterministic: reads only files inside the worktree.  The report
bodies come from `tests/fixtures/capability_evidence/reports_snapshot.json`,
which is written exclusively by `tools/audit/pin_capability_evidence.py`.

Usage:
    python3 tools/audit/capability_card_evidence.py --check
    python3 tools/audit/capability_card_evidence.py --check --json out.json --figure fig.png
Exit code 1 when any violation is found, so it can be used as a CI gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARDS_DIR = ROOT / "docs" / "capability_registry" / "cards"
INDEX = ROOT / "docs" / "capability_registry" / "index.json"
SNAPSHOT = ROOT / "tests" / "fixtures" / "capability_evidence" / "reports_snapshot.json"

PASS_STATES = {"PASS", "VERIFIED"}
# A quote that itself reports a failure/overrun may never coexist with PASS.
FAILURE_QUOTE = re.compile(r"是否\s*<3%\s*[:：]\s*否|不可接受|超.{0,4}15%|\bFAIL\b|❌")
PLACEHOLDER = re.compile(r"placeholder|占位|TODO|TBD|待补|not yet frozen", re.I)
SENTINEL_DIGESTS = {
    "0" * 32,
    "0" * 64,
    "f" * 32,
    "f" * 64,
}
ANCHOR_TYPES = {"platform_report", "repo_file"}


def line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def load_cards(cards_dir: Path = CARDS_DIR) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for f in sorted(cards_dir.glob("*.json")):
        card = json.loads(f.read_text(encoding="utf-8"))
        card["__file__"] = f.name
        cards[card.get("card_id", f.stem)] = card
    return cards


def anchored_text(card: dict, snapshot: dict, root: Path) -> tuple[str | None, str]:
    """Return (text, reason). text is None when the anchor cannot be resolved."""
    ve = card.get("validation_evidence") or {}
    atype = ve.get("anchor_type")
    if atype == "platform_report":
        rid = str(ve.get("report_id", ""))
        rec = (snapshot.get("reports") or {}).get(rid)
        if rec is None:
            return None, f"report_id={rid!r} not in pinned snapshot"
        return rec["body"], f"pinned report {rid}"
    if atype == "repo_file":
        path = ve.get("anchor_path")
        if not path:
            return None, "anchor_path missing"
        p = root / path
        if not p.exists():
            return None, f"anchor_path {path!r} missing on disk"
        return p.read_text(encoding="utf-8"), f"repo file {path}"
    return None, f"unknown anchor_type={atype!r}"


def check(cards: dict[str, dict], index: dict, snapshot: dict, root: Path = ROOT) -> tuple[dict, list[str]]:
    violations: list[str] = []
    per_card: dict[str, dict] = {}

    # ── E1 registry <-> disk ────────────────────────────────────────────
    listed = index.get("cards") or []
    listed_ids = [c.get("card_id") for c in listed]
    if len(listed_ids) != len(set(listed_ids)):
        violations.append(f"E1 registry lists duplicate card_ids: {listed_ids}")
    if set(listed_ids) != set(cards):
        missing = sorted(set(cards) - set(listed_ids))
        extra = sorted(set(listed_ids) - set(cards))
        violations.append(f"E1 registry/disk mismatch: only_disk={missing} only_registry={extra}")
    if index.get("total_cards") not in (None, len(cards)):
        violations.append(f"E1 total_cards={index.get('total_cards')} != {len(cards)} cards on disk")
    for ent in listed:
        cid = ent.get("card_id")
        card = cards.get(cid)
        if not card:
            continue
        if ent.get("repo") and card.get("repo") and ent["repo"] != card["repo"]:
            violations.append(f"E1 {cid} repo drift: registry={ent['repo']} card={card['repo']}")
        if ent.get("name") and card.get("name") and ent["name"] != card["name"]:
            violations.append(f"E1 {cid} name drift: registry={ent['name']!r} card={card['name']!r}")
        card_status = (card.get("validation_evidence") or {}).get("verification_status")
        if ent.get("verification_status") != card_status:
            violations.append(
                f"E1 {cid} verification_status drift: registry={ent.get('verification_status')!r} card={card_status!r}"
            )

    # ── E2..E7 per card ─────────────────────────────────────────────────
    for cid, card in sorted(cards.items()):
        ve = card.get("validation_evidence") or {}
        status = ve.get("verification_status")
        atype = ve.get("anchor_type")
        text, reason = anchored_text(card, snapshot, root)
        row = {
            "card_id": cid,
            "file": card.get("__file__"),
            "anchor_type": atype,
            "report_id": ve.get("report_id"),
            "status": status,
            "anchor_reason": reason,
            "checks": {},
        }
        c = row["checks"]

        # E2 resolvable anchor.  An honest "we have no evidence" card is allowed,
        # but only as an explicit, documented empty anchor that may not claim PASS.
        declared_empty = atype == "none"
        if declared_empty:
            ok = bool(str(ve.get("gap_reason") or "").strip()) and status not in PASS_STATES
            e2 = ok
            if not ok:
                violations.append(
                    f"E2 {cid} anchor_type=none requires a gap_reason and a non-passing status "
                    f"(got status={status!r}, gap_reason={'yes' if ve.get('gap_reason') else 'no'})"
                )
        else:
            e2 = atype in ANCHOR_TYPES and text is not None
            if atype == "platform_report":
                rid = str(ve.get("report_id", ""))
                if not re.fullmatch(r"\d+", rid):
                    e2 = False
                    violations.append(f"E2 {cid} report_id={rid!r} is not a numeric platform report id")
            if not e2:
                violations.append(f"E2 {cid} anchor unresolvable ({reason})")
        c["E2"] = e2

        # E3 deterministic digest
        e3 = "n/a" if declared_empty else False
        if declared_empty:
            pass
        elif text is not None:
            body = text.encode("utf-8")
            want_sha = ve.get("report_body_sha256")
            want_md5 = ve.get("report_md5")
            got_sha, got_md5 = hashlib.sha256(body).hexdigest(), hashlib.md5(body).hexdigest()
            e3 = got_sha == want_sha and got_md5 == want_md5
            if not e3:
                violations.append(
                    f"E3 {cid} digest mismatch: card(sha={str(want_sha)[:12]},md5={str(want_md5)[:12]}) "
                    f"!= recomputed(sha={got_sha[:12]},md5={got_md5[:12]})"
                )
            row["body_sha256"] = got_sha
            row["body_md5"] = got_md5
        else:
            violations.append(f"E3 {cid} digest not verifiable: anchor unresolvable ({reason})")
        c["E3"] = e3

        # E4 placeholder anchor (no anchor -> nothing can be a placeholder)
        e4 = "n/a" if declared_empty else True
        blob = "{}" if declared_empty else json.dumps(ve, ensure_ascii=False)
        m = PLACEHOLDER.search(blob)
        if m:
            e4 = False
            violations.append(f"E4 {cid} placeholder wording in anchor: {m.group(0)!r}")
        for key in ("report_md5", "report_body_sha256"):
            if str(ve.get(key, "")).lower() in SENTINEL_DIGESTS:
                e4 = False
                violations.append(f"E4 {cid} sentinel digest in {key}")
        c["E4"] = e4

        # E7 quote hit (computed before E5, E5 consumes it)
        quote = ve.get("quote_match")
        e7 = "n/a" if declared_empty else False
        if declared_empty:
            pass
        elif not quote:
            violations.append(f"E7 {cid} missing quote_match (claim without a verbatim quote)")
        elif text is not None:
            hit = [ln for ln in text.split("\n") if re.search(quote, ln)]
            if not hit:
                violations.append(f"E7 {cid} quote_match={quote!r} does not hit anchored text")
            elif ve.get("quote_line_sha256") != line_sha256(hit[0]):
                violations.append(
                    f"E7 {cid} quote_line_sha256 drift: card={ve.get('quote_line_sha256')!r} "
                    f"recomputed={line_sha256(hit[0])!r}"
                )
            else:
                e7 = True
                row["quote_hit"] = hit[0].strip()[:120]
        else:
            violations.append(f"E7 {cid} quote not verifiable: anchor unresolvable ({reason})")
        c["E7"] = e7

        # E6 tolerance consistency
        err, tol = ve.get("relative_error_pct"), ve.get("tolerance_pct")
        e6 = True
        if err is not None and tol is not None and status in PASS_STATES and abs(float(err)) > float(tol):
            e6 = False
            violations.append(f"E6 {cid} |error|={err}% > tolerance={tol}% while status={status}")
        c["E6"] = e6

        # E5 overclaim
        e5 = True
        if status in PASS_STATES:
            if not all(x is True for x in (e2, e3, e7)):
                e5 = False
                violations.append(f"E5 {cid} OVERCLAIM: status={status} but evidence anchor E2/E3/E7 not green")
            elif isinstance(quote, str) and FAILURE_QUOTE.search(row.get("quote_hit", "")):
                e5 = False
                violations.append(
                    f"E5 {cid} OVERCLAIM: status={status} while the quoted evidence asserts failure "
                    f"({row.get('quote_hit', '')[:60]!r})"
                )
        c["E5"] = e5
        if declared_empty:
            row["verdict"] = "DECLARED_EMPTY"
        elif all(x is True for x in (e2, e3, e4, e7)):
            row["verdict"] = "ANCHORED"
        else:
            row["verdict"] = "UNVERIFIABLE"
        per_card[cid] = row

    declared = {cid: (c.get("validation_evidence") or {}).get("verification_status") for cid, c in cards.items()}
    result = {
        "schema": "capability-card-evidence/v1",
        "cards_checked": len(cards),
        "checks": ["E1", "E2", "E3", "E4", "E5", "E6", "E7"],
        "summary": {
            "violations": len(violations),
            "anchored": sum(1 for r in per_card.values() if r["verdict"] == "ANCHORED"),
            "declared_empty": sum(1 for r in per_card.values() if r["verdict"] == "DECLARED_EMPTY"),
            "unverifiable": sum(1 for r in per_card.values() if r["verdict"] == "UNVERIFIABLE"),
            "pass_claims": sum(1 for s in declared.values() if s in PASS_STATES),
            "pass_claims_supported": sum(
                1
                for cid, s in declared.items()
                if s in PASS_STATES and per_card[cid]["verdict"] == "ANCHORED" and per_card[cid]["checks"]["E5"] is True
            ),
        },
        "cards": per_card,
        "violations": violations,
    }
    return result, violations


def figure(result: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    cards = sorted(result["cards"])
    per_card_codes = [k for k in result["checks"] if k in result["cards"][cards[0]]["checks"]]
    e1_ok = not any(v.startswith("E1") for v in result["violations"])
    codes = ["E1"] + per_card_codes
    def val(x):
        return 1.0 if x is True else (0.55 if x == "n/a" else 0.0)

    grid = np.array(
        [[1.0 if e1_ok else 0.0] + [val(result["cards"][c]["checks"][k]) for k in per_card_codes] for c in cards]
    )
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(codes)), codes)
    ax.set_yticks(range(len(cards)), cards)
    labels = {(1.0, 0.0): "FAIL", (0.0, 0.0): "FAIL", (1.0, 1.0): "PASS", (0.55, 1.0): "n/a"}
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            ax.text(j, i, labels.get((round(grid[i, j], 2), 1.0 if i >= 0 else 0.0), "?"), ha="center", va="center", fontsize=8)
    ax.set_title(
        f"Capability-card evidence chain (E1-E7): {result['summary']['anchored']}/{result['cards_checked']} anchored, "
        f"{result['summary']['violations']} violations"
    )
    ax.set_xlabel("check")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"figure -> {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--figure", type=Path)
    a = ap.parse_args(argv)
    cards = load_cards()
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    result, violations = check(cards, index, snapshot)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"json -> {a.json}")
    if a.figure:
        figure(result, a.figure)
    s = result["summary"]
    print(
        f"cards={result['cards_checked']} anchored={s['anchored']} unverifiable={s['unverifiable']} "
        f"pass_claims={s['pass_claims']} supported={s['pass_claims_supported']} violations={s['violations']}"
    )
    for v in violations:
        print("  !", v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())