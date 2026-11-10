"""P162-N10 机器判据：结构面清单 + 依赖方向门 + 棘轮 + 词级门复用。

判据（与战役 metadata 对齐，数字全部程序化取自工件/实时扫描，不手抄）：

C1 清单完备性  manifest ⊇ 磁盘全部 api/domain 模块，差异 0，每条字段非空，
               且模块数 >= policy.min_required_modules（战役口径 36）。
C2 依赖方向门  向核心层注入 ``from api.domain.<mod> import *`` → 必须红，
               并报出 file:line；存量依赖全部登记为带到期豁免。
C3 棘轮双向    domain/ 新增文件 → 红；删除已登记文件 → 待确认(3) → confirm-removal
               后基线 −1 且转绿；内容漂移 → 红 → bless 转绿。
C4 兼容性      复用 tools/audit/domain_leak_scan.py 的同一 denylist / 同一
               scan_paths / 同一判定（scanner 与 denylist 指纹冻结在工件里）。
C5 确定性与预算 同仓两次扫描结果摘要相同；单次结构扫描 ≤ 120 s。
C6 负控三件套  manifest 缺失 → 仅词级 + WITHHELD（不崩不假绿）；
               manifest 为空 → fail-closed 红；豁免无到期 → 红。
C7 向后兼容    不改变任何现有 API 声明面（路由普查指纹不变）。

Run: python3 -m pytest tests/test_domain_surface_manifest.py -q
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import pathlib
import shutil
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "audit" / "domain_surface_manifest.py"
ARTIFACTS = REPO_ROOT / "artifacts" / "m9079"
TODAY = "2026-11-10"  # 判据日：豁免/审批链按该日判定，保证结果确定


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # dataclass 解析注解需要模块已注册
    spec.loader.exec_module(module)
    return module


dsm = _load(TOOL_PATH, "domain_surface_manifest_tool")
seam = _load(REPO_ROOT / "backend" / "api" / "core" / "domain_provider.py", "domain_provider_seam")

SYNTH_POLICY = {
    "schema_version": "1.0",
    "purpose": "synthetic fixture policy（零领域语义：全部用合成模块名）",
    "surface_roots": ["backend/api/domain"],
    "layers": {"core": ["backend/api/core", "backend/services"], "route": ["backend/api"], "tooling": ["tools"]},
    "layer_exclude": ["backend/api/domain"],
    "provider_seam": {"module": "api.core.domain_provider", "callable": "provide_domain"},
    "ownership_rules": [
        {
            "names": ["__init__"],
            "prefix": ["tensor"],
            "owner_repo": "TensorPlatform",
            "target_layer": "backend/api/core",
            "rationale": "platform mechanism fixture",
        }
    ],
    "default_owner_repo": "example-domain-repo",
    "default_target_layer": "domains/example",
    "default_rationale": "synthetic fixture",
    "default_status": "DECLARED-exempt",
    "default_expiry": "2027-03-31",
    "default_reason": "fixture",
    "default_dependency_reason": "fixture",
    "default_approval_reason": "fixture",
    "approval_authority": "owner",
    "budget_seconds": 120,
    "min_required_modules": 2,
}


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_workspace(tmp_path: pathlib.Path, *, modules=("alpha_kernel", "beta_kernel")) -> pathlib.Path:
    """合成一座最小仓：域面 + 核心层 + 一处存量越界依赖。"""
    ws = tmp_path / "ws"
    _write(ws / "config" / "domain_surface_policy.json", json.dumps(SYNTH_POLICY, ensure_ascii=False, indent=2))
    _write(ws / "backend" / "api" / "__init__.py", "")
    _write(ws / "backend" / "api" / "domain" / "__init__.py", "")
    for name in modules:
        _write(ws / "backend" / "api" / "domain" / f"{name}.py", f"def helper():\n    return '{name}'\n")
    _write(ws / "backend" / "api" / "core" / "__init__.py", "")
    _write(ws / "backend" / "api" / "core" / "registry.py", "VALUE = 1\n")
    _write(ws / "backend" / "services" / "consumer.py", "from api.domain.alpha_kernel import helper\n")
    _write(ws / "tools" / "plain_tool.py", "VALUE = 2\n")
    return ws


def build_baseline(ws: pathlib.Path, ref: str = "T-1") -> None:
    code, info = dsm.apply_change(ws, kind="generate", by="owner", ref=ref, issued=TODAY, reason="fixture baseline")
    assert code == 0, info


def resign(ws: pathlib.Path, mutate, *, kind: str = "fixture", ref: str = "T-edit") -> None:
    """按当前磁盘内容重签清单（用于构造负控场景，模拟一次被批准的变更）。"""
    policy = dsm.load_policy(ws)
    manifest = dsm.load_manifest(ws)
    manifest = mutate(manifest) if mutate else manifest
    manifest["integrity"]["content_sha256"] = dsm.content_digest(manifest)
    approvals = dsm.load_approvals(ws) or {"schema_version": "1.0", "records": []}
    approvals["records"].append(
        {
            "id": f"{kind}-{manifest['integrity']['content_sha256'][:12]}",
            "kind": kind,
            "by": "owner",
            "issued": TODAY,
            "ref": ref,
            "reason": "fixture",
            "modules_after": len(manifest.get("modules", [])),
            "content_sha256_after": manifest["integrity"]["content_sha256"],
        }
    )
    _write(ws / dsm.MANIFEST_REL, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    _write(ws / dsm.APPROVALS_REL, json.dumps(approvals, ensure_ascii=False, indent=2) + "\n")
    assert policy is not None


# --------------------------------------------------------------------------- #
# C1 清单完备性
# --------------------------------------------------------------------------- #
def test_c1_manifest_covers_domain_surface_exactly():
    result = dsm.check(REPO_ROOT, today=TODAY)
    policy = dsm.load_policy(REPO_ROOT)
    on_disk = dsm.surface_files(REPO_ROOT, policy)
    baseline = result["surface"]["baseline"]

    assert result["surface"]["added"] == [], "manifest 漏登记磁盘上的域模块"
    assert result["surface"]["missing"] == [], "manifest 登记了磁盘上不存在的域模块"
    assert set(on_disk) == set(baseline), "清单与磁盘差异必须为 0"
    assert len(baseline) >= policy["min_required_modules"], (
        f"清单模块数 {len(baseline)} < 战役口径 {policy['min_required_modules']}"
    )
    # 每条含 owner_repo 与状态，且无空字段
    manifest = dsm.load_manifest(REPO_ROOT)
    for module in manifest["modules"]:
        for field in ("path", "sha256", "owner_repo", "target_layer", "status", "expiry", "migration_ref", "reason"):
            assert module.get(field), f"{module.get('path')} 字段 {field} 为空"
        assert module["status"] in ("migrated", "DECLARED-exempt")
        assert len(module["sha256"]) == 64
    assert result["verdict"] == "pass"
    assert result["exit_code"] == dsm.EXIT_PASS


# --------------------------------------------------------------------------- #
# C2 依赖方向门
# --------------------------------------------------------------------------- #
def test_c2_injected_core_to_domain_import_turns_red(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    assert dsm.check(ws, today=TODAY)["exit_code"] == dsm.EXIT_PASS

    injected = ws / "backend" / "api" / "core" / "sneaky.py"
    _write(injected, "from api.domain.alpha_kernel import *\n")
    result = dsm.check(ws, today=TODAY)

    assert result["exit_code"] == dsm.EXIT_FAIL
    assert result["verdict"] == "fail"
    assert "dependency_not_registered" in result["reasons"]
    violations = result["dependencies"]["unregistered"]
    assert {"path": "backend/api/core/sneaky.py", "line": 1, "module": "api.domain.alpha_kernel",
            "kind": "from"} in violations


def test_c2_dynamic_domain_import_also_flagged(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    _write(
        ws / "backend" / "services" / "loader.py",
        "from importlib import import_module\n\n\ndef load():\n    return import_module('api.domain.beta_kernel')\n",
    )
    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_FAIL
    assert any(v["path"] == "backend/services/loader.py" and v["line"] == 5
               for v in result["dependencies"]["unregistered"])


def test_c2_existing_dependencies_are_registered_with_expiry():
    census = json.loads((ARTIFACTS / "dependency_census.json").read_text(encoding="utf-8"))
    result = dsm.check(REPO_ROOT, today=TODAY)
    manifest = dsm.load_manifest(REPO_ROOT)

    assert result["dependencies"]["unregistered"] == [], "存量依赖必须全部登记"
    assert result["dependencies"]["observed"] <= census["sites"], "依赖棘轮只减不增"
    assert result["dependencies"]["registered"] <= census["pairs"], "豁免账只减不增"
    assert result["dependencies"]["expanded"] == [], "单点依赖数不得扩大"
    assert result["dependencies"]["observed"] == census["sites"]
    for record in manifest["dependency_exemptions"]:
        assert record["expiry"], f"{record['path']}:{record['module']} 豁免缺到期"
        assert record["allowed_sites"] >= 1
    assert result["dependencies"]["expired"] == []


# --------------------------------------------------------------------------- #
# C3 棘轮双向
# --------------------------------------------------------------------------- #
def test_c3_new_domain_file_turns_red(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    _write(ws / "backend" / "api" / "domain" / "gamma_kernel.py", "def helper():\n    return 'gamma'\n")

    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_FAIL
    assert "surface_growth" in result["reasons"]
    assert result["surface"]["added"] == ["backend/api/domain/gamma_kernel.py"]


def test_c3_deleting_registered_module_requires_confirmation_then_baseline_minus_one(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    before = dsm.load_manifest(ws)["modules"]
    assert len(before) == 3  # __init__ + alpha + beta

    (ws / "backend" / "api" / "domain" / "beta_kernel.py").unlink()
    pending = dsm.check(ws, today=TODAY)
    assert pending["exit_code"] == dsm.EXIT_REMOVAL_PENDING
    assert pending["verdict"] == "removal_pending"
    assert pending["surface"]["missing"] == ["backend/api/domain/beta_kernel.py"]
    # 未确认前不得把删除当绿
    assert pending["exit_code"] != dsm.EXIT_PASS

    code, info = dsm.apply_change(ws, kind="confirm-removal", by="owner", ref="T-2", issued=TODAY)
    assert code == dsm.EXIT_PASS, info
    assert info["modules_after"] == len(before) - 1, "确认删除后基线必须 −1"
    assert info["detail"]["removed"] == ["backend/api/domain/beta_kernel.py"]

    after = dsm.check(ws, today=TODAY)
    assert after["exit_code"] == dsm.EXIT_PASS
    assert after["manifest"]["module_count"] == len(before) - 1


def test_c3_removal_confirmation_refuses_mixed_changes(tmp_path):
    """不能借「确认删除」的名义夹带新增（防删功能换绿 + 偷加域面）。"""
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    (ws / "backend" / "api" / "domain" / "beta_kernel.py").unlink()
    _write(ws / "backend" / "api" / "domain" / "delta_kernel.py", "def helper():\n    return 'delta'\n")

    code, info = dsm.apply_change(ws, kind="confirm-removal", by="owner", ref="T-3", issued=TODAY)
    assert code == dsm.EXIT_FAIL
    assert info["error"] == "removal_only"


def test_c3_content_drift_red_then_bless(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    _write(ws / "backend" / "api" / "domain" / "alpha_kernel.py", "def helper():\n    return 'changed'\n")

    drift = dsm.check(ws, today=TODAY)
    assert drift["exit_code"] == dsm.EXIT_FAIL
    assert drift["surface"]["content_drift"] == ["backend/api/domain/alpha_kernel.py"]

    code, info = dsm.apply_change(ws, kind="bless", by="owner", ref="T-4", issued=TODAY)
    assert code == dsm.EXIT_PASS, info
    assert dsm.check(ws, today=TODAY)["exit_code"] == dsm.EXIT_PASS


def test_approval_authority_enforced(tmp_path):
    ws = build_workspace(tmp_path)
    code, info = dsm.apply_change(ws, kind="generate", by="some-agent", ref="T-5", issued=TODAY)
    assert code == dsm.EXIT_FAIL
    assert info["error"] == "approval_authority_invalid"


# --------------------------------------------------------------------------- #
# C4 词级门复用（同一 denylist / 同一路径 / 同一判定）
# --------------------------------------------------------------------------- #
def test_c4_word_scan_contract_frozen():
    contract = json.loads((ARTIFACTS / "word_scan_contract.json").read_text(encoding="utf-8"))
    scanner = REPO_ROOT / "tools" / "audit" / "domain_leak_scan.py"
    denylist = REPO_ROOT / "config" / "domain_word_denylist.json"

    assert hashlib.sha256(scanner.read_bytes()).hexdigest() == contract["scanner_sha256"], (
        "domain_leak_scan.py 被改动——C4 要求复用同一判定，不得改 #608 语义"
    )
    assert hashlib.sha256(denylist.read_bytes()).hexdigest() == contract["denylist_sha256"], (
        "域词表被改动——C4 要求同一 denylist 输入"
    )

    word = dsm.run_word_scan(REPO_ROOT)
    assert word["status"] == "ok"
    assert word["denylist"] == contract["denylist"]
    assert word["files_scanned"] == contract["files_scanned"]
    assert word["word_count"] == contract["word_count"]
    assert word["hit_count"] == contract["hit_count"] == 0

    # 结构门把词级结果作为补充证据原样携带（不改变词级判定）
    result = dsm.check(REPO_ROOT, today=TODAY)
    assert result["word_scan"]["digest"] == word["digest"]
    assert result["word_scan"]["ok"] is True


# --------------------------------------------------------------------------- #
# C5 确定性与预算
# --------------------------------------------------------------------------- #
def test_c5_two_runs_identical_and_within_budget():
    t0 = time.perf_counter()
    first = dsm.check(REPO_ROOT, today=TODAY)
    t1 = time.perf_counter()
    second = dsm.check(REPO_ROOT, today=TODAY)
    t2 = time.perf_counter()

    assert first["digest"] == second["digest"], "同仓两次扫描摘要必须一致（确定性）"
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    wall = max(t1 - t0, t2 - t1)
    assert wall <= first["budget_limit_seconds"], f"结构扫描耗时 {wall:.1f}s 超过预算 {first['budget_limit_seconds']}s"


# --------------------------------------------------------------------------- #
# C6 负控三件套
# --------------------------------------------------------------------------- #
def test_c6_missing_manifest_degrades_to_word_only_withheld(tmp_path):
    ws = build_workspace(tmp_path)  # 不建基线
    result = dsm.check(ws, today=TODAY)

    assert result["exit_code"] == dsm.EXIT_WITHHELD
    assert result["verdict"] == "withheld"
    assert "manifest_missing" in result["reasons"]
    assert result["word_scan"]["status"] in ("ok", "unavailable")
    assert result["exit_code"] != dsm.EXIT_PASS, "清单缺失不得假绿"
    assert result["exit_code"] != dsm.EXIT_FAIL, "清单缺失不得被当作结构判红（它是缺证，不是证据为负）"


def test_c6_empty_manifest_fails_closed(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    resign(ws, lambda manifest: {**manifest, "modules": []})
    # 空清单 + 空模块账：content_digest 重签后仍需判红（fail-closed）
    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_FAIL
    assert "manifest_empty" in result["reasons"]
    assert result["verdict"] == "fail"


def test_c6_exemption_without_expiry_turns_red(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)

    def strip_expiry(manifest):
        for record in manifest["dependency_exemptions"]:
            record.pop("expiry", None)
        for module in manifest["modules"]:
            module.pop("expiry", None)
        return manifest

    resign(ws, strip_expiry)
    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_FAIL
    assert any(r.startswith("exemption_missing_expiry") for r in result["reasons"])
    assert result["exemptions"]["missing_expiry"], "必须点名缺到期的豁免"


def test_anti_bypass_hand_edited_manifest_turns_red(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    manifest = dsm.load_manifest(ws)
    manifest["modules"].append(
        {
            "path": "backend/api/domain/handmade.py",
            "sha256": "0" * 64,
            "owner_repo": "example-domain-repo",
            "target_layer": "domains/example",
            "status": "DECLARED-exempt",
            "expiry": "2027-03-31",
            "migration_ref": "hand",
            "reason": "hand edit",
        }
    )
    _write(ws / dsm.MANIFEST_REL, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_FAIL
    assert "manifest_hand_edited" in result["reasons"], "手改清单必须被完整性校验抓到"


# --------------------------------------------------------------------------- #
# provider 接缝：核心层唯一允许的域能力解析路径
# --------------------------------------------------------------------------- #
def test_provider_seam_is_the_sanctioned_path(tmp_path):
    ws = build_workspace(tmp_path)
    build_baseline(ws)
    _write(
        ws / "backend" / "api" / "core" / "caller.py",
        "from api.core.domain_provider import provide_domain\n\n\ndef call():\n    return provide_domain('alpha')\n",
    )
    result = dsm.check(ws, today=TODAY)
    assert result["exit_code"] == dsm.EXIT_PASS, result["reasons"]
    assert result["dependencies"]["provider_seam_files"] == ["backend/api/core/caller.py"]


def test_provider_seam_resolution_and_fail_closed():
    seam.reset_registry()
    module_path = WS_MODULE = "sys:version_info"
    seam.register_domain("example", module_path, owner_repo="example-domain-repo")
    assert seam.provided_names() == ["example"]
    assert seam.provide_domain("example") is sys.version_info
    assert seam.describe("example")["owner_repo"] == "example-domain-repo"

    with pytest.raises(seam.DomainNotProvided):
        seam.provide_domain("undeclared")
    with pytest.raises(seam.DomainNotProvided):
        seam.register_domain("bad", "no-separator", owner_repo="x")
    with pytest.raises(seam.DomainNotProvided):
        seam.register_domain("no-owner", module_path, owner_repo="")
    with pytest.raises(seam.DomainNotProvided):
        seam.register_domain("missing-attr", "sys:definitely_not_there", owner_repo="x")
        seam.provide_domain("missing-attr")
    seam.reset_registry()
    assert WS_MODULE  # 保持变量引用，避免误读为死代码


# --------------------------------------------------------------------------- #
# C7 向后兼容：现有 API 声明面不变
# --------------------------------------------------------------------------- #
def test_c7_api_route_surface_unchanged():
    frozen = json.loads((ARTIFACTS / "api_route_census.json").read_text(encoding="utf-8"))
    census = dsm.route_census(REPO_ROOT)
    assert census["files"] == frozen["files"], "backend/api 路由文件数变化 → 可能改变了现有 API 行为"
    assert census["routes"] == frozen["routes"], "路由声明数变化 → 可能改变了现有 API 行为"
    assert census["sha256"] == frozen["sha256"], "路由 (文件,方法,路径) 清单指纹变化"


def test_c7_new_core_package_is_additive_only():
    """本单只新增 backend/api/core/*，未改动任何既有后端模块（静态证据）。"""
    for rel in ("backend/api/core/__init__.py", "backend/api/core/domain_provider.py"):
        assert (REPO_ROOT / rel).is_file()
    tree = ast.parse((REPO_ROOT / "backend/api/core/domain_provider.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported == ["__future__", "importlib", "dataclasses", "typing"], (
        f"core 接缝只依赖标准库，实际 {imported}"
    )