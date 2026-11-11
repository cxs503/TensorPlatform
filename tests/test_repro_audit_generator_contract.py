"""repro_audit 生成器契约（p167w2-n10）测试 —— 全部使用合成夹具，零领域语义。

覆盖：
  * JSON 路径取值 / 数值容差比对 / 白名单归一；
  * 静态审计：有信息量字面量的"回抄"能被抓、小整数不误伤、自读路径能被抓；
  * 运行时读审计：能识别"生成器读自己被审计的工件"；
  * 派生引擎幂等：两次重算逐字节相同；
  * 契约端到端：合成小仓里 base 通过门、负控三刀（手抄 / 生成器搬走 / 硬编码随机）全部被拒。
"""
from __future__ import annotations

import json
import os
import textwrap

import pytest

from tools.repro_audit.generators import contract, negcontrols, recompute


# ─────────────────────────── 基础工具 ───────────────────────────
def test_get_path_handles_indices_and_nesting():
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}, "top": 3}
    assert contract.get_path(doc, "top") == 3
    assert contract.get_path(doc, "a.b[1].c") == 2
    with pytest.raises(KeyError):
        contract.get_path(doc, "a.missing")


def test_values_match_numeric_tolerance_and_bool_strictness():
    assert contract.values_match(1.0, 1.0 + 1e-12, 1e-9)
    assert not contract.values_match(1.0, 1.1, 1e-9)
    assert contract.values_match(True, True)
    assert not contract.values_match(True, 1)          # bool 不当 0/1
    assert contract.values_match([1, {"k": 2.5}], [1, {"k": 2.5}])
    assert not contract.values_match([1, 2], [1, 2, 3])


def test_normalise_drops_whitelisted_fields_recursively():
    doc = {"generated_at": "t1", "nested": {"timestamp": "x", "value": 2}}
    other = {"generated_at": "t2", "nested": {"timestamp": "y", "value": 2}}
    assert contract.values_match(contract._normalise(doc), contract._normalise(other))


# ─────────────────────────── 静态审计 ───────────────────────────
def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def test_static_audit_flags_distinctive_literal_backcopy(tmp_path):
    mod = _write(tmp_path, "gen_copy.py", '''
        """合成：把结果写死。"""
        OUTPUT_PATH = "artifacts/demo/report.json"
        def generate():
            return {"ratio": 0.8347219}
    ''')
    audit = contract.static_audit(mod, "artifacts/demo/report.json", [0.8347219])
    assert audit["literal_backcopy"] == [0.8347219]


def test_static_audit_does_not_flag_small_integer_constants(tmp_path):
    mod = _write(tmp_path, "gen_ok.py", '''
        """合成：12/4/8 是维度与容差常量，不是被断言的头部值。"""
        N_CHUNKS = 12
        TOL = 4
        def generate():
            return {"total": N_CHUNKS, "chunks": 8}
    ''')
    audit = contract.static_audit(mod, "artifacts/demo/report.json", [12, 4])
    assert audit["literal_backcopy"] == []


def test_static_audit_flags_self_read_path_literal(tmp_path):
    mod = _write(tmp_path, "gen_selfread.py", '''
        OUTPUT_PATH = "artifacts/demo/report.json"
        def generate():
            return json.load(open("artifacts/demo/report.json"))
    ''')
    audit = contract.static_audit(mod, "artifacts/demo/report.json", [1])
    assert audit["self_read"] == ["artifacts/demo/report.json"]


# ─────────────────────────── 运行时读审计 ───────────────────────────
def test_self_read_paths_detects_generator_reading_its_artifact():
    run = {"_root": "/tmp/root", "reads": ["/tmp/root/artifacts/demo/report.json",
                                           "/tmp/root/artifacts/demo/other.json"]}
    assert contract.self_read_paths(run, "artifacts/demo/report.json") == [
        "/tmp/root/artifacts/demo/report.json"]


# ─────────────────────────── 派生引擎幂等 ───────────────────────────
def test_recompute_is_byte_identical_across_runs(tmp_path):
    artifact = tmp_path / "artifacts" / "demo" / "report.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"rows": [{"ok": True}, {"ok": False}]}), encoding="utf-8")
    channels = tmp_path / "artifacts" / "demo" / "channels.json"
    channels.write_text(json.dumps({"channels": [
        {"channel_id": "demo#total", "artifact": "artifacts/demo/report.json",
         "target": "total", "expr": "n.len('rows')"},
        {"channel_id": "demo#passed", "artifact": "artifacts/demo/report.json",
         "target": "passed", "expr": "n.count_true('rows.ok')"},
    ]}), encoding="utf-8")
    out1 = tmp_path / "out1.json"
    out2 = tmp_path / "out2.json"
    payload = recompute.run_node(str(artifact), str(out1), str(channels), repo_root=str(tmp_path))
    recompute.run_node(str(artifact), str(out2), str(channels), repo_root=str(tmp_path))
    assert payload["channels"] == {"demo#total": 2, "demo#passed": 1}
    assert out1.read_bytes() == out2.read_bytes()


# ─────────────────────────── 门禁逻辑 ───────────────────────────
def _ledger(mature: int, total: int, grades: list[str]) -> dict:
    return {"coverage": {"mature": mature, "total": total},
            "channels": [{"channel_id": f"demo#{i}", "grade": g,
                          "detail": {"generator_sha256": "x" if g != "G0" else None}}
                         for i, g in enumerate(grades)]}


def test_gate_rejects_below_coverage_threshold():
    verdict = contract.gate(_ledger(23, 36, ["G3"] * 23 + ["G0"] * 13), min_coverage=24)
    assert not verdict["passed"]
    assert any(r.startswith("coverage_below_threshold") for r in verdict["reasons"])


def test_gate_rejects_channel_below_min_grade():
    verdict = contract.gate(_ledger(3, 3, ["G3", "G2", "G1"]), min_coverage=3, min_grade="G2")
    assert not verdict["passed"]
    assert any(r.startswith("channels_below_min_grade") for r in verdict["reasons"])


def test_gate_rejects_stale_ledger():
    verdict = contract.gate(_ledger(3, 3, ["G3"] * 3), min_coverage=3,
                            freshness={"fresh": False, "problems": ["generator_changed_since_ledger:x"]})
    assert not verdict["passed"]
    assert "generator_changed_since_ledger:x" in verdict["reasons"]


def test_gate_passes_when_coverage_grade_and_provenance_hold():
    verdict = contract.gate(_ledger(24, 36, ["G3"] * 24 + ["G0"] * 12), min_coverage=24)
    assert verdict["passed"], verdict["reasons"]


# ─────────────────────────── 负控端到端 ───────────────────────────
def test_negative_controls_all_behave_as_expected():
    report = negcontrols.run_controls()
    assert report["all_ok"], json.dumps(report["checks"], ensure_ascii=False)
    observed = report["observed"]
    assert observed["base"]["gate_passed"] is True
    for cut in ("cut1_handcopy", "cut2_generator_moved", "cut3_hardcoded_seed"):
        assert observed[cut]["gate_passed"] is False