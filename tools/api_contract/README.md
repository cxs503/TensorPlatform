# `tools/api_contract` — OpenAPI contract-testing harness

Generic, **domain-agnostic** contract testing for any HTTP service that publishes an
OpenAPI 3.x document. Built for TensorPlatform; used to audit the
ship-performance-platform backend (see `runs/m14740/`).

## Why a harness and not a script

The previous attempt at this node generated cases from the spec but executed them
only against a live service that was being restarted by its supervisor: 804 of 881
cases ended as `UNREACHABLE`, so "100 % coverage" was coverage of *generated*
cases, not of *executed* ones. This harness fixes that at the mechanism level:

* **executed** coverage is what is reported (a response was actually received);
* health-gated retry + resume-from-checkpoint, so a supervised restart costs
  seconds instead of the whole sweep;
* an in-process **ASGI** target (`app.openapi()` + Starlette `TestClient`) gives a
  second, socket-free execution path and lets the harness diff *deployed* spec
  against *repository* spec (real drift detection);
* a criterion with zero executed evidence reports `WITHHELD`, never `PASS`
  (anti-vacuous-green).

## Layout

| module | responsibility |
|---|---|
| `spec.py` | load spec (path/URL/ASGI app), 3-state degradation, `$ref` inlining, operation enumeration, status-code sets |
| `generate.py` | one `valid` case per operation + `invalid_*` variants + the 5-scenario status matrix |
| `targets.py` | `HttpTarget` (health-gated retry), `ASGITarget` (in-process), `UnreachableTarget` (hermetic negative control) |
| `checks.py` | JSON-Schema validation (OpenAPI 3.0/3.1 normalised), status conformance, finiteness, declarative numeric invariants (whitelisted AST evaluator), determinism diff |
| `breaking.py` | spec-to-spec diff (10 change codes, 5 required classes) + mutation injectors that prove each detector fires |
| `runner.py` | checkpointed case sweep, matrix, references (incl. cross-endpoint `prefetch`), determinism, sensitivity/MOCK probes, negative controls |
| `report.py` | criteria (a)–(f) as numbers with evidence path, JSON + Markdown artifacts |
| `cli.py` | `run`, `diff`, `mutations` |

## Usage

```bash
# full suite (all endpoints/URLs/payloads injected by the config, not by code)
python -m tools.api_contract.cli run --config runs/m14740/contract_config.json

# regression gate for CI: fail the build on an incompatible spec change
python -m tools.api_contract.cli diff --base old_openapi.json --head new_openapi.json \
       --fail-on-breaking --out drift.json

# prove the detector still fires on all five required change classes
python -m tools.api_contract.cli mutations --spec current_openapi.json
```

`run` exits `0` when the report is written (including degraded/`WITHHELD` runs) and
`1` only with `--strict` and a failed criterion — a CI job decides whether a
contract violation blocks a merge; the harness never hides one.

## Degradation contract

| situation | state | behaviour |
|---|---|---|
| spec source missing / unreadable / unreachable | `WITHHELD` | report written, exit 0 |
| spec present but malformed / not OpenAPI | `ERROR` | report written, exit 0 |
| endpoint unreachable / timeout | per-case `ERROR` row | run continues, coverage drops honestly |
| response schema unusable | schema check marked `not checked` | never a crash |

## Invariant file (numeric precision without hand-typed numbers)

```json
{"name": "…", "path": "…", "request": {...}, "criterion": true,
 "prefetch": [{"name": "res", "path": "…", "request": {...}}],
 "checks": [{"id": "…", "lhs": "ct", "rhs": "rt*1000/(0.5*rho*S*V**2)",
             "rel_tol": 0.03, "quantization": {"lhs": 0.005}}]}
```

Expressions are evaluated by a whitelisted AST walker (`+ - * / ** %`, `abs`,
`min`, `max`, `sqrt`, `log`, `log10`, `exp`, comparisons) over the flat response
tree and the request fields; `prefetch` injects a second endpoint's response as
`<name>_<leaf>`. `criterion` selects whether a check counts towards acceptance or
is reported as a diagnostic finding. `quantization` adds the published decimal
resolution of a field to the absolute tolerance, so a 2-dp published value is not
judged against a 6-dp identity.

## Iron rule compliance

`tools/api_contract/**` contains **no** endpoint, host, port or domain noun; a test
(`test_harness_sources_contain_no_endpoint_or_host_literals`) fails the build if one
appears. Everything application specific is injected through run configs under
`runs/`.