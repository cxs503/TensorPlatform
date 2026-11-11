"""巡检主体测试：正控 0 误报、三种符号迁移全部判死、三态互斥且穷尽。"""
from __future__ import annotations

from pathlib import Path

from verification_vitality.collector import CollectResult
from verification_vitality.patrol import build_mapping, discover_units, patrol, patrol_summary


def test_healthy_repo_has_zero_dead_and_no_false_positive(healthy_repo):
    report = patrol(healthy_repo, registry_path=healthy_repo / "config/capabilities.json",
                    timeout=90)
    s = report["summary"]
    assert report["collect"]["exit_code"] == 0
    assert s["dead"] == 0, f"健康仓误报 dead: {patrol_summary(report)['dead_units']}"
    assert s["alive"] == 2
    assert s["unmapped"] == 1
    assert (s["alive"] + s["dead"] + s["unmapped"]) == s["n_units"] == 3
    assert s["caps_dead"] == 0 and s["caps_alive"] == 2
    by_unit = {x["unit"]: x for x in report["surfaces"]}
    assert by_unit["tests/test_base.py"]["capability_ids"] == ["synthetic.base"]
    assert by_unit["tests/test_unregistered.py"]["registered"] is False


def test_symbol_migration_is_dead(migrated_repo):
    repo, name = migrated_repo
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    s = patrol_summary(report)
    assert s["dead"] == 1, f"{name} 未被检出为 dead"
    assert s["dead_units"] == ["tests/test_case.py"]
    assert s["dead_capabilities"] == ["synthetic.base"]
    surf = report["surfaces"][0]
    assert surf["status"] == "dead"
    assert any(r.startswith("static:") for r in surf["dead_reasons"])
    assert report["collect"]["exit_code"] != 0


def test_three_migration_kinds_are_all_detected(migrated_repo):
    """负控：3 种符号迁移检出率必须 3/3（每参数一个仓库，逐个断言）。"""
    repo, name = migrated_repo
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    static_kinds = {f["kind"] for s in report["surfaces"] for f in s["evidence"]["static"]}
    assert static_kinds & {"module_missing", "symbol_missing"}, name


def test_static_only_patrol_without_running_pytest(healthy_repo):
    injected = CollectResult(exit_code=0)
    report = patrol(healthy_repo, registry_path=healthy_repo / "config/capabilities.json",
                    collect_result=injected)
    assert report["summary"]["dead"] == 0
    assert report["collect"]["collected_total"] == 0  # 注入的空收集结果，不假装跑了


def test_collect_error_alone_makes_unit_dead(repo_factory):
    """静态检查通过、但运行期收集失败（conftest/数据缺失）也要判死。"""
    repo = repo_factory({
        "pkg/__init__.py": "",
        "tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef needed():\n    return 1\n",
        "tests/test_a.py": "def test_a(needed):\n    assert needed == 1\n",
        "tests/test_b.py": "DATA = open('data/absent_cases.csv').read()\n\n\ndef test_b():\n    assert DATA\n",
    }, {"capabilities": [{"capability_id": "a.cap", "test_patterns": ["tests/test_b.py"]},
                         {"capability_id": "b.cap", "test_patterns": ["tests/test_a.py"]}]})
    report = patrol(repo, registry_path=repo / "config/capabilities.json", timeout=90)
    by_unit = {x["unit"]: x for x in report["surfaces"]}
    assert by_unit["tests/test_a.py"]["status"] == "alive"
    assert by_unit["tests/test_b.py"]["status"] == "dead"
    assert by_unit["tests/test_b.py"]["evidence"]["collect"]["error_kind"] == "data_file_missing"


def test_registry_patterns_drive_mapping_not_hardcoded_names():
    units = ["tests/test_alpha.py", "tests/test_beta_x.py", "tests/other.py"]
    registry = [{"capability_id": "c1", "test_patterns": ["test_alpha"]},
                {"capability_id": "c2", "test_patterns": ["test_beta"]}]
    mapping = build_mapping(units, registry)
    assert mapping["tests/test_alpha.py"] == ["c1"]
    assert mapping["tests/test_beta_x.py"] == ["c2"]
    assert mapping["tests/other.py"] == []


def test_explicit_mapping_overrides_registry():
    units = ["tests/test_x.py"]
    registry = [{"capability_id": "registry.cap", "test_patterns": ["test_x"]}]
    mapping = build_mapping(units, registry, explicit={"explicit.cap": [r"tests/test_x\.py"]})
    assert mapping["tests/test_x.py"] == ["explicit.cap"]


def test_discover_units_skips_init_and_conftest(healthy_repo):
    units = discover_units(healthy_repo, ("tests",))
    assert "tests/test_base.py" in units
    assert not any(u.endswith(("__init__.py", "conftest.py")) for u in units)


def test_missing_registry_degrades_to_all_unmapped(healthy_repo):
    report = patrol(healthy_repo, registry_path=healthy_repo / "config/absent.json", timeout=90)
    assert report["summary"]["alive"] == 0
    assert report["summary"]["unmapped"] == 3