"""Regression coverage for retiring built-in industry demonstrations."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


BACKEND = Path(__file__).resolve().parents[2]


def test_component_seeds_exclude_retired_industry_models():
    tree = ast.parse((BACKEND / "migrations/seed_components.py").read_text())
    components = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "COMPONENTS" for target in node.targets)
    )
    names = {item["name"] for item in components}
    assert "炼钢温度预测" not in names
    assert "钢表面缺陷检测" not in names
    assert "轴承磨损预测" in names
    assert "工单文本分类" in names


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_DSN") or not shutil.which("psql"),
    reason="requires TEST_POSTGRES_DSN and psql",
)
def test_cleanup_is_scoped_idempotent_and_preserves_references():
    migration = (BACKEND / "migrations/040_withdraw_industry_demo_content.sql").read_text()
    setup = """
    BEGIN;
    CREATE TEMP TABLE datasets (
        id integer PRIMARY KEY, name text, organization_id integer, file_path text
    );
    CREATE TEMP TABLE data_assets (
        id integer PRIMARY KEY, name text, type text DEFAULT 'dataset',
        user_id integer, storage_info jsonb, source text,
        status text DEFAULT 'active', updated_at timestamptz
    );
    CREATE TEMP TABLE components (
        id integer PRIMARY KEY, name text, type text, description text,
        parameters jsonb, workspace_id integer
    );
    CREATE TEMP TABLE pricing_configs (component_id integer REFERENCES components);
    CREATE TEMP TABLE billing_records (component_id integer REFERENCES components);
    INSERT INTO datasets VALUES
        (1, 'UCI Steel Industry Energy Consumption', NULL,
         '/app/public_datasets/uci-steel-industry-energy-consumption/raw/data.csv'),
        (2, '生产过程机工艺数据(钢企Demo)', NULL, '/demo.csv'),
        (3, 'UCI Gas Turbine CO and NOx', NULL, '/gas.csv');
    INSERT INTO data_assets (id, name, storage_info, source) VALUES
        (1, 'UCI 钢铁行业能耗基准数据', '{"dataset_id":1}', 'UCI Machine Learning Repository'),
        (2, '生产过程机工艺能效验证数据', '{"dataset_id":2}', '示例组织脱敏/模拟示范数据'),
        (3, 'UCI 燃机排放与产能基准数据', '{"dataset_id":3}', 'UCI Machine Learning Repository'),
        (4, '自建数据资产', '{"dataset_id":1}', '用户上传'),
        (5, 'UCI 钢铁行业能耗基准数据', '{"dataset_id":1}', 'UCI Machine Learning Repository');
    UPDATE data_assets SET user_id = 42 WHERE id = 5;
    INSERT INTO components VALUES
        (1, '炼钢温度预测', 'time_series_forecast', '预测炼钢过程中温度变化趋势',
         '{"algorithm":"GRU","framework":"PyTorch","accuracy":"93%"}', NULL),
        (2, '钢表面缺陷检测', 'computer_vision', '检测钢材表面缺陷（裂纹、划痕等）',
         '{"algorithm":"ResNet50","framework":"PyTorch","accuracy":"97%","classes":["normal","crack","scratch","pit"]}', NULL);
    INSERT INTO components SELECT 3, name, type, description, parameters, 42 FROM components WHERE id = 1;
    INSERT INTO components SELECT 4, name, type, description, parameters, NULL FROM components WHERE id = 1;
    INSERT INTO components SELECT 5, name, type, description, parameters, NULL FROM components WHERE id = 1;
    INSERT INTO components SELECT 6, name, type, description, '{"algorithm":"custom"}', NULL FROM components WHERE id = 1;
    INSERT INTO pricing_configs VALUES (4);
    INSERT INTO billing_records VALUES (5);
    """
    checks = """
    SELECT json_build_object(
        'archived', (SELECT array_agg(id ORDER BY id) FROM data_assets WHERE status = 'archived'),
        'active', (SELECT array_agg(id ORDER BY id) FROM data_assets WHERE status = 'active'),
        'components', (SELECT array_agg(id ORDER BY id) FROM components),
        'datasets', (SELECT count(*) FROM datasets),
        'billing', (SELECT count(*) FROM billing_records),
        'pricing', (SELECT count(*) FROM pricing_configs)
    );
    """
    result = subprocess.run(
        ["psql", os.environ["TEST_POSTGRES_DSN"], "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
        input=setup + migration + checks + migration + checks + "ROLLBACK;",
        text=True, capture_output=True, check=True,
    )
    snapshots = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    expected = {
        "archived": [1, 2], "active": [3, 4, 5], "components": [3, 4, 5, 6],
        "datasets": 3, "billing": 1, "pricing": 1,
    }
    assert snapshots == [expected, expected]
