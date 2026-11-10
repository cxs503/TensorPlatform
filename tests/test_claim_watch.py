#!/usr/bin/env python3
"""claim_watch test suite.

Every fixture is synthetic: repositories named ``ra``/``rb``, artifacts named
``alpha.json``/``beta.bin``, units ``uu``/``vv``. No domain vocabulary, no
domain numbers -- the module is a platform mechanism and its tests must not
borrow meaning from any domain.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from tools.claim_watch import checker, claims as claim_mod, fingerprint as fp_mod, harvest


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fp(arch="x86_64", python="3.11.6", pkg="1.2.3"):
    return fp_mod.env_fingerprint(arch=arch, platform_system="linux", byteorder="little",
                                  pointer_bits=64, python=python,
                                  package_versions={"synthetic-pkg": pkg})


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


@pytest.fixture()
def tree(tmp_path):
    """Synthetic worktree: one claim artifact, one dependency, one unrelated file."""
    root = tmp_path / "ra"
    dep = _write(str(root / "data" / "alpha.json"), '{"alpha": 1}\n')
    art = _write(str(root / "reports" / "beta.json"),
                 json.dumps({"value": 2.0, "unit": "uu"}) + "\n")
    other = _write(str(root / "data" / "gamma.json"), '{"gamma": 3}\n')
    resolver = claim_mod.RepoResolver({"ra": str(root)})
    claim = claim_mod.make_claim(
        claim_id="ra:reports/beta.json#0",
        value=2.0,
        unit="uu",
        artifact_uri="ra:reports/beta.json",
        artifact_sha256=claim_mod.sha256_file(art),
        verified_at="2026-01-01T00:00:00+00:00",
        evidence_grade="REAL",
        env_fingerprint=_fp(),
        depends_on=[{"uri": "ra:data/alpha.json",
                     "sha256": claim_mod.sha256_file(dep),
                     "kind": "artifact"}],
    )
    return {"root": str(root), "resolver": resolver, "claim": claim, "art": art,
            "dep": dep, "other": other, "env": _fp()}


# --------------------------------------------------------------------------- #
# schema / validation
# --------------------------------------------------------------------------- #
def test_valid_claim_is_accepted(tree):
    assert claim_mod.validate_claim(tree["claim"]) == []


def test_missing_required_field_is_invalid(tree):
    bad = dict(tree["claim"])
    bad.pop("artifact_sha256")
    result = checker.check_claim(bad, tree["resolver"], tree["env"])
    assert result.status == checker.INVALID
    assert result.effective_grade == "WITHHELD"


def test_malformed_hash_is_invalid(tree):
    bad = dict(tree["claim"], artifact_sha256="NOTAHASH")
    assert claim_mod.validate_claim(bad)
    assert checker.check_claim(bad, tree["resolver"], tree["env"]).status == checker.INVALID


def test_unexpected_key_is_invalid(tree):
    bad = dict(tree["claim"], colour="blue")
    assert any("unexpected" in e for e in claim_mod.validate_claim(bad))


# --------------------------------------------------------------------------- #
# OK path
# --------------------------------------------------------------------------- #
def test_intact_claim_is_ok_and_keeps_grade(tree):
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.OK
    assert (result.recorded_grade, result.effective_grade) == ("REAL", "REAL")
    assert result.causes == []


def test_withheld_claim_never_upgrades(tree):
    claim = dict(tree["claim"], evidence_grade="WITHHELD")
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.OK
    assert result.effective_grade == "WITHHELD"


# --------------------------------------------------------------------------- #
# BROKEN
# --------------------------------------------------------------------------- #
def test_tampered_artifact_is_broken(tree):
    with open(tree["art"], "a", encoding="utf-8") as fh:
        fh.write(" ")
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.BROKEN
    assert result.effective_grade == "WITHHELD"
    assert [c["code"] for c in result.causes] == ["artifact_hash_mismatch"]


def test_absent_artifact_is_broken(tree):
    os.remove(tree["art"])
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.BROKEN
    assert result.causes[0]["code"] == "artifact_missing"


# --------------------------------------------------------------------------- #
# STALE
# --------------------------------------------------------------------------- #
def test_changed_dependency_is_stale(tree):
    with open(tree["dep"], "a", encoding="utf-8") as fh:
        fh.write("\n")
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.STALE
    assert result.effective_grade == "WITHHELD"
    assert result.deps[0]["state"] == "changed"


def test_missing_dependency_is_stale(tree):
    os.remove(tree["dep"])
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.STALE
    assert result.causes[0]["code"] == "dep_missing"


def test_unpinned_dependency_is_stale(tree):
    claim = dict(tree["claim"])
    claim["depends_on"] = [{"uri": "ra:data/alpha.json", "sha256": None, "kind": "declaration"}]
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.STALE
    assert result.causes[0]["code"] == "dep_unpinned"


def test_precedence_broken_beats_stale(tree):
    os.remove(tree["dep"])
    with open(tree["art"], "a", encoding="utf-8") as fh:
        fh.write(" ")
    result = checker.check_claim(tree["claim"], tree["resolver"], tree["env"])
    assert result.status == checker.BROKEN
    codes = {c["code"] for c in result.causes}
    assert {"artifact_hash_mismatch", "dep_missing"} <= codes


# --------------------------------------------------------------------------- #
# SUSPECT / ENV_DRIFT
# --------------------------------------------------------------------------- #
def test_cross_architecture_is_suspect(tree):
    claim = dict(tree["claim"], env_fingerprint=_fp(arch="loongarch64"))
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.SUSPECT
    assert result.effective_grade == "WITHHELD"
    assert result.env["assurance"] == "asserted"


def test_arch_family_alias_is_not_a_mismatch():
    a = _fp(arch="x86_64")
    b = fp_mod.env_fingerprint(arch="amd64", platform_system="linux", byteorder="little",
                               pointer_bits=64, python="3.11.6",
                               package_versions={"synthetic-pkg": "1.2.3"})
    assert fp_mod.compare(a, b)["verdict"] == fp_mod.MATCH
    assert fp_mod.arch_family("loong64") == fp_mod.arch_family("loongarch64") == "loongarch64"


def test_runtime_drift_downgrades_one_step(tree):
    claim = dict(tree["claim"], env_fingerprint=_fp(python="3.10.0"))
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.ENV_DRIFT
    assert (result.recorded_grade, result.effective_grade) == ("REAL", "MOCK")


def test_runtime_drift_from_mock_reaches_withheld(tree):
    claim = dict(tree["claim"], env_fingerprint=_fp(python="3.10.0"), evidence_grade="MOCK")
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.effective_grade == "WITHHELD"


def test_unasserted_environment_is_reported_not_punished(tree):
    claim = dict(tree["claim"], env_fingerprint=None)
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.OK
    assert result.env["assurance"] == "unasserted"


def test_interface_only_claim_is_never_downgraded(tree):
    claim = dict(tree["claim"], evidence_grade="STUB")
    with open(tree["dep"], "a", encoding="utf-8") as fh:
        fh.write("\n")
    result = checker.check_claim(claim, tree["resolver"], tree["env"])
    assert result.status == checker.STALE
    assert result.effective_grade == "STUB"


def test_downgrade_is_monotone():
    ladder = {"REAL": 2, "MOCK": 1, "WITHHELD": 0}
    for grade in ("REAL", "MOCK", "WITHHELD"):
        for status in checker.STATUSES:
            effective = checker.downgrade(grade, status)
            if effective == "STUB":
                continue
            assert ladder[effective] <= ladder[grade]


# --------------------------------------------------------------------------- #
# registry resolution + harvesting
# --------------------------------------------------------------------------- #
def test_resolver_handles_repo_tokens_and_absolute_paths(tree):
    assert tree["resolver"].resolve("ra:data/alpha.json") == tree["dep"]
    assert tree["resolver"].resolve("file://" + tree["dep"]) == tree["dep"]
    assert tree["resolver"].resolve("zz:data/alpha.json") is None
    assert tree["resolver"].resolve("ra:data/none.json") is None


def test_extract_pairs_recognises_recorded_shapes():
    doc = {
        "explicit": {"path": "a/one.json", "sha256": "0" * 64},
        "manifest": {"b/two.json": "1" * 64, "c/three.md": "2" * 64},
        "prefixed": {"study": "d/four.json", "study_sha256": "3" * 64},
        "pairs": [["e/five.bin", "4" * 64]],
    }
    found = {(p["path"], p["sha256"]) for p in harvest.extract_pairs(doc)}
    assert ("a/one.json", "0" * 64) in found
    assert ("b/two.json", "1" * 64) in found
    assert ("d/four.json", "3" * 64) in found
    assert ("e/five.bin", "4" * 64) in found
    assert len(found) == 5


def test_build_claims_is_read_only_and_structured(tmp_path):
    root = tmp_path / "rb"
    _write(str(root / "data" / "alpha.json"), '{"alpha": 1}\n')
    _write(str(root / "reports" / "manifest.json"),
           json.dumps({"generated_on": "2026-02-02",
                       "artifacts": {"data/alpha.json": "a" * 64}}))
    before = {p: os.stat(os.path.join(dp, p)).st_mtime_ns
              for dp, _, fns in os.walk(str(root)) for p in fns}
    claims = harvest.build_claims(str(root), "rb", {"rb": str(root)}, current_env=_fp())
    after = {p: os.stat(os.path.join(dp, p)).st_mtime_ns
             for dp, _, fns in os.walk(str(root)) for p in fns}
    assert before == after
    assert len(claims) == 1
    claim = claims[0]
    assert claim["artifact_uri"] == "rb:reports/manifest.json"
    assert claim["depends_on"][0]["uri"] == "rb:data/alpha.json"
    assert claim["unit"] in ("uu", "digest", None)
    assert claim_mod.validate_claim(claim) == []


def test_ledger_roundtrip(tmp_path, tree):
    path = str(tmp_path / "ledger.json")
    assert claim_mod.dump_ledger([tree["claim"]], path) == 1
    back = claim_mod.load_ledger(path)
    assert back[0]["claim_id"] == tree["claim"]["claim_id"]


def test_summarize_counts_and_transitions(tree):
    claims = [tree["claim"], dict(tree["claim"], claim_id="ra:x#1",
                                 depends_on=[{"uri": "ra:data/alpha.json",
                                              "sha256": "b" * 64, "kind": "artifact"}])]
    results = checker.check_claims(claims, tree["resolver"], tree["env"])
    summary = checker.summarize(results)
    assert summary["n_claims"] == 2
    assert summary["by_status"][checker.OK] == 1
    assert summary["by_status"][checker.STALE] == 1
    assert summary["n_downgraded"] == 1
    assert summary["downgrades"]["REAL->WITHHELD"] == 1


def test_fingerprint_digest_is_order_independent():
    a = fp_mod.env_fingerprint(arch="x86_64", platform_system="linux", byteorder="little",
                               pointer_bits=64, python="3.11.6",
                               package_versions={"p": "1", "q": "2"})
    b = fp_mod.env_fingerprint(arch="x86_64", platform_system="linux", byteorder="little",
                               pointer_bits=64, python="3.11.6",
                               package_versions={"q": "2", "p": "1"})
    assert a["digest"] == b["digest"]
    assert fp_mod.digest_fields(a) == a["digest"]

# --------------------------------------------------------------------------- #
# batch probes on a synthetic tree (the shapes the audit uses on real trees)
# --------------------------------------------------------------------------- #
def _synthetic_repo(tmp_path, name="rc"):
    root = tmp_path / name
    _write(str(root / "src" / "alpha.json"), '{"alpha": 1}\n')
    _write(str(root / "src" / "beta.json"), '{"beta": 2}\n')
    manifest = _write(str(root / "reports" / "manifest.json"),
                      json.dumps({"generated_on": "2026-03-03",
                                  "unit": "vv",
                                  "artifacts": {"src/alpha.json": None}}))
    claim = claim_mod.make_claim(
        claim_id="rc:reports/manifest.json#000",
        value="sha256:" + "c" * 16,
        unit="digest",
        artifact_uri="rc:reports/manifest.json",
        artifact_sha256=claim_mod.sha256_file(manifest),
        verified_at="2026-03-03T00:00:00+00:00",
        evidence_grade="REAL",
        env_fingerprint=_fp(),
        depends_on=[{"uri": "rc:src/alpha.json",
                     "sha256": claim_mod.sha256_file(str(root / "src" / "alpha.json")),
                     "kind": "artifact"}],
    )
    return str(root), claim


def test_mutation_matrix_detects_three_fault_classes(tmp_path):
    from tools.claim_watch import audit as audit_mod

    root, claim = _synthetic_repo(tmp_path)
    resolver = claim_mod.RepoResolver({"rc": root})
    env = _fp()
    out = audit_mod.mutation_matrix([claim, claim, claim], resolver, env,
                                    workdir=str(tmp_path / "sandbox"), per_class=1)
    assert out["n_mutations"] == 3
    assert out["detection_rate"] == 1.0
    assert {row["fault"]: row["observed_status"] for row in out["rows"]} == {
        "tamper_artifact": checker.BROKEN,
        "change_dep": checker.STALE,
        "swap_env": checker.SUSPECT,
    }


def test_verified_fresh_excludes_drifted_dependency(tmp_path):
    from tools.claim_watch import audit as audit_mod

    root, claim = _synthetic_repo(tmp_path)
    resolver = claim_mod.RepoResolver({"rc": root})
    fresh, stats = audit_mod.verified_fresh_claims([claim], resolver, 10)
    assert stats["n_selected"] == 1, stats
    # mutate the dependency: the independent sha256sum ground truth now disagrees
    with open(os.path.join(root, "src", "alpha.json"), "a", encoding="utf-8") as fh:
        fh.write(" ")
    fresh2, stats2 = audit_mod.verified_fresh_claims([claim], resolver, 10)
    assert fresh2 == []
    assert stats2["n_hash_confirmed"] == 0


def test_sha256sum_ground_truth_matches_haslib(tmp_path):
    from tools.claim_watch import audit as audit_mod

    path = _write(str(tmp_path / "x.bin"), "payload")
    assert audit_mod.sha256sum_files([path])[path] == claim_mod.sha256_file(path)
