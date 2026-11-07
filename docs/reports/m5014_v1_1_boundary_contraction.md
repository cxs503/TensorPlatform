# Platform Report v1.1 — Boundary Contraction and Numeric Re-Derivation (judge case #616)

**Mission**: `IN17-B5` (campaign node, TensorPlatform) · **Branch**: `agent/m5014`
**Subject artifact**: `docs/reports/system_research_report_v1.md` (v1.0 → v1.1, in place)
**Verdicts**: boundary gate **PASS** · T3 anti-fabrication **PASS** (0 unvalidated of 106 claims)

---

## 1. Case: what was wrong with v1.0

The platform research report is the *organization-level* account of a domain-neutral harness. v1.0 broke
that boundary in two independent ways.

**(a) Domain-engineering content in a platform document.** §2.3 restated the fleet's flagship application case
in full — hull class, principal dimensions, installed power, a named geographic route, and an environmental
state matrix — plus domain capability enumerations in §2.1 and domain wording in the plan descriptions.
None of that belongs in the harness report: it belongs to the domain repository that owns the case.

**(b) Numbers that its own provenance artifact did not support.** The report's appendix claimed
"T3 Validation: 47/47 claims validated", while the archived validator actually resolved 24 of 107 claims
(`artifacts/m5014_gate_evidence.json → legacy_validator_on_v1_0`). Concretely:

| Defect in v1.0 | Evidence |
|---|---|
| Repository table rows summed to **329** missions while the total line said **330** | `by_repository` sums to 330; one row printed 72 instead of 73 |
| Twelve per-repository success rates printed values found nowhere in the metrics artifact | e.g. printed 94.4 % / 92.9 % / 88.9 % vs recorded 91.8 % / 95.2 % / 96.3 % |
| Campaign node total printed **82** with status split 18 / 33 / 31, while the per-plan table in the same section sums to **65** | `sum(campaign_nodes.node_count) = 65`, status split 17 / 33 / 15 |
| Derived shares (21.8 %, 58.5 %, 37.8 %, 22 %) inherited the wrong denominators | recomputed 22.1 %, 58.8 %, 23.1 %, 26.2 % |
| §5.3 quoted a 47/47 validator result the validator never produced | legacy run: 24/107 |

The old validator could not catch any of this: it only knew four metric keys, and it resolved *any* percentage
against the single global success rate, so "91.4 %" was accepted as evidence of "91.8 %". A gate that cannot
say NO is not a gate.

## 2. Method

Three mechanisms, each with a machine verdict, all domain vocabulary injected as data
(`config/report_boundary_gate.json`, `config/report_boundary_edits_v1_1.json`) so that
`backend/`, `tools/` and `tests/` stay free of domain semantics.

1. **Hardened T3 validator** (`tools/chronicle/validate_report.py`). It now builds a *provenance index* from
   the metrics artifact — mission counts and status shares, per-repository counts / rates / shares and their
   cumulative top-k sums, per-model counts and shares, per-plan and per-status node counts, hourly
   maximum/minimum, per-worker reuse, window hours, lifecycle seconds↔minutes — and resolves each claim
   against it in three ways:
   * **subject scoping** — on a record line (markdown table row, campaign registry entry) only entries whose
     subject appears on that line are eligible, so a cell can never borrow a neighbour's value;
   * **unit-family scoping** — a count claim may only be resolved by a metric family of the same kind
     ("4 repositories" is not satisfied by a repository that happens to have four missions);
   * **declared constants** — bounds and gate thresholds that are not measurements
     (`config/report_declared_constants.json`), each pinned to the exact wording it may back and counted
     separately, never as metric-validated.
   Extraction was also made claim-honest: `(?<![\d.])` stops a heading number ("### 4.2 Repository …") and a
   rate tail ("6.9 missions/hour") from becoming phantom claims.
2. **Deterministic re-derivation** (`tools/chronicle/tighten_report_v1_1.py`). Every numeric cell is recomputed
   from `artifacts/m4977_metrics.json`; boundary edits are loaded from the injected data file. Anchors fail
   loud, the run is idempotent (regex-anchored, fixpoint-stable self-referential numbers), and each edit is
   logged with its provenance source in `artifacts/m5014_v1_1_edits.json` (34 edits: 26 provenance,
   6 boundary, plus the version note).
3. **Boundary gate** (`tools/chronicle/report_boundary.py`). Eight independent checks: version note (marker,
   precedent id, rationale, domain-owner reference), ten-section structure, case-detail patterns, domain
   terms outside whitelisted record lines, presence of the single reference line, code neutrality of
   `tools/ backend/ tests/ src/ optimizer/ api/`, metrics artifact existence, T3 verdict. Legacy domain code
   is snapshotted in `artifacts/m5014_code_neutrality_baseline.json` (33 files → WARN, out of scope);
   any *new* or newly-dirty file is a FAIL.

