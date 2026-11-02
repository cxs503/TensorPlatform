"""PG9 figure-data provenance gate — regression tests.

The gate executes a figure generator under a recording bootstrap and adjudicates
every plotted array against the artifact tree. These tests pin the semantics
that cost the paper line two drafts (H3 "figs 2-5 synthesised"):

* artifact-backed series  -> RESOLVED, exit 0;
* inline-synthesised data -> PG9_synthesised_figure_data, exit 1;
* declared analytic curve -> ANALYTIC_DECLARED only if it carries a citation;
* a declaration without citation is a FAIL, not a pass;
* a crashing generator is reported as unverified (WARN), never as PASS.

Run: python3 -m pytest --noconftest backend/tests/test_figure_provenance_gate.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.paper_gate.figure_provenance import (  # noqa: E402
    check_figure_provenance,
    citation_values,
    harvest_series,
    resolve_series,
)

TOOL = Path(__file__).resolve().parents[2] / "tools" / "paper_gate" / "figure_provenance.py"


# ───────────────────────────── fixtures ─────────────────────────────


@pytest.fixture()
def workspace(tmp_path):
    """A bundle with an artifact JSON and an output figure dir."""
    art = tmp_path / "artifacts"
    art.mkdir()
    re = [1e6, 2e6, 5e6, 1e7, 2e7]
    cf = [0.00480, 0.00445, 0.00403, 0.00372, 0.00350]
    (art / "resistance.json").write_text(json.dumps(
        {"runs": [{"Re": r, "Cf": c} for r, c in zip(re, cf)],
         "meta": {"scale": 1.0}}) + "\n", encoding="utf-8")
    # a CSV artifact with a different series
    (art / "grid.csv").write_text(
        "h,error\n0.10,4.1e-3\n0.05,1.02e-3\n0.025,2.55e-4\n0.0125,6.30e-5\n",
        encoding="utf-8")
    figs = tmp_path / "figures"
    figs.mkdir()
    return {"root": tmp_path, "art": art, "figs": figs}


def write_gen(ws, body: str, name: str = "gen.py") -> Path:
    p = ws["root"] / name
    p.write_text(body, encoding="utf-8")
    return p


GEN_FROM_JSON = '''
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

here = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(here, "artifacts", "resistance.json")))
re = [row["Re"] for row in d["runs"]]
cf = [row["Cf"] for row in d["runs"]]
fig, ax = plt.subplots()
ax.plot(re, cf, "o-", label="artifact")
fig.savefig(os.path.join(here, "figures", "resistance.png"))
'''

GEN_SYNTHESISED = '''
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

here = os.path.dirname(os.path.abspath(__file__))
re = np.array([1e6, 2e6, 5e6, 1e7, 2e7])
cf = 0.075 / (np.log10(re) - 2.0) ** 2      # computed, never read from an artifact
fig, ax = plt.subplots()
ax.plot(re, cf)
fig.savefig(os.path.join(here, "figures", "ittc57.png"))
'''

GEN_CRASH = '''
raise RuntimeError("boom: missing input artifact")
'''

GEN_EMPTY = '''
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

here = os.path.dirname(os.path.abspath(__file__))
fig, ax = plt.subplots()
ax.text(0.5, 0.5, "schematic only")
fig.savefig(os.path.join(here, "figures", "schematic.png"))
'''


# ───────────────────────────── corpus / resolver ─────────────────────────────


def test_harvest_finds_json_and_csv_series(workspace):
    corpus, stats = harvest_series([workspace["art"]])
    assert stats["files"] == 2
    assert stats["series"] >= 4          # Re, Cf, h, error
    lengths = sorted(corpus)
    assert 5 in lengths and 4 in lengths
    pointers = {it["pointer"] for items in corpus.values() for it in items}
    assert any("Re" in p for p in pointers)
    assert any("error" in p for p in pointers)


def test_resolve_exact_and_window(workspace):
    corpus, _ = harvest_series([workspace["art"]])
    import numpy as np
    assert resolve_series(np.array([0.00480, 0.00445, 0.00403, 0.00372, 0.00350]),
                          corpus, 1e-6, 1e-12)["mode"] == "exact"
    # a 2-point slice of the 5-point Cf series must resolve as a window
    hit = resolve_series(np.array([0.00445, 0.00403]), corpus, 1e-6, 1e-12)
    assert hit is not None and hit["mode"].startswith("window")
    assert resolve_series(np.array([9.9, 9.8, 9.7]), corpus, 1e-6, 1e-12) is None


# ───────────────────────────── adjudication ─────────────────────────────


def test_artifact_backed_figure_passes(workspace):
    gen = write_gen(workspace, GEN_FROM_JSON)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["verdict"] == "PASS", out["findings"]
    assert out["coverage"]["provenanced"] == out["coverage"]["plotted_series"] == 1
    assert out["series"][0]["verdict"] == "RESOLVED"
    assert "resistance.json" in out["series"][0]["citation"]


def test_synthesised_figure_fails(workspace):
    gen = write_gen(workspace, GEN_SYNTHESISED)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["verdict"] == "FAIL"
    codes = [f["code"] for f in out["findings"]]
    assert "PG9_synthesised_figure_data" in codes
    synth = [f for f in out["findings"] if f["code"] == "PG9_synthesised_figure_data"][0]
    assert "ittc57.png" in synth["figure"]
    assert synth["line"] is not None          # call-site is reported, not just a verdict


def test_synthesised_fails_only_when_no_artifact_holds_it(workspace):
    """Same generator, but the artifact tree *does* hold the values -> RESOLVED.

    The gate judges values, not data flow; that limitation is declared in the
    module docstring, and this test pins the intended (conservative) behaviour.
    """
    import numpy as np
    re = [1e6, 2e6, 5e6, 1e7, 2e7]
    cf = list(0.075 / (np.log10(np.array(re, dtype=float)) - 2.0) ** 2)
    (workspace["art"] / "ittc57_reference.json").write_text(
        json.dumps({"cf_curve": cf}), encoding="utf-8")
    gen = write_gen(workspace, GEN_SYNTHESISED)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["verdict"] == "PASS"
    assert out["series"][0]["verdict"] == "RESOLVED"


def test_analytic_declaration_with_citation_is_graded(workspace):
    gen = write_gen(workspace, GEN_SYNTHESISED)
    decl = workspace["root"] / "figprov.json"
    decl.write_text(json.dumps({"figures": {"ittc57.png": [{
        "kind": "analytic",
        "citation": "ITTC-1957 Performance Prediction Method, §3 (Cf = 0.075/(log10 Re - 2)^2)",
        "expr": "0.075/(log10(Re)-2)**2"}]}}), encoding="utf-8")
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"],
                                  declarations=[decl])
    assert out["verdict"] == "PASS", out["findings"]
    assert out["series"][0]["verdict"] == "ANALYTIC_DECLARED"
    assert out["citations"] and "analytic:" in out["citations"][0]


def test_declaration_without_citation_fails(workspace):
    gen = write_gen(workspace, GEN_SYNTHESISED)
    decl = workspace["root"] / "figprov.json"
    decl.write_text(json.dumps({"figures": {"ittc57.png": [{"kind": "analytic"}]}}),
                    encoding="utf-8")
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"],
                                  declarations=[decl])
    assert out["verdict"] == "FAIL"
    assert [f["code"] for f in out["findings"]] == ["PG9_declaration_missing_citation"]
    assert out["series"][0]["verdict"] == "DECLARATION_INCOMPLETE"


def test_generator_crash_is_warn_not_pass(workspace):
    gen = write_gen(workspace, GEN_CRASH)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["verdict"] == "WARN"
    assert any(f["code"] == "PG9_generator_crash" for f in out["findings"])
    assert out["coverage"]["provenanced"] == 0


def test_schematic_without_data_is_warn(workspace):
    gen = write_gen(workspace, GEN_EMPTY)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["verdict"] == "WARN"
    assert any(f["code"] == "PG9_figure_without_recorded_data" for f in out["findings"])


def test_partial_provenance_warns_on_synthetic_errorbars(workspace):
    gen = write_gen(workspace, '''
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

here = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(here, "artifacts", "grid.csv"))) if False else None
import csv
rows = list(csv.DictReader(open(os.path.join(here, "artifacts", "grid.csv"))))
h = [float(r["h"]) for r in rows]
err = [float(r["error"]) for r in rows]
yerr = np.full(len(h), 1e-4)          # invented error bars, no artifact behind them
fig, ax = plt.subplots()
ax.errorbar(h, err, yerr=yerr)
fig.savefig(os.path.join(here, "figures", "grid.png"))
''')
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    assert out["series"][0]["verdict"] == "RESOLVED"
    assert any(f["code"] == "PG9_partial_provenance" for f in out["findings"])
    assert out["verdict"] == "WARN"


def test_citation_is_pasteable(workspace):
    """Every RESOLVED series ships a citation an author can paste and re-derive."""
    gen = write_gen(workspace, GEN_FROM_JSON)
    out = check_figure_provenance(gen, artifact_roots=[workspace["art"]],
                                  out_fig_dir=workspace["figs"])
    cite = out["citations"][0]
    values = citation_values(cite)
    assert len(values) == 5
    import numpy as np
    assert np.allclose(values, [0.00480, 0.00445, 0.00403, 0.00372, 0.00350])


def test_citation_values_reads_csv_and_column_pointer(workspace):
    csv_cite = f'{workspace["art"] / "grid.csv"}#$.error'
    import numpy as np
    assert np.allclose(citation_values(csv_cite), [4.1e-3, 1.02e-3, 2.55e-4, 6.30e-5])
    col_cite = f'{workspace["art"] / "resistance.json"}#$.runs[*].Re'
    assert np.allclose(citation_values(col_cite),
                       [1e6, 2e6, 5e6, 1e7, 2e7])


# ───────────────────────────── CLI contract ─────────────────────────────


def test_cli_exit_codes(workspace):
    ok = write_gen(workspace, GEN_FROM_JSON, "ok.py")
    bad = write_gen(workspace, GEN_SYNTHESISED, "bad.py")
    base = [sys.executable, str(TOOL), "--artifact-root", str(workspace["art"]),
            "--out-fig-dir", str(workspace["figs"])]
    r_ok = subprocess.run(base + ["--generator", str(ok), "--json-out",
                                  str(workspace["root"] / "ok.json")],
                          capture_output=True, text=True, timeout=300)
    r_bad = subprocess.run(base + ["--generator", str(bad)],
                           capture_output=True, text=True, timeout=300)
    r_missing = subprocess.run(base + ["--generator", str(workspace["root"] / "nope.py")],
                               capture_output=True, text=True, timeout=300)
    assert r_ok.returncode == 0, r_ok.stderr
    assert r_bad.returncode == 1, r_bad.stderr
    assert r_missing.returncode == 2
    payload = json.loads(Path(workspace["root"] / "ok.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS" and payload["corpus"]["series"] >= 4