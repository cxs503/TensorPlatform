"""repro_audit.probe_runner —— 轻量级探针执行器（零领域语义）。

探针（probe）是比完整审计更轻量的检查：不跑测试，只做结构性验证。
用于快速筛选/预检，成本远低于完整复证。

探针类型：
  * **artifact_existence**：节点声明的工件是否在仓里存在。
  * **criteria_parseable**：判据文本能否被 parse_tolerance 解析。
  * **test_file_syntax**：测试文件能否被 Python 解析（ast.parse）。
  * **json_wellformed**：JSON 工件是否格式正确。
  * **sha256_stable**：工件 sha256 是否与声明一致。

设计约束：
  * **只读**：不写被审计仓。
  * **幂等**：同输入同输出。
  * **快速**：单探针 <1s（不跑 subprocess）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from typing import Any

from . import canonical, core


# ─────────────────────────── 探针定义 ───────────────────────────

class ProbeResult:
    """单个探针结果。"""
    __slots__ = ("probe_type", "ok", "detail", "mission_id")

    def __init__(self, probe_type: str, ok: bool, detail: str = "", mission_id: int | None = None):
        self.probe_type = probe_type
        self.ok = ok
        self.detail = detail
        self.mission_id = mission_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_type": self.probe_type,
            "ok": self.ok,
            "detail": self.detail,
            "mission_id": self.mission_id,
        }


def probe_artifact_existence(node: dict[str, Any], workdir: str) -> ProbeResult:
    """检查节点声明的工件是否在仓里存在。"""
    mid = canonical.normalize_mission_id(node.get("mission_id"))
    meta = node.get("metadata") or {}
    artifacts = meta.get("artifacts") or []
    if not artifacts:
        return ProbeResult("artifact_existence", True, "no_artifacts_declared", mid)

    missing = []
    for art in artifacts:
        if not isinstance(art, str):
            continue
        path = os.path.join(workdir, art)
        if not os.path.exists(path):
            missing.append(art)

    ok = len(missing) == 0
    detail = f"missing={len(missing)}/{len(artifacts)}" if missing else "all_present"
    return ProbeResult("artifact_existence", ok, detail, mid)


def probe_criteria_parseable(node: dict[str, Any]) -> ProbeResult:
    """检查判据文本能否被 parse_tolerance 解析。"""
    mid = canonical.normalize_mission_id(node.get("mission_id"))
    meta = node.get("metadata") or {}
    criteria = meta.get("acceptance_criteria") or []
    if not criteria:
        return ProbeResult("criteria_parseable", True, "no_criteria", mid)

    unparseable = []
    for c in criteria:
        if not isinstance(c, str):
            unparseable.append(str(c))
            continue
        tol = core.parse_tolerance(c)
        if tol == core.OBS_REL_TOL and not re.search(r"\d", c):
            # 没有数值且回退到默认 → 可能无法解析
            unparseable.append(c)

    ok = len(unparseable) == 0
    detail = f"unparseable={len(unparseable)}/{len(criteria)}" if unparseable else "all_parseable"
    return ProbeResult("criteria_parseable", ok, detail, mid)


def probe_test_file_syntax(workdir: str, test_files: list[str]) -> ProbeResult:
    """检查测试文件能否被 Python ast.parse 解析。"""
    if not test_files:
        return ProbeResult("test_file_syntax", True, "no_test_files", None)

    errors = []
    for rel in test_files[:20]:  # 限制检查数量
        path = os.path.join(workdir, rel)
        if not os.path.exists(path):
            errors.append(f"{rel}:not_found")
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                source = fh.read(100_000)
            ast.parse(source)
        except SyntaxError as exc:
            errors.append(f"{rel}:{exc.msg}")

    ok = len(errors) == 0
    detail = f"errors={len(errors)}/{len(test_files)}" if errors else "all_valid"
    return ProbeResult("test_file_syntax", ok, detail, None)


def probe_json_wellformed(workdir: str, json_files: list[str]) -> ProbeResult:
    """检查 JSON 工件是否格式正确。"""
    if not json_files:
        return ProbeResult("json_wellformed", True, "no_json_files", None)

    errors = []
    for rel in json_files[:20]:
        path = os.path.join(workdir, rel)
        if not os.path.exists(path):
            errors.append(f"{rel}:not_found")
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{rel}:{type(exc).__name__}")

    ok = len(errors) == 0
    detail = f"errors={len(errors)}/{len(json_files)}" if errors else "all_valid"
    return ProbeResult("json_wellformed", ok, detail, None)


def probe_sha256_stable(workdir: str, artifacts: list[dict[str, Any]]) -> ProbeResult:
    """检查工件 sha256 是否与声明一致。"""
    if not artifacts:
        return ProbeResult("sha256_stable", True, "no_artifacts", None)

    mismatches = []
    for art in artifacts[:20]:
        rel = art.get("path", "")
        expected = art.get("sha256", "")
        if not expected:
            continue
        path = os.path.join(workdir, rel)
        if not os.path.exists(path):
            mismatches.append(f"{rel}:not_found")
            continue
        actual = core.sha256_file(path)
        if actual != expected:
            mismatches.append(f"{rel}:mismatch")

    ok = len(mismatches) == 0
    detail = f"mismatches={len(mismatches)}/{len(artifacts)}" if mismatches else "all_stable"
    return ProbeResult("sha256_stable", ok, detail, None)


# ─────────────────────────── 探针编排 ───────────────────────────

ALL_PROBES = [
    "artifact_existence",
    "criteria_parseable",
    "test_file_syntax",
    "json_wellformed",
    "sha256_stable",
]


def run_probe(probe_type: str, node: dict[str, Any], workdir: str,
              committed_files: list[str] | None = None,
              anchored_artifacts: list[dict[str, Any]] | None = None) -> ProbeResult:
    """执行单个探针。"""
    if committed_files is None:
        try:
            committed_files = core.committed_files(workdir)
        except Exception:
            committed_files = []

    test_files = [f for f in committed_files if f.endswith(".py") and
                  re.search(r"(^|/)tests?/|(^|/)test_", f)]
    json_files = [f for f in committed_files if f.endswith(".json")]

    if probe_type == "artifact_existence":
        return probe_artifact_existence(node, workdir)
    elif probe_type == "criteria_parseable":
        return probe_criteria_parseable(node)
    elif probe_type == "test_file_syntax":
        return probe_test_file_syntax(workdir, test_files)
    elif probe_type == "json_wellformed":
        return probe_json_wellformed(workdir, json_files)
    elif probe_type == "sha256_stable":
        return probe_sha256_stable(workdir, anchored_artifacts or [])
    else:
        return ProbeResult(probe_type, False, f"unknown_probe_type: {probe_type}",
                           canonical.normalize_mission_id(node.get("mission_id")))


def run_all_probes(node: dict[str, Any], workdir: str,
                   committed_files: list[str] | None = None,
                   anchored_artifacts: list[dict[str, Any]] | None = None
                   ) -> list[ProbeResult]:
    """执行所有探针，返回结果列表。"""
    results = []
    for probe_type in ALL_PROBES:
        result = run_probe(probe_type, node, workdir, committed_files, anchored_artifacts)
        results.append(result)
    return results


def summarize_probes(results: list[ProbeResult]) -> dict[str, Any]:
    """汇总探针结果：通过率、失败列表。"""
    total = len(results)
    passed = sum(1 for r in results if r.ok)
    failed = [r.to_dict() for r in results if not r.ok]

    return {
        "total": total,
        "passed": passed,
        "failed_count": len(failed),
        "pass_rate": (passed / total) if total > 0 else None,
        "failed_probes": failed,
    }
