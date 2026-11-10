"""运行期插件：用**真实 pytest 运行**采集能力 marker 的结局（C1 的运行期一半）。

验证三件事：
1. 插件能区分 passed / failed / skipped / xfailed（定级的分母）；
2. 插件对未打 marker 的测试零影响（跑一遍不改变通过/失败计数）；
3. 采集结果与仓内记录的 ``expected_outcomes.json`` 一致（防漂移）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capability_discovery.plugin import (
    OUTCOME_ENV,
    load_outcome_records,
    load_outcomes,
)

SUITE = ROOT / "tools" / "capability_discovery" / "fixtures" / "synthetic_suite"
RECORDED = ROOT / "tools" / "capability_discovery" / "fixtures" / "expected_outcomes.json"

PLUGIN = "tools.capability_discovery.plugin"


def _env() -> dict:
    return {"PYTHONPATH": str(ROOT), "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"}


def _run_pytest(workdir: Path, extra_args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args, "."],
        cwd=str(workdir),
        env=_env(),
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    work = tmp_path_factory.mktemp("cd_plugin")
    suite = work / "suite"
    shutil.copytree(SUITE, suite)
    outcomes = work / "outcomes.json"
    with_plugin = _run_pytest(suite, ["-p", PLUGIN, "--capability-outcomes", str(outcomes)])
    without_plugin = _run_pytest(suite, [])
    return {
        "suite": suite,
        "outcomes_path": outcomes,
        "with_plugin": with_plugin,
        "without_plugin": without_plugin,
        "doc": json.loads(outcomes.read_text(encoding="utf-8")),
    }


def _short(nodeid: str) -> str:
    """把 nodeid 归一成 ``文件名::[类::]测试`` 便于跨 rootdir 比较。"""
    parts = nodeid.replace("\\", "/").split("::")
    return "::".join([parts[0].split("/")[-1], *parts[1:]])


class TestPluginCapture:
    def test_outcome_file_written_with_schema(self, session):
        doc = session["doc"]
        assert doc["schema"] == "capability_discovery.outcomes.v1"
        assert doc["session"]["collected"] >= 12
        assert len(doc["tests"]) >= 12

    def test_all_outcome_states_distinguished(self, session):
        states = {_short(t["nodeid"]): t["outcome"] for t in session["doc"]["tests"]}
        assert states["test_synthetic_core.py::test_trapezoid_matches_closed_form"] == "passed"
        assert states["test_synthetic_core.py::test_contradiction_case"] == "failed"
        assert states["test_synthetic_core.py::test_skipped_case"] == "skipped"
        assert states["test_synthetic_core.py::test_expected_failure_case"] == "xfailed"
        assert states["test_synthetic_core.py::test_richardson_param[8]"] == "passed"
        assert states["test_synthetic_core.py::test_richardson_param[16]"] == "passed"

    def test_marker_metadata_captured_including_class_and_module(self, session):
        by_node = {_short(t["nodeid"]): t for t in session["doc"]["tests"]}
        class_case = by_node["test_synthetic_family.py::TestVectorNorm::test_axis_aligned"]
        ids = {m["kwargs"].get("id") for m in class_case["markers"]}
        assert ids == {"synth.algebra.vector_norm", "synth.family.module_scope"}
        trap = by_node["test_synthetic_core.py::test_trapezoid_matches_closed_form"]
        assert trap["markers"][0]["kwargs"]["level"] == "REAL"
        # 非字面量 id 在运行期已被求值 → 插件能拿到真值（静态扫描拿不到）
        dyn = by_node["test_synthetic_dynamic.py::test_dynamic_marker_value"]
        assert dyn["markers"][0]["kwargs"]["id"] == "synth.dynamic.value"

    def test_plugin_does_not_change_test_results(self, session):
        assert session["with_plugin"].returncode == session["without_plugin"].returncode == 1
        with_summary = session["with_plugin"].stdout.strip().splitlines()[-1]
        without_summary = session["without_plugin"].stdout.strip().splitlines()[-1]
        for token in ("failed", "passed", "skipped", "xfailed"):
            assert with_summary.count(token) == without_summary.count(token), (
                f"插件改变了测试结局: {token}\n{with_summary}\n{without_summary}"
            )

    def test_recorded_outcomes_match_live_run(self, session):
        live = {_short(nid): st for nid, st in load_outcomes(session["outcomes_path"]).items()}
        recorded = {_short(nid): st for nid, st in load_outcomes(RECORDED).items()}
        assert live == recorded, "仓内记录的运行结局与现场运行漂移，需重新生成 expected_outcomes.json"

    def test_record_loader_exposes_markers(self, session):
        items = load_outcome_records(session["outcomes_path"])
        assert items and all("markers" in t for t in items)
        assert any(len(t["markers"]) == 2 for t in items)


class TestEnvVarPath:
    def test_env_var_target_is_honoured(self, tmp_path):
        suite = tmp_path / "suite"
        shutil.copytree(SUITE, suite)
        out = tmp_path / "env_outcomes.json"
        env = _env() | {OUTCOME_ENV: str(out)}
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", PLUGIN, "."],
            cwd=str(suite),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 1
        assert out.exists() and json.loads(out.read_text(encoding="utf-8"))["tests"]

    def test_no_path_means_no_file(self, tmp_path):
        suite = tmp_path / "suite"
        shutil.copytree(SUITE, suite)
        _run_pytest(suite, ["-p", PLUGIN])
        assert list(suite.glob("*outcomes*.json")) == []