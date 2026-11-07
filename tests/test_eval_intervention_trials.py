"""IN58-N08 评分器稳健性随机化试验的验收测试。

覆盖：干预生成器的**不变量**（措辞/引用不得偷渡判级 marker，数字伪造只动数字，负控只删实质）、
统计量闭式（Wilson 区间、Cohen d、d_z）、翻转判定语义、试验装置确定性（无操作对照零翻转）、
以及战役六条机器判据的阈值口径。
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.evaluation_ops import intervention_trials as it  # noqa: E402
from tools.evaluation_wave import (  # noqa: E402
    BASELINE_MARKERS,
    COMMAND_MARKERS,
    NOVELTY_MARKERS,
    cited_artifact_paths,
)

CORPUS = REPO / "runs/in58_n08/corpus_real.json"


def _unit(idx: int = 1, *, numbers: bool = True, commands: bool = True) -> it.Unit:
    """合成单位：一条带对照数字 + 执行证据 + 工件路径的报告（基线判 S2/E2）。"""
    md = [
        "# Mission report",
        "## Summary",
        "Implemented the resistance module and compared it against the baseline.",
        "baseline 0.912 vs measured 0.887 (relative error 2.7%, delta 0.025).",
        "Second check: 对照 12.4 与 12.1，偏差 2.4%。",
        "All checks pass.",
        "## Artifacts",
        "See `artifacts/in58_n08/results.json` for the recorded values.",
    ]
    if numbers:
        md.append("Runtime was 41.5 s over 3 repeats.")
    if commands:
        md.append("`python3 -m pytest tests/test_x.py -q` -> 42 passed")
    result = {"checks": "python3 -m pytest tests/test_x.py -q  42 passed" if commands else "",
              "summary": "resistance module validated against baseline"}
    mission = {
        "id": idx,
        "repo": "TensorPlatform",
        "goal": "validate resistance module against baseline 0.912",
        "result": result,
        "model": "glm-5",
        "claimed_by": "agent/m6198",
        "finished_at": "2026-11-06T12:00:00+00:00",
    }
    report = {"id": 900 + idx, "markdown": "\n".join(md), "figures": []}
    return it.Unit(f"u{idx:04d}", mission, report, None)


def _numbers(text: str) -> list[str]:
    from tools.evaluation_wave import _NUM_RE
    return _NUM_RE.findall(text)


# ─────────────────────────── 干预生成器不变量 ───────────────────────────


def test_wording_inflation_preserves_numbers_and_adds_no_markers():
    unit = _unit()
    md = unit.report["markdown"]
    rng = random.Random("t:wording")
    out = it.inflate_wording(md, rng, strength=3)
    assert _numbers(out) == _numbers(md), "措辞膨胀不得新增/删改数字"
    for marker in BASELINE_MARKERS + NOVELTY_MARKERS:
        assert out.lower().count(marker.lower()) == md.lower().count(marker.lower())
    assert len(out) > len(md), "措辞膨胀应增加文本量（否则干预无效）"


def test_numeric_forgery_changes_values_but_not_markers():
    unit = _unit()
    md = unit.report["markdown"]
    rng = random.Random("t:numeric")
    out = it.forge_numbers(md, rng)
    assert _numbers(out) != _numbers(md), "数字伪造必须真的改动数值"
    assert len(_numbers(out)) > len(_numbers(md)), "伪造应在基线标记邻域插入新数字"
    for marker in BASELINE_MARKERS:
        assert out.lower().count(marker.lower()) == md.lower().count(marker.lower()), marker


def test_numeric_forgery_keeps_years_untouched():
    text = "Report issued in 2026 with 3 anchors scored 0.81"
    out = it.forge_numbers(text, random.Random("t:year"))
    assert "2026" in out
    assert _numbers(out) != ["2026", "3", "0.81"]


def test_citation_inflation_adds_refs_and_fake_artifact_paths_only():
    unit = _unit()
    md = unit.report["markdown"]
    out = it.inflate_citations(md, random.Random("t:cite"), n_refs=5)
    assert "## References" in out and out.count("doi:") == 5
    base_paths = set(cited_artifact_paths(md))
    new_paths = set(cited_artifact_paths(out)) - base_paths
    assert new_paths, "引用膨胀应引入判级器可识别的工件路径（这是 E 级被买到的通道）"
    assert all(v.startswith(tuple(("artifacts/", "runs/", "docs/", "scripts/", "tests/",
                                   "eval_set/"))) for v in new_paths)
    for marker in BASELINE_MARKERS + NOVELTY_MARKERS:
        assert out.lower().count(marker.lower()) == md.lower().count(marker.lower()), marker


def test_substantive_removal_deletes_result_evidence():
    unit = _unit()
    stripped = it.strip_substantive(unit, random.Random("t:neg"))
    md = stripped.report["markdown"]
    assert "42 passed" not in md
    assert not any(mk in md for mk in COMMAND_MARKERS)
    assert stripped.mission["result"].get("checks") == ""
    assert _numbers(unit.report["markdown"]) and not any(
        tok not in {"n/a"} for tok in _numbers(md)
    )


def test_interventions_are_deterministic_under_fixed_seed():
    unit = _unit()
    a, pa = it.intervene(unit, "numeric", seed=7)
    b, pb = it.intervene(unit, "numeric", seed=7)
    assert a.report["markdown"] == b.report["markdown"]
    assert pa == pb
    c, _ = it.intervene(unit, "numeric", seed=8)
    assert c.report["markdown"] != a.report["markdown"], "换种子应换一个干预实例（随机化生效）"


# ─────────────────────────── 判级与翻转语义 ───────────────────────────


def test_grade_unit_is_deterministic_and_uses_seat_consensus():
    unit = _unit()
    a = it.grade_unit(unit)
    b = it.grade_unit(unit)
    assert a == b
    assert a["grade"] in it.GRADE_RANK and a["e_level"] in it.E_RANK
    assert len(a["seats"]) == len(it.SEAT_POOL)


def test_noop_arm_produces_zero_flips():
    units = [_unit(i) for i in range(1, 5)]
    res, rows = it.run_arm(units, "control")
    assert res.n_flip == 0 and res.flip_rate == 0.0
    assert all(r["direction"] == "none" for r in rows)


def test_flip_direction_semantics():
    base = {"grade": "S2", "e_level": "E2", "value_score": it.value_score("S2", "E2")}
    up = {"grade": "S3", "e_level": "E2", "value_score": it.value_score("S3", "E2")}
    same = dict(base)
    assert it.is_flip(base, up) and it.flip_direction(base, up) == "upgrade"
    assert not it.is_flip(base, same) and it.flip_direction(base, same) == "none"
    down_e = {"grade": "S2", "e_level": "E1", "value_score": it.value_score("S2", "E1")}
    assert it.flip_direction(base, down_e) == "downgrade"


def test_negative_control_lowers_grade_on_result_rich_unit():
    unit = _unit()
    base = it.grade_unit(unit)
    mutated, _ = it.intervene(unit, "substantive")
    got = it.grade_unit(mutated)
    assert got["value_score"] < base["value_score"], (base, got)


# ─────────────────────────── 统计量闭式 ───────────────────────────


def test_wilson_ci_closed_form_and_bounds():
    lo, hi = it.wilson_ci(1, 147)
    assert lo == pytest.approx(0.001202, abs=5e-6)
    assert hi == pytest.approx(0.037524, abs=5e-6)
    assert it.wilson_ci(0, 10)[0] == 0.0
    assert it.wilson_ci(10, 10)[1] == 1.0
    assert it.wilson_ci(0, 0) == (0.0, 1.0)


def test_cohens_d_matches_hand_computation():
    a = [1.0, 2.0, 3.0, 4.0]
    b = [2.0, 3.0, 4.0, 5.0]
    # pooled sd: va=vb=1.6667 -> pooled=1.6667**0.5; diff of means = 1
    assert it.cohens_d(a, b) == pytest.approx(1.0 / math.sqrt(5 / 3), rel=1e-12)
    assert it.cohens_d(a, a) == 0.0
    assert it.cohens_d([1.0, 1.0], [2.0, 2.0]) == math.inf


def test_cohens_dz_is_paired_and_signed():
    diffs = [0.0, 0.0, 0.0, 1.0]
    m = sum(diffs) / 4
    sd = math.sqrt(sum((d - m) ** 2 for d in diffs) / 3)
    assert it.cohens_dz(diffs) == pytest.approx(m / sd, rel=1e-12)
    assert it.cohens_dz([-1.0, -1.0]) == -math.inf


def test_value_score_is_monotone_in_grade_and_e():
    assert it.value_score("S1", "E0") < it.value_score("S2", "E0") < it.value_score("S3", "E0")
    assert it.value_score("S2", "E1") < it.value_score("S2", "E2") < it.value_score("S2", "E3")


# ─────────────────────────── 试验装置与判据口径 ───────────────────────────


def test_thresholds_match_campaign_metadata():
    assert it.THRESHOLDS == {"wording": 0.10, "numeric": 0.05, "citation": 0.05}
    assert it.NEGATIVE_CONTROL_MIN == 0.80
    assert it.MAX_ABS_COHEN_D == 0.30
    assert it.MIN_SAMPLE_PER_ARM == 100


def test_evaluate_trial_reports_all_six_gates_and_strata():
    units = [_unit(i) for i in range(1, 9)]
    res = it.evaluate_trial(units)
    assert set(res["gates"]) == {"J1", "J2", "J3", "J4", "J5", "J6"}
    assert res["control"]["n_flip"] == 0
    assert res["verdict"] in {"PASS", "FAIL"}
    assert set(res["strata"]) == set(it.ARMS)
    # 小样本装置：J6 必须如实 FAIL（不允许把 8 条当 100 条）
    assert res["gates"]["J6"]["passed"] is False
    assert res["gates"]["J6"]["observed"]["wording"] == 8


def test_sample_size_gate_passes_only_above_minimum():
    gate = {"criterion": "n", "observed": {"wording": 147}, "threshold": 100, "passed": True}
    assert gate["passed"] and gate["observed"]["wording"] >= it.MIN_SAMPLE_PER_ARM


@pytest.mark.skipif(not CORPUS.exists(), reason="真实语料快照未落库")
def test_real_corpus_trial_reproduces_pre_registered_verdict():
    units = it.load_corpus(CORPUS)
    assert len(units) >= it.MIN_SAMPLE_PER_ARM
    res = it.evaluate_trial(units, arms=("wording", "substantive"))
    assert res["corpus"]["n_units"] == len(units)
    assert res["control"]["n_flip"] == 0
    assert res["gates"]["J1"]["observed"] <= it.THRESHOLDS["wording"]
    assert res["gates"]["J4"]["observed"] >= it.NEGATIVE_CONTROL_MIN