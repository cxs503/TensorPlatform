"""KPI 注册表测试（m9358）。

验证：
1. 注册表 JSON 格式正确
2. 每条 KPI 必须有 recompute_sql 或 recompute_scan 或 not_recomputable=true
3. not_recomputable=true 的 KPI 必须有 not_recomputable_reason
4. 所有 ratio 型 KPI 必须有 denominator_spec
5. 所有 KPI 必须有 sensitivity 字段
"""
import json
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = ROOT / "tools" / "metrics" / "kpi_registry.json"


@pytest.fixture
def registry():
    """加载 KPI 注册表"""
    with open(REGISTRY_PATH) as fp:
        return json.load(fp)


def test_registry_format(registry):
    """测试注册表格式正确"""
    assert "kpis" in registry
    assert "gaming_vectors" in registry
    assert isinstance(registry["kpis"], dict)
    assert len(registry["kpis"]) > 0


def test_each_kpi_has_recompute_or_fail_closed(registry):
    """测试每条 KPI 必须有 recompute_sql 或 recompute_scan 或 not_recomputable=true"""
    for kpi_name, kpi_spec in registry["kpis"].items():
        has_sql = kpi_spec.get("recompute_sql") is not None
        has_scan = kpi_spec.get("recompute_scan") is not None
        is_not_recomputable = kpi_spec.get("not_recomputable", False)
        
        assert has_sql or has_scan or is_not_recomputable, (
            f"KPI '{kpi_name}' 必须有 recompute_sql 或 recompute_scan 或 not_recomputable=true"
        )


def test_not_recomputable_has_reason(registry):
    """测试 not_recomputable=true 的 KPI 必须有 not_recomputable_reason"""
    for kpi_name, kpi_spec in registry["kpis"].items():
        if kpi_spec.get("not_recomputable", False):
            assert "not_recomputable_reason" in kpi_spec, (
                f"KPI '{kpi_name}' 标记为 not_recomputable 但缺少 not_recomputable_reason"
            )
            assert len(kpi_spec["not_recomputable_reason"]) > 10, (
                f"KPI '{kpi_name}' 的 not_recomputable_reason 太短"
            )


def test_ratio_kpis_have_denominator_spec(registry):
    """测试所有 ratio 型 KPI 必须有 denominator_spec"""
    for kpi_name, kpi_spec in registry["kpis"].items():
        if kpi_spec.get("unit") == "ratio":
            assert "denominator_spec" in kpi_spec, (
                f"ratio 型 KPI '{kpi_name}' 必须有 denominator_spec"
            )
            denom_spec = kpi_spec["denominator_spec"]
            assert "definition" in denom_spec
            assert "gaming_risk" in denom_spec


def test_all_kpis_have_sensitivity(registry):
    """测试所有 KPI 必须有 sensitivity 字段"""
    for kpi_name, kpi_spec in registry["kpis"].items():
        assert "sensitivity" in kpi_spec, (
            f"KPI '{kpi_name}' 必须有 sensitivity 字段"
        )


def test_gaming_vectors_defined(registry):
    """测试 5 类操纵向量都已定义"""
    gv = registry["gaming_vectors"]
    expected_vectors = ["M1_finish_empty", "M2_duplicate_artifact", "M3_copy_samples",
                        "M4_modify_ledger", "M5_denominator_split"]
    for vec in expected_vectors:
        assert vec in gv, f"操纵向量 '{vec}' 未定义"
        assert "description" in gv[vec]
        assert "target_kpis" in gv[vec]
        assert "detection" in gv[vec]


def test_prop7619_fix_documented(registry):
    """测试 prop-7619 修复已记录"""
    assert "_prop7619_fix" in registry
    assert "0.6667" in registry["_prop7619_fix"]
    assert "静默改写" in registry["_prop7619_fix"]