## 3. Results

| Quantity | v1.0 text | v1.1 text |
|---|---|---|
| Numeric claims extracted | 107 (legacy extractor) / 106 (hardened) | 106 |
| Resolved by metrics | 24 (legacy gate) / 80 (hardened gate) | **102** |
| Declared non-measured constants | — | 4 |
| **Unvalidated (fabrication surface)** | **22** | **0** |
| Boundary gate | FAIL (VERSION_NOTE ×3, DOMAIN_DETAIL ×1, DOMAIN_TERM ×3, REFERENCE_LINE ×1) | **PASS (8/8)** |
| Repository rows sum to mission total | 329 ≠ 330 | 330 = 330 |
| Campaign node total vs per-plan sum | 82 vs 65 | 65 = 65 |
| Domain case detail in §2.3 | full restatement | single reference line to the domain repository |

**Figure** — `artifacts/m5014_gate_evidence.png`: left panel, resolution of the same metrics artifact against
the v1.0 and v1.1 texts (unvalidated 22 → 0); right panel, the boundary-gate check matrix (v1.0 FAIL → v1.1 PASS).
Machine-readable source: `artifacts/m5014_gate_evidence.json`.

The report keeps its ten sections, its figures, its table structure and every organization-level metric
(throughput 53 h⁻¹ peak, concurrency 5 workers, coverage matrix 12 repositories × 330 missions, model
diversity 6). `artifacts/m4977_metrics.json` was restored from commit `5df5916a` (the archive the report cites)
so that the provenance chain is reproducible from the worktree.

## 4. Reproduce

```bash
python3 tools/chronicle/tighten_report_v1_1.py --t3-json artifacts/m5014_t3_validation.json   # idempotent
python3 tools/chronicle/validate_report.py --report docs/reports/system_research_report_v1.md \
        --metrics artifacts/m4977_metrics.json --declared config/report_declared_constants.json \
        --json-out artifacts/m5014_t3_validation.json --strict        # PASS: 106 claims, 0 unvalidated
python3 tools/chronicle/report_boundary.py --write-baseline           # legacy snapshot (once)
python3 tools/chronicle/report_boundary.py                            # BOUNDARY GATE: PASS
python3 tools/chronicle/m5014_evidence.py                             # evidence JSON + figure
python3 -m pytest tests/test_report_boundary.py tests/test_chronicle_tools.py -q   # 48 passed
```

## 5. Caveats and scope

* **Declared constants are not measurements.** Four printed numbers (a 25 % bound, the 3 % six-element gate
  threshold, an 8-hour worker observation span, and the "top 4 repositories" selection size) are declared,
  not derived. They are counted separately in every verdict; a stricter future rule should either snapshot
  the worker span in the metrics extractor or drop the sentence.
* **Legacy domain debt is baselined, not fixed.** 33 pre-existing `tools/`/`backend/`/`tests/` files carry
  domain vocabulary (ice/polar modules). Refactoring them is outside this node; the gate prevents *growth*
  and this change adds zero new debt.
* **Residual false-pass surface.** Subject scoping applies to record lines; in free prose a number that
  coincides with some other metric value can still resolve. The gate narrows borrowing, it does not
  eliminate coincidence — hence the unit-family and subject rules, and the small, justified declared set.
* **Metric window.** All numbers describe the 48-hour window of `artifacts/m4977_metrics.json`; the report is
  a snapshot, not a steady-state claim.

## 6. Error judgement (vs. baseline)

| Key quantity | Baseline (source of truth) | Report value | Relative error | < 3 %? |
|---|---|---|---|---|
| Total missions | 330 (`missions.total_missions`) | 330 | 0 % | yes |
| Σ repository rows | 330 (`by_repository`) | 330 | 0 % | yes (v1.0: 329, 0.30 %) |
| Campaign nodes | 65 (`sum(campaign_nodes)`) | 65 | 0 % | yes (v1.0: 82, 26.2 % — rejected) |
| Status split done/in-flight/blocked | 17 / 33 / 15 | 17 / 33 / 15 | 0 % | yes (v1.0: 18 / 33 / 31) |
| Success rate | 91.82 % (`303/330`) | 91.8 % | 0.02 % | yes |
| Unvalidated numeric claims | 0 (gate target) | 0 | — | yes (v1.0: 22) |

No key quantity exceeds the 3 % gate; the two v1.0 defects that did (node total 26.2 %, status split up to
106 %) were corrected rather than reported.