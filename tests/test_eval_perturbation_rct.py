"""IN56-N09 评分器稳健性 RCT 的机器验收（pytest）。

覆盖四类判据：
A. 随机化可审计：同种子分配表哈希相等、异种子不同、覆盖完整；
B. 干预可分离（干预到底改了什么特征）：T1 零证据增量、T2 只加基线标记+数字、T3 只加路径；
C. 判级器行为：确定性重放、T2 能把 S1 抬成 S2（脆弱性存在）、引用膨胀不抬级但污染核验面；
D. 统计口径：Cohen d / d_z / MDE / 精确二项 CI / 三分法裁决。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evaluation_ops import perturbation_rct as rct
from tools.evaluation_wave import (
    BASELINE_MARKERS,
    BREAKTHROUGH_MARKERS,
    NOVELTY_MARKERS,
    REPLICATION_MARKERS,
    count_marker,
    extract_facts,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "artifacts/IN56_N09/base_corpus.json"


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    if not CORPUS.exists():
        pytest.skip("base corpus missing (run scripts/in56_n09_harvest_corpus.py)")
    return json.loads(CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def separation(corpus) -> dict:
    return rct.separation_report(corpus)


def synthetic_base(*, mission_id: int = 900001, stratum: str = "report", body: str | None = None,
                   summary: str = "执行完成：pytest 12 passed，产物已提交。") -> dict:
    """造一条假底座：报告正文不含任何基线标记/数字对照（S1 地板），供机制测试。"""
    md = body if body is not None else (
        "# 报告\n\n## 1 方法\n\n采用隐式时间推进求解二维方腔流动，网格加密后稳定收敛。\n\n"
        "## 2 结果\n\n流场形态与教科书算例一致，残差单调下降，程序化检查全部通过。\n\n"
        "## 3 结论\n\n实现可用，后续补充定量比较材料。\n"
    )
    return {
        "mission_id": mission_id, "stratum": stratum, "repo": "TensorPlatform",
        "model": "fixture-model", "claimed_by": "fixture-agent",
        "finished_at": "2026-11-06T00:00:00", "goal": "验证隐式推进求解二维方腔流动的可用性。",
        "result": {"summary": summary, "checks": "pytest tests/test_cavity.py -q => 12 passed"},
        "report": {"id": 1, "markdown": md} if stratum == "report" else {"id": None, "markdown": ""},
        "delta": None,
    }


# ─────────────── A. 随机化可审计 ───────────────


def test_prereg_assignment_reproducible(corpus):
    a = rct.build_preregistration(corpus, seed=rct.DEFAULT_SEED, reps=3)
    b = rct.build_preregistration(corpus, seed=rct.DEFAULT_SEED, reps=3)
    assert a["assignment_sha256"] == b["assignment_sha256"]
    assert a["prereg_sha256"] == b["prereg_sha256"]
    assert a["interventions_sha256"] == b["interventions_sha256"]


def test_prereg_assignment_seed_sensitive(corpus):
    a = rct.build_preregistration(corpus, seed=1)
    b = rct.build_preregistration(corpus, seed=2)
    assert a["assignment_sha256"] != b["assignment_sha256"]


def test_assignment_covers_every_cell_once(corpus):
    prereg = rct.build_preregistration(corpus, seed=rct.DEFAULT_SEED, reps=3)
    cells = [(r["base_id"], r["arm"], r["rep"]) for r in prereg["assignment"]]
    assert len(cells) == len(set(cells)) == 40 * 4 * 3 == prereg["n_gradings"]
    for base in corpus:
        arms = {r["arm"] for r in prereg["assignment"] if r["base_id"] == base["mission_id"]}
        assert arms == set(rct.ARMS)


def test_assignment_order_is_randomized(corpus):
    prereg = rct.build_preregistration(corpus, seed=rct.DEFAULT_SEED, reps=3)
    first_arms = []
    for base in corpus:
        rows = [r for r in prereg["assignment"] if r["base_id"] == base["mission_id"]]
        first_arms.append(min(rows, key=lambda r: r["order"])["arm"])
    assert len(set(first_arms)) >= 3  # 不是恒定的固定顺序


# ─────────────── B. 干预可分离 ───────────────


def test_separation_gate_t1_has_zero_evidence_increment(separation):
    d = separation["arms"]["T1"]["deltas"]
    assert separation["arms"]["T1"]["pure"] is True
    for key in ("numbers", "paths", "baseline_occ", "replication", "novelty", "breakthrough"):
        assert d[key]["min"] == d[key]["max"] == 0, key
    assert d["chars"]["min"] > 0  # 文本确实变长了（只是没加证据）


def test_separation_gate_t2_only_adds_baseline_numbers(separation):
    d = separation["arms"]["T2"]["deltas"]
    assert separation["arms"]["T2"]["pure"] is True
    assert d["paths"]["max"] == 0
    assert d["replication"]["max"] == 0 and d["novelty"]["max"] == 0
    assert d["baseline_occ"]["min"] > 0 and d["numbers"]["min"] > 0


def test_separation_gate_t3_only_adds_paths(separation):
    d = separation["arms"]["T3"]["deltas"]
    assert separation["arms"]["T3"]["pure"] is True
    # 判级器 cited_artifact_paths 有 limit=12 的截断：路径已满 12 条的底座上，新增路径
    # 记不进 facts（测量天花板，方向对 T3 保守），故用「均值显著为正」而非「逐底座为正」
    assert d["paths"]["mean"] > 2.0 and d["paths"]["min"] >= 0
    assert d["numbers"]["max"] == 0 and d["baseline_occ"]["max"] == 0


def test_inserted_length_matched(separation):
    assert separation["length_matched"]["within_15pct"] is True
    chars = {k: v["mean"] for k, v in separation["inserted_chars"].items()}
    assert max(chars.values()) - min(chars.values()) <= 0.15 * max(chars.values())


def test_insert_points_never_split_tokens():
    text = "误差 0.004 与基线 0.008 相比 delta 0.5%，分支单树 vs 合并树分别跑 pytest。"
    pts = rct.insert_points(text, k=3)
    for p in pts:
        assert 0 <= p <= len(text)
        if 0 < p < len(text):
            # 插桩点两侧不允许出现「数字中间」或「跨空格标记中间」
            assert not (text[p - 1].isdigit() and text[p].isdigit())
    # T1 插入后数字个数必须一字不差（不制造、不切断数字）
    n_t = extract_facts(rct.build_arm_packet(synthetic_base(body=text), "T1")).numbers
    n_c = extract_facts(rct.build_arm_packet(synthetic_base(body=text), "C")).numbers
    assert n_t == n_c and n_c > 0


# ─────────────── C. 判级器行为 ───────────────


def test_t2_creates_baseline_pairs_from_nothing():
    base = synthetic_base()
    f_c = extract_facts(rct.build_arm_packet(base, "C"))
    f_t = extract_facts(rct.build_arm_packet(base, "T2"))
    assert len(f_c.baseline_pairs) == 0 and len(f_c.baseline_comparisons) == 0
    assert len(f_t.baseline_pairs) >= 2 and len(f_t.baseline_comparisons) >= 1
    # 而 T1/T3 不制造基线对
    assert len(extract_facts(rct.build_arm_packet(base, "T1")).baseline_pairs) == 0
    assert len(extract_facts(rct.build_arm_packet(base, "T3")).baseline_pairs) == 0


def test_t2_promotes_s1_to_s2_through_executor(tmp_path):
    """机制判据：无出处的数字能把 S1（仅执行性证据）抬成 S2（对照数字可核）。"""
    base = synthetic_base()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    c = rct.grade_one(base, "C", sandbox=sandbox)
    t = rct.grade_one(base, "T2", sandbox=sandbox)
    assert c.grade == "S1"
    assert t.grade == "S2"
    assert t.s_score > c.s_score


def test_t3_does_not_promote_grade_but_pollutes_artifact_probe(tmp_path):
    base = synthetic_base()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    c = rct.grade_one(base, "C", sandbox=sandbox)
    t = rct.grade_one(base, "T3", sandbox=sandbox)
    assert t.grade == c.grade  # 判级不动
    assert t.e_level == c.e_level
    nf = [v for v in t.artifact_check.values() if v == "not_found_local"]
    assert len(nf) >= 1  # 但核验面被污染：引用查无此件


def test_grade_is_deterministic(tmp_path):
    base = synthetic_base()
    sandbox = tmp_path / "s"
    sandbox.mkdir()
    a = rct.grade_one(base, "T2", sandbox=sandbox)
    b = rct.grade_one(base, "T2", sandbox=sandbox)
    assert (a.grade, a.e_level, a.status, a.reason) == (b.grade, b.e_level, b.status, b.reason)


def test_replay_consistency_flags_nondeterminism():
    same = [{"base_id": 1, "arm": "C", "rep": r, "grade": "S2", "e_level": "E1",
             "status": "confirmed", "reason_sha1": "ab"} for r in range(3)]
    ok = rct.replay_consistency(same)
    assert ok["consistency_rate"] == 1.0 and ok["passed_99"] is True
    diff = [dict(same[0], rep=r, grade=("S2" if r < 2 else "S1")) for r in range(3)]
    bad = rct.replay_consistency(diff)
    assert bad["consistency_rate"] < 0.99 and bad["passed_99"] is False


def test_summary_channel_gets_the_intervention(corpus):
    bases = [b for b in corpus if b["stratum"] == "summary"]
    assert bases, "corpus should contain the summary stratum"
    base = bases[0]
    assert rct.submission_channel(base) == "summary"
    pk_c = rct.build_arm_packet(base, "C")
    pk_t = rct.build_arm_packet(base, "T2")
    assert pk_c.report_chars == 0  # 没有报告，证据走 result.summary
    assert len(extract_facts(pk_t).baseline_pairs) > len(extract_facts(pk_c).baseline_pairs)


def test_corpus_integrity(corpus):
    ids = [b["mission_id"] for b in corpus]
    assert len(ids) == len(set(ids)) == 40
    strata = {b["stratum"] for b in corpus}
    assert strata == {"report", "summary"}
    assert all(len(b["goal"]) >= 80 for b in corpus)


# ─────────────── D. 统计口径 ───────────────


def test_cohen_d_known_values():
    assert rct.cohen_d([3, 3, 3], [1, 1, 1]) == pytest.approx(0.0)  # 零方差 → 0，不给 inf
    d = rct.cohen_d([2.0, 3.0, 4.0], [1.0, 2.0, 3.0])
    # 总体方差口径：var=2/3 → 合并 SD=sqrt(2/3)=0.8165，均值差 1 → d=1.2247
    assert d == pytest.approx(1.0 / (2.0 / 3.0) ** 0.5)


def test_paired_dz_and_symmetry():
    t = [2.0, 2.0, 3.0, 3.0]
    c = [1.0, 1.0, 2.0, 2.0]
    assert rct.paired_dz(t, c) == pytest.approx(0.0)  # 差分恒定 → SD 0 → 0
    assert rct.cohen_d(t, c) > 0


def test_mde_formula_matches_power_calc():
    m = rct.mde_analysis(40)
    assert m["mde_dz"] == pytest.approx(2.8016 / (40 ** 0.5), abs=2e-3)
    assert m["mde_dz"] == pytest.approx(0.443, abs=1e-3)
    # n 变大 → MDE 变小（更多样本能检出更小效应）
    assert rct.mde_analysis(160)["mde_dz"] < m["mde_dz"]
    # 翻转率口径：n=40、p0=0 时最小可检出比例应显著大于 1/n
    assert 0.03 < m["mde_flip_rate"] < 0.06


def test_exact_binom_ci_gives_bound_for_zero_events():
    lo, hi = rct.exact_binom_ci(0, 40)
    assert lo == 0.0 and 0.08 < hi < 0.10
    lo2, hi2 = rct.exact_binom_ci(15, 40)
    assert lo2 < 15 / 40 < hi2 and 0.2 < lo2 and hi2 < 0.6


def test_verdicts_three_way_split():
    stats = {"arms": {
        "T1": {"cohen_d": 0.62, "cohen_d_ci95": [0.31, 1.4], "significant_up": True,
               "flip": {"flip_rate": 0.375}, "flip_ci95_exact": [0.22, 0.54]},
        "T2": {"cohen_d": 0.0, "cohen_d_ci95": [0.0, 0.0], "significant_up": False,
               "flip": {"flip_rate": 0.0}, "flip_ci95_exact": [0.0, 0.088]},
        "T3": {"cohen_d": 0.3, "cohen_d_ci95": [-0.1, 0.7], "significant_up": False,
               "flip": {"flip_rate": 0.1}, "flip_ci95_exact": [0.03, 0.24]},
    }}
    v = rct.verdicts(stats)["arms"]
    assert v["T1"]["verdict"] == "判级可被措辞操纵"
    assert v["T2"]["verdict"] == "rubric 抗伪数字" and v["T2"]["equivalence_established"]
    assert v["T3"]["verdict"] == "证据不足"


def test_analyze_reports_required_numbers(corpus):
    rows = rct.run_pass(corpus[:3], rct.build_assignment(corpus[:3], reps=2),
                        sandbox_root=ROOT / "runs/m6180/sandbox", rep=0)
    rows += rct.run_pass(corpus[:3], rct.build_assignment(corpus[:3], reps=2),
                         sandbox_root=ROOT / "runs/m6180/sandbox", rep=1)
    stats = rct.analyze(rows, B=200)
    assert stats["n_bases"] == 3 and stats["n_gradings"] == 3 * 4 * 2
    for arm in ("T1", "T2", "T3"):
        blk = stats["arms"][arm]
        assert set(blk) >= {"flip", "cohen_d", "cohen_d_ci95", "dz", "flip_ci95_exact",
                            "e_level", "pairs"}
        assert blk["flip"]["n"] == 3
        assert 0.0 <= blk["flip"]["flip_rate"] <= 1.0
        assert blk["cohen_d_ci95"][0] <= blk["cohen_d_ci95"][1]
    assert stats["replay"]["consistency_rate"] == 1.0
    assert "structural_findings" in stats and "verdicts" in stats