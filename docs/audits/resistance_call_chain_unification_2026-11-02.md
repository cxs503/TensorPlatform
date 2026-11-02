# Resistance call-chain audit and unification

Date: 2026-11-02  
Branch: `fix/unify-resistance-call-chain-2026-11-02`

## Decision

The public resistance endpoint and the in-process benchmark validator now call the
same pure-function kernel: `backend/services/ship_resistance.py::resistance_breakdown`.
The change does **not** assert that this empirical kernel is physically validated
or suitable for arbitrary merchant hulls.

## Before

- `backend/api/calculations.py::calc_resistance` independently calculated wetted
  surface, Reynolds/Froude numbers, ITTC-1957 friction, form resistance and the
  empirical residual term inline.
- `backend/api/domain/ship_benchmark.py::_call_resistance_api` already called
  `services.ship_resistance.resistance_breakdown` in-process.
- `backend/services/resistance_core.py` and
  `backend/api/domain/resistance_core.py` remain separate implementations. The
  latter adds a published-geometry gate and a validity verdict; it is not the
  public endpoint's current physics path.
- The request field `wetted_surface_m2` was labeled `input_validated` merely
  because a caller supplied it. The endpoint itself did not validate the
  geometry/provenance behind that value.

## Changes in this PR

1. Extended the shared `ship_resistance.resistance_breakdown` function to accept
   the endpoint's `wetted_surface_m2` override and `form_factor`, preserving
   the force decomposition and default-form-factor behavior.
2. Routed `calc_resistance` through that shared kernel. The API response retains
   existing fields and adds `model_id`, `validity_status` and `validity_notes`.
3. Assigned the explicit model identifier
   `suboff_axisymmetric_empirical_v1`. The status is deliberately
   `unvalidated_empirical_estimate`; a software gate is not a physical validation.
4. Changed the surface provenance label to `caller_supplied_unverified` when an
   area is passed. The API does not certify it merely by accepting it.
5. Added regression tests for API/benchmark call-chain parity, force identities,
   form-factor behavior, area provenance and low-Reynolds-number handling.

## Physical scope and remaining risks

The shared kernel still uses the axisymmetric estimate
`S = pi * B * L * (0.5 + 0.25 * Cb)` when no surface is supplied, plus the
empirical residual fit `Cr = 0.002 * (1 + 2 Cb) * Fn^2.5` above its low-Froude
floor. The fit has no published provenance in this repository and is explicitly
SUBOFF-like. It must not be reported as a general Holtrop-Mennen merchant-ship
model. For KVLCC2 and DTMB-5415, the prior geometry audit reported substantial
bias in the axisymmetric default surface estimate; that physical limitation is
not repaired by sharing a function.

The response's `validity_status` is therefore not a pass/fail accuracy verdict.
The notes warn about missing provenance and out-of-range Cb/Fn values. Geometry
validation and comparison against a traceable reference remain separate gates.

The geometry-gated implementation in `backend/api/domain/resistance_core.py`
is intentionally not deleted in this PR: its published-geometry registry,
surface handling and verdict semantics need a separate reconciliation before
it can safely be made canonical. This PR only removes the duplicate inline
physics from the live repository endpoint and makes its path match the existing
in-process benchmark kernel.

## Verification

Run from the repository root:

```bash
pytest -q backend/tests/feat/test_resistance_kernel_unification.py
pytest -q tools/verif/test_ship_calcs_verification.py
pytest -q backend/tests/feat/test_benchmark_chain_m1388.py
```

These commands are recorded for CI/reviewer execution. Tests were not executed
from the GitHub editing environment used to prepare this branch.
