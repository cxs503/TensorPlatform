"""C2: the 4-abstract / 8-concrete contract of ``SolverAdapter``.

These tests are about the *shape* of the abstraction, so they inspect the class
object rather than behaviour: which methods are abstract, which are concrete,
whether a partially implemented subclass can be instantiated (it must not), and
whether the lifecycle guards actually intercept the four hooks.
"""

from __future__ import annotations

import inspect

import pytest

from tools.solver_adapter import (
    SOLVER_IDS,
    FieldSpec,
    LifecycleError,
    SolverAdapter,
    State,
    build_adapter,
)

ABSTRACT_HOOKS = ("init", "step", "get_output", "cleanup")
CONCRETE_METHODS = (
    "manifest",
    "check_compatibility",
    "export_field",
    "import_field",
    "map_to",
    "conservation_report",
    "checkpoint",
    "restore",
)


def test_solver_adapter_is_abstract():
    assert inspect.isabstract(SolverAdapter)
    with pytest.raises(TypeError):
        SolverAdapter()  # type: ignore[abstract]


def test_exactly_four_abstract_hooks():
    assert set(SolverAdapter.__abstractmethods__) == set(ABSTRACT_HOOKS)


@pytest.mark.parametrize("name", ABSTRACT_HOOKS)
def test_abstract_hook_is_marked_abstract(name):
    assert getattr(getattr(SolverAdapter, name), "__isabstractmethod__", False) is True


@pytest.mark.parametrize("name", CONCRETE_METHODS)
def test_concrete_method_is_callable_and_not_abstract(name):
    method = getattr(SolverAdapter, name, None)
    assert callable(method), f"{name} missing from the protocol surface"
    assert not getattr(method, "__isabstractmethod__", False)


def test_public_surface_is_four_hooks_plus_eight_methods():
    """No accidental extra member: 4 abstract hooks and 8 concrete methods, no more."""
    public = {
        name
        for name, value in vars(SolverAdapter).items()
        if not name.startswith("_") and callable(value)
    }
    assert public == set(CONCRETE_METHODS) | set(ABSTRACT_HOOKS), sorted(
        public ^ (set(CONCRETE_METHODS) | set(ABSTRACT_HOOKS))
    )
    concrete = {
        name for name in public
        if not getattr(vars(SolverAdapter)[name], "__isabstractmethod__", False)
    }
    assert concrete == set(CONCRETE_METHODS), sorted(concrete ^ set(CONCRETE_METHODS))
    assert len(concrete) == 8


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_is_concrete(solver_id):
    adapter = build_adapter(solver_id)
    assert not type(adapter).__abstractmethods__


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_every_adapter_exposes_all_eight_concrete_methods(solver_id):
    adapter = build_adapter(solver_id)
    for name in CONCRETE_METHODS:
        assert callable(getattr(adapter, name))


def test_partial_implementation_cannot_be_instantiated():
    class Half(SolverAdapter):
        SOLVER_ID = "half"
        FIELDS = (FieldSpec(name="density", kind="density", dim_vector=(1, -3, 0, 0)),)

        def init(self, config=None):
            return None

        def step(self, dt):
            raise AssertionError("not reached")

        def get_output(self, field_name):
            raise AssertionError("not reached")

    assert Half.__abstractmethods__ == frozenset({"cleanup"})
    with pytest.raises(TypeError):
        Half()  # type: ignore[abstract]


def test_missing_solver_id_is_refused():
    class NoId(SolverAdapter):
        FIELDS = (FieldSpec(name="density", kind="density", dim_vector=(1, -3, 0, 0)),)

        def init(self, config=None):
            return None

        def step(self, dt):
            raise AssertionError

        def get_output(self, field_name):
            raise AssertionError

        def cleanup(self):
            return None

    with pytest.raises(ValueError):
        NoId()


def test_missing_fields_is_refused():
    class NoFields(SolverAdapter):
        SOLVER_ID = "nofields"

        def init(self, config=None):
            return None

        def step(self, dt):
            raise AssertionError

        def get_output(self, field_name):
            raise AssertionError

        def cleanup(self):
            return None

    with pytest.raises(ValueError):
        NoFields()


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_lifecycle_guards_are_installed(solver_id):
    adapter = build_adapter(solver_id)
    assert adapter.state is State.NEW
    with pytest.raises(LifecycleError):
        adapter.step(0.01)
    with pytest.raises(LifecycleError):
        adapter.get_output("density")
    with pytest.raises(LifecycleError):
        adapter.export_field("density")


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_second_init_is_refused(solver_id):
    adapter = build_adapter(solver_id)
    adapter.init()
    with pytest.raises(LifecycleError):
        adapter.init()


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_cleanup_finalises_and_blocks_further_use(solver_id):
    adapter = build_adapter(solver_id)
    adapter.init()
    adapter.cleanup()
    assert adapter.state is State.FINALIZED
    with pytest.raises(LifecycleError):
        adapter.step(0.01)
    with pytest.raises(LifecycleError):
        adapter.get_output("density")
    with pytest.raises(LifecycleError):
        adapter.init()


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_illegal_actions_are_not_traced(solver_id):
    """A refused action must not pollute the audit trace."""
    adapter = build_adapter(solver_id)
    before = len(adapter.lifecycle_trace.entries)
    with pytest.raises(LifecycleError):
        adapter.step(0.01)
    assert len(adapter.lifecycle_trace.entries) == before


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_legal_action_sequence_is_traced_in_order(solver_id):
    adapter = build_adapter(solver_id)
    adapter.init()
    adapter.step(0.01)
    adapter.get_output("density")
    adapter.cleanup()
    assert adapter.lifecycle_trace.actions() == ["init", "step", "get_output", "cleanup"]
    assert [e["seq"] for e in adapter.lifecycle_trace.entries] == [0, 1, 2, 3]


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_manifest_is_protocol_conformant(solver_id):
    manifest = build_adapter(solver_id).manifest()
    assert manifest.protocol_version == "solver-protocol/v1"
    assert manifest.mesh.dim == adapter_mesh_dim(solver_id)
    assert manifest.fields


def adapter_mesh_dim(solver_id: str) -> int:
    return build_adapter(solver_id).mesh.dim


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_adapter_rejects_unknown_config_shape(solver_id):
    with pytest.raises(ValueError):
        build_adapter(solver_id, dim=3, lo=(0.0, 0.0), hi=(1.0, 1.0))


@pytest.mark.parametrize("solver_id", SOLVER_IDS)
def test_static_and_dynamic_adapters_both_step(solver_id):
    adapter = build_adapter(solver_id, n_cells=4)
    adapter.init()
    report = adapter.step(0.01)
    assert report.step_index == 1
    assert report.finite is True
    assert report.state_digest