"""守卫：验证资产必须存在、校验和一致，且消费方走版本化路径。

背景（真实事故）：参考实现与平台探针存档曾放在 `artifacts/` 下，被合并门禁滤除后，
12 项验证测试静默退化为 FAIL/ERROR —— 门禁看起来"还在"，实际已不执行任何判定。
本文件把"资产在位"变成可执行的断言，防止同一失效模式复发。

运行：python3 -m pytest tests/test_verifier_assets_integrity.py -q
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(HERE, "data", "verifier_assets")
MANIFEST = os.path.join(ASSETS, "manifest.json")

# 消费验证资产的测试模块：必须能解析到版本化路径（而不是只认 artifacts/）
CONSUMERS = [
    "tests/test_hydrostatics_reference.py",
    "tests/test_propulsion_openwater_gate.py",
    "tests/test_maneuvering_reference.py",
    "tests/verif/test_damage_stability_verdict.py",
]


@pytest.fixture(scope="module")
def manifest():
    assert os.path.exists(MANIFEST), "验证资产清单缺失（tests/data/verifier_assets/manifest.json）"
    with open(MANIFEST) as f:
        return json.load(f)


def test_manifest_is_not_empty(manifest):
    assert len(manifest["files"]) >= 11


def test_every_asset_present_with_matching_checksum(manifest):
    bad = []
    for rel, meta in manifest["files"].items():
        path = os.path.join(ASSETS, rel)
        if not os.path.exists(path):
            bad.append((rel, "missing"))
            continue
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if digest != meta["sha256"]:
            bad.append((rel, "sha256"))
        elif os.path.getsize(path) != meta["bytes"]:
            bad.append((rel, "size"))
    assert not bad, f"验证资产缺失/被改动：{bad}（被合并门禁滤除？见 README）"


def test_assets_are_not_under_gated_artifacts_dir(manifest):
    """资产路径不得位于 artifacts/ 之下，否则下次合并又会被静默滤除。"""
    for rel in manifest["files"]:
        assert not rel.startswith("artifacts/")


def test_consumer_tests_use_versioned_path():
    missing = []
    for rel in CONSUMERS:
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        if "verifier_assets" not in src:
            missing.append(rel)
    assert not missing, f"这些测试仍只依赖 artifacts/ 路径：{missing}"


def test_missing_asset_fails_loudly(monkeypatch):
    """负对照：资产被移走时测试必须报错，而不是静默跳过或假通过。"""
    import test_hydrostatics_reference as H  # 同目录，pytest 已把 tests/ 加入 sys.path

    monkeypatch.setattr(H, "SNAP_CANDIDATES", [os.path.join(ASSETS, "__nope__")])
    with pytest.raises(BaseException) as ei:
        H._load_snapshot()
    assert ei.type.__name__ == "Failed", ei.type  # pytest.fail 而非 skip