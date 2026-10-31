"""Guard: the reissued report (report id=700 rework, mission 774) must stay in
lock-step with the run artifacts.

The failure mode that produced report 700's five numeric defects was "numbers
typed by hand, drifting away from the artifacts". These tests recompute the
numbers straight from ``runs/ice_class/*`` and assert that the very same strings
appear in ``docs/ice_class/report_m774.md`` — so a hand edit of the report, or a
regenerated artifact, fails CI instead of silently publishing a wrong report.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "ice_class"
REPORT = ROOT / "docs" / "ice_class" / "report_m774.md"
METRICS = ROOT / "docs" / "ice_class" / "metrics_m774.json"

pytestmark = pytest.mark.skipif(
    not (RUN / "pareto_front.csv").exists() or not REPORT.exists(),
    reason="ice-class rework artifacts not checked out",
)


def _rows():
    with (RUN / "pareto_front.csv").open(newline="") as fh:
        return [
            {k: float(v) for k, v in r.items() if k != "feasible"}
            for r in csv.DictReader(fh)
        ]


@pytest.fixture(scope="module")
def rows():
    return _rows()


@pytest.fixture(scope="module")
def markdown():
    return REPORT.read_text(encoding="utf-8")


def _assert_present(md: str, needle: str, what: str):
    assert needle in md, f"{what} {needle!r} not found in reissued report"


def test_objective_ranges_match_artifact(rows, markdown):
    """Defects 1-3: the published ranges must equal min/max of the CSV."""
    section = markdown.split("### 3.2 目标区间")[1].split("###")[0]
    for key, fmt in (("R_ice", "%.2f"), ("R_ow", "%.2f"), ("GM", "%.4f")):
        lo = min(r[key] for r in rows)
        hi = max(r[key] for r in rows)
        _assert_present(section, fmt % lo + " | " + fmt % hi, f"{key} range row")
    # the stale report-700 numbers may only survive as the quoted defect, never
    # as the published range of this reissue
    for stale in ("[26.3, 200.4]", "[54.8, 304.6]", "[1.06, 8.75]"):
        assert stale not in section, f"stale claim {stale!r} survived in the range table"
        assert stale in markdown, "the correction table should quote the stale claim"


def test_representative_designs_match_artifact(rows, markdown):
    """Defect 4: representative points must be the actual CSV extreme rows."""
    ice = min(rows, key=lambda r: r["R_ice"])
    ow = min(rows, key=lambda r: r["R_ow"])
    gm = max(rows, key=lambda r: r["GM"])
    for name, r in (("Ice-optimal", ice), ("OW-optimal", ow), ("GM-optimal", gm)):
        line = ("| %s" % name, "%.2f" % r["R_ice"], "%.2f" % r["R_ow"], "%.4f" % r["GM"])
        assert any(all(s in ln for s in line) for ln in markdown.splitlines()), (
            "%s row (%.2f kN / %.2f kN / %.4f m) missing from report"
            % (name, r["R_ice"], r["R_ow"], r["GM"])
        )


def test_single_seed_claim(rows):
    """Defect 5: the artifact records one seed only, so no multi-seed claim."""
    meta = json.loads((RUN / "optimization_result.json").read_text())["metadata"]
    md = REPORT.read_text(encoding="utf-8")
    assert meta["seed"] == 42
    for ln in md.splitlines():
        if "Tested across 8 random seeds" in ln:
            assert "| 5 |" in ln and "整段删除" in ln, (
                "retracted multi-seed claim quoted outside the correction table: %s" % ln
            )
    # every mention of the retracted claim must sit in a negating context
    for m in re.finditer(r"8\s*个\s*种子", md):
        ctx = md[max(0, m.start() - 30): m.end() + 40]
        assert any(w in ctx for w in ("不能", "不得", "夸大", "删除", "无据", "撤回")), (
            "multi-seed claim asserted without evidence: ...%s..." % ctx
        )
    assert "seed=42" in md.replace(" ", "") or "seed=42" in md
    # no per-seed run directories exist -> claim is WITHHELD, must be stated
    assert not list(RUN.glob("seed_*")), "unexpected multi-seed artifacts"
    assert "WITHHELD" in md


def test_error_section_answers_under_three_percent(markdown):
    """Defect 7: R_ice / R_ow must answer '是否<3%' explicitly, not 'N/A'."""
    section = markdown.split("## 8.")[1].split("## 9.")[0]
    assert "是否 <3%？" in section
    for qty in ("R_ice", "R_ow", "GM"):
        assert qty in section
    rows = [ln for ln in section.splitlines() if ln.startswith("| R_ice") or ln.startswith("| R_ow")]
    assert len(rows) == 2
    for ln in rows:
        assert "**否**" in ln, f"quantity row does not answer explicitly: {ln}"
    assert "N/A" not in section.split("判定口径")[0], "vague N/A verdict survived"


def test_figure_exemption_documented(markdown):
    """Defect 6: missing field-contour plots need a written exemption."""
    assert "### 5.1 为什么没有「场量云图」" in markdown
    assert "本任务不是场仿真" in markdown


def test_metrics_json_consistent_with_csv(rows):
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    assert m["n_rows_csv"] == len(rows)
    assert abs(m["R_ice_lo"] - min(r["R_ice"] for r in rows)) < 1e-9
    assert abs(m["R_ice_hi"] - max(r["R_ice"] for r in rows)) < 1e-9
    assert abs(m["R_ow_hi"] - max(r["R_ow"] for r in rows)) < 1e-9
    assert abs(m["GM_hi"] - max(r["GM"] for r in rows)) < 1e-9
    assert m["convergence"]["max_hv_change_rate_last_5"] < 0.01
    assert m["constraints"]["all_pass"] is True
    assert m["tradeoff_ratio"] > 0.10


def test_front_audit_disclosed(rows):
    """The dominated duplicate found during the rework must stay disclosed."""
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    assert m["domination_pairs"] == []           # strict float: mission criterion
    assert m["domination_pairs_merged"], "analytic-equal GM case no longer detected"
    md = REPORT.read_text(encoding="utf-8")
    assert "### 3.2b 前沿非支配性审计" in md
    assert "退化解" in md