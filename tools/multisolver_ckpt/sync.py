"""Coupling-instant bookkeeping: which checkpoint instants are resumable.

A coupled run advances several solvers with different step sizes.  The only
instants at which a *coupled* state is meaningful are the synchronisation
points where every solver sits exactly on a master boundary.  A checkpoint
taken between sub-steps captures a mixture of logical times: solver A is one
sub-step ahead of solver B.  Loading such a state and continuing is not a
resumed run, it is a different (inconsistent) run -- which is exactly what the
acceptance criterion AC3 requires to be *detected*, not silently accepted.

This module makes the instant a first-class object:

:class:`CouplingTimeGrid`
    ``dt_master``, integer sub-step ratio ``n_sub`` and the exact time grid.
    Construction rejects non-finite/negative steps and ratios that do not
    divide evenly, i.e. the grid cannot be ill-defined.

:class:`SyncReport`
    the verdict for one bundle: are all phases zero, are all logical times
    bit-identical, is the state therefore a restartable coupling instant.

:class:`SyncPolicy`
    what to do with a verdict: ``STRICT`` refuses (raises), ``WARN`` records
    ``sync_point=False`` and lets the caller decide, ``ALLOW`` records only.

:class:`SyncGuard`
    the gatekeeper used by the store: ``check_save`` before writing,
    ``check_load`` before restoring.

Domain neutrality: only integer phases and float times are inspected, never
what the arrays contain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping

from .errors import NonSyncPointError
from .protocol import StateBundle, canonical_json, sha256_bytes


class SyncPolicy(str, Enum):
    STRICT = "strict"   # a non-sync instant is an error
    WARN = "warn"       # record and continue
    ALLOW = "allow"     # record only (raw state capture, e.g. forensics)


@dataclass(frozen=True)
class CouplingTimeGrid:
    """Master step, integer sub-step ratio and exact time arithmetic."""

    dt_master: float = 1.0
    n_sub: int = 4
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.dt_master) or self.dt_master <= 0.0:
            raise ValueError(f"dt_master must be finite and positive, got {self.dt_master!r}")
        if int(self.n_sub) < 1:
            raise ValueError(f"n_sub must be >= 1, got {self.n_sub!r}")
        if self.tolerance < 0.0:
            raise ValueError("tolerance must be >= 0")
        object.__setattr__(self, "n_sub", int(self.n_sub))
        object.__setattr__(self, "dt_master", float(self.dt_master))
        object.__setattr__(self, "tolerance", float(self.tolerance))

    @property
    def dt_sub(self) -> float:
        return self.dt_master / self.n_sub

    def sub_dt(self, steps_per_master: int) -> float:
        """Step size of a solver that advances ``steps_per_master`` times per master step."""

        spm = int(steps_per_master)
        if spm < 1 or self.n_sub % spm:
            raise ValueError(
                f"steps_per_master={spm} does not divide the grid ratio n_sub={self.n_sub}"
            )
        return self.dt_master / spm

    def master_time(self, master_step: int) -> float:
        if int(master_step) < 0:
            raise ValueError("master_step must be >= 0")
        return float(int(master_step) * self.dt_master)

    def digest(self) -> str:
        return sha256_bytes(canonical_json({
            "dt_master": self.dt_master.hex(),
            "n_sub": self.n_sub,
            "tolerance": self.tolerance,
        }).encode("ascii"))


@dataclass
class SyncReport:
    """Verdict for one state bundle."""

    sync_point: bool
    master_step: int
    phases: Dict[str, int] = field(default_factory=dict)
    times: Dict[str, float] = field(default_factory=dict)
    phase_spread: int = 0
    time_spread: float = 0.0
    reason: str = ""

    def to_manifest(self) -> Dict[str, Any]:
        return {
            "sync_point": bool(self.sync_point),
            "master_step": int(self.master_step),
            "phases": {k: int(v) for k, v in sorted(self.phases.items())},
            "times_hex": {k: float(v).hex() for k, v in sorted(self.times.items())},
            "phase_spread": int(self.phase_spread),
            "time_spread": float(self.time_spread),
            "reason": self.reason,
        }

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any], grid: CouplingTimeGrid) -> "SyncReport":
        times = {k: float.fromhex(v) for k, v in dict(payload.get("times_hex") or {}).items()}
        return cls(
            sync_point=bool(payload.get("sync_point", False)),
            master_step=int(payload.get("master_step", -1)),
            phases={k: int(v) for k, v in dict(payload.get("phases") or {}).items()},
            times=times,
            phase_spread=int(payload.get("phase_spread", 0)),
            time_spread=float(payload.get("time_spread", 0.0)),
            reason=str(payload.get("reason", "")),
        )

    def explain(self) -> str:
        if self.sync_point:
            return f"master step {self.master_step}: all solvers on the coupling instant"
        lagging = sorted(self.phases.items(), key=lambda kv: kv[1])
        return (
            f"master step {self.master_step} is NOT a coupling instant "
            f"(phases {dict(lagging)}, spread {self.phase_spread}, time spread {self.time_spread:g}): "
            f"{self.reason or 'solvers sit on different logical times'}"
        )


def assess_sync(bundle: StateBundle, grid: CouplingTimeGrid | None = None) -> SyncReport:
    """Decide whether ``bundle`` is a restartable coupling instant."""

    grid = grid or CouplingTimeGrid()
    phases = bundle.phases()
    times = bundle.times()
    phase_spread = max(phases.values()) - min(phases.values()) if phases else 0
    if times:
        t_ref = times[sorted(times)[0]]
        time_spread = max(abs(t - t_ref) for t in times.values())
    else:  # pragma: no cover - bundles are never empty
        time_spread = 0.0

    expected_time = grid.master_time(bundle.step)
    reasons = []
    if phase_spread != 0:
        reasons.append(f"per-solver phases disagree (spread {phase_spread} sub-steps)")
    if time_spread > grid.tolerance:
        reasons.append(f"logical times disagree by {time_spread:g} > tolerance {grid.tolerance:g}")
    off_grid = {s: t for s, t in times.items() if abs(t - expected_time) > grid.tolerance}
    if off_grid:
        reasons.append(
            f"logical time off the master grid at step {bundle.step} "
            f"(expected {expected_time:g}, got {off_grid})"
        )
    sync_point = not reasons
    return SyncReport(
        sync_point=sync_point,
        master_step=bundle.step,
        phases=phases,
        times=times,
        phase_spread=phase_spread,
        time_spread=time_spread,
        reason="; ".join(reasons),
    )


class SyncGuard:
    """Policy gate for saving/loading coupled checkpoints."""

    def __init__(self, grid: CouplingTimeGrid | None = None, policy: SyncPolicy | str = SyncPolicy.STRICT) -> None:
        self.grid = grid or CouplingTimeGrid()
        self.policy = SyncPolicy(policy)

    # -- save path -------------------------------------------------------- #
    def check_save(self, bundle: StateBundle) -> SyncReport:
        report = assess_sync(bundle, self.grid)
        if not report.sync_point and self.policy is SyncPolicy.STRICT:
            raise NonSyncPointError(f"refusing to checkpoint: {report.explain()}")
        return report

    # -- load path -------------------------------------------------------- #
    def check_load(self, recorded: Mapping[str, Any] | SyncReport) -> SyncReport:
        report = (
            recorded if isinstance(recorded, SyncReport)
            else SyncReport.from_manifest(recorded, self.grid)
        )
        if not report.sync_point and self.policy is SyncPolicy.STRICT:
            raise NonSyncPointError(
                f"refusing to restore a mid-interval coupled state: {report.explain()}"
            )
        return report


def sync_manifest_section(report: SyncReport, grid: CouplingTimeGrid) -> Dict[str, Any]:
    payload = report.to_manifest()
    payload["grid"] = {"dt_master_hex": grid.dt_master.hex(), "n_sub": grid.n_sub,
                       "tolerance": grid.tolerance, "digest": grid.digest()}
    payload["policy_verdict"] = "sync" if report.sync_point else "non_sync"
    return payload