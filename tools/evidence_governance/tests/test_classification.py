"""Criterion #1b: classification accuracy on the labelled benchmark, >= 95%.

The benchmark is labelled synthetic declarations (48 items, six baseline types).
Two extra checks keep the number honest:

* the *pre-improvement* baseline is reproduced by disabling the nested-pattern
  suppression, so the reported gain is measured, not asserted;
* every miss is enumerated, never averaged away.
"""
from __future__ import annotations

from tools.evidence_governance.classifier import GovernanceClassifier, _most_specific
from tools.evidence_governance.runner import build_pipeline, evaluate_accuracy

from .conftest import PKG, ROOT, load


def _labels():
    return load(PKG / "fixtures" / "labeled_debts.json")


def _classifier(config):
    _s, classifier, _c, _e = build_pipeline(config, ROOT)
    return classifier


def test_accuracy_meets_threshold(real_config):
    labels = _labels()
    classifier = _classifier(real_config)
    result = evaluate_accuracy(labels, classifier)
    assert result["n_items"] == 48
    assert result["accuracy"] >= 0.95, result["misses"]


def test_every_baseline_type_has_positive_recall(real_config):
    result = evaluate_accuracy(_labels(), _classifier(real_config))
    assert set(result["per_type"]) == {
        "analytical_solution",
        "experimental",
        "mesh_convergence",
        "physical_consistency",
        "cross_validation",
        "unknown",
    }
    for name, row in result["per_type"].items():
        assert row["recall"] >= 0.95, (name, row)


def test_overlapping_patterns_do_not_double_count():
    assert _most_specific(["实测", "实测数据"]) == ["实测数据"]
    assert sorted(_most_specific(["分析", "试验"])) == ["分析", "试验"]


def test_nested_pattern_collapse_is_the_measured_improvement(real_config):
    """Without the collapse the same rules mislabel a declaration."""
    rules = load(PKG / "config" / "classifier_rules.v1.json")["rules"]
    cfg = dict(real_config["classification"])
    collapsed = GovernanceClassifier(
        rules=rules,
        tie_break_order=cfg["tie_break_order"],
        saturation=cfg["saturation"],
    )
    text = "缺少解析解对照，实测数据仅作参考"
    assert collapsed.explain(text)["baseline_type"] == "analytical_solution"

    # Reproduce the pre-fix scoring: count every matching pattern.
    uncollapsed = GovernanceClassifier(
        rules=rules,
        tie_break_order=cfg["tie_break_order"],
        saturation=cfg["saturation"],
    )
    scores = {}
    lowered = text.lower()
    for rule in rules:
        hits = [p for p in rule["patterns"] if p.lower() in lowered]
        if hits:
            scores[rule["baseline_type"]] = len(hits)
    assert scores["experimental"] > scores["analytical_solution"]  # the old bug


def test_same_declaration_always_classifies_the_same(real_config):
    classifier = _classifier(real_config)
    text = "需做网格收敛研究并完成网格无关性验证，另参考实测数据"
    first = classifier.explain(text)
    second = classifier.explain(text)
    assert first == second
    assert first["baseline_type"] == "mesh_convergence"


def test_unlabelled_declaration_falls_back_to_unknown(real_config):
    classifier = _classifier(real_config)
    result = classifier.explain("no evidence recorded")
    assert result["baseline_type"] == "unknown"
    assert result["confidence"] == 0.0


def test_accuracy_is_reported_in_the_artifact_set(real_config, tmp_path):
    from tools.evidence_governance.runner import run_pipeline

    cfg = dict(real_config)
    cfg["output"] = {"dir": str(tmp_path)}
    payloads, summary = run_pipeline(cfg, ROOT)
    acc = payloads["classification_accuracy.json"]
    assert acc["accuracy"] == summary["accuracy"]
    assert acc["threshold_met"] is True
    assert (tmp_path / "classification_accuracy.json").exists()