"""repro_audit.shims —— 兼容性垫片（零领域语义）。

为不同环境/版本提供统一接口，屏蔽差异：
  * **Python 版本兼容**：3.9 vs 3.10+ 的类型注解差异。
  * **pytest 版本兼容**：不同版本的输出格式差异。
  * **git 版本兼容**：不同版本的命令支持差异。
  * **API 版本兼容**：平台 API 的向后兼容层。

设计约束：
  * **透明**：调用方无需感知垫片存在。
  * **幂等**：同输入同输出。
  * **可测试**：每个垫片都有独立的单元测试。
"""
from __future__ import annotations

import re
import sys
from typing import Any


# ─────────────────────────── Python 版本兼容 ───────────────────────────

PYTHON_VERSION = sys.version_info[:2]


def normalize_type_annotation(annotation: Any) -> str:
    """归一化类型注解字符串（处理 3.9 vs 3.10+ 差异）。"""
    if not isinstance(annotation, str):
        return str(annotation)
    # 3.10+ 的 X | Y → Union[X, Y]
    if "|" in annotation and PYTHON_VERSION < (3, 10):
        parts = [p.strip() for p in annotation.split("|")]
        return f"Union[{', '.join(parts)}]"
    return annotation


def ensure_list_type(value: Any) -> list:
    """确保值是列表（处理单值/None/迭代器）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


# ─────────────────────────── pytest 输出兼容 ───────────────────────────

# pytest 6.x vs 7.x vs 8.x 的输出格式差异
_PYTEST_SUMMARY_PATTERNS = [
    # 8.x: "1 failed, 3 passed in 0.10s"
    re.compile(r"(?P<counts>(?:\d+ (?:passed|failed|error|errors|skipped|xfailed|"
               r"xpassed|deselected|warning|warnings)(?:, )?)+)"),
    # 7.x: "==== 1 failed, 3 passed in 0.10s ===="
    re.compile(r"=+\s*(?P<counts>(?:\d+ (?:passed|failed|error|errors|skipped)(?:, )?)+)\s*=+"),
    # 6.x: "short test summary info" 后的计数
    re.compile(r"(?P<counts>\d+ (?:passed|failed|error)(?:, \d+ (?:passed|failed|error))*)"),
]


def parse_pytest_summary(text: str) -> dict[str, int]:
    """从 pytest 输出里抽取计数（兼容多版本）。"""
    counts: dict[str, int] = {}
    for pattern in _PYTEST_SUMMARY_PATTERNS:
        for line in reversed(text.strip().splitlines()):
            m = pattern.search(line)
            if not m:
                continue
            counts_str = m.group("counts")
            for part in re.findall(r"(\d+) ([a-z]+)", counts_str):
                key = part[1]
                if key in ("errors", "warnings"):
                    key = key[:-1]  # normalize plural
                counts[key] = int(part[0])
            if counts:
                return counts
    return counts


def normalize_pytest_case_id(case_id: str) -> str:
    """归一化 pytest 用例 ID（处理不同版本的路径格式）。"""
    # 去掉前导 ./
    case_id = re.sub(r"^\./", "", case_id)
    # 统一路径分隔符
    case_id = case_id.replace("\\", "/")
    return case_id


# ─────────────────────────── git 命令兼容 ───────────────────────────

def git_archive_command(workdir: str, dest: str) -> str:
    """生成 git archive 命令（兼容不同 git 版本）。"""
    # 基础命令（所有版本都支持）
    return f"git -C '{workdir}' archive HEAD | tar -x -C '{dest}'"


def git_ls_tree_command(workdir: str) -> str:
    """生成 git ls-tree 命令（兼容不同 git 版本）。"""
    return f"git -C '{workdir}' ls-tree -r --name-only HEAD"


# ─────────────────────────── API 版本兼容 ───────────────────────────

def normalize_api_response(response: Any, endpoint: str) -> Any:
    """归一化 API 响应（处理不同版本的字段名差异）。"""
    if endpoint == "/tensor/campaigns/plans":
        # v1: items 在顶层；v2: items 在 data.items
        if isinstance(response, dict) and "data" in response and "items" in response["data"]:
            response["items"] = response["data"]["items"]
        return response
    elif endpoint.endswith("/nodes"):
        # v1: 返回列表；v2: 返回 {items: [...]}
        if isinstance(response, list):
            return {"items": response}
        return response
    return response


def build_api_request(path: str, body: dict[str, Any] | None = None,
                      token: str | None = None) -> dict[str, Any]:
    """构建 API 请求（统一格式）。"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return {
        "path": path,
        "body": body,
        "headers": headers,
    }


# ─────────────────────────── 通用工具 ───────────────────────────

def safe_json_loads(text: str, default: Any = None) -> Any:
    """安全解析 JSON（失败时返回默认值，不抛异常）。"""
    import json
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """安全转换为 int（失败时返回默认值）。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float（失败时返回默认值）。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default
