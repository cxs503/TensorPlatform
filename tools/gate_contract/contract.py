"""gate.json 契约的结构层：规范化 JSON、摘要、JSON 指针解析、阈值算子、环境指纹。

零领域语义：本模块只有「数字 / 单位 / 工件 / sha256 / 算子」这些结构性概念。
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "gate-contract/1.0"
REGISTRY_SCHEMA_VERSION = "gate-criteria-registry/1.0"
CLAIMS_SCHEMA_VERSION = "gate-claims/1.0"

#: 阈值算子白名单（结构化，不解释物理含义）
OPS = ("le", "lt", "ge", "gt", "abs_le", "eq", "in_range", "all_in_range")


class ContractError(Exception):
    """结构错误。``code`` 直接成为判定里的问题码（可被 SQL 统计）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ── 规范化与摘要 ────────────────────────────────────────────────


def canonical_json(obj: Any) -> str:
    """确定性 JSON：键排序 + 紧凑分隔 + 禁 NaN。同一对象任何架构上逐字节相同。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def canonical_sha256(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def env_fingerprint() -> dict:
    """运行环境指纹。

    ``stable_digest`` 刻意**不含**主机/架构字段：跨架构复算时它必须相同，
    因此可以证明「判定不依赖机器」。主机差异留在 ``host_digest`` 里备查，
    但它不参与任何判定摘要（见 :func:`tools.gate_contract.verify.gate_digest`）。
    """
    fp = {
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "system": platform.system(),
        "byteorder": sys.byteorder,
        "float_model": "ieee754-binary64",
    }
    fp["stable_digest"] = canonical_sha256({
        "implementation": fp["implementation"],
        "python_minor": ".".join(str(x) for x in sys.version_info[:2]),
        "float_model": fp["float_model"],
    })
    fp["host_digest"] = canonical_sha256({"machine": fp["machine"], "system": fp["system"]})
    return fp


# ── 指针解析 ────────────────────────────────────────────────────


def resolve(doc: Any, pointer: str | None = None, select: str | None = None,
            field: str | None = None) -> Any:
    """按 ``a/b/0/c`` 取子树；``select`` 对列表做确定性聚合（absmax/max/min/mean）。

    聚合是必需的：证据工件多把逐点误差存成数组，契约只承认一个标量数字，
    聚合口径写在 gate.json 里，因此判定可复算、不靠人挑数字。
    """
    cur = doc
    if pointer:
        for raw in str(pointer).split("/"):
            if raw == "":
                continue
            if isinstance(cur, list):
                try:
                    cur = cur[int(raw)]
                except (ValueError, IndexError):
                    raise ContractError("pointer_unresolved", str(pointer)) from None
            elif isinstance(cur, dict):
                if raw not in cur:
                    raise ContractError("pointer_unresolved", str(pointer))
                cur = cur[raw]
            else:
                raise ContractError("pointer_unresolved", str(pointer))
    if select:
        if not isinstance(cur, list) or not cur:
            raise ContractError("pointer_unresolved", f"{pointer}#{select}")
        values: list[float] = []
        for item in cur:
            raw = item.get(field) if (field and isinstance(item, dict)) else item
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ContractError("pointer_unresolved", f"{pointer}#{field}")
            v = float(raw)
            if not math.isfinite(v):
                raise ContractError("value_not_finite", f"{pointer}#{field}")
            values.append(v)
        if select == "absmax":
            return max(values, key=abs)
        if select == "max":
            return max(values)
        if select == "min":
            return min(values)
        if select == "mean":
            return sum(values) / len(values)
        raise ContractError("pointer_unresolved", f"select={select}")
    return cur


# ── 工件测量 ────────────────────────────────────────────────────


def _contained_path(evidence_root: str | Path | None, rel: str) -> Path:
    """工件路径必须**留在门目录内**：绝对路径 / ``..`` 穿越 / 符号链接外逃一律拒绝。

    证据契约的意义是「任何人都能就地复算」，所以工件只能是门目录的相对子路径；
    允许外逃等于允许把门外的任意文件当作证据（且 sha256 会替它背书）。
    """
    raw = str(rel)
    if not raw or raw.startswith("/") or "\\" in raw or raw.startswith("~"):
        raise ContractError("artifact_path_escape", raw)
    if any(part == ".." for part in raw.replace("\\", "/").split("/")):
        raise ContractError("artifact_path_escape", raw)
    root = Path(evidence_root or ".").resolve()
    path = root / raw
    try:
        resolved = path.resolve()
    except OSError:
        raise ContractError("artifact_path_escape", raw) from None
    if resolved != root and root not in resolved.parents:
        raise ContractError("artifact_path_escape", raw)
    return resolved


def measure(entry: dict, evidence_root: str | Path | None) -> tuple[Any, str]:
    """从一个判据条目复算实测值：工件存在性 + sha256 复算 + 指针解析 + 线性换算。"""
    art = entry.get("artifact")
    if not isinstance(art, dict):
        raise ContractError("artifact_missing", str(entry.get("id")))
    rel = art.get("path")
    if not isinstance(rel, str) or not rel:
        raise ContractError("artifact_missing", str(entry.get("id")))
    path = _contained_path(evidence_root, rel)
    try:
        data = path.read_bytes()
    except OSError:
        raise ContractError("artifact_missing", rel) from None
    digest = sha256_bytes(data)
    want = art.get("sha256")
    if not isinstance(want, str) or not want:
        raise ContractError("artifact_sha_missing", rel)
    if digest != want:
        raise ContractError("artifact_sha_mismatch", f"{Path(rel).name}:{digest[:12]}!={want[:12]}")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractError("artifact_unparsable", rel) from None
    value = resolve(doc, entry.get("pointer"), entry.get("select"), entry.get("field"))
    try:
        scale = float(entry.get("scale", 1.0) if entry.get("scale") is not None else 1.0)
    except (TypeError, ValueError):
        raise ContractError("scale_invalid", str(entry.get("scale"))) from None
    if isinstance(value, list):
        scaled = [float(v) * scale for v in value]
        if not all(math.isfinite(v) for v in scaled):
            raise ContractError("value_not_finite", rel)
        return scaled, digest
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("pointer_unresolved", type(value).__name__)
    out = float(value) * scale
    if not math.isfinite(out):
        raise ContractError("value_not_finite", rel)
    return out, digest


def same_value(a: Any, b: Any, rel_tol: float = 1e-9) -> bool:
    """实测值比对：标量按相对容差，数组逐元素；非数字直接不等。"""
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y, rel_tol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        x, y = float(a), float(b)
        if x == y:
            return True
        return abs(x - y) <= rel_tol * max(abs(x), abs(y), 1e-300)
    return a == b


def apply_op(op: str, value: Any, threshold: Any) -> bool:
    """结构化阈值判定。返回 True=PASS（阈值满足），False=违反。"""
    if op not in OPS:
        raise ContractError("op_invalid", str(op))
    if op == "all_in_range":
        if (not isinstance(threshold, list) or len(threshold) != 2
                or not isinstance(value, list)):
            raise ContractError("threshold_invalid", str(op))
        lo, hi = float(threshold[0]), float(threshold[1])
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise ContractError("threshold_invalid", str(op))
        return all(lo <= float(v) <= hi for v in value)
    if isinstance(value, list) or isinstance(value, bool):
        raise ContractError("value_not_numeric", str(op))
    if not isinstance(value, (int, float)):
        raise ContractError("value_not_numeric", str(op))
    v = float(value)
    if not math.isfinite(v):
        raise ContractError("value_not_finite", "")
    if op == "in_range":
        if not isinstance(threshold, list) or len(threshold) != 2:
            raise ContractError("threshold_invalid", str(op))
        lo, hi = float(threshold[0]), float(threshold[1])
        return lo <= v <= hi
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ContractError("threshold_invalid", str(op))
    t = float(threshold)
    if not math.isfinite(t):
        raise ContractError("threshold_invalid", "nan")
    if op == "le":
        return v <= t
    if op == "lt":
        return v < t
    if op == "ge":
        return v >= t
    if op == "gt":
        return v > t
    if op == "abs_le":
        return abs(v) <= t
    return v == t  # eq
