"""Fail-closed dispatch gate + interception ledger.

``DispatchGate.preflight`` is the only thing a dispatcher has to call before
handing a node to a worker. Decisions:

    decision="dispatch"  -> the node may be handed out
    decision="defer"     -> do not hand out; the returned attribution explains
                            exactly which upstream artefact is missing or whose
                            caliber does not match

Any internal error while evaluating a contract is converted into ``defer`` (fail
closed): a broken contract must never let a half-ready node through, and it also
must never crash the dispatch loop.

``DeliveryLedger`` records deferrals and their follow-up outcome so that the
"intercept -> supply upstream -> dispatch again -> first-pass success" rate can
be reported (AC5). The ledger is a plain in-memory/file-backed structure: no
clock reads, timestamps are injected.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .contracts import build_contract_table, load_contracts
from .models import CONFLICT, NOT_READY, READY, InputContract, ReadinessResult, UpstreamState
from .resolver import evaluate_readiness


@dataclass
class LedgerEntry:
    node: str
    status: str
    reasons: List[str]
    at: str = ""
    outcome: Optional[str] = None      # "released" | "blocked_final" | None
    first_pass: Optional[bool] = None   # released and completed without retry

    def as_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "status": self.status,
            "reasons": list(self.reasons),
            "at": self.at,
            "outcome": self.outcome,
            "first_pass": self.first_pass,
        }


@dataclass
class DeliveryLedger:
    """Interception bookkeeping (AC5)."""

    entries: List[LedgerEntry] = field(default_factory=list)

    def record_deferral(self, result: ReadinessResult, at: str = "") -> LedgerEntry:
        entry = LedgerEntry(node=result.node, status=result.status, reasons=result.blocking_reasons, at=at)
        self.entries.append(entry)
        return entry

    def record_release(self, node: str, first_pass: Optional[bool], at: str = "") -> Optional[LedgerEntry]:
        for entry in reversed(self.entries):
            if entry.node == node and entry.outcome is None:
                entry.outcome = "released"
                entry.first_pass = first_pass
                if at:
                    entry.at = at
                return entry
        return None

    def record_final_block(self, node: str, at: str = "") -> Optional[LedgerEntry]:
        for entry in reversed(self.entries):
            if entry.node == node and entry.outcome is None:
                entry.outcome = "blocked_final"
                if at:
                    entry.at = at
                return entry
        return None

    def catch_rate(self) -> Dict[str, Any]:
        """Interception statistics used by AC5 reporting."""
        released = [e for e in self.entries if e.outcome == "released"]
        first_pass = [e for e in released if e.first_pass is True]
        return {
            "interceptions": len(self.entries),
            "released_after_interception": len(released),
            "blocked_final": len([e for e in self.entries if e.outcome == "blocked_final"]),
            "released_first_pass": len(first_pass),
            "first_pass_rate_after_fix": (len(first_pass) / len(released)) if released else None,
            "released_with_retry": len([e for e in released if e.first_pass is False]),
        }

    def to_json(self, path: str) -> str:
        payload = {"entries": [e.as_dict() for e in self.entries], "stats": self.catch_rate()}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path


class DispatchGate:
    """Pre-dispatch interface readiness gate."""

    def __init__(
        self,
        nodes: Optional[List[Mapping[str, Any]]] = None,
        contracts_path: Optional[str] = None,
        contracts: Optional[Mapping[str, InputContract]] = None,
    ) -> None:
        self._nodes = {str(n.get("key", n.get("id", ""))): n for n in (nodes or [])}
        declared = dict(contracts or {})
        declared.update(load_contracts(contracts_path))
        self._contracts = build_contract_table(self._nodes.values(), declared) if self._nodes else declared
        self.ledger = DeliveryLedger()

    def contract_for(self, node_key: str) -> Optional[InputContract]:
        return self._contracts.get(str(node_key))

    def preflight(
        self,
        node_key: str,
        upstream_states: Mapping[str, UpstreamState],
        at: str = "",
        record: bool = True,
    ) -> Dict[str, Any]:
        """Return the dispatch verdict for ``node_key`` (fail closed)."""
        key = str(node_key)
        contract = self._contracts.get(key)
        if contract is None:
            node = self._nodes.get(key, {})
            if not node:
                verdict = {
                    "node": key,
                    "status": NOT_READY,
                    "decision": "defer",
                    "attributions": [
                        {
                            "requirement": "(node)",
                            "outcome": "missing",
                            "message": f"node '{key}' is not registered in the plan; refusing to dispatch (fail closed)",
                        }
                    ],
                    "blocking_reasons": [f"node '{key}' is not registered in the plan; refusing to dispatch"],
                    "contract_source": "absent",
                }
                if record:
                    self.ledger.record_deferral(
                        ReadinessResult(node=key, status=NOT_READY, attributions=[], details={}), at=at
                    )
                return verdict
            contract = build_contract_table([node]).get(key)

        try:
            result = evaluate_readiness(contract, upstream_states)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - fail closed on any contract pathology
            result = ReadinessResult(
                node=key,
                status=NOT_READY,
                attributions=[],
                contract_source=getattr(contract, "source", "unknown"),
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
            result_details = {
                "node": key,
                "status": NOT_READY,
                "decision": "defer",
                "attributions": [
                    {
                        "requirement": "(contract)",
                        "outcome": "missing",
                        "message": f"contract evaluation failed for '{key}' ({type(exc).__name__}); failing closed",
                    }
                ],
                "blocking_reasons": [f"contract evaluation failed for '{key}'; failing closed"],
                "contract_source": result.contract_source,
            }
            if record:
                self.ledger.record_deferral(result, at=at)
            return result_details

        verdict = result.as_dict()
        verdict["decision"] = "dispatch" if result.status == READY else "defer"
        if record and not result.dispatchable:
            self.ledger.record_deferral(result, at=at)
        return verdict


def verdict_line(verdict: Mapping[str, Any]) -> str:
    """One-line human readable rendering used in dispatch logs."""
    badge = {"dispatch": "PASS", "defer": "BLOCK"}.get(str(verdict.get("decision")), "?")
    reasons = verdict.get("blocking_reasons") or []
    head = f"[{badge}] {verdict.get('node')} status={verdict.get('status')}"
    if not reasons:
        return head
    if verdict.get("status") == CONFLICT:
        return head + " | caliber: " + "; ".join(reasons)
    return head + " | upstream: " + "; ".join(reasons)