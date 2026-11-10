# Benchmark dataset generation (`tools/benchmark_gen`)

Mission **P269W2-N03** — *traceable benchmark dataset generation: configuration-injected
body/condition parameters → dataset → sha256 archive, anchored on a closed-form solution.*

## Why this exists

Verification datasets in this ecosystem were previously bespoke scripts: geometry numbers,
case parameters and reference values were typed into each analysis script, so nobody could
answer "which inputs produced this number, and has anything drifted since?". This package
turns that into a **pipeline with a contract**: a body is seven geometry parameters, a
condition is an immersion ratio plus fluid properties and a speed list, both injected from
JSON; every dataset ships its own closed-form reference, its relative deviations and its
sha256 identity.

## Architecture

| module | role | key property |
|---|---|---|
| `numeric.py` | production path: discrete offset grid (801 stations × 401 waterlines), composite Simpson, per-cell 2×2 Gauss for wetted surface | body-agnostic; does **not** exploit the separability of the parametric form, so the measured error is a genuine station/panel error |
| `analytic.py` | closed-form reference: `∫f dx`, `∫f³ dx`, `∫x²f dx` via Euler Beta functions; `∫g dz`, `∫z g dz` as polynomials in `|z|^p` | zero shared code with the numeric path — a sign/power error cannot cancel |
| `generator.py` | spec loading, pipeline registry (name or `module:callable`), dataset/matrix assembly, Monte-Carlo cross-check, CLI | new domains plug in through configuration; no code change |
| `provenance.py` | canonical JSON (12 significant digits), three hashes, archive manifest, independent verifier | `dataset_id ≡ H(params, seed, version, code)` — A2 |
| `make_report.py` | figures + `report.md` + `metrics.json` from the archive | no number is typed by hand |

Body form (all parameters injected):

```
y(x, z) = B/2 · f(u) · g(z),      u = 2|x| / L
f(u) = 1                                  if u ≤ m
     = (1 - v^n)^s                        v = (u - m)/(1 - m)
g(z) = 1 - (|z| / D)^p                    (g = 0 at the keel)
```

Shipped specs (`specs/bodies/*.json`, `specs/conditions/*.json`) are three synthetic forms
(parabolic plan, prismatic mid-body, V-section) × three immersion conditions. The parabolic
family (`n=2, s=1, p=2, m=0`) is the classical Wigley-type reference hull: every coefficient
is rational (`Cb = 4/9`, `Cm = Cp = Cwp = 2/3`, `KB = 5T/8` above the keel,
`BM_T = 3B²/(35T)`, `BM_L = 3L²/(40T)`), which is what makes it the analytic anchor.

## Provenance contract

* `provenance_key` (= `dataset_id`) — `sha256` over `{schema, generator_version,
  code_fingerprint, pipeline, seed, body, condition}`. Same request ⇒ same id, bit for bit;
  any parameter, the seed or the generator version changes ⇒ new id.
* `content_sha256` — `sha256` over the canonical payload (floats rounded to 12 significant
  digits, sorted keys, no whitespace). Catches silent numeric drift.
* `file_sha256` — `sha256` of the written JSON, recorded in `MANIFEST.json` and
  `SHA256SUMS` (never inside the file itself, which would be self-referential).
* Per-file `.sha256` sidecars; `verify_archive()` re-derives all three and checks id
  uniqueness, so "9 datasets" cannot silently become 8 or contain a duplicate.

The seed is not decoration: it drives the independent uniform-sampling Monte-Carlo volume
(20 000 samples) that cross-checks the closed form at the 3σ level, so changing the seed
legitimately changes the payload — and therefore its hash.

## Usage

```bash
# generate + self-verify (9 datasets, production grid)
python3 -m tools.benchmark_gen --spec-dir tools/benchmark_gen/specs \
    --out artifacts/benchmark_gen/datasets

# audit an existing archive
python3 -m tools.benchmark_gen --verify artifacts/benchmark_gen/datasets

# figures + report from the archive
python3 -m tools.benchmark_gen.make_report --archive artifacts/benchmark_gen/datasets \
    --out artifacts/benchmark_gen

# tests (40 collected cases)
python3 -m pytest tests/benchmark_gen -q
```

Programmatic use:

```python
from tools.benchmark_gen import generate_dataset, provenance

doc = generate_dataset({"id": "my_body", "params": {...}},
                       {"id": "my_case", "params": {"draft_ratio": 0.8}}, seed=42)
doc["provenance"]["dataset_id"]        # sha256 identity
provenance.verify_record(doc)["ok"]    # True on an untouched record
```

## Acceptance mapping

| criterion | where |
|---|---|
| A1 ≥3 bodies × ≥3 conditions | `test_matrix_three_bodies_three_conditions`, `specs/` |
| A2 sha256 ↔ (params, seed, version) one-to-one | `test_provenance_key_recomputes_from_request`, `test_seed_changes_key_and_content`, `test_version_change_changes_key`, `test_assembly_round_trip_*` |
| A3 closed-form deviation ≤ 0.1 % | `test_numeric_matches_closed_form` (9 parametrised cases), `test_rational_anchors_of_closed_form`; measured worst case 5.71e-07 |
| A4 no vessel names in generator code | `test_generator_has_no_vessel_names`, `test_no_spec_ids_hardcoded_in_code` |
| A5 ≥12 pytest cases | 40 collected |

## Boundaries and limitations (honest declarations)

* **Domain-semantics boundary.** The platform iron law keeps domain content out of
  mechanism code. The generator core (spec loading, registry, hashing, archive) is
  domain-neutral and carries no body/case data — proven by
  `test_no_spec_ids_hardcoded_in_code` and `test_generator_has_no_vessel_names`. The single
  shipped *pipeline* (`hydrostatics`) does use naval-architecture observable names
  (`Cb`, `KB`, `BM_T`, …) because the mission requires a hydrostatics verification anchor
  in this repo; it is registered through the same registry as any injected pipeline
  (`test_pipeline_registry_dotted_path_injection`), so it can be replaced by configuration.
  Any further domain belongs in the domain repo, not here.
* **No tabulated-offsets interpolation.** Offsets are evaluated from the parametric form at
  the quadrature nodes, so the reported error is quadrature error only; the interpolation
  error of a real table-of-offsets hull is **not** covered.
* **Wetted surface has no elementary closed form**: the reference is a high-order (8-node
  Gauss) quadrature, labelled `evidence_level: quadrature` in every dataset.
* **Validity envelope**: `draft ≤ form_depth` (deck immersion rejected explicitly),
  exponents ≥ 1 (finite integrals, non-singular derivatives).
* **LCB/LCF are identically zero** for this parameter family (symmetric about midships):
  the match there is a symmetry artefact, not evidence of a general longitudinal-balance
  capability.
* `code_fingerprint` is part of `dataset_id`: changing the generator invalidates old ids by
  design.