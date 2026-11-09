"""门判据内容化（C2b）验收测试：`ops/taskplane/gate_content_criteria.py` + 放门接线。

判据先于实现，机器可裁：

1. **声明解析**：`gate_criteria.checks` / `acceptance_checks` 两种声明位都认；
   未知 kind / 缺必填字段 / 非法 op / 非法 digest / 重复 id / 负 tolerance
   → 显式 `SpecError`（声明错误绝不静默跳过）；未声明 → `NOT_DECLARED`。
2. **三态裁决**：PASS（每条判据都被实测值满足）；FAIL（判据可判且被违反：
   阈值不达标 / 摘要不符 / 逃逸 roots / 类型冲突）；WITHHELD（证据不足：
   文件缺失 / JSON 损坏 / 指针不存在 / 值为 null）。FAIL 优先于 WITHHELD。
3. **缺陷复现（A/B 基线）**：空洞工件 `{}` 在**旧门**（仅存在性）下被放行，
   在**新门**（声明了 checks）下被 `content_withheld` 拦住——这是本次修复的
   真改进点，有对照组而非「测试绿即完成」。
4. **向后兼容**：未声明 checks 的门判定逐字段不变；语义等价回归。
5. **确定性与接线**：同一输入两次求值 digest 相同；`run_once` 遇到内容不达标
   只写 wait 审计、节点状态保持 blocked（绝不写 failed）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.taskplane import gate_content_criteria as cc  # noqa: E402
from ops.taskplane.gate_auto_release import (  # noqa: E402
    ACTION_RELEASE,
    ACTION_WAIT,
    FROM_STATUS,
    MemoryStore,
    evaluate_gate,
    run_once,
)


# ─────────────────────────── 夹具（全部合成，零领域语义） ───────────────────────────

@pytest.fixture()
def repo_dir(tmp_path: Path) -> Path:
    root = tmp_path / "demo_repo"
    (root / "artifacts" / "demo").mkdir(parents=True)
    return root


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def _meta(checks, artifacts=("artifacts/demo/verdict.json",), **extra) -> dict:
    meta = {"gate_criteria": {"artifacts": list(artifacts), "checks": list(checks)}}
    meta.update(extra)
    return meta


VERDICT_JSON = {"verdict": "PASS", "checks": {"residual": 0.012, "samples": [1, 2, 3]},
                "notes": "synthetic fixture"}


def _residual_check(**over) -> dict:
    spec = {"id": "residual", "kind": "json_pointer",
            "path": "artifacts/demo/verdict.json", "pointer": "/checks/residual",
            "op": "<=", "value": 0.03}
    spec.update(over)
    return spec


# ─────────────────────────── 1. 声明解析 ───────────────────────────

class TestParseChecks:
    def test_absent_checks_is_not_declared(self):
        verdict = cc.evaluate_content({}, roots=())
        assert verdict.declared is False
        assert verdict.verdict == cc.VERDICT_NOT_DECLARED
        assert verdict.blocking is False

    def test_gate_criteria_checks_source(self):
        specs, source = cc.parse_checks(_meta([_residual_check()]))
        assert source == "metadata.gate_criteria"
        assert [s.id for s in specs] == ["residual"]
        assert specs[0].kind == "json_pointer"

    def test_acceptance_checks_list_source(self):
        specs, source = cc.parse_checks({"acceptance_checks": [_residual_check()]})
        assert source == "metadata.acceptance_checks"
        assert len(specs) == 1

    def test_derived_id_is_deterministic(self):
        spec = cc.parse_check({"kind": "exists", "path": "a/b.json"})
        assert spec.id == "exists@a/b.json"

    @pytest.mark.parametrize("raw,needle", [
        ({"kind": "teleport", "path": "x"}, "未知 kind"),
        ({"kind": "json_pointer", "path": "x", "pointer": "/p", "op": "<="}, "缺少必填字段"),
        ({"kind": "json_pointer", "path": "x", "pointer": "/p", "op": "≈", "value": 1}, "未知 op"),
        ({"kind": "sha256", "path": "x", "digest": "deadbeef"}, "64 位十六进制"),
        ({"kind": "min_bytes", "path": "x", "value": 1, "tolerance": -1}, "非负"),
    ])
    def test_invalid_specs_raise(self, raw, needle):
        with pytest.raises(cc.SpecError) as exc:
            cc.parse_check(raw)
        assert needle in str(exc.value)

    def test_duplicate_ids_raise(self):
        with pytest.raises(cc.SpecError) as exc:
            cc.parse_checks(_meta([_residual_check(id="dup"), _residual_check(id="dup")]))
        assert "重复" in str(exc.value)

    def test_checks_must_be_array(self):
        with pytest.raises(cc.SpecError):
            cc.parse_checks({"gate_criteria": {"checks": "residual<=0.03"}})


# ─────────────────────────── 2. 通用算子 / JSON Pointer ───────────────────────────

class TestPrimitives:
    @pytest.mark.parametrize("op,observed,expected,ok", [
        ("<=", 0.012, 0.03, True), ("<=", 0.31, 0.03, False),
        ("==", "PASS", "PASS", True), ("!=", "FAIL", "PASS", True),
        (">=", 3, 3, True), ("~=", 1.0000000001, 1.0, True),
        ("in", "PASS", ["PASS", "FAIL"], True), ("in", "WITHHELD", ["PASS"], False),
        ("contains", "abc-residual", "residual", True),
    ])
    def test_compare(self, op, observed, expected, ok):
        assert cc.compare(op, observed, expected)[0] is ok

    def test_compare_type_conflict_raises(self):
        with pytest.raises(ValueError):
            cc.compare("<=", "not-a-number", 0.03)

    @pytest.mark.parametrize("pointer,expected_present,expected_value", [
        ("/checks/residual", True, 0.012),
        ("/checks/samples/2", True, 3),
        ("/checks/samples/9", False, None),
        ("/a~1b", True, "slash"),
        ("/missing", False, None),
    ])
    def test_json_pointer(self, pointer, expected_present, expected_value):
        doc = {"checks": {"residual": 0.012, "samples": [1, 2, 3]}, "a/b": "slash"}
        present, value = cc.json_pointer(doc, pointer)
        assert present is expected_present
        if expected_present:
            assert value == expected_value

    def test_bad_pointer_rejected(self):
        with pytest.raises(ValueError):
            cc.json_pointer({"a": 1}, "a")


# ─────────────────────────── 3. 三态裁决 ───────────────────────────

class TestTriState:
    def test_pass(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_PASS
        assert verdict.checks[0].observed == 0.012
        assert verdict.checks[0].evidence["size_bytes"] > 0

    def test_fail_on_threshold(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json",
               {"checks": {"residual": 0.42}})
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_FAIL
        assert verdict.blocking is True

    def test_withheld_on_absent_pointer(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {})  # 空洞工件
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_WITHHELD
        assert verdict.checks[0].detail == "pointer_absent"
        # 关键纪律：证据不足绝不折算成 PASS
        assert verdict.passed is False

    def test_withheld_on_missing_file(self, repo_dir):
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_WITHHELD
        assert verdict.checks[0].detail == "not_found"

    def test_withheld_on_corrupt_json(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", "{not json")
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_WITHHELD
        assert verdict.checks[0].detail.startswith("json_invalid")

    def test_withheld_on_null_value(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {"checks": {"residual": None}})
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_WITHHELD
        assert verdict.checks[0].detail == "value_null"

    def test_fail_on_type_conflict(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json",
               {"checks": {"residual": "pending"}})
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_FAIL
        assert verdict.checks[0].detail.startswith("compare_error")

    def test_fail_overrides_withheld(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {"checks": {"residual": 0.42}})
        meta = _meta([_residual_check(),
                      {"id": "nope", "kind": "json_pointer",
                       "path": "artifacts/demo/verdict.json", "pointer": "/absent",
                       "op": "<=", "value": 1}])
        verdict = cc.evaluate_content(meta, roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_FAIL
        assert verdict.to_dict()["counts"] == {"PASS": 0, "FAIL": 1, "WITHHELD": 1}

    def test_sha256_match_and_mismatch(self, repo_dir):
        target = _write(repo_dir / "artifacts/demo/blob.bin", "payload-0123456789")
        real = cc._sha256_of(target)
        ok_meta = _meta([{"id": "dig", "kind": "sha256",
                          "path": "artifacts/demo/blob.bin", "digest": real}])
        assert cc.evaluate_content(ok_meta, roots=[str(repo_dir)]).verdict == cc.VERDICT_PASS
        bad_meta = _meta([{"id": "dig", "kind": "sha256",
                           "path": "artifacts/demo/blob.bin", "digest": "0" * 64}])
        verdict = cc.evaluate_content(bad_meta, roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_FAIL
        assert verdict.checks[0].detail == "digest_mismatch"

    def test_min_bytes(self, repo_dir):
        _write(repo_dir / "artifacts/demo/blob.bin", "x" * 32)
        ok = _meta([{"id": "sz", "kind": "min_bytes",
                     "path": "artifacts/demo/blob.bin", "value": 16}])
        assert cc.evaluate_content(ok, roots=[str(repo_dir)]).verdict == cc.VERDICT_PASS
        bad = _meta([{"id": "sz", "kind": "min_bytes",
                      "path": "artifacts/demo/blob.bin", "value": 64}])
        assert cc.evaluate_content(bad, roots=[str(repo_dir)]).verdict == cc.VERDICT_FAIL

    def test_text_contains_negate(self, repo_dir):
        _write(repo_dir / "docs/report.md", "residual 0.012 <= 0.03\n")
        meta = _meta([{"id": "no_todo", "kind": "text_contains",
                       "path": "docs/report.md", "pattern": "TODO", "negate": True}],
                     artifacts=("docs/report.md",))
        assert cc.evaluate_content(meta, roots=[str(repo_dir)]).verdict == cc.VERDICT_PASS
        _write(repo_dir / "docs/report.md", "TODO: fill numbers\n")
        assert cc.evaluate_content(meta, roots=[str(repo_dir)]).verdict == cc.VERDICT_FAIL

    def test_json_count(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        meta = _meta([{"id": "n", "kind": "json_count",
                       "path": "artifacts/demo/verdict.json",
                       "pointer": "/checks/samples", "op": ">=", "value": 3}])
        verdict = cc.evaluate_content(meta, roots=[str(repo_dir)])
        assert verdict.verdict == cc.VERDICT_PASS
        assert verdict.checks[0].observed == 3

    def test_path_traversal_is_fail(self, repo_dir, tmp_path):
        outside = _write(tmp_path / "outside.json", VERDICT_JSON)
        assert outside.exists()
        meta = _meta([_residual_check(path="../outside.json")])
        verdict = cc.evaluate_content(meta, roots=[str(repo_dir / "artifacts/demo")])
        assert verdict.verdict == cc.VERDICT_FAIL
        assert verdict.checks[0].detail == "escapes_roots"

    def test_repo_scoping(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        ok = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)],
                                 repo="demo_repo")
        assert ok.verdict == cc.VERDICT_PASS
        other = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)],
                                    repo="someone_else")
        assert other.verdict == cc.VERDICT_WITHHELD
        assert other.checks[0].detail.startswith("repo_mismatch")

    def test_deterministic_digest(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        a = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        b = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        assert a.digest() == b.digest()
        assert len(a.digest()) == 64

    def test_markdown_renders_observed_values(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        verdict = cc.evaluate_content(_meta([_residual_check()]), roots=[str(repo_dir)])
        md = cc.render_markdown(verdict)
        assert "| residual |" in md and "0.012" in md and "PASS" in md


# ─────────────────────────── 4. 接线 + A/B 缺陷复现 ───────────────────────────

def _gate_node(gate_id: int, meta: dict, deps=(1,)) -> dict:
    return {"id": gate_id, "plan_id": 1, "name": f"G{gate_id}", "node_type": "gate",
            "status": FROM_STATUS, "dependencies": list(deps), "metadata": meta}


def _done(node_id: int) -> dict:
    return {"id": node_id, "plan_id": 1, "name": f"N{node_id}", "node_type": "task",
            "status": "done", "dependencies": [], "metadata": {}}


class TestGateWiring:
    def test_defect_baseline_hollow_artifact_used_to_release(self, repo_dir):
        """A/B 基线：空洞 `{}` 工件在旧门（未声明 checks）下被放行 —— 这是缺陷本体。"""
        _write(repo_dir / "artifacts/demo/verdict.json", {})
        legacy = _meta([], artifacts=("artifacts/demo/verdict.json",))
        legacy["gate_criteria"].pop("checks")
        node = _gate_node(90, legacy)
        by_id = {1: _done(1), 90: node}
        decision = evaluate_gate(node, by_id, [str(repo_dir)])
        assert decision.action == ACTION_RELEASE
        assert decision.reason == "satisfied"

    def test_fix_hollow_artifact_now_withheld(self, repo_dir):
        """同一空洞工件，新门声明 checks 后拦住（WITHHELD，不是 FAIL——工件可能还在写）。"""
        _write(repo_dir / "artifacts/demo/verdict.json", {})
        node = _gate_node(91, _meta([_residual_check()]))
        by_id = {1: _done(1), 91: node}
        decision = evaluate_gate(node, by_id, [str(repo_dir)])
        assert decision.action == ACTION_WAIT
        assert decision.reason == "content_withheld"
        assert decision.checks["content"]["verdict"] == cc.VERDICT_WITHHELD
        assert decision.checks["content"]["checks"][0]["detail"] == "pointer_absent"

    def test_content_satisfied_releases(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        node = _gate_node(92, _meta([_residual_check()]))
        decision = evaluate_gate(node, {1: _done(1), 92: node}, [str(repo_dir)])
        assert decision.action == ACTION_RELEASE
        assert decision.checks["content"]["ok"] is True
        assert decision.checks["content"]["digest"] == cc.evaluate_content(
            node["metadata"], [str(repo_dir)]).digest()

    def test_content_violation_waits_not_failed(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {"checks": {"residual": 0.9}})
        node = _gate_node(93, _meta([_residual_check()]))
        decision = evaluate_gate(node, {1: _done(1), 93: node}, [str(repo_dir)])
        assert decision.action == ACTION_WAIT
        assert decision.reason == "content_fail"

    def test_invalid_spec_never_releases(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        node = _gate_node(94, _meta([{"kind": "teleport", "path": "x"}]))
        decision = evaluate_gate(node, {1: _done(1), 94: node}, [str(repo_dir)])
        assert decision.action == ACTION_WAIT
        assert decision.reason == "content_spec_invalid"
        assert "teleport" in decision.checks["content"]["error"]

    def test_legacy_gate_without_checks_unchanged(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {})
        legacy = {"gate_criteria": {"artifacts": ["artifacts/demo/verdict.json"]}}
        node = _gate_node(95, legacy)
        decision = evaluate_gate(node, {1: _done(1), 95: node}, [str(repo_dir)])
        assert decision.action == ACTION_RELEASE
        assert decision.checks["content"] == {"ok": True, "declared": False,
                                              "verdict": "NOT_DECLARED"}
        # 旧字段语义逐条保留
        assert decision.checks["artifacts"]["ok"] is True
        assert decision.checks["upstream"]["ok"] is True

    def test_upstream_pending_still_dominates(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        node = _gate_node(96, _meta([_residual_check()]))
        pending = {"id": 1, "plan_id": 1, "name": "N1", "node_type": "task",
                   "status": "in_progress", "dependencies": [], "metadata": {}}
        decision = evaluate_gate(node, {1: pending, 96: node}, [str(repo_dir)])
        assert decision.action == ACTION_WAIT
        assert decision.reason == "upstream_pending"

    def test_run_once_keeps_blocked_and_audits_content_fail(self, repo_dir):
        _write(repo_dir / "artifacts/demo/verdict.json", {"checks": {"residual": 0.9}})
        good = _write_gate_fixture(repo_dir)
        gate = _gate_node(97, good)
        store = MemoryStore([_done(1), gate])
        result = run_once(store, roots=[str(repo_dir)], actor="pytest", run_id="t1")
        assert result["counts"][ACTION_RELEASE] == 0
        assert result["counts"][ACTION_WAIT] == 1
        statuses = {int(n["id"]): n["status"] for n in store.nodes()}
        assert statuses[97] == FROM_STATUS  # 绝不写 failed、也不放行
        waits = [r for r in store.audit if r["action"] == ACTION_WAIT]
        assert waits and waits[-1]["reason"] == "content_fail"
        assert waits[-1]["detail"]["content"]["verdict"] == cc.VERDICT_FAIL


def _write_gate_fixture(root: Path) -> dict:
    """合成门夹具：工件 + 两条判据（数值阈值 + 摘要）。"""
    target = _write(root / "artifacts/demo/verdict.json",
                    {"checks": {"residual": 0.9}})
    return _meta([_residual_check(),
                  {"id": "dig", "kind": "sha256",
                   "path": "artifacts/demo/verdict.json",
                   "digest": cc._sha256_of(target)}])

# ─────────────────────────── 5. CLI ───────────────────────────

class TestCli:
    def test_cli_writes_json_and_markdown(self, repo_dir, tmp_path):
        _write(repo_dir / "artifacts/demo/verdict.json", VERDICT_JSON)
        meta_path = tmp_path / "meta.json"
        meta_path.write_text(json.dumps(_meta([_residual_check()])), encoding="utf-8")
        out_json = tmp_path / "out" / "verdict.json"
        out_md = tmp_path / "out" / "verdict.md"
        rc = cc.main(["--metadata", str(meta_path), "--roots", str(repo_dir),
                      "--json-out", str(out_json), "--md-out", str(out_md)])
        assert rc == 0
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["verdict"] == cc.VERDICT_PASS
        assert payload["checks"][0]["observed"] == 0.012
        assert "| residual |" in out_md.read_text(encoding="utf-8")

    def test_cli_nonzero_on_blocking_verdict(self, repo_dir, tmp_path):
        _write(repo_dir / "artifacts/demo/verdict.json", {"checks": {"residual": 0.5}})
        meta_path = tmp_path / "meta.json"
        meta_path.write_text(json.dumps(_meta([_residual_check()])), encoding="utf-8")
        rc = cc.main(["--metadata", str(meta_path), "--roots", str(repo_dir),
                      "--json-out", str(tmp_path / "v.json")])
        assert rc == 1
