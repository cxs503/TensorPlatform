"""sealed_evidence.sealer —— 从任务 result/报告抽取制品引用 → 路径归一化 → sha256 → 写契约（幂等）。

用法：
    from tools.sealed_evidence.sealer import seal_evidence
    contract = seal_evidence(mission_id="m10144", workdir="/path/to/work", ...)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from .schema import build_sealed_evidence, validate_sealed_evidence, _sha256_file


# 从报告/结果中抽取 artifact 路径的模式
_ARTIFACT_PATH_RE = re.compile(
    r'(?:artifacts|outputs|results|figures|data)/[^\s"\'<>]+'
)
_JSON_PATH_RE = re.compile(r'"(?:path|file|output|artifact)"\s*:\s*"([^"]+)"')


def _extract_artifacts_from_report(report_path: str) -> list[dict[str, Any]]:
    """从报告 JSON 中抽取 artifact 引用。

    扫描报告中的路径字段，返回 [{path, sha256, bytes}] 列表。
    """
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return artifacts

    def _walk(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                child_prefix = f"{prefix}.{key}" if prefix else key
                if isinstance(val, str):
                    # 检查是否是 artifact 路径
                    if _ARTIFACT_PATH_RE.search(val) or key in ("path", "file", "output", "artifact", "png"):
                        norm = os.path.normpath(val).lstrip("./")
                        if norm not in seen_paths:
                            seen_paths.add(norm)
                            full_path = os.path.join(os.path.dirname(report_path), norm) if not os.path.isabs(norm) else norm
                            if os.path.isfile(full_path):
                                artifacts.append({
                                    "path": norm,
                                    "sha256": _sha256_file(full_path),
                                    "bytes": os.path.getsize(full_path),
                                })
                            else:
                                artifacts.append({
                                    "path": norm,
                                    "sha256": "",
                                    "bytes": 0,
                                })
                elif isinstance(val, (dict, list)):
                    _walk(val, child_prefix)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{prefix}[{i}]")

    _walk(report)
    return artifacts


def _extract_artifacts_from_workdir(workdir: str, patterns: list[str] | None = None) -> list[dict[str, Any]]:
    """从工作目录扫描 artifact 文件。

    Args:
        workdir: 工作目录路径
        patterns: 文件模式列表（默认扫描 artifacts/, outputs/, results/, figures/ 下的文件）

    Returns:
        [{path, sha256, bytes}] 列表
    """
    if patterns is None:
        patterns = ["artifacts/**", "outputs/**", "results/**", "figures/**"]

    artifacts: list[dict[str, Any]] = []
    import glob

    for pattern in patterns:
        full_pattern = os.path.join(workdir, pattern)
        for filepath in glob.glob(full_pattern, recursive=True):
            if os.path.isfile(filepath):
                rel = os.path.relpath(filepath, workdir)
                artifacts.append({
                    "path": rel,
                    "sha256": _sha256_file(filepath),
                    "bytes": os.path.getsize(filepath),
                })

    return artifacts


def seal_evidence(
    mission_id: str,
    workdir: str,
    criteria: list[dict[str, Any]] | None = None,
    replay_cmd: str | None = None,
    replay_cwd: str = ".",
    replay_timeout_s: int = 300,
    report_path: str | None = None,
    output_path: str | None = None,
    sealed_at: str | None = None,
) -> dict[str, Any]:
    """封存任务证据。

    流程：
    1. 从报告/工作目录抽取 artifact 引用
    2. 路径归一化
    3. 计算 sha256
    4. 构建契约（幂等）
    5. 可选写入文件

    Args:
        mission_id: 任务标识
        workdir: 工作目录
        criteria: 判据列表（可选，默认从报告抽取）
        replay_cmd: 重放命令（可选，默认 "pytest tests/"）
        replay_cwd: 重放工作目录
        replay_timeout_s: 重放超时
        report_path: 报告文件路径（可选）
        output_path: 输出契约文件路径（可选）
        sealed_at: 封存时间戳（可选）

    Returns:
        sealed_evidence 契约字典
    """
    # 抽取 artifacts
    artifacts: list[dict[str, Any]] = []

    if report_path and os.path.isfile(report_path):
        artifacts.extend(_extract_artifacts_from_report(report_path))

    # 扫描工作目录
    dir_artifacts = _extract_artifacts_from_workdir(workdir)
    # 合并（去重）
    seen = {a["path"] for a in artifacts}
    for a in dir_artifacts:
        if a["path"] not in seen:
            artifacts.append(a)
            seen.add(a["path"])

    # 构建判据
    if criteria is None:
        # 默认判据：所有 artifacts 归入一个判据
        criteria = [{
            "id": "default",
            "text": f"All artifacts for mission {mission_id}",
            "artifacts": artifacts,
        }]
    else:
        # 合并提供的判据中的 artifacts
        for c in criteria:
            existing_paths = {a["path"] for a in c.get("artifacts", [])}
            for a in artifacts:
                if a["path"] not in existing_paths:
                    c.setdefault("artifacts", []).append(a)

    # 默认重放命令
    if replay_cmd is None:
        # 检查是否有 pytest 测试
        test_dir = os.path.join(workdir, "tests")
        if os.path.isdir(test_dir):
            replay_cmd = "pytest tests/ -x --timeout=60"
        else:
            replay_cmd = "echo 'no tests found'"

    # 构建契约
    contract = build_sealed_evidence(
        mission_id=mission_id,
        criteria=criteria,
        replay_cmd=replay_cmd,
        replay_cwd=replay_cwd,
        replay_timeout_s=replay_timeout_s,
        sealed_at=sealed_at,
    )

    # 校验
    errs = validate_sealed_evidence(contract)
    if errs:
        raise ValueError(f"Invalid sealed evidence contract: {errs}")

    # 写入文件（幂等：如果内容相同则不写）
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tmp_path = output_path + ".tmp"
        content = json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if os.path.isfile(output_path):
            with open(output_path, "r") as f:
                existing = f.read()
            if existing == content:
                pass  # 幂等：内容相同，不写
            else:
                with open(tmp_path, "w") as f:
                    f.write(content)
                os.replace(tmp_path, output_path)
        else:
            with open(tmp_path, "w") as f:
                f.write(content)
            os.replace(tmp_path, output_path)

    return contract
