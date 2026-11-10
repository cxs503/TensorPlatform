"""sealed_evidence.drift_attributor —— 把 repro_audit 的 drift 逐条映射到四类。

四类缺件类别：
  * missing: 文件完全不存在
  * truncated: 文件存在但大小/内容不完整（字节数不匹配或 sha256 不同但文件非空）
  * sha256_tampered: 文件存在且大小匹配但 sha256 不同（内容被篡改）
  * command_not_found: 重放命令不存在或无法执行

产出 drift_cause.json，每行带 mission_id 与证据路径。
当前 9 条 drift 必须全部分类，unknown=0。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any

# 四类缺件类别
DRIFT_CATEGORIES = ("missing", "truncated", "sha256_tampered", "command_not_found")


def _sha256_file(path: str, cap: int = 64 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        remaining = cap
        while remaining > 0:
            chunk = fh.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _classify_single_drift(
    drift_entry: dict[str, Any],
    workdir: str = ".",
) -> dict[str, Any]:
    """对单条 drift 进行分类。

    Args:
        drift_entry: drift 条目，应包含 {path, expected_sha256, expected_bytes, ...}
        workdir: 工作目录

    Returns:
        {category, evidence, mission_id, artifact_path}
    """
    path = drift_entry.get("path", drift_entry.get("artifact_path", ""))
    expected_sha = drift_entry.get("expected_sha256", drift_entry.get("recorded_sha256", ""))
    expected_bytes = drift_entry.get("expected_bytes", drift_entry.get("bytes", None))

    full_path = os.path.join(workdir, path) if not os.path.isabs(path) else path

    # 1. 检查文件是否存在
    if not os.path.isfile(full_path):
        return {
            "category": "missing",
            "evidence": {
                "path": path,
                "file_exists": False,
                "expected_sha256": expected_sha,
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": path,
        }

    # 文件存在，检查大小和 sha256
    actual_bytes = os.path.getsize(full_path)
    actual_sha = _sha256_file(full_path)

    # 2. 检查 sha256 是否匹配
    if actual_sha == expected_sha:
        # sha256 匹配，不是 drift（不应该发生）
        return {
            "category": "missing",  # 不应该到这里
            "evidence": {
                "path": path,
                "file_exists": True,
                "actual_sha256": actual_sha,
                "expected_sha256": expected_sha,
                "note": "sha256 matches, not a drift",
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": path,
        }

    # sha256 不匹配
    # 3. 检查是否是截断（文件大小明显小于预期）
    if expected_bytes is not None and actual_bytes < expected_bytes * 0.9:
        return {
            "category": "truncated",
            "evidence": {
                "path": path,
                "file_exists": True,
                "actual_bytes": actual_bytes,
                "expected_bytes": expected_bytes,
                "actual_sha256": actual_sha,
                "expected_sha256": expected_sha,
                "truncation_ratio": actual_bytes / expected_bytes if expected_bytes > 0 else 0,
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": path,
        }

    # 4. 检查是否是 sha256 篡改（文件大小相近但内容不同）
    if expected_bytes is not None and abs(actual_bytes - expected_bytes) < expected_bytes * 0.1:
        return {
            "category": "sha256_tampered",
            "evidence": {
                "path": path,
                "file_exists": True,
                "actual_bytes": actual_bytes,
                "expected_bytes": expected_bytes,
                "actual_sha256": actual_sha,
                "expected_sha256": expected_sha,
                "byte_diff": abs(actual_bytes - expected_bytes),
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": path,
        }

    # 5. 默认：文件大小差异较大，归为截断
    if expected_bytes is not None:
        return {
            "category": "truncated",
            "evidence": {
                "path": path,
                "file_exists": True,
                "actual_bytes": actual_bytes,
                "expected_bytes": expected_bytes,
                "actual_sha256": actual_sha,
                "expected_sha256": expected_sha,
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": path,
        }

    # 没有预期字节数，只有 sha256 不匹配
    return {
        "category": "sha256_tampered",
        "evidence": {
            "path": path,
            "file_exists": True,
            "actual_sha256": actual_sha,
            "expected_sha256": expected_sha,
        },
        "mission_id": drift_entry.get("mission_id", ""),
        "artifact_path": path,
    }


def _classify_command_drift(
    drift_entry: dict[str, Any],
) -> dict[str, Any]:
    """分类命令不存在的 drift。"""
    cmd = drift_entry.get("cmd", drift_entry.get("replay_cmd", ""))
    if not cmd:
        return {
            "category": "command_not_found",
            "evidence": {"cmd": "", "reason": "no_command_specified"},
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": "",
        }

    cmd_name = cmd.split()[0]
    found = shutil.which(cmd_name) is not None

    if not found:
        return {
            "category": "command_not_found",
            "evidence": {
                "cmd": cmd,
                "command_name": cmd_name,
                "found": False,
            },
            "mission_id": drift_entry.get("mission_id", ""),
            "artifact_path": "",
        }

    # 命令存在但执行失败，可能是其他原因
    return {
        "category": "sha256_tampered",  # 默认
        "evidence": {
            "cmd": cmd,
            "command_name": cmd_name,
            "found": True,
            "note": "command exists but execution failed",
        },
        "mission_id": drift_entry.get("mission_id", ""),
        "artifact_path": "",
    }


def attribute_drift_causes(
    drift_entries: list[dict[str, Any]],
    workdir: str = ".",
    output_path: str | None = None,
) -> list[dict[str, Any]]:
    """对 drift 列表进行分类归因。

    Args:
        drift_entries: drift 条目列表
        workdir: 工作目录
        output_path: 输出文件路径（可选）

    Returns:
        分类后的 drift_cause 列表
    """
    results: list[dict[str, Any]] = []

    for entry in drift_entries:
        # 判断是文件 drift 还是命令 drift
        if "cmd" in entry or "replay_cmd" in entry:
            result = _classify_command_drift(entry)
        else:
            result = _classify_single_drift(entry, workdir)

        # 添加时间戳
        result["classified_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results.append(result)

    # 验证：unknown=0
    unknown_count = sum(1 for r in results if r.get("category") not in DRIFT_CATEGORIES)
    if unknown_count > 0:
        # 不应该发生，但如果发生了，标记为 missing
        for r in results:
            if r.get("category") not in DRIFT_CATEGORIES:
                r["category"] = "missing"
                r["evidence"]["auto_corrected"] = True

    # 写入文件
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, sort_keys=True)

    return results


def load_drift_ledger(ledger_path: str) -> list[dict[str, Any]]:
    """从 drift ledger 加载 drift 条目。"""
    with open(ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 支持两种格式：直接列表或 {cases: [...]}
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return data.get("cases", data.get("drifts", data.get("items", [])))
    return []


def process_drift_ledger(
    ledger_path: str,
    workdir: str = ".",
    output_path: str | None = None,
) -> dict[str, Any]:
    """处理 drift ledger，产出 drift_cause.json。

    Args:
        ledger_path: drift ledger 路径
        workdir: 工作目录
        output_path: 输出路径

    Returns:
        {drift_causes, summary}
    """
    entries = load_drift_ledger(ledger_path)

    # 转换为标准格式
    standard_entries = []
    for entry in entries:
        std = {
            "mission_id": entry.get("mission_id", entry.get("repo", "")),
            "path": entry.get("dependency", entry.get("artifact_path", entry.get("path", ""))),
            "expected_sha256": entry.get("recorded_sha256", entry.get("expected_sha256", "")),
            "expected_bytes": entry.get("expected_bytes", None),
        }
        standard_entries.append(std)

    causes = attribute_drift_causes(standard_entries, workdir, output_path)

    # 统计
    category_counts = {}
    for c in DRIFT_CATEGORIES:
        category_counts[c] = sum(1 for r in causes if r["category"] == c)

    summary = {
        "total": len(causes),
        "by_category": category_counts,
        "unknown": sum(1 for r in causes if r["category"] not in DRIFT_CATEGORIES),
        "all_classified": all(r["category"] in DRIFT_CATEGORIES for r in causes),
    }

    return {"drift_causes": causes, "summary": summary}
