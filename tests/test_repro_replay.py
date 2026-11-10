"""repro_replay 单元/端到端测试（全部合成夹具，零领域语义）。

覆盖：
* 数值比对口径（observed 相对 / diagnostic 绝对 / 挥发键剔除 / 缺失键）
* 四类漂移归因（float_path / dependency_version / source_change / environment）
* KPI 逐项加总一致性与 blocked 不进分母
* 分层抽样的幂等性与分层覆盖
* 干净检出重跑的四种终局（pass / drift / fail / blocked）
* verify_audit 的机器判据自检
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.audit import repro_replay as rr  # noqa: E402


# ────────────────────────── 纯函数层 ──────────────────────────

def test_flatten_numbers_skips_bool_and_non_finite():
    got = rr.flatten_numbers({"a": 1, "b": [2.5, True, "x"], "c": {"d": None, "e": 3}})
    assert got == {"a": 1.0, "b[0]": 2.5, "c.e": 3.0}
    assert rr.flatten_numbers({"bad": float("nan")}) == {}


def test_volatile_keys_excluded_from_comparison():
    rep = {"error": 1e-3, "generated_at": 111.0, "runtime_sec": 5.0, "rtol": 2.0}
    rec = {"error": 1e-3, "generated_at": 999.0, "runtime_sec": 7.0, "rtol": 2.0}
    items, excluded, missing = rr.compare_maps(rep, rec)
    assert [i["key"] for i in items] == ["error", "rtol"]
    assert set(excluded) == {"generated_at", "runtime_sec"}
    assert missing == []
    assert all(i["verdict"] == "pass" for i in items)


def test_classify_kind_observed_vs_diagnostic():
    assert rr.classify_kind(1.0, 1.0) == "observed"
    assert rr.classify_kind(1e-9, -1e-9) == "diagnostic"


def test_compare_value_observed_relative_tolerance():
    ok = rr.compare_value(100.0, 100.0 * (1 + 5e-7))
    bad = rr.compare_value(100.0, 100.0 * (1 + 5e-6))
    assert ok["verdict"] == "pass" and ok["kind"] == "observed"
    assert bad["verdict"] == "drift" and bad["rel_delta"] == pytest.approx(5e-6, rel=1e-3)


def test_compare_value_diagnostic_absolute_tolerance():
    # 近零量：相对差可以很大，但绝对差 ≤1e-12 仍判 pass
    ok = rr.compare_value(1e-15, 2e-15)
    bad = rr.compare_value(1e-12, 5e-12)
    assert ok["kind"] == "diagnostic" and ok["verdict"] == "pass"
    assert bad["verdict"] == "drift"


def test_compare_maps_reports_missing_keys_separately():
    items, _, missing = rr.compare_maps({"a": 1.0, "b": 2.0}, {"a": 1.0, "c": 3.0})
    assert [i["key"] for i in items] == ["a"]
    assert missing == ["b", "c"]


# ────────────────────────── 四类归因 ──────────────────────────

def test_attribution_float_path():
    attr = rr.attribute_drift({"rel_delta": 1e-12}, {"unstable": False}, {})
    assert attr["label"] == "float_path"


def test_attribution_environment_when_unstable():
    attr = rr.attribute_drift({"rel_delta": 0.5}, {"unstable": True, "max_rel": 0.3}, {})
    assert attr["label"] == "environment"
    assert "非确定性" in attr["reason"]


def test_attribution_dependency_version():
    ev = {"version_mismatch": {"meta.numpy_version": {"reported": "1.24.0", "current": "2.4.6"}}}
    attr = rr.attribute_drift({"rel_delta": 0.01}, {"unstable": False}, ev)
    assert attr["label"] == "dependency_version"


def test_attribution_source_change_for_external_imports():
    ev = {"version_mismatch": None, "external_imports": ["somenumeric"]}
    attr = rr.attribute_drift({"rel_delta": 0.01}, {"unstable": False}, ev)
    assert attr["label"] == "source_change"
    assert "somenumeric" in attr["reason"]


def test_attribution_defaults_to_environment():
    attr = rr.attribute_drift({"rel_delta": 0.01}, {"unstable": False}, {"external_imports": []})
    assert attr["label"] == "environment"
    assert attr["label"] in rr.ATTRIBUTIONS


# ────────────────────────── KPI 口径 ──────────────────────────

def test_kpi_excludes_blocked_from_denominator():
    items = ([{"verdict": "pass"}] * 6 + [{"verdict": "drift"}] * 2
             + [{"verdict": "fail"}] * 2 + [{"verdict": "blocked"}] * 5)
    kpi = rr.kpi_from_items(items)
    assert kpi["counts"] == {"pass": 6, "drift": 2, "fail": 2, "blocked": 5}
    assert kpi["denominator"] == 10 and kpi["repro_rate"] == pytest.approx(0.6)
    assert kpi["blocked_share"] == pytest.approx(5 / 15)


def test_kpi_undefined_when_all_blocked():
    kpi = rr.kpi_from_items([{"verdict": "blocked"}] * 3)
    assert kpi["repro_rate"] is None and kpi["denominator"] == 0


def test_kpi_consistency_tolerance_1e_9():
    items = [{"verdict": "pass"}] * 2 + [{"verdict": "drift"}] * 1
    assert rr.kpi_consistent(2 / 3, items)
    assert rr.kpi_consistent(2 / 3 + 1e-10, items)
    assert not rr.kpi_consistent(2 / 3 + 1e-6, items)


# ────────────────────────── 抽样 ──────────────────────────

def _synthetic_frame():
    frame = []
    nid = 1
    for repo in ("repoA", "repoB", "repoC"):
        for year in (2025, 2026):
            for _ in range(3):
                frame.append({"node_id": nid, "mission_id": nid, "repo": repo, "commit": "deadbeef",
                              "stratum": [repo, str(year)], "layer": "numeric", "specs": [],
                              "test_files": []})
                nid += 1
    return frame


def test_stratified_sample_is_idempotent_and_covers_strata():
    frame = _synthetic_frame()
    a = rr.stratified_sample(frame, 9, seed="s")
    b = rr.stratified_sample(list(reversed(frame)), 9, seed="s")
    assert [x["node_id"] for x in a] == [x["node_id"] for x in b]
    assert len(a) == 9
    assert len({tuple(x["stratum"]) for x in a}) == 6      # 先跨层轮转
    assert len({x["repo"] for x in a}) == 3


def test_stratified_sample_seed_changes_selection():
    frame = _synthetic_frame()
    assert {x["node_id"] for x in rr.stratified_sample(frame, 6, seed="s1")} != \
           {x["node_id"] for x in rr.stratified_sample(frame, 6, seed="s2")}


def test_stratified_sample_caps_at_frame_size():
    frame = _synthetic_frame()
    assert len(rr.stratified_sample(frame, 999, seed="s")) == len(frame)


# ────────────────────────── 端到端：干净检出重跑 ──────────────────────────

def _make_repo(tmp_path, *, artifact: dict, producer_src: str | None, name: str = "repoA"):
    """合成一个 git 仓：一个工件 + 一个生产者脚本。"""
    repo = tmp_path / name
    (repo / "artifacts").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    (repo / "artifacts" / "m.json").write_text(json.dumps(artifact), encoding="utf-8")
    if producer_src is not None:
        (repo / "tools" / "producer.py").write_text(producer_src, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "syn"], cwd=repo, check=True,
                   env={**os.environ, **env})
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout.strip()
    return repo, sha


def _producer(value_expr: str) -> str:
    return ("import json, os\n"
            "art = os.path.join('artifacts', 'm.json')\n"
            "os.makedirs('artifacts', exist_ok=True)\n"
            f"json.dump({{'metric': {value_expr}, 'residual': 0.0}}, open(art, 'w'))\n")


def _node(sha: str, repo: str = "repoA", spec: bool = True) -> dict:
    return {"node_id": 1, "mission_id": 1, "repo": repo, "commit": sha,
            "stratum": [repo, "numeric"], "layer": "numeric",
            "specs": [{"artifact": "artifacts/m.json", "script": "tools/producer.py"}] if spec else [],
            "test_files": []}


def test_replay_node_pass_when_recomputed_matches(tmp_path):
    repo, sha = _make_repo(tmp_path, artifact={"metric": 2.0, "residual": 0.0},
                           producer_src=_producer("2.0"))
    out = rr.replay_node(_node(sha), str(tmp_path), {"report_id": 1})
    assert out["verdict"] == "pass"
    assert out["layer"] == "numeric"
    assert {i["key"] for i in out["items"]} == {"metric", "residual"}
    assert all(i["verdict"] == "pass" for i in out["items"])
    assert out["anchors"] and out["anchors"][0]["export"]["files"] > 0


def test_replay_node_drift_is_attributed(tmp_path):
    repo, sha = _make_repo(tmp_path, artifact={"metric": 2.0, "residual": 0.0},
                           producer_src=_producer("2.5"))
    out = rr.replay_node(_node(sha), str(tmp_path), None)
    assert out["verdict"] == "drift"
    assert out["attribution"]["label"] in rr.ATTRIBUTIONS
    assert out["attribution"]["n_drift"] == 1
    # 判据 (c)：同一 drift 必须两次重跑一致
    assert out["stability"]["runs"] == 2 and out["stability"]["unstable"] is False


def test_replay_node_unstable_drift_becomes_fail(tmp_path):
    src = ("import json, os, random\n"
           "os.makedirs('artifacts', exist_ok=True)\n"
           "json.dump({'metric': 2.0 + random.random()}, open(os.path.join('artifacts','m.json'),'w'))\n")
    repo, sha = _make_repo(tmp_path, artifact={"metric": 2.0}, producer_src=src)
    out = rr.replay_node(_node(sha), str(tmp_path), None)
    assert out["verdict"] == "fail"
    assert out["attribution"]["label"] == "environment"


def test_replay_node_blocked_when_not_a_producer(tmp_path):
    repo, sha = _make_repo(tmp_path, artifact={"metric": 2.0},
                           producer_src="print('i do not write artifacts')\n")
    out = rr.replay_node(_node(sha), str(tmp_path), None)
    assert out["verdict"] == "blocked"
    assert "not_a_producer" in out["blocked_reason"]


def test_replay_node_blocked_on_timeout(tmp_path):
    repo, sha = _make_repo(tmp_path, artifact={"metric": 2.0},
                           producer_src="import time\ntime.sleep(30)\n")
    out = rr.replay_node(_node(sha), str(tmp_path), None, timeout=6)
    assert out["verdict"] == "blocked"
    assert "timeout" in out["blocked_reason"]
    assert out["duration_sec"] <= 6 + 10


def test_replay_node_uses_test_layer_when_no_numeric_spec(tmp_path):
    repo = tmp_path / "repoT"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n",
                                               encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "syn"],
                   cwd=repo, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout.strip()
    node = {"node_id": 2, "mission_id": 2, "repo": "repoT", "commit": sha,
            "stratum": ["repoT", "test"], "layer": "test", "specs": [],
            "test_files": ["tests/test_ok.py"]}
    out = rr.replay_node(node, str(tmp_path), None)
    assert out["verdict"] == "pass"
    assert out["layer"] == "test"
    assert out["test_layer"]["counts"].get("passed") == 1


# ────────────────────────── 机器判据自检 ──────────────────────────

def _synthetic_audit(n_items=32, repos=3):
    items = []
    for i in range(n_items):
        verdict = "pass" if i % 4 else ("drift" if i % 8 == 0 else "blocked")
        item = {"node_id": i, "mission_id": i, "repo": f"repo{i % repos}", "commit": "c",
                "verdict": verdict, "items": [], "duration_sec": 1.0,
                "anchors": [{"export": {}}], "stability": None, "attribution": None}
        if verdict == "drift":
            item["attribution"] = {"label": "float_path", "reason": "x"}
            item["stability"] = {"runs": 2, "unstable": False}
        items.append(item)
    return {"items": items, "kpi": rr.kpi_from_items(items)}


def test_verify_audit_passes_on_complete_evidence():
    audit = _synthetic_audit()
    rep = rr.verify_audit(audit)
    assert rep["n_beyond_30"] and rep["n_repos"] >= 3
    assert rep["kpi_consistent"] and rep["blocked_not_in_denominator"]
    assert rep["all_criteria_pass"] is True


def test_verify_audit_flags_missing_attribution_and_budget():
    audit = _synthetic_audit()
    for it in audit["items"]:
        if it["verdict"] == "drift":
            it["attribution"] = None
    audit["kpi"] = rr.kpi_from_items(audit["items"])
    rep = rr.verify_audit(audit)
    assert rep["drift_without_attribution"]
    assert rep["all_criteria_pass"] is False

    audit2 = _synthetic_audit()
    audit2["items"][0]["duration_sec"] = rr.PER_NODE_TIMEOUT + 1
    rep2 = rr.verify_audit(audit2)
    assert rep2["over_budget_nodes"] == [0]


def test_verify_audit_detects_kpi_detail_mismatch():
    audit = _synthetic_audit()
    audit["kpi"]["repro_rate"] = 0.5
    rep = rr.verify_audit(audit)
    assert rep["kpi_consistent"] is False
    assert rep["all_criteria_pass"] is False

# ────────────────────────── 注入式灵敏度对照 ──────────────────────────

def test_inject_relative_and_absolute():
    base = {"a": 2.0, "b": 1e-9}
    assert rr.inject(base, "a", "rel", 1e-3)["a"] == pytest.approx(2.0 * 1.001)
    assert rr.inject(base, "b", "abs", 1e-6)["b"] == pytest.approx(1e-9 + 1e-6)
    assert base["a"] == 2.0                     # 不改原映射（纯函数）


def test_pick_keys_separates_observed_and_diagnostic():
    obs, diag = rr._pick_keys({"x": 5.0, "y": 1e-9, "ts": 123.0}, {"x": 5.0, "y": 1e-9, "ts": 9.0})
    assert obs == "x" and diag == "y"


def test_injected_offset_is_detected_by_comparator():
    committed = {"m": 2.0}
    recomputed = {"m": 2.0}
    hit, _, _ = rr.compare_maps(committed, rr.inject(recomputed, "m", "rel", 1e-3))
    assert hit[0]["verdict"] == "drift" and hit[0]["kind"] == "observed"
    miss, _, _ = rr.compare_maps(committed, rr.inject(recomputed, "m", "rel", 1e-9))
    assert miss[0]["verdict"] == "pass"


def test_local_modules_is_layout_aware(tmp_path):
    (tmp_path / "src" / "pkgA").mkdir(parents=True)
    (tmp_path / "src" / "pkgA" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "modB.py").write_text("x = 1\n", encoding="utf-8")
    assert {"pkgA", "modB"} <= rr.local_modules(str(tmp_path))
