"""End-to-end pipeline on synthetic repositories + zero-domain-semantics guard."""
from __future__ import annotations

import json
from pathlib import Path

from tools.capability_evidence_chain import pipeline

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
LEXICON = REPO / "tools" / "evidence_domain_lexicon.json"


def _run(synth_config, synth_root, ks_boot=200):
    return pipeline.run(
        synth_config,
        root=synth_root,
        ks_boot=ks_boot,
        package_dir=PKG,
        lexicon_path=LEXICON if LEXICON.exists() else None,
        node="synth-node",
    )


def test_pipeline_end_to_end_produces_all_sections(synth_config, synth_root):
    art = _run(synth_config, synth_root)
    for key in ("config", "scan_stats", "association", "capabilities",
                "mde_table", "reclassification", "criteria", "digests"):
        assert key in art
    assert art["node"] == "synth-node"
    assert len(art["capabilities"]) == 4
    assert art["reclassification"]["summary"]["n_pass"] == 6


def test_pipeline_is_deterministic(synth_config, synth_root):
    a = _run(synth_config, synth_root)
    b = _run(synth_config, synth_root)
    assert a["digests"]["scan"] == b["digests"]["scan"]
    assert a["digests"]["capabilities"] == b["digests"]["capabilities"]
    assert a["digests"]["mde_table"] == b["digests"]["mde_table"]
    assert (a["reclassification"]["summary"]["claims_digest"]
            == b["reclassification"]["summary"]["claims_digest"])


def test_pipeline_criteria_reflect_small_fixture_honestly(synth_config, synth_root):
    art = _run(synth_config, synth_root)
    crit = art["criteria"]
    # only two synthetic repos: the five-repository requirement must fail
    assert crit["a_cross_repo_scan"]["repos_scanned"] == 2
    assert crit["a_cross_repo_scan"]["pass"] is False
    assert crit["b_scenarios"]["pass"] is True          # all three scenarios ran
    assert crit["c_mde_table"]["pass"] is False         # 4 rows < 10 by design
    assert crit["d_reclassification"]["pass"] is True
    assert crit["all_pass"] is False


def test_artifact_is_written_and_reloadable(synth_config, synth_root, tmp_path):
    art = _run(synth_config, synth_root)
    out = pipeline.write_artifact(art, tmp_path / "nested" / "art.json")
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["digests"]["scan"] == art["digests"]["scan"]


def test_zero_domain_check_passes_on_the_package():
    result = pipeline.zero_domain_check(PKG, LEXICON if LEXICON.exists() else None)
    assert result["status"] in ("PASS", "SKIPPED")
    assert result.get("n_hits", 0) == 0, result.get("hits")


def test_zero_domain_check_detects_a_planted_term(tmp_path):
    prepared = tmp_path / "pkg"
    prepared.mkdir()
    lex = tmp_path / "lex.json"
    lex.write_text(json.dumps({"ascii_terms": ["zetaterm"], "cjk_terms": ["合成域词"]}),
                   encoding="utf-8")
    clean = pipeline.zero_domain_check(prepared, lex)
    assert clean["status"] == "PASS"
    (prepared / "module.py").write_text("VALUE = 'zetaterm'  # 合成域词\n", encoding="utf-8")
    dirty = pipeline.zero_domain_check(prepared, lex)
    assert dirty["status"] == "FAIL" and dirty["n_hits"] >= 2


def test_zero_domain_check_degrades_when_lexicon_missing(tmp_path):
    res = pipeline.zero_domain_check(tmp_path, None)
    assert res["status"] == "SKIPPED"


def test_tests_use_synthetic_domain_names_only():
    """The test fixtures must not smuggle a real physics domain into the package."""
    text = "\n".join(
        p.read_text(encoding="utf-8") for p in (PKG / "tests").glob("*.py")
    )
    assert "synth_solver_a" in text
    forbidden_names = ("Tensor" + "LBM", "Tensor" + "FEM", "KVL" + "CC2", "Holt" + "rop")
    for forbidden in forbidden_names:
        assert forbidden not in text

def test_criteria_zero_domain_block_is_boolean(synth_config, synth_root):
    art = _run(synth_config, synth_root)
    block = art["criteria"]["f_zero_domain"]
    assert set(block) >= {"status", "n_hits", "pass"}
    assert isinstance(block["pass"], bool)
    assert art["criteria"]["all_pass"] is False   # 2 synthetic repos < 5 by design
