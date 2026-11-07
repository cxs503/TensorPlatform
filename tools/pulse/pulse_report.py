#!/usr/bin/env python3
"""ASCII trend report over the last N saturation samples.

Read-only consumer of ``saturation_pulse``: renders the three-state load
(online / busy / queued) plus held nodes as sparklines and a table, so the
autoregressive loop can *see* the pulse without a BI stack.

    python3 -m tools.pulse.pulse_report --limit 20
    python3 -m tools.pulse.pulse_report --limit 20 --json

Rows written before IN15-O1 carry free text in ``note`` (e.g.
``'online=43 busy=34 queued=2 idle=7 held16=11'``).  Those rows are reported with
``source=legacy`` and only their three integer columns are used -- the report
never invents a missing queued value.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tools.pulse.saturation_pulse import (
    TABLE,
    PulseError,
    PulseInputError,
    connect,
    open_read_snapshot,
    resolve_dsn,
)

SPARK_CHARS = " ▁▂▃▄▅▆▇█"

SELECT_SQL = (
    f"SELECT ts, online, busy, held, released, note FROM {TABLE} "
    "ORDER BY ts DESC LIMIT %s"
)


def sparkline(values: Sequence[Optional[float]], width: Optional[int] = None) -> str:
    """Fixed-scale ASCII sparkline; ``None``/missing values render as a gap."""
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    span = hi - lo
    out = []
    for value in values:
        if value is None:
            out.append(" ")
            continue
        frac = 0.5 if span == 0 else (float(value) - lo) / span
        idx = min(len(SPARK_CHARS) - 1, max(0, round(frac * (len(SPARK_CHARS) - 1))))
        out.append(SPARK_CHARS[idx])
    text = "".join(out)
    if width and width > 0 and len(text) > width:
        text = text[-width:]
    return text


def _parse_note(note: Any) -> Dict[str, Any]:
    if isinstance(note, (bytes, bytearray)):
        note = note.decode("utf-8", "replace")
    if not note:
        return {}
    try:
        payload = json.loads(note)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalise_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    note = _parse_note(row.get("note"))
    ts = row.get("ts")
    if isinstance(ts, datetime):
        ts_iso = ts.astimezone(timezone.utc).isoformat()
    else:
        ts_iso = str(ts)
    item: Dict[str, Any] = {
        "ts": ts_iso,
        "online": row.get("online"),
        "busy": row.get("busy"),
        "held": row.get("held"),
        "released": row.get("released") or "",
        "source": "pulse-v1" if note else "legacy",
        "queued": note.get("queued"),
        "idle": note.get("idle"),
        "running_missions": note.get("running_missions"),
        "window_s": note.get("window_s"),
    }
    return item


def load_samples(conn, limit: int = 20) -> List[Dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise PulseInputError(f"limit must be a positive int, got {limit!r}")
    with open_read_snapshot(conn):
        rows = conn.execute(SELECT_SQL, (limit,)).fetchall()
    samples = [normalise_row({"ts": r[0], "online": r[1], "busy": r[2], "held": r[3],
                              "released": r[4], "note": r[5]}) for r in rows]
    samples.reverse()  # chronological
    return samples


def render(samples: Sequence[Mapping[str, Any]], *, width: int = 40) -> str:
    if not samples:
        return (f"saturation pulse trend: no samples yet in {TABLE} "
                "(run `python3 -m tools.pulse.saturation_pulse --commit`)")
    lines: List[str] = []
    first, last = samples[0]["ts"], samples[-1]["ts"]
    window = next((s.get("window_s") for s in reversed(samples) if s.get("window_s")), None)
    header = f"saturation pulse trend  samples={len(samples)}  {first} -> {last}"
    if window:
        header += f"  window={window}s"
    lines.append(header)
    lines.append("")
    for key, label in (("online", "online "), ("busy", "busy   "),
                       ("queued", "queued "), ("held", "held   ")):
        series = [s.get(key) for s in samples]
        known = [v for v in series if v is not None]
        spark = sparkline(series, width=width)
        if known:
            stats = f"min={min(known):>3} mean={sum(known)/len(known):>6.1f} max={max(known):>3}"
        else:
            stats = "min=  - mean=     - max=  -"
        lines.append(f"{label} |{spark}| {stats}")
    lines.append("")
    lines.append(f"{'ts (UTC)':<34} {'online':>6} {'busy':>5} {'queued':>6} {'idle':>5} "
                 f"{'run':>4} {'held':>5} {'rel':>4}  source")
    for sample in samples:
        released = len([p for p in str(sample.get("released") or "").split(",") if p])
        lines.append(
            f"{sample['ts']:<34} {_fmt(sample.get('online')):>6} {_fmt(sample.get('busy')):>5} "
            f"{_fmt(sample.get('queued')):>6} {_fmt(sample.get('idle')):>5} "
            f"{_fmt(sample.get('running_missions')):>4} {_fmt(sample.get('held')):>5} "
            f"{released:>4}  {sample['source']}"
        )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.pulse.pulse_report",
        description="ASCII trend over the last N saturation samples.",
    )
    parser.add_argument("--limit", type=int, default=20, help="number of samples (default 20)")
    parser.add_argument("--width", type=int, default=40, help="sparkline width")
    parser.add_argument("--dsn", default=None, help="override DSN")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of ASCII")
    args = parser.parse_args(argv)
    try:
        conn = connect(args.dsn)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"ERROR: cannot connect: {exc}", file=sys.stderr)
        return 1
    try:
        samples = load_samples(conn, args.limit)
    except PulseInputError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 2
    except PulseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.json:
        print(json.dumps({"table": TABLE, "dsn": resolve_dsn(args.dsn),
                          "count": len(samples), "samples": samples},
                         ensure_ascii=False, sort_keys=True))
    else:
        print(render(samples, width=args.width))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())