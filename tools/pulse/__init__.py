"""Saturation-pulse metrology for the autonomous campaign dispatcher (IN15-O1).

Public surface:

* :mod:`tools.pulse.saturation_pulse` -- three-state (online / busy / queued)
  collector that writes one row per sample into ``saturation_pulse``.
* :mod:`tools.pulse.pulse_report` -- ASCII trend report over the last N samples.

Both are read-only towards every pre-existing table; the single schema action is
the idempotent DDL of ``tools/pulse/sql/001_saturation_pulse.sql``.
"""

__all__ = ["saturation_pulse", "pulse_report"]