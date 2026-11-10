"""Already-merged de-duplication for salvage candidates.

A salvage candidate is *not* a contribution when its bytes already live in the
baseline branch: that content is somebody else's merged work (or the same
mission's work that landed through another route).  Counting it would turn
"trace" into "achievement", which is exactly the failure mode this module must
prevent.

Two prune tiers, both proven by byte identity, never by path alone:

``head``     content == baseline ref (``main``) blob at the same path.
             Reason ``content_identical_to_baseline``.
``history``  content == blob at the same path in one of the last ``limit``
             commits of the baseline that touched that path, i.e. the exact
             bytes already landed and were later superseded by newer work.
             Reason ``content_landed_in_baseline_history``.

Anything else is a genuine delta (``content_differs_from_baseline`` or
``path_absent_in_baseline``) and is kept.  The tier that fired is recorded per
file in the manifest so the decision is auditable rather than implicit.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import gitutil

PRUNE_HEAD = "head"
PRUNE_HISTORY = "history"
KEEP_ABSENT = "path_absent_in_baseline"
KEEP_DIFFERS = "content_differs_from_baseline"

REASON_IDENTICAL = "content_identical_to_baseline"
REASON_LANDED = "content_landed_in_baseline_history"


@dataclass(frozen=True)
class Decision:
    pruned: bool
    tier: str | None
    reason: str
    baseline_sha256: str | None

    def as_dict(self) -> dict:
        return {
            "pruned": self.pruned,
            "tier": self.tier,
            "reason": self.reason,
            "baseline_sha256": self.baseline_sha256,
        }


class Deduper:
    """Caches baseline blob lookups so a scan does not re-shell the same query."""

    def __init__(
        self,
        worktree: str,
        baseline_ref: str = "main",
        history_limit: int = 10,
        use_history_tier: bool = True,
    ) -> None:
        self.worktree = str(worktree)
        self.baseline_ref = baseline_ref
        self.history_limit = history_limit
        self.use_history_tier = use_history_tier
        self._cache: dict[tuple[str, str], str | None] = {}
        self._commits: dict[str, list[str]] = {}

    def baseline_sha256(self, path: str, rev: str | None = None) -> str | None:
        rev = rev or self.baseline_ref
        key = (rev, path)
        if key not in self._cache:
            self._cache[key] = gitutil.blob_sha256(self.worktree, rev, path)
        return self._cache[key]

    def _history_hits(self, path: str) -> list[str]:
        if path not in self._commits:
            self._commits[path] = gitutil.commits_touching(
                self.worktree, self.baseline_ref, path, limit=self.history_limit
            )
        return self._commits[path]

    def decide(self, path: str, content_sha256: str) -> Decision:
        head = self.baseline_sha256(path)
        if head is None:
            return Decision(False, None, KEEP_ABSENT, None)
        if head == content_sha256:
            return Decision(True, PRUNE_HEAD, REASON_IDENTICAL, head)
        if self.use_history_tier:
            for commit in self._history_hits(path):
                if commit == self.baseline_ref:
                    continue
                if self.baseline_sha256(path, rev=commit) == content_sha256:
                    return Decision(True, PRUNE_HISTORY, REASON_LANDED, head)
        return Decision(False, None, KEEP_DIFFERS, head)


def prune_rate(decisions: list[Decision]) -> float:
    """Share of candidates whose bytes were proven already present in baseline."""
    if not decisions:
        return 0.0
    return sum(1 for d in decisions if d.pruned) / len(decisions)