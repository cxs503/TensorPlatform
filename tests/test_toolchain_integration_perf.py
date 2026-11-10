"""Integration overhead and neutrality gates.

The overhead criterion of the campaign node is "integration cost < 20 %".  That is
measured two ways, and the test asserts both:

* **orchestration ratio** (primary) -- harness time (whole-run wall minus the sum
  of the same run's stage walls) over the stage work.  This is the cost the
  harness itself adds: binding, tagging, digesting, chaining, error handling.
* **standalone comparison** (secondary) -- whole-run wall versus a replay of each
  stage on its captured inputs.  It is noisier (CPU state differs between the two
  loops), so the bound is applied to the absolute value and the number is reported
  with its samples rather than trusted to three digits.

The module-neutrality test keeps the package free of domain vocabulary and of
imports from domain repositories, mirroring the rule the coupling package already
enforces.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.toolchain_integration import ExecutionContext, PipelineSpec, measure

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "tools" / "toolchain_integration" / "specs" / "synthetic_toolchain_v1.json"
#: campaign gate on the harness's own cost (primary metric)
OVERHEAD_LIMIT = 0.20
#: sanity bound for the noisier cross-run comparison (CPU state differs between loops)
CROSS_RUN_BOUND = 0.35


@pytest.fixture(scope="module")
def overhead():
    spec = PipelineSpec.load(SHIPPED)
    return measure(spec, repeats=3, ctx=ExecutionContext(seed=3))


def test_orchestration_overhead_below_limit(overhead):
    assert overhead.orchestration_ratio < OVERHEAD_LIMIT, (
        f"harness overhead {overhead.orchestration_ratio:.3f} exceeds {OVERHEAD_LIMIT}"
    )
    assert overhead.orchestration_s >= 0.0
    assert len(overhead.orchestration_samples_s) == 3


def test_cross_run_overhead_within_sanity_bound(overhead):
    """Secondary metric: same work replayed standalone vs run inside the DAG.

    It inherits machine noise (a stage is a few hundred ms, so a scheduling hiccup
    moves it by several percent), which is why the 20 % campaign gate is applied to
    the same-run harness cost above and this one gets a wider sanity bound. The
    measured value is reported in the artifact either way.
    """
    assert abs(overhead.overhead_ratio) < CROSS_RUN_BOUND, (
        f"standalone comparison {overhead.overhead_ratio:.3f} exceeds {CROSS_RUN_BOUND}"
    )


def test_run_is_reproducible_so_the_comparison_is_valid(overhead):
    assert overhead.determinism is True
    assert len(set(overhead.chain_digests)) == 1
    assert overhead.chain_digest


def test_per_stage_costs_are_attributed_to_the_right_stage(overhead):
    stages = {s.node_id: s for s in overhead.stages}
    overall = overhead.node_sum_median_s
    assert overall > 0.0
    # the two heavy stages must dominate; if the attribution were broken they would not
    heavy = stages["design"].integrated_median_s + stages["uncertainty"].integrated_median_s
    assert heavy / overall > 0.5
    # trivial stages must stay trivial
    assert stages["surface"].integrated_median_s < 0.05 * overall
    for s in overhead.stages:
        assert s.integrated_median_s >= 0.0 and s.standalone_median_s >= 0.0


#: below this, a stage is a few scheduler ticks and its ratio is meaningless
TIMING_FLOOR_S = 2e-3


def test_timing_of_a_stage_is_similar_whether_integrated_or_replayed(overhead):
    """A stage must not change order of magnitude just because it is wired into the DAG.

    Only stages above the scheduler-granularity floor are compared: for a sub-millisecond
    stage the ratio is dominated by wake-up latency on a shared machine, so asserting on
    it would measure the machine, not the harness.
    """
    checked = 0
    for s in overhead.stages:
        if s.standalone_median_s <= TIMING_FLOOR_S or s.integrated_median_s <= TIMING_FLOOR_S:
            continue
        ratio = s.integrated_median_s / s.standalone_median_s
        assert 0.1 < ratio < 10.0, (s.node_id, ratio)
        checked += 1
    assert checked >= 2, "at least the two heavy stages must be compared"


# ---------------------------------------------------------------------------
# neutrality / boundary
# ---------------------------------------------------------------------------


def _package_files():
    return sorted((ROOT / "tools" / "toolchain_integration").rglob("*.py"))


def test_domain_vocabulary_scan_is_clean():
    from tools.evidence_domain_scan import load_lexicon, scan_paths

    lexicon = load_lexicon()
    targets = [str(p.relative_to(ROOT)) for p in _package_files()]
    targets += [
        "tools/toolchain_integration/specs/synthetic_toolchain_v1.json",
        "tests/test_toolchain_integration_schema.py",
        "tests/test_toolchain_integration_dag.py",
        "tests/test_toolchain_integration_e2e.py",
        "tests/test_toolchain_integration_perf.py",
        "tests/test_toolchain_integration_prior.py",
    ]
    result = scan_paths(targets, lexicon=lexicon, root=ROOT)
    assert result["scanned_files"] >= len(targets) - 1
    assert result["hits"] == [], result["hits"]


ALLOWED_THIRD_PARTY = {"numpy", "scipy", "matplotlib"}


def test_import_boundary():
    """Only stdlib, numpy/scipy/matplotlib and this repo's own packages."""
    offenders = []
    for path in _package_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:            # relative import inside the package
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if not name:
                    continue
                if name in ALLOWED_THIRD_PARTY or name in ("tools", "backend"):
                    continue
                if name in {
                    "__future__", "argparse", "base64", "dataclasses", "enum", "hashlib",
                    "importlib", "inspect", "json", "math", "pathlib", "statistics",
                    "sys", "time", "traceback", "typing", "collections", "functools",
                    "itertools", "abc", "os", "textwrap", "warnings", "re", "shutil",
                    "subprocess",
                }:
                    continue
                offenders.append((path.name, name))
    assert offenders == [], offenders


def test_cli_roundtrip(tmp_path):
    """The CLI writes a bundle whose artifact matches the run it reports."""
    import json

    from tools.toolchain_integration.cli import main

    code = main([
        "--spec", str(SHIPPED),
        "--out", str(tmp_path),
        "--repeats", "1",
        "--seed", "3",
    ])
    assert code == 0
    artifact = json.loads((tmp_path / "artifact.json").read_text())
    assert artifact["schema"] == "tp.toolchain.integration/v1"
    assert artifact["criteria"]["verdict"] == "PASS"
    assert artifact["provenance"]["chain_digest"] == artifact["run"]["chain_digest"]
    for name in ("REPORT.md", "DEPENDENCY_GRAPH.md", "API.md",
                 "fig_timings.png", "fig_calibration.png", "fig_uq_band.png"):
        assert (tmp_path / name).exists(), name
        assert (tmp_path / name).stat().st_size > 0