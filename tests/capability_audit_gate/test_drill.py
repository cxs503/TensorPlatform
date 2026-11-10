"""回归防护演练（``drill``）的验收测试。

覆盖：golden 语料上 100% 检出、漏检时 pass=False、跳过工作树写入、
无目标能力时不算通过（证据不足）。全部用合成域（alpha/beta…）夹具，零领域语义。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.capability_audit_gate import drill, gate, spec as spec_mod

REPO = Path(__file__).resolve().parents[2]
GOLDEN_SPEC = REPO / "config/capability_audit_gate/golden/spec.json"
GOLDEN_ROOT = REPO / "config/capability_audit_gate/golden"


@pytest.fixture(scope="module")
def golden_spec() -> dict:
    return spec_mod.load_spec(GOLDEN_SPEC)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_drill_detects_every_real_capability_on_golden(golden_spec):
    result = drill.run_drill(golden_spec, root=GOLDEN_ROOT, target_level="REAL")
    assert result["targets"] >= 2, "golden corpus must contain REAL targets to be meaningful"
    assert result["missed"] == []
    assert result["detection_rate"] == 1.0
    assert result["pass"] is True
    # 注入后的等级必须是正面反证（MOCK），而不是"证据不足"（WITHHELD）
    for row in result["rows"]:
        assert row["injected_level"] == "MOCK"
        assert row["change"] in {"REFUTATION", "DOWNGRADE"}


def test_drill_does_not_mutate_the_worktree(golden_spec):
    files = [REPO / rel for rel in drill._spec_paths(golden_spec)]
    before = {f: _sha(f) for f in files if f.is_file()}
    drill.run_drill(golden_spec, root=GOLDEN_ROOT, target_level="REAL")
    after = {f: _sha(f) for f in before}
    assert before == after
    assert all(f.read_text(encoding="utf-8") != drill.TAMPER_SOURCE for f in before)


def test_drill_reports_miss_when_gate_cannot_see_the_downgrade(golden_spec, tmp_path, monkeypatch):
    """负控：把基线换成"已注入 MOCK 后的等级"，gate 就看不到变化——此时不得判 pass。"""
    real_gate = gate.compare

    def blind_compare(report, baseline, ack=None):
        result = real_gate(report, baseline, ack)
        result = copy.deepcopy(result)
        result["violations"] = [v for v in result["violations"] if v["change"] == "NO_SUCH"]
        return result

    monkeypatch.setattr(gate, "compare", blind_compare)
    result = drill.run_drill(golden_spec, root=GOLDEN_ROOT, target_level="REAL",
                             out_dir=tmp_path)
    assert result["detection_rate"] == 0.0
    assert result["pass"] is False
    assert len(result["missed"]) == result["targets"]
    assert json.loads((tmp_path / "drill.json").read_text(encoding="utf-8"))["pass"] is False


def _mock_only_spec() -> dict:
    """只含一个 MOCK 能力的迷你 spec（在 golden 语料里判定为 MOCK）。"""
    return spec_mod.validate_spec({
        "schema_version": "1.0",
        "capabilities": [{"id": "gamma.estimate",
                          "bindings": {"code": ["gamma_estimate/impl.py"],
                                       "tests": ["gamma_estimate/checks_impl.py"]}}],
    })


def test_drill_without_targets_is_not_a_pass(tmp_path):
    result = drill.run_drill(_mock_only_spec(), root=GOLDEN_ROOT, target_level="REAL",
                             out_dir=tmp_path)
    assert result["targets"] == 0
    assert result["detection_rate"] is None
    assert result["pass"] is False
    assert "nothing to drill" in result["note"]


def test_drill_cli_exit_codes(tmp_path):
    from tools.capability_audit_gate import cli

    ok = cli.main(["drill", "--spec", str(GOLDEN_SPEC), "--root", str(GOLDEN_ROOT),
                   "--out", str(tmp_path / "drill")])
    assert ok == 0
    mini = tmp_path / "mini.json"
    mini.write_text(json.dumps(_mock_only_spec()), encoding="utf-8")
    no_target = cli.main(["drill", "--spec", str(mini), "--root", str(GOLDEN_ROOT),
                          "--target-level", "REAL", "--out", str(tmp_path / "drill_none")])
    assert no_target == cli.EXIT_CONFIG


def test_frozen_config_baseline_matches_current_tree():
    """仓内基线必须与当前树一致：否则 CI 第一跑就会把"陈旧基线"当降级（假红）。"""
    spec = spec_mod.load_spec(REPO / "config/capability_audit_gate/spec.json")
    from tools.capability_audit_gate import engine

    report = engine.run_audit(spec, root=REPO)
    baseline = gate.load_baseline(REPO / "config/capability_audit_gate/baseline.json")
    comparison = gate.compare(report, baseline)
    assert comparison["fail"] is False, comparison["violations"]
    assert comparison["counts"]["violations"] == 0

def test_ci_workflow_is_valid_yaml_and_wires_the_gates():
    """CI 配置文件本身必须可解析，且回归门引用版本化基线、带演练步骤。

    背景：本节点修的正是「workflow 引用了一个从未入仓的基线 → 每次 PR 都以退出码 4
    中止」这类"死门"缺陷；把 workflow 的 YAML 合法性与关键 wiring 纳入测试，
    防止同类缺陷再从配置层混进来。
    """
    yaml = pytest.importorskip("yaml")
    wf_path = REPO / ".github/workflows/capability-audit.yml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    assert wf["env"]["BASELINE"].startswith("config/"), "baseline must be versioned data"
    steps = wf["jobs"]["audit-gate"]["steps"]
    runs = "\n".join(str(st.get("run") or "") for st in steps)
    assert "--baseline \"$BASELINE\"" in runs or "--baseline $BASELINE" in runs
    assert "cli drill" in runs, "C3 needs the regression drill step in CI"
    assert "--fail-on-gate" in runs and "--enforce-acceptance" in runs
    assert "tests collected" in runs, "C6 needs a test-count gate"
    assert (REPO / wf["env"]["BASELINE"]).is_file(), "frozen baseline must exist in repo"
