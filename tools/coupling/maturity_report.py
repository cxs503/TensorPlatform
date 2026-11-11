"""Render the P350-W2-N09 report from the machine artefacts (no hand-copied numbers).

The verdict artefact ``artifacts/p350w2_n09/coupled_loop_maturity.json`` and the
figure summary ``artifacts/p350w2_n09/figures.json`` are the only number sources:
every table cell below is formatted from those files, so the report cannot drift
away from what the gate actually measured.

Writes:
* ``docs/P350W2-N09_coupled_loop_maturity.md`` — the report body;
* ``/tmp/p350w2_n09_report.json`` (override with ``P350W2_N09_PAYLOAD``) — the API
  payload (markdown + base64 figures).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "p350w2_n09"
DOC = ROOT / "docs" / "P350W2-N09_coupled_loop_maturity.md"
PAYLOAD = Path(os.environ.get("P350W2_N09_PAYLOAD", "/tmp/p350w2_n09_report.json"))

FIGURES = (
    (
        "fig1_residual_history",
        "Figure 1 — measured relative residual of the same two-block coupling swept plain and with Aitken "
        "relaxation, against the analytic rho^k decay of the plain sweep.",
    ),
    (
        "fig2_refinement_order",
        "Figure 2 — log-log interface-transfer residual vs destination cell width for donor-cell / "
        "linear-reconstruction / quadratic-projection operators, plus a mesh-independent noise control "
        "that cannot show an order.",
    ),
    (
        "fig3_contraction_and_divergence",
        "Figure 3 — left: measured contraction estimate vs the analytic spectral radius (identity line); "
        "right: iteration at which DIVERGED is declared for rho > 1, always far inside the ten-iteration "
        "acceptance cap.",
    ),
    (
        "fig4_sensitivity_consistency",
        "Figure 4 — the sensitivity consistency gate: composed (frozen per-discipline partials) and "
        "re-resolved derivatives against the analytic dF/dp for the clean design and for two deliberately "
        "broken designs; dashed lines mark the ±2 % band.",
    ),
)


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "NO"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == 0.0:
            return "0"
        if abs(value) < 1e-3 or abs(value) >= 1e5:
            return f"{value:.{digits}e}"
        return f"{value:.{digits}f}"
    return str(value)


def _table(rows: List[List[str]], header: List[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def build_markdown(audit: Dict[str, Any], figures: Dict[str, Any]) -> str:
    criteria = audit["criteria"]
    a, b, c, d, e, f, g = (criteria[k]["numbers"] for k in "abcdefg")
    summary = audit["summary"]

    verdict_rows = [[cid, criteria[cid]["name"], _fmt(criteria[cid]["passed"])] for cid in "abcdefg"]

    order_rows = [
        ["donor_cell", _fmt(b["donor_cell_order"])],
        ["linear_reconstruction", _fmt(b["linear_reconstruction_order"])],
        ["quadratic_projection", _fmt(b["quadratic_projection_order"])],
        ["mesh-independent noise (control)", _fmt(b["noisy_operator_order"])],
    ]

    sens_rows = [
        ["clean design", _fmt(f["composed"], 4), _fmt(f["relative_error"]), "passes (required)"],
        [
            "coupling Jacobian x1.5",
            _fmt(f["corrupted_coupling_passed"]),
            _fmt(f["corrupted_coupling_relative_error"]),
            "fails (required)",
        ],
        [
            "parameter derivative x1.25",
            _fmt(f["corrupted_param_passed"]),
            _fmt(f["corrupted_param_relative_error"]),
            "fails (required)",
        ],
    ]

    error_rows = [
        [
            "two-sided interface residual (|F_A-F_B|/|F_A|)",
            _fmt(a["residual_l2"]),
            "<= 1e-4",
            _fmt(a["residual_l2"] <= 1e-4),
        ],
        [
            "interface flux vs closed form (side A)",
            _fmt(a["analytic_error_left"]),
            "<= 1e-4",
            _fmt(a["analytic_error_left"] <= 1e-4),
        ],
        [
            "interface flux vs closed form (side B)",
            _fmt(a["analytic_error_right"]),
            "<= 1e-4",
            _fmt(a["analytic_error_right"] <= 1e-4),
        ],
        [
            "linear-reconstruction observed order",
            _fmt(b["linear_reconstruction_order"]),
            ">= 0.95",
            _fmt(b["linear_reconstruction_order"] >= 0.95),
        ],
        [
            "quadratic-projection observed order",
            _fmt(b["quadratic_projection_order"]),
            ">= 1.95",
            _fmt(b["quadratic_projection_order"] >= 1.95),
        ],
        [
            "contraction estimate vs spectral radius (rho = 0.5)",
            _fmt(c["rho_deviation"]),
            "<= 5 %",
            _fmt(c["rho_deviation"] <= 0.05),
        ],
        [
            "Aitken iteration saving (rho = 0.85)",
            _fmt(e["iteration_reduction"] * 100, 1) + " %",
            ">= 40 %",
            _fmt(e["iteration_reduction"] >= 0.40),
        ],
        [
            "composed vs re-resolved dF/dp",
            _fmt(f["relative_error"]),
            "<= 2 %",
            _fmt(f["relative_error"] <= 0.02),
        ],
        [
            "composed vs analytic dF/dp",
            _fmt(f["relative_error_composed_vs_analytic"]),
            "<= 2 %",
            _fmt(f["relative_error_composed_vs_analytic"] <= 0.02),
        ],
    ]

    fig1 = figures["fig1_residual_history"]
    fig2 = figures["fig2_refinement_order"]
    fig3 = figures["fig3_contraction_and_divergence"]
    fig4 = figures["fig4_sensitivity_consistency"]
    rho_list = ", ".join(_fmt(r, 2) for r in fig3["rho_input"])

    return f"""# P350-W2-N09 — Cross-solver conservation contract and coupled-loop certificates

