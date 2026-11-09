"""Anti-fabrication tests for the ThermalMultiphaseOpenLB OSS-INVENTORY report.

The claim under test is "no line number and no number in the report was typed by
hand".  Structural tests always run; pointer/probe/consistency tests need the
analysed source tree (``TMO_SRC``) or the committed probe artifact.

    TMO_SRC=/tmp/ThermalMultiphaseOpenLB pytest tests/test_oss_inventory_tmo.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "oss_inventory"
DOC = REPO_ROOT / "docs" / "oss_inventory" / "OSS-INV-ThermalMultiphaseOpenLB.md"
PNG = REPO_ROOT / "artifacts" / "oss_inventory" / "tmo_inventory.png"
PROBE = REPO_ROOT / "artifacts" / "oss_inventory" / "tmo_eos_probe.json"


def _load(name: str):
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def evidence():
    return _load("tmo_evidence")


@pytest.fixture(scope="module")
def inventory():
    return _load("tmo_inventory")


@pytest.fixture(scope="module")
def probe():
    assert PROBE.is_file(), "probe artifact missing: run tmo_eos_probe.py"
    return json.loads(PROBE.read_text())


SRC = os.environ.get("TMO_SRC")


# ---------------------------------------------------------------- structure
def test_doc_has_eight_sections():
    heads = re.findall(r"^## (.+)$", DOC.read_text(), flags=re.M)
    assert len(heads) >= 8, heads
    for kw in ("概况", "方法谱系", "边界", "多相", "GPU", "验证面", "映射", "Top3"):
        assert any(kw in h for h in heads), f"missing section: {kw}"


def test_scenario_key_and_title():
    text = DOC.read_text()
    assert "scenario_key=`oss-inventory`" in text
    assert text.splitlines()[0].startswith("# 【盘点】ThermalMultiphaseOpenLB")


def test_machine_blocks_parse(evidence):
    text = DOC.read_text()
    inv = re.findall(r"\[OSS-INV\]\n(.*?)\n\[/OSS-INV\]", text, flags=re.S)
    assert len(inv) == 1
    fields = {k.strip(): v.strip()
              for k, v in (line.split(":", 1) for line in inv[0].splitlines() if ":" in line)}
    for key in ("repo", "commit", "lang", "license", "methods", "multiphase",
                "boundary", "gpu", "verify", "summary"):
        assert fields.get(key, "").strip(), f"empty machine field {key}"
    assert fields["repo"] == evidence.REPO
    assert "C++" in fields["lang"]
    # honest negatives must be present, not glossed over
    for needle in ("NO MCMP", "NO free surface", "NO phase field", "NONE"):
        assert needle in fields["multiphase"] + fields["gpu"], needle
    assert "SINGLE-component" in fields["multiphase"]


def test_sol_blocks_cover_ledger_slugs(evidence):
    text = DOC.read_text()
    sols = re.findall(r"\[OSS-SOL\]\n(.*?)\n\[/OSS-SOL\]", text, flags=re.S)
    assert len(sols) >= 10
    slugs = set()
    for block in sols:
        f = {k.strip(): v.strip()
             for k, v in (line.split(":", 1) for line in block.splitlines() if ":" in line)}
        assert f["repo"] == evidence.REPO
        assert f["transfer"] in ("fits", "adapt", "reference")
        assert f["slug"].strip()
        # every file item must be a resolved ``path:line`` pointer
        for item in f["files"].split(", "):
            assert re.fullmatch(r"`[^`]+:\d+`", item), item
        slugs.add(f["slug"])
    wanted = {"GAP-LBM-MCMP-POISEUILLE", "MCMP 高密度比", "自由面（free surface）",
              "相场 Allen-Cahn", "GPU 融合 kernel", "分布式多卡", "稀疏/多孔",
              "案例组织", "测试与验证面", "CI"}
    assert wanted <= slugs, wanted - slugs
    assert any(s.startswith("NEW") for s in slugs), "gaps must be proposed as NEW items"


def test_figure_written_with_content():
    assert PNG.is_file() and PNG.stat().st_size > 20_000


# ------------------------------------------------------- probe self-consistency
def test_probe_numbers_are_internally_consistent(probe):
    rows = [r for r in probe["lattice_peng_robinson"]["rows"] if r["outcome"] == "OK"]
    assert len(rows) >= 5
    prev_ratio = None
    for r in rows:
        assert r["rho_liquid"] > r["rho_vapour"] > 0
        assert r["density_ratio"] == pytest.approx(r["rho_liquid"] / r["rho_vapour"])
        # density ratio must decrease monotonically with temperature
        if prev_ratio is not None:
            assert r["density_ratio"] < prev_ratio
        prev_ratio = r["density_ratio"]
        # Maxwell construction must close to machine precision
        assert abs(r["maxwell_area_residual"]["relative_to_psat_vdiff"]) < 1e-9


def test_probe_reproduces_repo_hardcoded_lattice_density(probe):
    """d2law2D hard-codes 8.72518 as the lattice liquid density at Tr=0.6."""
    calib = probe["d2law_calibration"]
    assert abs(calib["hardcode_vs_maxwell_rel_diff"]) < 1e-5
    assert calib["stated_physical_vapour"] == pytest.approx(0.9643)
    assert abs(probe["real_peng_robinson_water"]["rel_error_vapour_at_tr06"]) < 0.15


def test_carnahan_starling_defect_is_recorded(probe):
    cs = probe["carnahan_starling"]
    assert cs["outcome"] == "FAIL"
    assert "rhoc" in cs["error"]


def test_doc_numbers_match_probe(probe):
    """Every headline number in the doc must equal the probe artifact."""
    text = DOC.read_text()
    ok = [r for r in probe["lattice_peng_robinson"]["rows"] if r["outcome"] == "OK"]
    tr06 = next(r for r in ok if abs(r["tr"] - 0.6) < 1e-9)
    assert f"{tr06['rho_liquid']:.6f}" in text
    assert f"{tr06['density_ratio']:.4g}" in text
    calib = probe["d2law_calibration"]
    assert f"{calib['implied_physical_vapour']:.4g}" in text
    assert f"{abs(100 * calib['implied_vs_stated_rel_diff']):.1f}%" in text
    real = probe["real_peng_robinson_water"]
    assert f"{real['rows'][0]['rho_liquid']:.4g}" in text
    assert f"{abs(100 * real['rel_error_liquid_at_tr06']):.1f}%" in text


# ------------------------------------------------------ source-tree hard gates
@pytest.mark.skipif(not SRC, reason="set TMO_SRC to a ThermalMultiphaseOpenLB checkout")
def test_every_pointer_resolves(inventory, evidence):
    resolved = inventory.resolve_all(Path(SRC))
    assert len(resolved) == len(evidence.REFS)
    total = 0
    for key, items in resolved.items():
        assert items, f"no pointer for {key}"
        for item in items:
            total += 1
            line = (Path(SRC) / item.path).read_text(
                errors="replace").splitlines()[item.line - 1]
            assert item.needle in line, (key, item.path, item.line)
    assert total >= 100


@pytest.mark.skipif(not SRC, reason="set TMO_SRC to a ThermalMultiphaseOpenLB checkout")
def test_committed_doc_matches_generator(inventory, probe):
    src = Path(SRC)
    stats = inventory.code_stats(src)
    sweeps = inventory.sweep_alignment(src)
    assert inventory.render(src, probe, stats, sweeps).strip() == DOC.read_text().strip()


@pytest.mark.skipif(not SRC, reason="set TMO_SRC to a ThermalMultiphaseOpenLB checkout")
def test_sweep_alignment_finds_mismatches(inventory):
    sweeps = inventory.sweep_alignment(Path(SRC))
    # the C++ sweep never produces the sigma/a folders the eval script queries
    assert sweeps["sigma_consumed_but_not_produced"] == ["0.3", "0.31", "0.32"]
    assert sweeps["a_consumed_but_not_produced"] == ["0.051", "0.061"]
    assert sweeps["tr_theory_exceeds_sweep"] is True


@pytest.mark.skipif(not SRC, reason="set TMO_SRC to a ThermalMultiphaseOpenLB checkout")
def test_stats_are_derived_not_typed(inventory):
    st = inventory.code_stats(Path(SRC))
    assert st["total_loc"] > 10_000
    assert st["example_cpp_files"] == 12
    assert all(v == 0 for v in st["keyword_hits"].values()), st["keyword_hits"]
    assert st["license_files"] == []
    assert len(st["tmpfile_committed"]) == 12
    assert sum(g["loc"] for g in st["groups"].values()) <= st["header_loc"]