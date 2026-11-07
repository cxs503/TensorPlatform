#!/usr/bin/env python3
"""边界门对抗语料与度量的 pytest 判据（P20-N08）。

覆盖任务书机器可算验收判据：

* C1 语料可复现 + 配额      → test_corpus_hash_is_stable / test_corpus_quota_*
* C2 metrics.json 机器可读  → test_metrics_schema / test_metrics_artifact_on_disk
* C3 漏检清单（真实数字）    → test_missed_list_is_materialized
* C4 自伤负例必须判红        → test_self_injury_control_is_red
* C5 honest 条款            → test_honest_statement_present_when_recall_below_floor
* E4 加固不破门/不涨误报     → test_structural_rules_do_not_regress_calibration_corpus
                              / test_hardening_trades_no_false_positives

另含零领域语义自检（工具与测试源码里不得出现词表阻断词条）。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.boundary import adversarial_corpus as corpus_mod   # noqa: E402
from tools.boundary import adversarial_measure as measure_mod  # noqa: E402
from tools.boundary import pr_gate_scan as gate               # noqa: E402
from tools.boundary import structural_gate as structural_mod  # noqa: E402

LEXICON = REPO / gate.DEFAULT_LEXICON_REL
RULES = REPO / structural_mod.DEFAULT_RULES_REL
CORPUS = REPO / measure_mod.CORPUS_REL
SEEDS = REPO / corpus_mod.SEEDS_REL
METRICS = REPO / "artifacts/boundary/adversarial_metrics.json"
STATEMENT = REPO / "docs/boundary/边界声明.md"


@pytest.fixture(scope="module")
def lexicon() -> gate.Lexicon:
    return gate.Lexicon.load(LEXICON)


@pytest.fixture(scope="module")
def rules() -> structural_mod.StructuralRules:
    return structural_mod.StructuralRules.load(RULES)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return corpus_mod.build_corpus(SEEDS, LEXICON)


@pytest.fixture(scope="module")
def metrics() -> dict:
    return json.loads(METRICS.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# C1 可复现 + 配额
# --------------------------------------------------------------------------- #
def test_corpus_hash_is_stable(corpus):
    """1. 同种子两次生成 → 语料哈希逐字节稳定；与落盘语料一致（可复现）。"""

    again = corpus_mod.build_corpus(SEEDS, LEXICON)
    assert corpus["corpus_sha256"] == again["corpus_sha256"]
    on_disk = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert on_disk["corpus_sha256"] == corpus["corpus_sha256"]


def test_corpus_quota_and_class_shape(corpus):
    """2. 配额：6 类 × ≥35 条（≥25 正 + ≥10 负），总计 ≥200。"""

    quota = corpus["quota"]
    counts = corpus["counts"]
    assert counts["total"] >= quota["total_min"]
    assert set(counts["by_class"]) == set(corpus_mod.CLASSES)
    for cls, c in counts["by_class"].items():
        assert c["positive"] >= quota["per_class_pos_min"], cls
        assert c["negative"] >= quota["per_class_neg_min"], cls


def test_corpus_labels_and_paths(corpus, lexicon):
    """3. 负样本在词法上必须是干净的（误报率的分母）+ 路径必须落在代码扫描域。"""

    for sample in corpus["samples"]:
        assert sample["label"] in ("positive", "negative")
        assert lexicon.classify(sample["path"]) in ("blocked", "blocked_whitelist_code")
        if sample["label"] == "negative":
            for line in sample["lines"]:
                blocking = [h for h in gate.scan_line(line, lexicon) if h.blocking and not h.exempt_reason]
                assert blocking == [], (sample["id"], blocking)


def test_mutation_operators_cover_six_classes(corpus):
    """4. 六类变异算子都有正负样本，且每条正样本带机理标签与可救性标签。"""

    seeds = json.loads(SEEDS.read_text(encoding="utf-8"))
    assert len(seeds["mutation_operators"]) == 6
    for sample in corpus["samples"]:
        if sample["label"] == "positive":
            assert sample["mechanism"]
            assert sample["remediable_by"] in ("base", "vocabulary", "R1_normalize",
                                               "R2_notation", "R3_reference", "none")


# --------------------------------------------------------------------------- #
# C2 metrics.json
# --------------------------------------------------------------------------- #
def test_metrics_schema(metrics):
    """5. metrics.json 机器可读：per_class{recall,fp_rate,n} + overall{recall,fp_rate,precision}。"""

    assert metrics["schema"] == measure_mod.SCHEMA
    for key in ("baseline", "hardened"):
        block = metrics[key]
        assert set(block["per_class"]) == set(corpus_mod.CLASSES)
        for cls, row in block["per_class"].items():
            for field in ("recall", "fp_rate", "n"):
                assert field in row, (key, cls, field)
            assert 0.0 <= row["recall"] <= 1.0
            assert 0.0 <= row["fp_rate"] <= 1.0
        for field in ("recall", "fp_rate", "precision"):
            assert field in block["overall"]
    assert metrics["control"]["ok"] is True


def test_metrics_artifact_on_disk(metrics):
    """6. 交付物在 artifacts/ 落盘，且审计链（语料/词表/规则哈希）完整。"""

    assert METRICS.exists()
    assert metrics["corpus"]["sha256"]
    assert metrics["gates"]["baseline"]["lexicon"]["sha256"]
    assert metrics["gates"]["hardened"]["rules_sha256"]


# --------------------------------------------------------------------------- #
# C3 漏检清单
# --------------------------------------------------------------------------- #
def test_missed_list_is_materialized(metrics):
    """7. 基线漏检清单存在、逐条可查（id/类/机理/摘录），数字与混淆矩阵自洽。"""

    missed = metrics["baseline"]["missed"]
    overall = metrics["baseline"]["overall"]
    assert missed, "baseline missed list must not be empty for an adversarial corpus"
    assert len(missed) == min(measure_mod.TOP_MISSED, overall["fn"])
    for item in missed:
        assert item["id"] and item["class"] and item["mechanism"] and item["line"]
    # 逐类漏检数之和 == 总漏检数（混淆矩阵自洽）
    fn_sum = sum(row["fn"] for row in metrics["baseline"]["per_class"].values())
    assert fn_sum == overall["fn"]


# --------------------------------------------------------------------------- #
# C4 自伤控制
# --------------------------------------------------------------------------- #
def test_self_injury_control_is_red(corpus, lexicon, rules):
    """8. 注入一条已知会被拦的样本 → 基础门禁与加固门禁都必须判红（否则度量链路坏了）。"""

    control = corpus["control"]
    sample = {"path": control["path"], "lines": control["lines"]}
    diff = corpus_mod.build_diff(sample)
    assert gate.scan_diff(diff, lexicon).verdict == "FAIL"
    assert structural_mod.scan_diff_structural(diff, lexicon, rules)["verdict"] == "FAIL"
    assert control["expect"] == "FAIL"


# --------------------------------------------------------------------------- #
# C5 honest 条款
# --------------------------------------------------------------------------- #
def test_honest_statement_present_when_recall_below_floor(metrics):
    """9. 整体召回 < 0.7 时，边界声明必须落盘并写明「检出能力不足」。"""

    if metrics["baseline"]["overall"]["recall"] >= measure_mod.RECALL_FLOOR:
        pytest.skip("recall meets the floor; statement clause not triggered")
    assert STATEMENT.exists(), "C5 requires docs/boundary/边界声明.md"
    text = STATEMENT.read_text(encoding="utf-8")
    assert "边界门检出能力不足" in text


# --------------------------------------------------------------------------- #
# E4 加固：不破门 + 误报预算
# --------------------------------------------------------------------------- #
def test_structural_rules_do_not_regress_calibration_corpus(lexicon, rules, tmp_path):
    """10. 加固后复判 IN17-B1 校准语料的全部用例 → 判决与期望一致（不破已有门禁）。"""

    calibration = json.loads((REPO / "data/boundary/pr_gate_calibration_corpus.json")
                             .read_text(encoding="utf-8"))
    base_lex = gate.Lexicon.load(LEXICON)
    probe_raw = json.loads(LEXICON.read_text(encoding="utf-8"))
    probe_raw.update(calibration["synthetic_lexicon_overrides"])
    probe_path = tmp_path / "probe_lexicon.json"
    probe_path.write_text(json.dumps(probe_raw, ensure_ascii=False), encoding="utf-8")
    probe_lex = gate.Lexicon.load(probe_path)

    checked = 0
    for case in calibration["cases"]:
        if case.get("mode") == "git":
            merge = case["merge"]
            has = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{merge}^{{commit}}"],
                                 capture_output=True)
            if has.returncode != 0:
                continue  # 浅克隆：git 类用例不可用（learned lesson: 不伪造结果）
            base_sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"{merge}^1"],
                                      capture_output=True, text=True).stdout.strip()
            head_sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", merge],
                                      capture_output=True, text=True).stdout.strip()
            diff_text = gate.git_diff(base_sha, head_sha, REPO)
            report = structural_mod.scan_diff_structural(diff_text, base_lex, rules)
        else:
            diff_text = "\n".join(case["diff_lines"]) + "\n"
            report = structural_mod.scan_diff_structural(diff_text, probe_lex, rules)
        assert report["verdict"] == case["expect"], (case["id"], report["verdict"], case["expect"])
        checked += 1
    assert checked >= 8  # 至少合成机制用例全跑（git 类在浅克隆下可跳过）


def test_hardening_trades_no_false_positives(metrics):
    """11. E4 预算：加固后整体召回必须上升，且负样本误报上升 ≤ 2pp。"""

    base = metrics["baseline"]["overall"]
    hard = metrics["hardened"]["overall"]
    assert hard["recall"] > base["recall"]
    delta_pp = round((hard["fp_rate"] - base["fp_rate"]) * 100, 2)
    assert delta_pp <= measure_mod.FP_BUDGET_PP
    assert metrics["acceptance"]["E4_fp_budget_ok"] is True


def test_vocab_probe_quantifies_lexical_ceiling(metrics):
    """12. 词表扩张留出法：扩张集召回应显著高于留出集（词表类盲区只能靠词表增长）。"""

    probe = metrics["vocab_probe"]["per_class"]
    for cls, buckets in probe.items():
        if not buckets["expanded"]["n"] or not buckets["held_out"]["n"]:
            continue
        assert buckets["expanded"]["recall"] > buckets["held_out"]["recall"], cls


# --------------------------------------------------------------------------- #
# 零领域语义自检 + CLI
# --------------------------------------------------------------------------- #
def test_new_tool_and_test_sources_carry_no_real_term(lexicon):
    """13. 工具/测试源码里不得出现词表阻断词条（域语义只能活在 data/）。"""

    sources = [REPO / "tools/boundary/adversarial_corpus.py",
               REPO / "tools/boundary/adversarial_measure.py",
               REPO / "tools/boundary/structural_gate.py",
               pathlib.Path(__file__)]
    offenders = []
    for src in sources:
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
            for hit in gate.scan_line(line, lexicon):
                if hit.blocking and not hit.exempt_reason:
                    offenders.append((src.name, lineno, hit.term_id, hit.matched))
    assert offenders == []


def test_structural_cli_exit_codes(corpus, tmp_path):
    """14. CLI 端到端：控制样本走 stdin → 退出码 2 且 JSON 可回读；干净样本退出码 0。"""

    control = corpus["control"]
    out = tmp_path / "verdict.json"
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools/boundary/structural_gate.py"), "diff", "-",
         "--json", str(out)], input=corpus_mod.build_diff({"path": control["path"],
                                                           "lines": control["lines"]}),
        capture_output=True, text=True)
    assert proc.returncode == 2, proc.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["verdict"] == "FAIL"
    assert written["totals"]["structural_violations"] >= 0

    clean = subprocess.run(
        [sys.executable, str(REPO / "tools/boundary/structural_gate.py"), "diff", "-"],
        input=corpus_mod.build_diff({"path": "backend/api/probe_clean.py",
                                     "lines": ['RETRIES = 3  # bounded backoff']}),
        capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout + clean.stderr