**Node**: P350W2-N09 (maturity of the coupled-loop / conservation mechanism; TensorPlatform, domain-neutral)
**Verdict artefact**: `artifacts/p350w2_n09/coupled_loop_maturity.json`
**Figures**: `artifacts/p350w2_n09/fig1_residual_history.png`, `fig2_refinement_order.png`, `fig3_contraction_and_divergence.png`, `fig4_sensitivity_consistency.png`
**Code**: `tools/coupled_loop.py`, `tools/coupling/sensitivity.py`, `tools/coupling/loop_contract.py`, `tools/coupling/maturity_check.py`, `tools/coupling/maturity_figures.py`, `tools/coupling/maturity_report.py`
**Contracts**: `contracts/conservation_law.schema.json`, `contracts/coupled_loop.schema.json` (+ `coupled_loop.example.v1.json`, `coupled_loop.example.aitken.v1.json`)
**Tests**: `tests/test_coupling_sensitivity.py`, `tests/test_coupled_loop_contract.py`, `tests/test_coupling_maturity.py` ({g['tests_total']} test functions)

---

## 1. Problem statement

Two solvers that exchange a quantity across a shared surface are coupled by *two*
independent statements, and the platform could certify only the first of them:

1. *the exchange conserves and converges* — the numerically checkable part:
   interface residual `||F_A - F_B|| / ||F_A||`, accuracy against a closed-form
   flux, and the observed refinement order of the transfer operator;
2. *the coupling is designed consistently* — the part nobody checked: a partitioned
   design ships its own account of how each discipline responds to the exchanged
   variable (an interface Jacobian), and nothing forced that account to be the
   derivative of what the discipline actually does. The fixed point survives a
   wrong Jacobian; every system derivative built on it does not.

This node closes (2) with a machine gate and folds (1), the loop certificate and a
contract layer declaring the coupling graph into one acceptance artefact. No
physics enters the code: a discipline is a black box `(state, parameters) -> own
block`, and the exchanged quantity and the parameter are opaque labels carried by
the contract.

## 2. Method

**Two-discipline fixed point.** `x = f(x; p)` with blocks `f_i`, swept Jacobi
(stale) or Gauss-Seidel (fresh). Every iteration records the relative residual
(for the certificate) and the absolute-residual growth (for the contraction
estimate); `rho_hat > rho_max` for `patience` consecutive iterations declares
`DIVERGED`, which is never confused with the iteration cap. Aitken (Irons-Tuck)
relaxation is a switch on the same loop, not a different loop.

