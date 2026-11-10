"""Adapter lifecycle state machine (protocol v1).

Three states and a fixed legal-action table — nothing else::

    NEW --init()--> READY --step()*--> READY --cleanup()--> FINALIZED
                      |  |
                      |  +--> get_output()  (read-only, stays READY)
                      |  +--> checkpoint()  (read-only, stays READY)
                      +--> checkpoint()     (also legal in NEW)

``get_output`` is legal in ``READY`` only: a solver that has not been
initialised has no output, and a solver that has been cleaned up has released
it.  ``restore`` is legal in ``NEW`` only — restoring over a live solver would
discard state without an audit trail.

The trace is append-only, so a checkpoint can prove *which* sequence of
transitions produced the state it carries.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from enum import Enum
from typing import Any, Dict, List, Mapping, Tuple

from .errors import LifecycleError

__all__ = ["State", "LifecycleTrace", "READ_ACTIONS", "transition"]


class State(str, Enum):
    NEW = "new"
    READY = "ready"
    FINALIZED = "finalized"


_LEGAL: Dict[Tuple[State, str], State] = {
    (State.NEW, "init"): State.READY,
    (State.READY, "step"): State.READY,
    (State.READY, "cleanup"): State.FINALIZED,
    (State.NEW, "restore"): State.READY,
    # read-only actions: legal in READY and state-preserving
    (State.READY, "get_output"): State.READY,
    (State.READY, "checkpoint"): State.READY,
    (State.NEW, "checkpoint"): State.NEW,
}

#: Read-only actions a caller may perform; everything else mutates the solver.
READ_ACTIONS = frozenset({"get_output", "checkpoint"})


@dataclass
class LifecycleTrace:
    """Append-only record of legal transitions with a running sequence number."""

    entries: List[Dict[str, Any]] = _dc_field(default_factory=list)

    def append(self, action: str, src: State, dst: State, detail: str = "") -> None:
        self.entries.append(
            {
                "seq": len(self.entries),
                "action": action,
                "from": src.value,
                "to": dst.value,
                "detail": detail,
            }
        )

    def actions(self) -> List[str]:
        return [str(e["action"]) for e in self.entries]

    def to_dict(self) -> Dict[str, Any]:
        return {"entries": [dict(e) for e in self.entries], "count": len(self.entries)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LifecycleTrace":
        raw = payload.get("entries")
        if not isinstance(raw, list):
            raise LifecycleError("lifecycle trace payload has no 'entries' list")
        trace = cls()
        for item in raw:
            if not isinstance(item, Mapping) or "action" not in item:
                raise LifecycleError(f"malformed lifecycle entry: {item!r}")
            trace.append(
                str(item["action"]),
                State(str(item["from"])),
                State(str(item["to"])),
                str(item.get("detail", "")),
            )
        return trace


def transition(state: State, action: str) -> State:
    """Return the next state for ``action``; raise on an illegal transition."""
    try:
        return _LEGAL[(state, action)]
    except KeyError:
        legal = sorted(a for (s, a) in _LEGAL if s is state)
        raise LifecycleError(
            f"action {action!r} is illegal in state {state.value!r} "
            f"(legal actions here: {legal})"
        ) from None