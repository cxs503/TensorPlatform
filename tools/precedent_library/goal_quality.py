#!/usr/bin/env python3
"""Goal-quality scorer + before/after samples for the precedent injection (IN24-E3).

Why this exists
---------------
"Let every mission see what good looks like before it starts" is only a claim until
it is measurable. This module turns mission-goal quality into eight machine-checkable
properties and scores real historical goals (before) against the same goal text with
the precedent injection appended (after). The comparison is the evidence that the
injection channel carries signal, not just words.

The eight checks are deliberately **generic** (no discipline vocabulary): a goal is
good when it is falsifiable and lands artifacts, regardless of which physics field
it belongs to. Domain wording is injected as data elsewhere.

Known limitation (stated, not papered over)
-------------------------------------------
- The scorer reads surface structure (regex properties). It cannot tell whether a
  cited baseline is the *right* baseline; that stays with independent review.
- "after" samples are deterministic re-writes: the same historical goal plus the
  injected rules. They measure the channel's expressive uplift, not delivered work.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DATA_DIR = Path(__file__).with_name("data")
SAMPLES_PATH = DATA_DIR / "goal_quality_samples.json"

#: Property -> regex. Order is presentation order, all weights equal.
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("quant_criteria", r"\d+(?:\.\d+)?\s*(?:%|‰|s\b|ms\b|min\b|h\b|kN\b|N\b|kg\b|Hz\b|dB\b|KB\b|MB\b|GB\b|°|个|条|项|行|次|倍|台|核|层|门)", "量化判据（带数字+单位/比例）"),
    ("artifact_paths", r"(?:artifacts|docs|tests|runs|src|tools|scripts)/[\w./\-]+", "交付工件路径可定位"),
    ("verification_cmd", r"(?:pytest|python3\s|make\s|curl\s|npm\s|cargo\s|bash\s)", "验证命令/测试可重跑"),
    ("baseline_ref", r"(?:基线|baseline|参考值|对照|对比|基准|vs\.?\s)", "基线/参考值对照"),
    ("honest_fail", r"(?:honest[-\s]?FAIL|如实|不可覆盖|partial[-\s]?FAIL)", "honest FAIL 纪律"),
    ("discipline_worktree", r"(?:worktree|分支|branch|EDIT_WHITELIST)", "分支/工作树纪律"),
    ("report_deliverable", r"(?:报告|report|manuscript|论文)", "报告交付要求"),
    ("no_duplicate_frontier", r"(?:全新|前沿|禁止重复|不重复|未做过|never\s+done)", "不重复既定案例的前沿声明"),
)

#: Score thresholds used to name the band (documented, not tunable per case).
BANDS: tuple[tuple[float, str], ...] = ((0.875, "excellent"), (0.625, "adequate"), (0.0, "weak"))


def _hits(text: str) -> dict[str, bool]:
    return {name: bool(re.search(pattern, text, flags=re.IGNORECASE)) for name, pattern, _ in CHECKS}


def score_goal(goal: str) -> dict[str, Any]:
    """Score one goal on the eight falsifiability properties."""
    text = goal or ""
    hits = _hits(text)
    passed = sum(hits.values())
    score = round(passed / len(CHECKS), 4)
    band = next(label for threshold, label in BANDS if score >= threshold)
    return {
        "score": score,
        "band": band,
        "passed": passed,
        "total": len(CHECKS),
        "checks": hits,
        "missing": [name for name, ok in hits.items() if not ok],
        "chars": len(text),
    }


def injection_suffix(lines: Sequence[str]) -> str:
    """The block appended to a goal so the mission sees the distilled rules."""
    if not lines:
        return ""
    return "\n\n【开工前必读·S 级判例注入】\n" + "\n".join(lines)


def build_comparison(samples: Sequence[Mapping[str, Any]], lines: Sequence[str]) -> dict[str, Any]:
    """Pair each before-sample with the same goal + injection; compute the delta.

    Samples are data (real mission goal excerpts). ``lines`` come from the precedent
    library, so the comparison cannot drift from the injected content.
    """
    rows: list[dict[str, Any]] = []
    for sample in samples:
        before = str(sample.get("goal", ""))
        after = before + injection_suffix(lines)
        b, a = score_goal(before), score_goal(after)
        rows.append({
            "sample_id": sample.get("sample_id"),
            "source": sample.get("source"),
            "kind": sample.get("kind", "before-after"),
            "before": b,
            "after": a,
            "delta": round(a["score"] - b["score"], 4),
            "recovered_checks": sorted(
                k for k, ok in a["checks"].items() if ok and not b["checks"][k]
            ),
        })
    deltas = [r["delta"] for r in rows]
    return {
        "rows": rows,
        "summary": {
            "n": len(rows),
            "mean_before": round(sum(r["before"]["score"] for r in rows) / len(rows), 4) if rows else 0.0,
            "mean_after": round(sum(r["after"]["score"] for r in rows) / len(rows), 4) if rows else 0.0,
            "mean_delta": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
            "min_delta": min(deltas) if deltas else 0.0,
            "max_delta": max(deltas) if deltas else 0.0,
            "channel": "PLAYBOOK append-only (<=120 char/line)",
        },
    }


def load_samples(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else SAMPLES_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return list(raw.get("samples", []))
    return list(raw)


def load_lines_from_library(path: str | Path | None = None) -> list[str]:
    from tools.precedent_library.library import load_cases, render_playbook_block

    meta, cases = load_cases(path)
    return render_playbook_block(cases, gate_line=meta.get("injection_gate_line"))


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI sugar
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/precedent_library/goal_quality_comparison.json")
    args = parser.parse_args(argv)
    comparison = build_comparison(load_samples(), load_lines_from_library())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(comparison["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())