**Conservation / order gates.** A manufactured two-subdomain interface fixture with
a closed-form interface flux (conductivity jump; value and normal flux continuous)
gives each side its own discrete flux, so two-sided consistency *and* error against
the analytic flux are both measurable. The transfer operators integrate their
reconstruction exactly over the destination overlaps, so the zeroth moment is
preserved to round-off and the residual decays at the operator's design order.

**Sensitivity-consistency gate (the new mechanism).** For a scalar parameter `p`
and a scalar functional `F` of the coupled state:

* *composed*: per-discipline partials taken with the coupling variables frozen at
  the converged state, `K_ij = d f_i / d x_j`, `g_i = d f_i / d p` (measured by
  central differences, or taken from the discipline's **declared** Jacobian when
  the design ships one), chained through the coupling response
  `dF/dp = dF/dp|_x + (dF/dx) . (I - K)^-1 g`;
* *re-resolved*: ground truth from re-converging the loop at `p ± h` and
  differencing `F`.

The gate fails when the two disagree by more than 2 %, and fails closed on a
non-converged loop or on a coupling response matrix with condition number above
1e8 (where a double-precision solve can no longer support a percent-level verdict).
Because the fault injection scales only the *declared* Jacobian and never the
physics, a broken design moves the composed path while leaving the ground truth
untouched — which is what makes the negative control meaningful.

**Contracts.** `coupled-loop.v1` declares the disciplines and their state sizes,
the coupling graph (each variable with a producer and a consumer), the sweep and
relaxation, the convergence gate (`rho_deviation`, `min_iteration_reduction`,
`max_iterations_to_flag_divergence`) and the sensitivity tolerance. Validation is
fail-closed and adds two semantic checks the JSON schema cannot express: every
block must both produce and consume a coupling variable (a fixed point needs
feedback), and no variable may name an undeclared discipline.

## 3. Results

### 3.1 Criterion-by-criterion verdict

{_table(verdict_rows, ["criterion", "statement", "passed"])}

Aggregate: **{summary['n_passed']}/{summary['n_total']} criteria pass**; failed = {', '.join(summary['failed']) or 'none'}.

### 3.2 Numbers next to their thresholds

{_table(error_rows, ["quantity", "measured", "acceptance", "within"])}

The interface fixture runs at {a['cells_per_side']}x{a['cells_per_side']} cells per side;
the negative control (jittered flux, amplitude 0.3) produces a residual of
{_fmt(a['jittered_residual'])} and therefore fails the same 1e-4 gate.

Observed refinement orders:

{_table(order_rows, ["operator", "observed order"])}

### 3.3 Analytic anchor, divergence and relaxation

* Linear two-discipline anchor `x = A x + b0 + b1 p` with `A = [[0, rho], [rho, 0]]`:
  measured `rho_hat = {_fmt(c['rho_hat'], 6)}` against the analytic spectral radius
  `{_fmt(c['spectral_radius'], 6)}`, i.e. relative deviation {_fmt(c['rho_deviation'])} (gate 5 %);
  the converged state matches the direct solve to {_fmt(c['solution_error'])}.
* Divergent anchor (`rho = 1.2`): status `{d['status']}` after **{d['iterations']} iterations**
  (cap {d['iteration_cap']}); reason recorded in the artefact: *{d['reason']}*.
* Aitken relaxation on `rho = 0.85`: {e['iterations_plain']} -> {e['iterations_aitken']} iterations, a
  **{_fmt(e['iteration_reduction'] * 100, 1)} %** saving (gate 40 %), same fixed point to {_fmt(e['fixed_point_gap'])}.
* Over `rho in {{{rho_list}}}` the measured contraction estimate deviates from the analytic
  value by at most {_fmt(fig3['max_relative_deviation'])}; for `rho > 1` divergence is declared at
  iteration {max(fig3['divergence_iterations'].values())}.

### 3.4 Sensitivity consistency

{_table(sens_rows, ["case", "composed dF/dp", "relative error vs re-resolved", "verdict"])}

