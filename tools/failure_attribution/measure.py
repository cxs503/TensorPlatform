"""KPI-SUCCESS-90 measurement: mission success rate + automatic failure attribution.

Two numbers, one command:

* **success rate** — ``succeeded / (succeeded + failed)`` over terminal missions,
  all-time and over a rolling window (default 7 days, from the config).
* **attribution coverage / accuracy** — how many failed missions get a
  machine-readable ``(stage, kind)``, and how well the text-only rules recover
  the declared stages that exist.

Input is a frozen CSV snapshot (``export.sql``) so the number is reproducible
from one file: the CSV sha256 goes into the result as ``snapshot_sha256``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .engine import (
    AttributionConfig,
    Mission,
    emit_backfill_sql,
    evaluate,
    load_config,
    mission_from_row,
    rule_stats,
)

TERMINAL = ("succeeded", "failed", "stopped", "revoked")


def _parse_ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_missions(path: Path | str) -> List[Dict[str, Any]]:
    """Read the CSV snapshot into plain dict rows."""
    rows: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(dict(row))
    return rows


def snapshot_digest(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_as_of(rows: Sequence[Dict[str, Any]]) -> datetime:
    """Deterministic anchor: newest terminal timestamp present in the snapshot.

    Using wall-clock time would make the rolling window drift between two runs of
    the *same* snapshot; deriving it from the data keeps reproduction
    byte-identical.
    """
    stamps = [
        _parse_ts(r.get("finished_at")) or _parse_ts(r.get("created_at"))
        for r in rows
    ]
    stamps = [t for t in stamps if t is not None]
    return max(stamps) if stamps else datetime.now(timezone.utc)


def success_metrics(
    rows: Sequence[Dict[str, Any]],
    *,
    as_of: Optional[datetime] = None,
    window_days: int = 7,
) -> Dict[str, Any]:
    """Compute success rate on all-time and rolling-window cohorts.

    Primary ratio = ``succeeded / (succeeded + failed)``. ``stopped``/``revoked``
    are deliberately excluded from the primary ratio (operator actions, not
    agent outcomes) but reported in the strict denominator for transparency.
    """
    as_of = as_of or datetime.now(timezone.utc)
    cutoff = as_of - timedelta(days=window_days)

    def _cohort(sel: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for r in sel:
            status = (r.get("status") or "").strip()
            counts[status] = counts.get(status, 0) + 1
        succeeded = counts.get("succeeded", 0)
        failed = counts.get("failed", 0)
        denom = succeeded + failed
        strict_denom = sum(counts.get(s, 0) for s in TERMINAL)
        return {
            "counts": dict(sorted(counts.items())),
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": round(succeeded / denom, 6) if denom else None,
            "strict_success_rate": round(succeeded / strict_denom, 6) if strict_denom else None,
            "terminal_in_ratio": denom,
        }

    terminal = [r for r in rows if (r.get("status") or "") in TERMINAL]
    window = [
        r
        for r in terminal
        if (_parse_ts(r.get("finished_at")) or _parse_ts(r.get("created_at"))) is not None
        and (_parse_ts(r.get("finished_at")) or _parse_ts(r.get("created_at"))) >= cutoff
    ]
    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "window_days": window_days,
        "window_start": cutoff.isoformat(),
        "all_time": _cohort(terminal),
        f"last_{window_days}d": _cohort(window),
    }


def attribute_failures(
    rows: Sequence[Dict[str, Any]],
    config: AttributionConfig,
) -> Dict[str, Any]:
    """Attribute every failed mission; report coverage + two accuracy arms."""
    failed = [r for r in rows if (r.get("status") or "") == "failed"]
    missions: List[Mission] = [mission_from_row(r, config) for r in failed]
    masked = evaluate(missions, config, mask_declared=True)
    unmasked = evaluate(missions, config, mask_declared=False)
    return {
        "failed_total": len(failed),
        "declared_present": sum(1 for m in missions if m.declared_stage),
        "declared_coverage": round(
            sum(1 for m in missions if m.declared_stage) / len(missions), 6
        )
        if missions
        else None,
        "masked": {k: v for k, v in masked.items() if k != "attributions"},
        "unmasked": {k: v for k, v in unmasked.items() if k != "attributions"},
        "attributions": masked["attributions"],
    }


def incident_cohesion(
    attributions: Sequence[Dict[str, Any]],
    rows: Sequence[Dict[str, Any]],
    *,
    min_bucket: int = 100,
) -> Dict[str, Any]:
    """Corroborate inferred buckets by *time clustering* (independent of text).

    For every inferred ``kind`` with at least ``min_bucket`` rows, compute the
    share of that bucket landing on its two busiest days and compare with the
    share the same two days hold among all failed missions (``lift``). A shared
    infrastructure cause (e.g. one disk-full incident) shows up as a high
    concentration and a large lift; diffuse agent-level causes do not.

    This is **corroboration, not ground truth** — it cannot label a single row.
    """
    day_of = {
        str(r.get("id")): str(r.get("created_at") or r.get("finished_at") or "")[:10]
        for r in rows
    }
    all_days: Dict[str, int] = {}
    for day in day_of.values():
        if day:
            all_days[day] = all_days.get(day, 0) + 1
    n_all = sum(all_days.values()) or 1

    buckets: Dict[str, List[str]] = {}
    for a in attributions:
        if a["source"] == "inferred":
            buckets.setdefault(a["kind"], []).append(str(a["mission_id"]))

    out: Dict[str, Any] = {}
    for kind, ids in buckets.items():
        if len(ids) < min_bucket:
            continue
        days: Dict[str, int] = {}
        for mid in ids:
            d = day_of.get(mid, "")
            if d:
                days[d] = days.get(d, 0) + 1
        top2 = sorted(days.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
        top2_days = [d for d, _ in top2]
        share = sum(c for _, c in top2) / len(ids) if ids else 0.0
        baseline = sum(all_days.get(d, 0) for d in top2_days) / n_all
        out[kind] = {
            "n": len(ids),
            "top_days": top2_days,
            "top2_share": round(share, 6),
            "baseline_share": round(baseline, 6),
            "lift": round(share / baseline, 3) if baseline else None,
            "corroborated": bool(share >= 0.5 and baseline and share / baseline >= 2.0),
        }
    return out


def trusted_view(
    attributions: Sequence[Dict[str, Any]],
    stats: Dict[str, Dict[str, Any]],
    *,
    min_precision: float,
    min_support: int,
) -> Dict[str, Any]:
    """Split attributions into trusted vs unvalidated inference.

    A ``declared`` stage is trusted by construction. An ``inferred`` one is
    trusted only if its rule clears the measured-precision gate on the labelled
    cohort (``support >= min_support`` and ``precision >= min_precision``);
    rules that never fired on labelled data are **unvalidated, not passing**.
    """
    trusted: List[Dict[str, Any]] = []
    unvalidated: List[Dict[str, Any]] = []
    for a in attributions:
        if a["source"] == "declared":
            trusted.append(a)
            continue
        if a["source"] != "inferred":
            continue
        s = stats.get(a["rule_id"] or "")
        ok = bool(s) and s["support"] >= min_support and (s["precision"] or 0) >= min_precision
        (trusted if ok else unvalidated).append(a)
    n = len(attributions)
    return {
        "trusted": len(trusted),
        "trusted_inferred": sum(1 for a in trusted if a["source"] == "inferred"),
        "unvalidated_inferred": len(unvalidated),
        "unknown": n - len(trusted) - len(unvalidated),
        "trusted_coverage": round(len(trusted) / n, 6) if n else None,
        "min_precision": min_precision,
        "min_support": min_support,
        "attributions": trusted,
    }


def blocked_pp(success_rate: Optional[float], target: float) -> Optional[float]:
    """Gap to target in percentage points (positive = below target)."""
    if success_rate is None:
        return None
    return round((target - success_rate) * 100, 2)


def run(
    missions_csv: Path | str,
    outdir: Path | str,
    *,
    config_path: Optional[Path | str] = None,
    as_of: Optional[datetime] = None,
    emit_sql: bool = True,
    sample_rows: int = 500,
) -> Dict[str, Any]:
    config = load_config(config_path)
    rows = load_missions(missions_csv)
    as_of = as_of or snapshot_as_of(rows)
    su = success_metrics(rows, as_of=as_of, window_days=config.window_days)
    att = attribute_failures(rows, config)

    stats = rule_stats([mission_from_row(r, config) for r in rows], config)
    trusted = trusted_view(
        att["attributions"],
        stats,
        min_precision=config.targets["min_rule_precision"],
        min_support=int(config.targets["min_rule_support"]),
    )
    key = f"last_{config.window_days}d"
    target = config.targets["success_rate_target"]
    result: Dict[str, Any] = {
        "kpi": "KPI-SUCCESS-90",
        "schema_version": config.schema_version,
        "snapshot_sha256": snapshot_digest(missions_csv),
        "snapshot_rows": len(rows),
        "targets": config.targets,
        "success": su,
        "attribution": {k: v for k, v in att.items() if k != "attributions"},
        "trusted_attribution": {k: v for k, v in trusted.items() if k != "attributions"},
        "rule_precision": stats,
        "incident_cohesion": incident_cohesion(att["attributions"], rows),
        "gap_pp": {
            "all_time": blocked_pp(su["all_time"]["success_rate"], target),
            key: blocked_pp(su[key]["success_rate"], target),
        },
        "criteria": {
            "success_rate_ge_target_all_time": (
                su["all_time"]["success_rate"] is not None
                and su["all_time"]["success_rate"] >= target
            ),
            f"success_rate_ge_target_{key}": (
                su[key]["success_rate"] is not None and su[key]["success_rate"] >= target
            ),
            "attribution_coverage_ge_target": (
                att["masked"]["coverage"] is not None
                and att["masked"]["coverage"] >= config.targets["coverage_target"]
            ),
            "trusted_coverage_ge_target": (
                trusted["trusted_coverage"] is not None
                and trusted["trusted_coverage"] >= config.targets["coverage_target"]
            ),
            "heldout_stage_accuracy_ge_target": (
                att["masked"]["heldout"]["stage_accuracy"] is not None
                and att["masked"]["heldout"]["stage_accuracy"] >= config.targets["accuracy_target"]
            ),
            "unknown_rate_le_target": (
                att["masked"]["unknown_rate"] is not None
                and att["masked"]["unknown_rate"] <= config.targets["unknown_rate_max"]
            ),
        },
    }

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # bucket table (csv) + attribution sample (csv) — both derived, no hand edits
    with open(out / "failure_buckets.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["stage", "kind", "count"])
        by_kind = att["masked"]["by_kind"]
        stage_of = {
            k: s for s, kinds in config.taxonomy.items() for k in kinds
        }
        for kind, count in by_kind.items():
            w.writerow([stage_of.get(kind, "unknown"), kind, count])

    with open(out / "rule_precision.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rule_id", "pred_stage", "support", "correct", "precision"])
        for rid, row in stats.items():
            w.writerow([rid, row["pred_stage"], row["support"], row["correct"], row["precision"]])

    sample = att["attributions"][:sample_rows]
    with open(out / "attribution_sample.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "mission_id", "stage", "kind", "rule_id", "field", "source",
                "confidence", "snippet",
            ],
        )
        w.writeheader()
        for row in sample:
            w.writerow(row)

    if emit_sql:
        from .engine import Attribution  # local import keeps engine standalone

        sql = emit_backfill_sql([Attribution(**a) for a in trusted["attributions"]])
        (out / "backfill_fail_stage.sql").write_text(sql, encoding="utf-8")
        result["backfill_sql_statements"] = sql.count("UPDATE ")
        (out / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return result