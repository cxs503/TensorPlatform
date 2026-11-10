#!/usr/bin/env python3
"""KPI 写入 provenance 强制器（AC0）：任何 guide.* 写入必须带 notes 内含 src= 与可复算命令，否则 fail-closed 拒写。

背景（prop-7619 静默改写事故）：
  * 0.6667 被无来源 0 覆盖 → 根因：写入时无 provenance 校验。
  * 本模块实现 fail-closed 机制：任何 KPI 写入请求若不带合规 provenance，一律拒绝。

用法：
  from tools.metrics.kpi_provenance import validate_provenance, ProvenanceError
  try:
      validate_provenance(kpi_record)
  except ProvenanceError as e:
      # fail-closed：拒绝写入
      ...
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ProvenanceError(Exception):
    """Provenance 校验失败异常（fail-closed）"""
    pass


@dataclass
class ProvenanceRecord:
    """合规的 provenance 记录"""
    kpi_key: str
    value: Optional[float]
    src: str  # 来源标识（如 "db:tensor_dev_missions", "scan:artifacts/..."）
    recompute_command: str  # 可复算命令
    timestamp: str
    sha256: str = ""
    notes: str = ""

    def __post_init__(self):
        if not self.sha256:
            content = json.dumps({
                "kpi_key": self.kpi_key,
                "value": self.value,
                "src": self.src,
                "recompute_command": self.recompute_command,
                "timestamp": self.timestamp,
            }, sort_keys=True)
            self.sha256 = hashlib.sha256(content.encode()).hexdigest()


# 正则：notes 中必须包含 src= 和 recompute=（或 cmd=）
_SRC_PATTERN = re.compile(r"src=\S+")
_RECOMPUTE_PATTERN = re.compile(r"(?:recompute|cmd)=\S+")


def validate_provenance(record: Dict[str, Any]) -> ProvenanceRecord:
    """校验 KPI 写入请求的 provenance 合规性。

    规则：
    1. 必须有 notes 字段
    2. notes 中必须包含 src=（来源标识）
    3. notes 中必须包含 recompute= 或 cmd=（可复算命令）
    4. 若 not_recomputable=true，则 value 必须为 None（fail-closed）

    Args:
        record: KPI 记录字典，必须包含 key, value, notes 等字段

    Returns:
        ProvenanceRecord 对象

    Raises:
        ProvenanceError: 若 provenance 不合规
    """
    key = record.get("key", "")
    value = record.get("value")
    notes = record.get("notes", "")
    not_recomputable = record.get("not_recomputable", False)

    # 规则 1: 必须有 notes
    if not notes:
        raise ProvenanceError(
            f"KPI '{key}': notes 字段缺失，无法验证来源（fail-closed 拒写）"
        )

    # 规则 2: notes 中必须包含 src=
    if not _SRC_PATTERN.search(notes):
        raise ProvenanceError(
            f"KPI '{key}': notes 中未找到 src= 标识（fail-closed 拒写）。"
            f"示例: src=db:tensor_dev_missions"
        )

    # 规则 3: notes 中必须包含 recompute= 或 cmd=
    if not _RECOMPUTE_PATTERN.search(notes):
        raise ProvenanceError(
            f"KPI '{key}': notes 中未找到 recompute= 或 cmd= 标识（fail-closed 拒写）。"
            f"示例: recompute=SELECT count(*) FROM ..."
        )

    # 规则 4: not_recomputable=true 时 value 必须为 None
    if not_recomputable and value is not None:
        raise ProvenanceError(
            f"KPI '{key}': 标记为 not_recomputable 但 value={value} 非 None（fail-closed 拒写）。"
            f"不可复算 KPI 的值必须为 None，降级为 WITHHELD。"
        )

    # 提取 src 和 recompute_command
    src_match = _SRC_PATTERN.search(notes)
    recompute_match = _RECOMPUTE_PATTERN.search(notes)
    src = src_match.group(0)[4:] if src_match else ""  # 去掉 "src=" 前缀
    recompute_cmd = recompute_match.group(0).split("=", 1)[1] if recompute_match else ""

    return ProvenanceRecord(
        kpi_key=key,
        value=value,
        src=src,
        recompute_command=recompute_cmd,
        timestamp=datetime.now(timezone.utc).isoformat(),
        notes=notes,
    )


def validate_kpi_write(kpi_key: str, value: Optional[float], notes: str,
                       not_recomputable: bool = False) -> Dict[str, Any]:
    """便捷函数：校验并返回合规的写入记录。

    Args:
        kpi_key: KPI 标识
        value: KPI 值（not_recomputable=True 时必须为 None）
        notes: 包含 src= 和 recompute= 的注释
        not_recomputable: 是否不可复算

    Returns:
        合规的写入记录字典

    Raises:
        ProvenanceError: 若校验失败
    """
    record = {
        "key": kpi_key,
        "value": value,
        "notes": notes,
        "not_recomputable": not_recomputable,
    }
    prov = validate_provenance(record)
    return {
        "kpi_key": prov.kpi_key,
        "value": prov.value,
        "src": prov.src,
        "recompute_command": prov.recompute_command,
        "timestamp": prov.timestamp,
        "sha256": prov.sha256,
        "status": "WITHHELD" if (not_recomputable or value is None) else "COMPUTED",
    }


def batch_validate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """批量校验 KPI 写入请求。

    Args:
        records: KPI 记录列表

    Returns:
        校验结果：{passed: [...], failed: [...], all_pass: bool}
    """
    passed = []
    failed = []

    for rec in records:
        try:
            result = validate_kpi_write(
                kpi_key=rec.get("key", ""),
                value=rec.get("value"),
                notes=rec.get("notes", ""),
                not_recomputable=rec.get("not_recomputable", False),
            )
            passed.append(result)
        except ProvenanceError as e:
            failed.append({"record": rec, "error": str(e)})

    return {
        "passed": passed,
        "failed": failed,
        "all_pass": len(failed) == 0,
        "total": len(records),
        "pass_count": len(passed),
        "fail_count": len(failed),
    }