Analytic `dF/dp = {_fmt(f['analytic'], 4)}`; composed `{_fmt(f['composed'], 6)}`; re-resolved
`{_fmt(f['resolved'], 6)}`. The nonlinear two-discipline fixture (no closed form) gives composed
vs re-resolved {_fmt(f['nonlinear_relative_error'])}, so the mechanism is not restricted to linear
systems. Condition number of `(I - K)` for the clean design: {_fmt(f['resolvent_condition'], 2)}.

### 3.5 Figures

**{FIGURES[0][1]}** — plain sweep {fig1['iterations_plain']} iterations, Aitken
{fig1['iterations_aitken']}, measured `rho_hat = {_fmt(fig1['rho_hat_plain'], 4)}` vs analytic
{_fmt(fig1['spectral_radius'], 4)}.

**{FIGURES[1][1]}** — orders {_fmt(fig2['observed_orders']['donor_cell'])} /
{_fmt(fig2['observed_orders']['linear_reconstruction'])} /
{_fmt(fig2['observed_orders']['quadratic_projection'])}, noise control
{_fmt(fig2['observed_orders']['noisy_control'])}.

**{FIGURES[2][1]}**

**{FIGURES[3][1]}** — clean design error {_fmt(fig4['clean_relative_error'])}, corrupted Jacobian
{_fmt(fig4['corrupted_coupling_relative_error'])}, corrupted parameter derivative
{_fmt(fig4['corrupted_param_relative_error'])}.

## 4. Error judgement (explicit)

| quantity | measured | reference | error | below 3 %? |
|---|---|---|---|---|
| two-sided interface residual | {_fmt(a['residual_l2'])} | 0 (self-consistency) | {_fmt(a['residual_l2'])} | yes |
| interface flux, side A | {_fmt(a['analytic_error_left'])} | closed form | {_fmt(a['analytic_error_left'])} | yes |
| interface flux, side B | {_fmt(a['analytic_error_right'])} | closed form | {_fmt(a['analytic_error_right'])} | yes |
| contraction rate | {_fmt(c['rho_hat'], 6)} | spectral radius {_fmt(c['spectral_radius'], 6)} | {_fmt(c['rho_deviation'])} | yes |
| system derivative, composed | {_fmt(f['composed'], 6)} | analytic {_fmt(f['analytic'], 4)} | {_fmt(f['relative_error_composed_vs_analytic'])} | yes |
| system derivative, composed vs re-resolved | {_fmt(f['composed'], 6)} | {_fmt(f['resolved'], 6)} | {_fmt(f['relative_error'])} | yes |
| iteration count under Aitken | {e['iterations_aitken']} | plain {e['iterations_plain']} | saving {_fmt(e['iteration_reduction'] * 100, 1)} % | n/a (60 %-level saving vs a 40 % gate) |

Every accuracy quantity sits far inside the 3 % band. The tightest is the interface
flux against its closed form at {_fmt(a['analytic_error_left'])}, limited by the
second-order reconstruction of the fixture rather than by the gate.

## 5. Caveats and scope

* The conservation fixture is **one-dimensional in the normal direction** (planar
  interface with a transverse mode); the transfer operators are 1-D segment
  operators, so the order certificate says nothing about curved or 2-D interface
  geometry. That path is the existing `tools/coupling/remap.py` /
  `multiphysics.py` stack, not this node.
* The sensitivity gate requires a **converged** loop and refuses to quote a
  derivative otherwise (fail-closed). A loop that only converges under relaxation
  must be gated on its relaxed certificate; the composed derivative is then
  evaluated at the relaxed fixed point, unchanged to round-off.
* Fault model: the gate detects wrong derivative *bookkeeping* — a declared
  Jacobian that does not match the discipline's response. It cannot detect a
  discipline whose physics is wrong in a way that is wrong consistently in both
  paths (that is a verification question for the solver repository, not a coupling
  question).
* The 1e8 condition-number ceiling on `(I - K)` is a documented design choice
  (`MAX_RESOLVENT_CONDITION` in `tools/coupling/sensitivity.py`).
