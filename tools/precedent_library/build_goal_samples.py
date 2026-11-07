#!/usr/bin/env python3
"""Regenerate ``data/goal_quality_samples.json`` from the control plane (IN24-E3).

The samples are *real* mission goals, quoted verbatim (truncated to an excerpt with a
recorded length) so the before/after comparison cannot be tuned by hand. Re-running
this script is the provenance: mission ids + fetch timestamp are stored next to the
text.

    python3 -m tools.precedent_library.build_goal_samples
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = Path(__file__).with_name("data") / "goal_quality_samples.json"
EXCERPT_CHARS = 1400

#: (mission id, role). Roles are labels for the comparison table, nothing more.
PICKS: tuple[tuple[int, str], ...] = (
    (5118, "metrology benchmark, honest-FAIL declared"),
    (5101, "truth-surface metric with criteria table"),
    (5121, "read-only aggregation endpoint"),
    (5047, "failed: no finish, no commit (anti-pattern)"),
)


def _api():
    sys.path.insert(0, str(REPO_ROOT / "agent_work"))
    import tensor_api  # noqa: WPS433 - repo-local helper

    return tensor_api


def main() -> int:
    api = _api()
    token = api.get_token()
    samples = []
    for mid, role in PICKS:
        raw = api.call(f"tensor/missions/{mid}", token=token)
        mission = raw.get("mission") or raw
        goal = (mission.get("goal") or "").strip()
        status = mission.get("status")
        samples.append({
            "sample_id": f"m{mid}",
            "source": f"tensor/missions/{mid}",
            "mission_status": status,
            "role": role,
            "goal_chars_total": len(goal),
            "goal": goal[:EXCERPT_CHARS],
        })
    payload = {
        "schema_version": "e3-goal-samples-v1",
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "excerpt_chars": EXCERPT_CHARS,
        "note": "Goals are quoted verbatim from the mission table; 'after' variants are built at "
                "scoring time by appending the precedent injection block (deterministic).",
        "samples": samples,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(DATA_PATH), "n": len(samples)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())