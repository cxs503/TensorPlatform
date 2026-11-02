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
import re

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
    "tests/verif/test_seakeeping_section_analytic.py",   # m1848：切片参考核
    "tests/test_vibration_analytic_bench.py",            # m1848：振动参考核
]


@pytest.fixture(scope="module")
def manifest():
    assert os.path.exists(MANIFEST), "验证资产清单缺失（tests/data/verifier_assets/manifest.json）"
    with open(MANIFEST) as f:
        return json.load(f)


def test_manifest_is_not_empty(manifest):
    assert len(manifest["files"]) >= 12
    assert len(manifest.get("derived", {})) >= 3


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


def test_derived_figures_verified_when_present_but_absence_is_legal(manifest):
    """派生图（.png）受推送黑名单永久滤除：在场则必须校验和一致，缺失不算失败。

    判据账本（证据 .json / 参考核 .py）仍是硬门；图只是渲染产物，可由 regenerator 重建。
    """
    bad = []
    for rel, meta in manifest.get("derived", {}).items():
        path = os.path.join(ASSETS, rel)
        if not os.path.exists(path):
            continue  # 合法：跨推送后必然缺席（tp_pusher v5.4 排除 *.png）
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if digest != meta["sha256"] or os.path.getsize(path) != meta["bytes"]:
            bad.append(rel)
    assert not bad, f"在场的派生图与清单不符：{bad}"


def test_derived_entries_declare_regenerator(manifest):
    """每个派生图必须声明重建来源，否则缺失即不可复现（证据链断）。"""
    bad = [rel for rel, meta in manifest.get("derived", {}).items()
           if not str(meta.get("regenerator", "")).strip()]
    assert not bad, f"派生图缺少 regenerator 出处：{bad}"


#: tp_pusher v5.4 推送黑名单（_EXCLUDE / _EXCLUDE2）后缀与路径前缀
UNPUSHABLE_SUFFIX = (".db", ".sqlite3", ".sqlite", ".png", ".log")
UNPUSHABLE_PREFIX = ("artifacts/", "artifact/", "runs/", "results/", "results-")


def test_required_assets_are_pushable(manifest):
    """硬门资产必须落在推送白区：否则分支推送后 main 上的守护必然转红（真实事故复发）。

    依据：/root/tp-agent/tp_pusher.py v5.4 `_EXCLUDE2 = \\.(db|sqlite3?|png|log)$`。
    """
    bad = [rel for rel in manifest["files"]
           if rel.endswith(UNPUSHABLE_SUFFIX) or rel.startswith(UNPUSHABLE_PREFIX)]
    assert not bad, f"这些硬门资产会被推送黑名单滤除，应移至 derived 或改存 .json/.py：{bad}"


def test_consumers_resolve_versioned_path_first(manifest):
    """消费方必须把版本化目录放在候选列表首位（artifacts/ 只作兜底）。"""
    bad = []
    for rel in CONSUMERS:
        path = os.path.join(ROOT, rel)
        assert os.path.exists(path), f"登记的消费方不存在：{rel}"
        src = open(path, encoding="utf-8").read()
        # 只看代码：文档字符串与注释里提到 artifacts/ 不构成依赖
        src = re.sub(r'"""[\s\S]*?"""', "", src)
        src = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        i_ver = src.find("verifier_assets")
        i_art = src.find("artifacts")
        if i_ver < 0:
            bad.append((rel, "no-versioned"))
        elif i_art >= 0 and i_art < i_ver:
            bad.append((rel, "artifacts-first"))
    assert not bad, f"版本化路径未优先：{bad}"
