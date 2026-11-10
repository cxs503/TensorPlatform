"""Three-state readiness resolver.

The resolver is a pure function: contract + upstream delivery states in,
``ReadinessResult`` out. No I/O, no clock, no domain vocabulary.
"""

from typing import Dict, List, Mapping, Optional

from .models import (
    CONFLICT,
    MODE_DECLARED,
    MODE_DELIVERED,
    MODE_TERMINAL_SUCCESS,
    NOT_READY,
    READY,
    ArtifactRequirement,
    Attribution,
    InputContract,
    ReadinessResult,
    UpstreamState,
    version_tuple,
)

_MODE_RANK = {MODE_DECLARED: 1, MODE_DELIVERED: 2, MODE_TERMINAL_SUCCESS: 3}


def _requirement_label(req: ArtifactRequirement) -> str:
    return f"{req.upstream}:{req.artifact}[{req.mode}]"


def _satisfied_by_mode(req: ArtifactRequirement, state: UpstreamState) -> bool:
    need = _MODE_RANK.get(req.mode, 2)
    if need >= _MODE_RANK[MODE_TERMINAL_SUCCESS]:
        return bool(state.succeeded)
    if need >= _MODE_RANK[MODE_DELIVERED]:
        return bool(state.delivered)
    return bool(state.dispatched or state.delivered)


def _caliber_problems(req: ArtifactRequirement, state: UpstreamState) -> List[str]:
    """Field / unit / version mismatches -> human readable strings."""
    problems: List[str] = []
    record = state.record
    if record is None:
        if req.fields or req.units or req.version_min:
            problems.append(
                f"upstream {req.upstream} delivered '{req.artifact}' without a machine-readable record; "
                f"cannot verify fields={list(req.fields)} units={req.units} version>={req.version_min}"
            )
        return problems

    missing_fields = [f for f in req.fields if f not in set(record.fields)]
    if missing_fields:
        problems.append(
            f"upstream {req.upstream} artefact '{req.artifact}' lacks required field(s) {missing_fields} "
            f"(declared fields: {list(record.fields)})"
        )
    if req.units and record.units and req.units != record.units:
        problems.append(
            f"unit mismatch on '{req.artifact}' from {req.upstream}: contract expects '{req.units}', "
            f"delivered '{record.units}'"
        )
    if req.version_min:
        want = version_tuple(req.version_min)
        got = version_tuple(record.version)
        if want is not None and got is not None and got < want:
            problems.append(
                f"version too old on '{req.artifact}' from {req.upstream}: contract needs >= {req.version_min}, "
                f"delivered {record.version}"
            )
    return problems


def evaluate_readiness(
    contract: InputContract,
    upstream_states: Mapping[str, UpstreamState],
) -> ReadinessResult:
    """Evaluate one node's input contract against the delivery registry snapshot."""
    status = READY
    attributions: List[Attribution] = []
    checked = 0

    for req in contract.requirements:
        targets = [req.upstream]
        if req.upstream == "*":
            targets = sorted(upstream_states.keys())
            if not targets:
                if req.required:
                    status = NOT_READY
                    attributions.append(
                        Attribution(
                            requirement=_requirement_label(req),
                            outcome="missing",
                            message=f"contract expects upstream artefacts '{req.artifact}' but no upstream node is registered",
                        )
                    )
                continue

        for target in targets:
            checked += 1
            state = upstream_states.get(target)
            if state is None:
                if req.required:
                    status = NOT_READY
                    attributions.append(
                        Attribution(
                            requirement=_requirement_label(req),
                            outcome="missing",
                            message=f"required upstream node '{target}' has no delivery record (never declared)",
                        )
                    )
                continue

            if not _satisfied_by_mode(req, state):
                if req.required:
                    status = NOT_READY
                    what = "not dispatched" if not state.dispatched else "not finished"
                    attributions.append(
                        Attribution(
                            requirement=_requirement_label(req),
                            outcome="missing",
                            message=(
                                f"required upstream node '{target}' is {what} at dispatch time "
                                f"(mode={req.mode}){'; ' + state.detail if state.detail else ''}"
                            ),
                        )
                    )
                continue

            problems = _caliber_problems(req, state)
            if problems:
                status = CONFLICT
                for problem in problems:
                    attributions.append(
                        Attribution(requirement=_requirement_label(req), outcome="caliber", message=problem)
                    )
                continue

            attributions.append(
                Attribution(
                    requirement=_requirement_label(req),
                    outcome="satisfied",
                    message=f"upstream '{target}' satisfies artefact '{req.artifact}' (mode={req.mode})",
                )
            )

    if status == READY and checked == 0:
        attributions.append(
            Attribution(
                requirement="(none)",
                outcome="satisfied",
                message="node declares no consumable upstream artefact; nothing to verify",
            )
        )

    return ReadinessResult(
        node=contract.node,
        status=status,
        attributions=attributions,
        contract_source=contract.source,
        details={
            "requirements": len(contract.requirements),
            "checked": checked,
            "contract_version": contract.version,
        },
    )


def summarize(results: List[ReadinessResult]) -> Dict[str, int]:
    counts = {READY: 0, NOT_READY: 0, CONFLICT: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts