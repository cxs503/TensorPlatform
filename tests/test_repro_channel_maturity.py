"""tests/test_repro_channel_maturity.py —— 数值通道成熟度审计 + 入库门。

全部夹具为**合成工件**（零领域语义）：只出现 ``alpha/beta/gamma`` 这类占位名，
不出现任何学科词、船型、仓名 —— 符合平台「零领域语义」铁律。

覆盖：
  * 派生引擎（derive）：路径解析、归约、AST 白名单、扰动原语
  * 通道评级 L0→L3 的每一档跃迁与降级原因
  * 入库门 fail-closed 六类失败面
  * 报表引用门
  * 真实仓库台账：覆盖率 ≥24/36 且全 L3、审计幂等、台账无漂移
  * 基线：历史 gen_*.py 注册表在同一口径下 L3 == 0（MOCK 证据）
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.repro_audit.generators import channel_specs, derive, maturity  # noqa: E402


# ─────────────────────────── 合成夹具 ───────────────────────────

def _fixture(tmp_path, extra=None):
    """合成仓库：一个工件 + 一条可派生的通道。"""
    art = {
        "rows": [
            {"name": "alpha", "state": "ok", "score": 2},
            {"name": "beta", "state": "bad", "score": 4},
            {"name": "gamma", "state": "ok", "score": 6},
        ],
        "summary": {"n_rows": 3, "n_ok": 2, "total_score": 12, "mean_score": 4.0},
    }
    if extra:
        art.update(extra)
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    (tmp_path / "artifacts" / "sample.json").write_text(
        json.dumps(art, indent=2), encoding="utf-8")
    return art


def _spec(**kw):
    base = dict(channel_id="sample#n_rows",
                artifact="artifacts/sample.json",
                target="summary.n_rows",
                expr="n.len('rows')",
                kind="tally",
                note="行数 == rows 行数")
    base.update(kw)
    return base


# ─────────────────────────── 派生引擎 ───────────────────────────

def test_values_flat_and_mapped():
    root = {"a": [{"v": 1}, {"v": 2}], "b": {"c": 3}}
    assert derive.values(root, "b.c") == [3]
    assert derive.values(root, "a.v") == [1, 2]
    assert derive.values(root, "a[1].v") == [2]
    assert derive.evaluate("n.sum('a.v')", root) == 3.0
    assert derive.evaluate("n.mean('a.v')", root) == 1.5


def test_evaluate_arithmetic_and_reducers():
    root = {"xs": [1, 2, 3, 4]}
    assert derive.evaluate("n.len('xs')*100/n.sum('xs')", root) == 40.0
    assert derive.evaluate("n.max('xs')-n.min('xs')", root) == 3.0
    assert derive.evaluate("n.abs(2-5)", root) == 3


def test_evaluate_rejects_arbitrary_code():
    with pytest.raises(ValueError):
        derive.evaluate("__import__('os').system('true')", {})
    with pytest.raises(ValueError):
        derive.evaluate("(lambda: 1)()", {})
    with pytest.raises(ValueError):
        derive.evaluate("n.unknown_reducer('x')", {"x": 1})


def test_numeric_literals_and_reducer_paths():
    assert derive.numeric_literals("n.len('a.b')*100") == [100.0]
    assert derive.reducer_paths("n.len('a.b')+n.sum('c')") == ["a.b", "c"]


def test_mutation_primitives():
    root = {"a": [{"v": 1}, {"v": 2}], "b": {"c": 5}}
    assert derive.drop_last_at(root, "a") is True
    assert root["a"] == [{"v": 1}]
    derive.set_at(root, "b.c", 7)
    assert root["b"]["c"] == 7
    assert derive.leaf_paths(root, "number") == ["a[0].v", "b.c"]


# ─────────────────────────── 评级跃迁 ───────────────────────────

def test_channel_reaches_L3(tmp_path):
    _fixture(tmp_path)
    rec = maturity.audit_channel(_spec(), str(tmp_path))
    assert rec["grade"] == "L3", rec["reasons"]
    ident = rec["identity"]
    assert len(ident["engine_sha256"]) == 64
    assert len(ident["spec_sha256"]) == 64
    assert len(ident["source_sha256"]) == 64
    assert rec["mutation"]["responsive"] >= 1


def test_missing_artifact_is_L0(tmp_path):
    rec = maturity.audit_channel(_spec(artifact="artifacts/nope.json"), str(tmp_path))
    assert rec["grade"] == "L0"
    assert "artifact_missing" in rec["reasons"]


def test_replay_mismatch_is_L0(tmp_path):
    _fixture(tmp_path)
    rec = maturity.audit_channel(_spec(target="summary.n_ok"), str(tmp_path))
    assert rec["grade"] == "L0"
    assert "replay_mismatch" in rec["reasons"]


def test_missing_target_is_L0(tmp_path):
    _fixture(tmp_path)
    rec = maturity.audit_channel(_spec(target="summary.absent"), str(tmp_path))
    assert rec["grade"] == "L0"
    assert "target_path_missing" in rec["reasons"]


def test_literal_echo_caps_at_L2(tmp_path):
    """派生式里写死等于被断言值的常量 → 回抄嫌疑，判不了 L3。"""
    art = _fixture(tmp_path)
    rec = maturity.audit_channel(
        _spec(target="summary.n_rows", expr="n.len('rows')*0+3"), str(tmp_path))
    assert art["summary"]["n_rows"] == 3
    assert rec["grade"] == "L2"
    assert any(r.startswith("literal_echo") for r in rec["reasons"])


def test_insensitive_output_caps_at_L2(tmp_path):
    """输出与输入无关（恒为 0 的差）→ 常函数，判不了 L3。"""
    _fixture(tmp_path, extra={"zero": 0})
    rec = maturity.audit_channel(
        _spec(target="zero", expr="n.len('rows')-n.len('rows')"), str(tmp_path))
    assert rec["grade"] == "L2"
    assert "output_insensitive_to_input" in rec["reasons"]


def test_tampered_artifact_breaks_replay(tmp_path):
    """工件被手改（明细没动、头部改了）→ 通道立刻翻红。"""
    _fixture(tmp_path)
    path = tmp_path / "artifacts" / "sample.json"
    doc = json.loads(path.read_text())
    doc["summary"]["n_rows"] = 4
    path.write_text(json.dumps(doc))
    rec = maturity.audit_channel(_spec(), str(tmp_path))
    assert rec["grade"] == "L0"
    assert "replay_mismatch" in rec["reasons"]


def test_trimmed_detail_breaks_tally(tmp_path):
    """明细被裁剪（尾部少一行）→ tally 通道翻红。"""
    _fixture(tmp_path)
    path = tmp_path / "artifacts" / "sample.json"
    doc = json.loads(path.read_text())
    doc["rows"] = doc["rows"][:2]
    path.write_text(json.dumps(doc))
    rec = maturity.audit_channel(_spec(), str(tmp_path))
    assert rec["grade"] == "L0"


# ─────────────────────────── 全量审计幂等 ───────────────────────────

def test_audit_is_idempotent(tmp_path):
    _fixture(tmp_path)
    specs = [_spec(), _spec(channel_id="sample#total_score",
                            target="summary.total_score",
                            expr="n.sum('rows.score')")]
    first = maturity.audit(str(tmp_path), specs)
    second = maturity.audit(str(tmp_path), specs)
    assert derive.canonical(first) == derive.canonical(second)
    assert first["coverage"] == second["coverage"]


def test_audit_counts_and_by_artifact(tmp_path):
    _fixture(tmp_path)
    specs = [_spec(), _spec(channel_id="x", target="summary.n_ok",
                            expr="n.count_eq('rows.state','ok')")]
    ledger = maturity.audit(str(tmp_path), specs)
    assert ledger["total"] == 2
    assert ledger["grades"]["L3"] == 2
    assert ledger["by_artifact"]["artifacts/sample.json"] == {"L3": 2, "channels": 2}


# ─────────────────────────── 入库门 fail-closed ───────────────────────────

def _write_ledger(tmp_path, specs, name="ledger.json"):
    ledger = maturity.audit(str(tmp_path), specs)
    out = tmp_path / name
    maturity.write_ledger(ledger, str(out))
    return out


def test_gate_passes_when_everything_mature(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    res = maturity.gate(str(tmp_path), str(led), min_coverage=1, channels=specs)
    assert res["passed"] is True, res["failures"]


def test_gate_fails_closed_without_ledger(tmp_path):
    _fixture(tmp_path)
    res = maturity.gate(str(tmp_path), str(tmp_path / "absent.json"),
                        min_coverage=0, channels=[_spec()])
    assert res["passed"] is False
    assert res["reason"] == "ledger_missing"


def test_gate_fails_closed_on_stale_ledger(tmp_path):
    """台账指纹被改（=工件不是当前生成器产出的）→ 门不通过。"""
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    doc = json.loads(led.read_text())
    doc["engine_sha256"] = "0" * 64
    led.write_text(json.dumps(doc))
    res = maturity.gate(str(tmp_path), str(led), min_coverage=0, channels=specs)
    assert res["passed"] is False
    assert any(f["reason"] == "stale_ledger" for f in res["failures"])


def test_gate_fails_closed_on_channel_set_mismatch(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    extra = specs + [_spec(channel_id="sample#total_score",
                           target="summary.total_score",
                           expr="n.sum('rows.score')")]
    res = maturity.gate(str(tmp_path), str(led), min_coverage=0, channels=extra)
    assert res["passed"] is False
    assert any(f["reason"] == "channel_set_mismatch" for f in res["failures"])


def test_gate_fails_closed_below_min_grade(tmp_path):
    _fixture(tmp_path)
    specs = [_spec(expr="n.len('rows')*0+3")]  # 回抄 → L2
    led = _write_ledger(tmp_path, specs)
    res = maturity.gate(str(tmp_path), str(led), min_coverage=0,
                        min_grade="L3", channels=specs)
    assert res["passed"] is False
    assert any(f["reason"] == "below_min_grade" for f in res["failures"])


def test_gate_fails_closed_on_coverage_shortfall(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    res = maturity.gate(str(tmp_path), str(led), min_coverage=24, channels=specs)
    assert res["passed"] is False
    assert any(f["reason"] == "coverage_shortfall" for f in res["failures"])


def test_gate_fails_closed_on_sample_too_small(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    res = maturity.gate(str(tmp_path), str(led), min_coverage=0,
                        total_required=36, channels=specs)
    assert res["passed"] is False
    assert any(f["reason"] == "sample_too_small" for f in res["failures"])


def test_gate_replay_grade_drift(tmp_path):
    """台账里的等级与新鲜审计不符（重放不一致）→ 门不通过。"""
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    doc = json.loads(led.read_text())
    doc["channels"][0]["grade"] = "L0"
    led.write_text(json.dumps(doc))
    res = maturity.gate(str(tmp_path), str(led), min_coverage=0, channels=specs)
    assert res["passed"] is False
    assert any(f["reason"] == "replay_grade_drift" for f in res["failures"])


# ─────────────────────────── 报表引用门 ───────────────────────────

def test_report_gate_flags_unbacked_artifact(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    (tmp_path / "artifacts" / "other.json").write_text("{}")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "markdown": "见 artifacts/sample.json 与 artifacts/other.json"}))
    res = maturity.gate_report(str(tmp_path), str(report), str(led))
    assert res["passed"] is False
    assert res["unbacked"] == ["artifacts/other.json"]


def test_report_gate_passes_when_all_backed(tmp_path):
    _fixture(tmp_path)
    specs = [_spec()]
    led = _write_ledger(tmp_path, specs)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"markdown": "见 artifacts/sample.json"}))
    assert maturity.gate_report(str(tmp_path), str(report), str(led))["passed"] is True


# ─────────────────────────── 真实仓库验收 ───────────────────────────

def test_real_manifest_is_frozen_sample_of_36():
    assert channel_specs.manifest_size() == 36
    ids = [c["channel_id"] for c in channel_specs.CHANNELS]
    assert len(ids) == len(set(ids)), "channel_id 必须唯一"
    assert all(c.get("expr") and c.get("artifact") and c.get("target")
               for c in channel_specs.CHANNELS)


def test_real_repo_coverage_meets_target():
    ledger = maturity.audit(ROOT)
    assert ledger["total"] == 36
    assert ledger["coverage"]["mature"] >= 24, ledger["grades"]
    assert ledger["grades"]["L0"] == 0, [c for c in ledger["channels"]
                                          if c["grade"] == "L0"]


def test_committed_ledger_matches_fresh_audit():
    """提交的台账必须与当前树新鲜审计逐通道一致（无漂移、可重放）。"""
    path = os.path.join(ROOT, maturity.DEFAULT_LEDGER)
    assert os.path.exists(path), path
    with open(path, encoding="utf-8") as fh:
        committed = json.load(fh)
    fresh = maturity.audit(ROOT)
    assert committed["manifest_sha256"] == fresh["manifest_sha256"]
    assert committed["engine_sha256"] == fresh["engine_sha256"]
    old = {c["channel_id"]: c["grade"] for c in committed["channels"]}
    new = {c["channel_id"]: c["grade"] for c in fresh["channels"]}
    assert old == new


def test_real_gate_passes():
    res = maturity.gate(ROOT, os.path.join(ROOT, maturity.DEFAULT_LEDGER),
                        min_coverage=24, min_grade="L3", total_required=36)
    assert res["passed"] is True, res["failures"]


def test_report_generator_injects_ledger_numbers():
    """报告里的覆盖数字必须与台账一致（防手抄漂移）。"""
    from tools.repro_audit.generators import report as report_mod
    led = maturity.audit(ROOT)
    text = report_mod.build(ROOT)
    assert "覆盖率：%d/%d" % (led["coverage"]["mature"], led["total"]) in text
    assert led["manifest_sha256"] in text
    assert "| 通道 | 类型 | 工件 |" in text


def test_legacy_registry_has_no_derivation_sound_channel():
    """基线：历史 gen_*.py 注册表在同一口径下无一条 L3（MOCK 证据）。"""
    base = maturity.legacy_baseline(ROOT)
    assert base["total"] == 36
    assert base["grades"]["L3"] == 0
    assert base["coverage"]["mature"] == 0