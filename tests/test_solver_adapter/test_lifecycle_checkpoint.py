"""Lifecycle, checkpoint and restore consistency across the eight adapters.

``checkpoint -> restore`` must give a *bit-identical* solver: same clock, same
step index, same field payloads, same field digests.  Anything less makes a long
run unreproducible, so the assertion is on the full state digest, not on a
tolerance.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from tools.solver_adapter import (
    SOLVER_IDS,
    LifecycleError,
    StateMismatchError,
    State,
    build_adapter,
)

N = 4


def _ready(solver_id: str, steps: int = 2, dt: float = 0.005):
    adapter = build_adapter(solver_id, n_cells=N)
    adapter.init()
    for _ in range(steps):
        adapter.step(dt)
    return adapter


# ------------------------------------------------------------------- restore
@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_checkpoint_restore_reproduces_state_exactly(solver_id):
    original = _ready(solver_id)
    checkpoint = original.checkpoint()
    clone = build_adapter(solver_id, n_cells=N)
    clone.restore(checkpoint)
    assert clone.state is State.READY
    assert clone.step_index == original.step_index
    assert clone.time == pytest.approx(original.time, abs=0.0)
    for name in original.manifest().field_names():
        np.testing.assert_array_equal(clone.export_field(name).values,
                                      original.export_field(name).values)
    clone_payload = clone.checkpoint()
    assert clone_payload["state_digest"] == checkpoint["state_digest"]
    # the provenance digest legitimately differs: restore appends an entry
    assert clone_payload["digest"] != checkpoint["digest"]
    assert "restore" in clone.lifecycle_trace.actions()
    assert clone.lifecycle_trace.actions()[-1] == "checkpoint"


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_restored_solver_continues_identically(solver_id):
    original = _ready(solver_id, steps=1)
    clone = build_adapter(solver_id, n_cells=N)
    clone.restore(original.checkpoint())
    first = original.step(0.01)
    second = clone.step(0.01)
    assert first.state_digest == second.state_digest
    for name in original.manifest().field_names():
        np.testing.assert_allclose(clone.export_field(name).values,
                                   original.export_field(name).values, rtol=0, atol=0)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_checkpoint_of_fresh_adapter_is_legal(solver_id):
    adapter = build_adapter(solver_id, n_cells=N)
    payload = adapter.checkpoint()
    assert payload["state"] == "new"
    assert payload["step_index"] == 0


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_restore_into_live_adapter_is_refused(solver_id):
    live = _ready(solver_id)
    with pytest.raises(LifecycleError):
        live.restore(live.checkpoint())


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_restore_after_cleanup_is_refused(solver_id):
    adapter = _ready(solver_id)
    payload = adapter.checkpoint()
    adapter.cleanup()
    with pytest.raises(LifecycleError):
        adapter.restore(payload)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_tampered_payload_is_refused(solver_id):
    payload = _ready(solver_id).checkpoint()
    tampered = copy.deepcopy(payload)
    tampered["step_index"] = int(tampered["step_index"]) + 1
    with pytest.raises(StateMismatchError):
        build_adapter(solver_id, n_cells=N).restore(tampered)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_tampered_field_is_refused(solver_id):
    payload = _ready(solver_id).checkpoint()
    name = next(iter(payload["fields"]))
    payload["fields"][name]["values"][0] = payload["fields"][name]["values"][0] + 1.0
    with pytest.raises(StateMismatchError):
        build_adapter(solver_id, n_cells=N).restore(payload)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_checkpoint_from_other_solver_is_refused(solver_id):
    other = "fem" if solver_id != "fem" else "lbm"
    payload = _ready(other).checkpoint()
    with pytest.raises(StateMismatchError):
        build_adapter(solver_id, n_cells=N).restore(payload)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_checkpoint_from_other_mesh_is_refused(solver_id):
    payload = _ready(solver_id, steps=1).checkpoint()
    with pytest.raises(StateMismatchError):
        build_adapter(solver_id, n_cells=N + 2).restore(payload)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_restore_rejects_garbage(solver_id):
    adapter = build_adapter(solver_id, n_cells=N)
    with pytest.raises((StateMismatchError, LifecycleError, ValueError)):
        adapter.restore({"not": "a checkpoint"})


def test_checkpoint_payload_is_json_serialisable():
    payload = _ready("lbm").checkpoint()
    text = json.dumps(payload, sort_keys=True)
    assert json.loads(text)["schema"] == "solver-adapter-checkpoint/v1"


def test_checkpoint_carries_the_lifecycle_trace():
    payload = _ready("fvm", steps=2).checkpoint()
    actions = [e["action"] for e in payload["lifecycle"]["entries"]]
    assert actions[:3] == ["init", "step", "step"]
    assert actions[-1] == "checkpoint"


# ------------------------------------------------------------ illegal inputs
@pytest.mark.parametrize("solver_id", SOLVER_IDS)
@pytest.mark.parametrize("dt", [0.0, -0.01, float("nan"), float("inf")])
def test_step_rejects_bad_dt(solver_id, dt):
    adapter = build_adapter(solver_id, n_cells=N)
    adapter.init()
    with pytest.raises(ValueError):
        adapter.step(dt)


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_unknown_field_is_a_field_lookup_error(solver_id):
    from tools.solver_adapter import FieldLookupError

    adapter = _ready(solver_id, steps=0)
    with pytest.raises(FieldLookupError):
        adapter.get_output("not_a_field")
    with pytest.raises(FieldLookupError):
        adapter.export_field("not_a_field")


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_cleanup_is_idempotent_only_once(solver_id):
    adapter = _ready(solver_id, steps=0)
    adapter.cleanup()
    with pytest.raises(LifecycleError):
        adapter.cleanup()


@pytest.mark.parametrize("solver_id", [s for s in SOLVER_IDS if s != "cad"])
def test_stability_guard_refuses_a_too_large_step(solver_id):
    adapter = build_adapter(solver_id, n_cells=N)
    adapter.init()
    with pytest.raises(ValueError):
        adapter.step(10.0)