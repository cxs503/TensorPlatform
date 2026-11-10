"""gate_verify：把「一份 gate.json + 一份权威判据登记表」复算成三段式判定。

判定不是声明，是函数：

    grade = f(gate.json, registry, artifacts)   # 纯确定性，离线，无 wall clock

规则（自上而下，任一完整性/可复算性缺口即落 WITHHELD）：

=====  ====================================  ==========  ===============================
id     check                                 fail code   on fail
=====  ====================================  ==========  ===============================
G00    gate.json 存在                        gate_missing           WITHHELD
G01    可解析 + 结构合法（字段/类型/版本）    schema_invalid         WITHHELD
G02    criteria 非空                          criteria_missing       WITHHELD
G03    判据在权威登记表里存在                registry_missing       WITHHELD
C01    单位非空                              unit_missing           WITHHELD
C02    算子 ∈ OPS                            op_invalid             WITHHELD
C03    工件存在（数字-工件存在性）          artifact_missing       WITHHELD
C04    工件 sha256 复算一致                  artifact_sha_mismatch  WITHHELD
C05    指针可解析出有限数字                  pointer_unresolved     WITHHELD
C06    数字可由工件复算（禁手抄/只贴日志）    pointer_missing/measured_mismatch  WITHHELD
C07    阈值与权威登记表一致（禁改宽）        threshold_mismatch     WITHHELD
C08    阈值判定                              threshold_violated     FAIL
=====  ====================================  ==========  ===============================

优先级：任一 WITHHELD 级缺口 → 整体 WITHHELD（不知道 ≠ 通过）；
否则任一 FAIL → FAIL；全 PASS → PASS。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import (OPS, SCHEMA_VERSION, ContractError, apply_op,
                       canonical_json, canonical_sha256, measure, same_value)

PASS = "PASS"
FAIL = "FAIL"
WITHHELD = "WITHHELD"

#: 完整性问题码（出现即 WITHHELD；不参与阈值判定）
INTEGRITY_CODES = frozenset({
    "gate_missing", "schema_invalid", "criteria_missing", "registry_missing",
    "unit_missing", "op_invalid", "artifact_missing", "artifact_sha_missing",
    "artifact_sha_mismatch", "artifact_unparsable", "pointer_unresolved",
    "pointer_missing", "measured_mismatch", "threshold_mismatch",
    "threshold_invalid", "value_not_finite", "value_not_numeric", "scale_invalid",
})

_REQUIRED_GATE_KEYS = {"schema_version", "gate_id", "mission_id", "criteria"}


def gate_digest(gate: dict) -> str:
    """gate.json 内容摘要。**刻意剔除 env_fingerprint**：环境不参与判定，
    因此同一份判据在 loong64 / x86 上摘要相同（跨架构一致的构造性保证）。"""
    body = {k: v for k, v in gate.items() if k != "env_fingerprint"}
    return canonical_sha256(body)


def _structural_issues(gate: Any) -> list[str]:
    if not isinstance(gate, dict):
        return ["not_an_object"]
    problems = [f"missing:{k}" for k in sorted(_REQUIRED_GATE_KEYS) if k not in gate]
    if gate.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version:{gate.get('schema_version')!r}")
    if not isinstance(gate.get("gate_id"), str) or not gate.get("gate_id", "").strip():
        problems.append("gate_id_not_string")
    if isinstance(gate.get("mission_id"), bool) or not isinstance(gate.get("mission_id"), int):
        problems.append("mission_id_not_int")
    if not isinstance(gate.get("criteria"), list):
        problems.append("criteria_not_list")
    return problems


def _registry_entry(registry: dict | None, gate_id: str | None) -> dict | None:
    if not isinstance(registry, dict):
        return None
    gates = registry.get("gates")
    if not isinstance(gates, dict) or not gate_id:
        return None
    entry = gates.get(gate_id)
    return entry if isinstance(entry, dict) else None


def _check_registry(gate: dict, registry: dict | None) -> dict | None:
    """权威登记表比对：算子/阈值/单位必须与登记表逐字一致（防「阈值被改宽」）。"""
    if registry is None:
        return None
    entry = _registry_entry(registry, gate.get("gate_id"))
    if entry is None:
        raise ContractError("registry_missing", str(gate.get("gate_id")))
    return entry


def verify_gate(gate_path: str | Path | None = None, *, gate_doc: dict | None = None,
                gate_json_text: str | None = None, registry: dict | None = None,
                evidence_root: str | Path | None = None) -> dict:
    """复算一份 gate.json。返回判定字典（可 JSON 序列化、可逐判据入台账）。"""
    issues: list[dict] = []

    def issue(code: str, criterion_id: str | None = None, detail: str = "") -> None:
        issues.append({"code": code, "criterion_id": criterion_id, "detail": detail})

    gate = gate_doc
    if gate is None and gate_json_text is not None:
        try:
            gate = json.loads(gate_json_text)
        except json.JSONDecodeError:
            issue("schema_invalid", None, "json_decode")
            gate = None
    if gate is None and gate_path is not None:
        p = Path(gate_path)
        if not p.is_file():
            issue("gate_missing", None, str(gate_path))
        else:
            try:
                gate = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                issue("schema_invalid", None, "json_decode")
    if gate is None:
        if not issues:
            issue("gate_missing", None, str(gate_path))
        return _finish(None, None, issues, [], registry is not None, gate_path)

    problems = _structural_issues(gate)
    if problems:
        for prob in problems:
            issue("schema_invalid", None, prob)
        return _finish(gate, gate_digest(gate), issues, [], registry is not None, gate_path)

    base = Path(gate_path).parent if gate_path is not None else Path(evidence_root or ".")
    if evidence_root is not None:
        base = Path(evidence_root)

    try:
        reg_entry = _check_registry(gate, registry)
    except ContractError as exc:
        issue(exc.code, None, exc.detail)
        reg_entry = None

    criteria = gate.get("criteria") or []
    if not criteria:
        issue("criteria_missing", None, "empty")

    rows: list[dict] = []
    for idx, entry in enumerate(criteria):
        cid = (entry.get("id") if isinstance(entry, dict) else None) or f"#{idx}"
        row: dict[str, Any] = {
            "id": str(cid), "op": entry.get("op") if isinstance(entry, dict) else None,
            "threshold": entry.get("threshold") if isinstance(entry, dict) else None,
            "unit": entry.get("unit") if isinstance(entry, dict) else None,
            "measured": None, "artifact": None, "sha256": None,
            "status": "withheld", "code": None,
        }
        try:
            if not isinstance(entry, dict):
                raise ContractError("schema_invalid", f"criterion:{cid}")
            unit = entry.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                raise ContractError("unit_missing", str(cid))
            op = entry.get("op")
            if op not in OPS:
                raise ContractError("op_invalid", str(op))
            art = entry.get("artifact")
            if not isinstance(art, dict):
                raise ContractError("artifact_missing", str(cid))
            row["artifact"] = art.get("path")
            row["sha256"] = art.get("sha256")
            if "pointer" not in entry or not entry.get("pointer"):
                # 只有数字没有复算路径 = 手抄；只有日志没有数字 = 同一条枪毙
                raise ContractError("pointer_missing", str(cid))
            value, digest = measure(entry, base)
            row["sha256"] = digest
            stored = entry.get("measured")
            if stored is None:
                raise ContractError("measured_mismatch", f"{cid}:stored_missing")
            if not same_value(stored, value):
                raise ContractError("measured_mismatch", f"{cid}:{stored}!={value}")
            row["measured"] = value
            if reg_entry is not None:
                ref = (reg_entry.get("criteria") or {}).get(str(cid))
                if not isinstance(ref, dict):
                    raise ContractError("threshold_mismatch", f"{cid}:not_in_registry")
                if canonical_json(ref.get("threshold")) != canonical_json(entry.get("threshold")) \
                        or ref.get("op") != entry.get("op") \
                        or ref.get("unit") != entry.get("unit"):
                    raise ContractError("threshold_mismatch", f"{cid}:{canonical_json(ref)}!={canonical_json({'op': entry.get('op'), 'threshold': entry.get('threshold'), 'unit': unit})}")
            ok = apply_op(op, value, entry.get("threshold"))
            row["status"] = "pass" if ok else "fail"
            row["code"] = None if ok else "threshold_violated"
        except ContractError as exc:
            row["status"] = "withheld"
            row["code"] = exc.code
            issue(exc.code, row["id"], exc.detail)
        rows.append(row)

    return _finish(gate, gate_digest(gate), issues, rows, registry is not None, gate_path)


def _finish(gate: dict | None, digest: str | None, issues: list[dict], rows: list[dict],
            registry_checked: bool, gate_path) -> dict:
    if any(i["code"] in INTEGRITY_CODES for i in issues):
        grade = WITHHELD
    elif any(r["status"] == "fail" for r in rows):
        grade = FAIL
    elif rows and all(r["status"] == "pass" for r in rows):
        grade = PASS
    else:
        grade = WITHHELD
    gate_id = gate.get("gate_id") if isinstance(gate, dict) else None
    mission_id = gate.get("mission_id") if isinstance(gate, dict) else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "gate_id": gate_id,
        "gate_sha256": digest,
        "grade": grade,
        "criteria": [{"id": r["id"], "op": r["op"], "threshold": r["threshold"],
                      "unit": r["unit"], "status": r["status"],
                      "measured": r["measured"]} for r in rows],
    }
    return {
        "gate_id": gate_id,
        "mission_id": mission_id,
        "grade": grade,
        "issues": issues,
        "criteria": rows,
        "gate_sha256": digest,
        "verdict_digest": canonical_sha256(payload),
        "registry_checked": registry_checked,
        "gate_path": str(gate_path) if gate_path is not None else None,
    }


def verify_dir(gate_root: str | Path, *, registry: dict | None = None) -> list[dict]:
    """复算一个门目录下所有 ``*/gate.json``（字典序遍历，确定性）。"""
    root = Path(gate_root)
    out = []
    for path in sorted(root.glob("*/gate.json")):
        res = verify_gate(path, registry=registry, evidence_root=path.parent)
        out.append(res)
    return out