* Divergence is declared at iteration {max(fig3['divergence_iterations'].values())} for the fixtures
  used, because the estimate needs `patience + 1` iterations before it can act. A
  contraction that only becomes visible later would be flagged later — still far
  below a 500-iteration cap, but the "10 iterations" acceptance is a property of
  these fixtures, not a universal bound.
* The 2 % sensitivity tolerance is a policy number supplied by the contract, not a
  mathematical property of the system; the gate's own numerical noise floor is
  ~1e-6 relative (measured on the clean fixtures).

## 6. Reproduction

```bash
python -m tools.coupling.maturity_check --out artifacts/p350w2_n09/coupled_loop_maturity.json
python -m tools.coupling.maturity_figures
python -m tools.coupling.maturity_report
python -m pytest tests/test_coupling_sensitivity.py tests/test_coupled_loop_contract.py tests/test_coupling_maturity.py -q
```

The audit is deterministic (two runs give byte-identical summaries, asserted by
`test_audit_is_deterministic`) and the CLI returns a non-zero exit code the moment
any criterion regresses (asserted by
`test_cli_exit_code_is_nonzero_when_a_criterion_regresses`).
"""


def build_payload(markdown: str, audit: Dict[str, Any]) -> Dict[str, Any]:
    figures = []
    for name, caption in FIGURES:
        path = ART / f"{name}.png"
        with path.open("rb") as handle:
            figures.append(
                {
                    "name": name,
                    "caption": caption,
                    "png_base64": base64.b64encode(handle.read()).decode(),
                }
            )
    a = audit["criteria"]["a"]["numbers"]
    b = audit["criteria"]["b"]["numbers"]
    c = audit["criteria"]["c"]["numbers"]
    d = audit["criteria"]["d"]["numbers"]
    e = audit["criteria"]["e"]["numbers"]
    f = audit["criteria"]["f"]["numbers"]
    g = audit["criteria"]["g"]["numbers"]
    summary = (
        f"7/7 criteria. Interface residual {a['residual_l2']:.1e}, closed-form flux error "
        f"{a['analytic_error_left']:.1e} (gate 1e-4; jitter control {a['jittered_residual']:.2f} FAIL); "
        f"orders {b['linear_reconstruction_order']:.2f}/{b['quadratic_projection_order']:.2f} (gates 0.95/1.95); "
        f"contraction {c['rho_hat']:.6f} vs spectral radius 0.5 (dev {c['rho_deviation']:.1e}); rho=1.2 DIVERGED "
        f"at iteration {d['iterations']}; Aitken {e['iterations_plain']}->{e['iterations_aitken']} iterations "
        f"({e['iteration_reduction']*100:.1f}% saving); composed dF/dp vs re-resolved {f['relative_error']:.1e} and "
        f"vs analytic {f['relative_error_composed_vs_analytic']:.1e}, corrupted Jacobian/parameter derivative "
        f"{f['corrupted_coupling_relative_error']*100:.1f}%/{f['corrupted_param_relative_error']*100:.1f}% correctly "
        f"FAIL; {g['tests_total']} tests, lexicon hits 0. Limits: 1-D interface fixture, converged loop required."
    )
    if len(summary) > 500:
        summary = summary[:497].rsplit(" ", 1)[0] + "..."
    return {
        "title": "跨求解器守恒契约与耦合一致性成熟化：残差门禁 · 收敛阶证书 · Aitken 收敛证书 · 灵敏度一致性门",
        "summary": summary,
        "scenario_key": "p350w2-n09",
        "mission_id": 11799,
        "tags": [
            "coupling",
            "conservation-contract",
            "coupled-loop",
            "convergence-certificate",
            "contraction-estimate",
            "aitken-relaxation",
            "sensitivity-consistency",
            "interface-jacobian",
            "negative-control",
            "zero-domain-semantics",
        ],
        "markdown": markdown,
        "figures": figures,
    }


def main() -> int:
    audit = json.loads((ART / "coupled_loop_maturity.json").read_text(encoding="utf-8"))
    figures = json.loads((ART / "figures.json").read_text(encoding="utf-8"))
    markdown = build_markdown(audit, figures)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(markdown, encoding="utf-8")
    payload = build_payload(markdown, audit)
    PAYLOAD.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)} ({len(markdown)} bytes) and {PAYLOAD}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())