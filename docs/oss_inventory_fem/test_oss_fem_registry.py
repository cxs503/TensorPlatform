#!/usr/bin/env python3
"""Offline integrity tests for the FEM OSS inventory (mission m8692).

These tests are network-free: they assert that the curated registry is exactly a
function of the API captures stored in raw/. Run:

    python3 -m pytest docs/oss_inventory_fem/test_oss_fem_registry.py -q
"""
from __future__ import annotations

import json
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")


def _j(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def reg():
    with open(os.path.join(HERE, "oss_fem_registry.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def details():
    return _j("repo_details.json")["repos"]


@pytest.fixture(scope="module")
def commits():
    return _j("repo_commits.json")["repos"]


@pytest.fixture(scope="module")
def readmes():
    return _j("repo_readme.json")["repos"]


def test_minimum_inventory_size(reg):
    assert len(reg["repositories"]) >= 20
    core = [r for r in reg["repositories"] if r["tier"] == 1 and r["tier1_class"] == "solver-core"]
    assert len(core) >= 8, f"tier1 solver-core only {len(core)}"
    assert reg["metadata"]["search_rounds"] >= 8
    assert reg["metadata"]["candidate_pool_size"] >= 20


def test_required_fields_present(reg):
    required = ["repo", "tier", "lang", "license", "stars", "last_commit", "focus", "summary",
                "tier_rationale", "completeness", "evidence", "html_url"]
    for r in reg["repositories"]:
        for k in required:
            assert r.get(k) not in (None, "", []), f"{r['repo']}: missing {k}"
        assert r["tier"] in (1, 2, 3)
        assert re.match(r"^[\w.-]+/[\w.-]+$", r["repo"])
        assert isinstance(r["stars"], int) and r["stars"] >= 0
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", r["last_commit"])


def test_no_duplicate_repos(reg):
    names = [r["repo"] for r in reg["repositories"]]
    assert len(names) == len(set(names))


def test_tier1_has_discretisation_core(reg):
    """tier1 == complete FEM solver: element library + assembly must be evidenced."""
    for r in reg["repositories"]:
        if r["tier"] == 1:
            assert r["completeness"]["geometry_elements"], r["repo"]
            assert r["completeness"]["assembly"], r["repo"]


def test_fields_traceable_to_raw_capture(reg, details, commits):
    """Zero hand-typed metadata: every field must equal the captured API value."""
    for r in reg["repositories"]:
        d = details[r["repo"]]
        c = commits[r["repo"]]
        assert r["stars"] == d["stars"], r["repo"]
        assert r["lang"] == d["lang"], r["repo"]
        assert r["license"] == d["license"], r["repo"]
        assert r["last_commit"] == c["last_commit"][:10], r["repo"]
        assert r["evidence"]["detail_api"] == "/repos/" + r["repo"]


def test_evidence_quotes_are_grounded(reg, details, readmes):
    for r in reg["repositories"]:
        q = r["evidence"]["readme_quote"].lower()
        head = (readmes[r["repo"]].get("readme_head") or "").lower()
        desc = (details[r["repo"]].get("description") or "").lower()
        assert q in head or q in desc, f"{r['repo']}: quote not grounded"
        assert r["evidence"]["quote_source"] in ("readme", "description")


def test_gap_proposals(reg):
    gaps = reg["gap_proposals"]
    assert len(gaps) >= 6
    names = {r["repo"] for r in reg["repositories"]}
    for g in gaps:
        assert g["gap_id"].startswith("GAP-FEM-")
        assert g["priority"] in ("high", "medium", "low")
        assert g["axis"] in ("基准验证面", "可微化", "HPC 扩展", "工程化")
        assert g["acceptance"].strip()
        assert g["anchor_repos"], g["gap_id"]
        for a in g["anchor_repos"]:
            assert a in names, f"{g['gap_id']}: anchor {a} not in registry"


def test_statistics_consistent(reg):
    repos = reg["repositories"]
    st = reg["statistics"]
    assert st["curated_repos"] == len(repos)
    for t in (1, 2, 3):
        assert st["tier_counts"][str(t)] == sum(1 for r in repos if r["tier"] == t)
    assert st["tier1_core_repos"] and not (set(st["tier1_core_repos"]) & set(st["tier1_framework_repos"]))
    assert sum(st["language_distribution"].values()) == len(repos)
    assert sum(st["license_distribution"].values()) == len(repos)
    assert not st["evidence_problems"]


def test_verification_record(reg):
    path = os.path.join(HERE, "verification.json")
    if not os.path.exists(path):
        pytest.skip("verification.json not generated in this checkout")
    v = json.load(open(path, encoding="utf-8"))
    assert v["structural_check"]["passed"], v["structural_check"]["violations"]
    assert v["drift_check"]["field_error_rate"] < 0.03
    assert v["counts"]["curated_repos"] == len(reg["repositories"])


def test_figures_exist():
    figdir = os.path.join(HERE, "figures")
    if not os.path.isdir(figdir):
        pytest.skip("figures not generated in this checkout")
    names = os.listdir(figdir)
    assert sum(1 for n in names if n.endswith(".png")) >= 3, names