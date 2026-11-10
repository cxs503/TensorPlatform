"""跨仓接口契约校验 (零领域语义)：格式 · 单位 · 参考系 · 跨生产者一致。

检查项 (每条都给 ``code``/``subject``/``result``/``detail``，可机器汇总):

C1 信封格式      —— 信封文件存在、schema 字段等于契约版本、必需元数据键齐、
                    ``generated_utc`` 为 RFC3339 UTC 且可解析；
C2 单位可解析    —— 每个量声明的单位标签能被量纲代数解析 (未知符号 = FAIL)；
C3 量纲一致      —— 声明单位与 manifest 里该量的参考单位同量纲 (允许 kN vs N)；
C4 参考系合法    —— frame ∈ contract.allowed_frames；
C5 跨生产者一致  —— 同一量名在不同仓里必须同量纲、同参考系；
C6 legacy 元数据 —— 无信封工件按"历史格式"统计: schema/mission/时间戳/单位
                    四类元数据键的存在率 (跨仓契约漂移的量化证据)。

``result`` 取值 PASS / FAIL / NA (NA 表示该检查对此工件不适用，不计入分母)。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import ENVELOPE_SCHEMA
from .units import UnitError, dimension_signature, parse_unit

RFC3339_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(Z|\+00:00)$")

REQUIRED_ENVELOPE_KEYS = ("schema", "node_ext_id", "mission_id", "repo",
                          "git_head", "generated_utc", "quantities", "artifacts")

LEGACY_METADATA_KEYS = {
    "schema": ("schema", "schema_version"),
    "mission": ("mission", "mission_id", "node"),
    "timestamp": ("generated_at", "generated_utc", "timestamp", "created_at"),
    "unit": ("unit", "units"),
}


def check_envelope(envelope: dict[str, Any], node: dict[str, Any],
                   require_schema: str = ENVELOPE_SCHEMA) -> list[dict[str, Any]]:
    """C1: 信封结构契约。"""
    checks: list[dict[str, Any]] = []

    def add(code: str, ok: bool, detail: str) -> None:
        checks.append({"code": code, "subject": node["ext_id"],
                       "result": "PASS" if ok else "FAIL", "detail": detail})

    missing = [k for k in REQUIRED_ENVELOPE_KEYS if k not in envelope]
    add("C1.required_keys", not missing,
        "all required keys present" if not missing else f"missing keys: {missing}")
    add("C1.schema", envelope.get("schema") == require_schema,
        f"schema={envelope.get('schema')!r} expected={require_schema!r}")
    add("C1.node_binding", envelope.get("node_ext_id") == node["ext_id"],
        f"envelope node_ext_id={envelope.get('node_ext_id')!r} "
        f"manifest={node['ext_id']!r}")
    add("C1.mission_binding", envelope.get("mission_id") == node["mission_id"],
        f"envelope mission_id={envelope.get('mission_id')!r} "
        f"manifest={node['mission_id']!r}")
    ts = envelope.get("generated_utc")
    ts_ok = isinstance(ts, str) and bool(RFC3339_UTC.match(ts))
    if ts_ok:
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            ts_ok = False
    add("C1.timestamp", ts_ok, f"generated_utc={ts!r} (need RFC3339 UTC)")
    add("C1.repo", isinstance(envelope.get("repo"), str) and bool(envelope["repo"]),
        f"repo={envelope.get('repo')!r}")
    return checks


def check_quantities(quantities: list[dict[str, Any]], allowed_frames: list[str]
                     ) -> list[dict[str, Any]]:
    """C2/C3/C4: 单位可解析 + 量纲一致 + 参考系合法。"""
    checks: list[dict[str, Any]] = []
    for q in quantities:
        subj = f"{q.get('node_ext_id')}:{q['name']}"
        unit = q.get("unit")
        if q.get("unit_source") == "manifest":
            checks.append({"code": "C2.unit_parse", "subject": subj, "result": "NA",
                           "detail": ("unit only declared in manifest; legacy artifact "
                                      "carries no unit tag (see C6.unit)")})
            if q.get("unit_ref") is not None:
                checks.append({"code": "C3.dimension", "subject": subj, "result": "NA",
                               "detail": "no artifact-side unit to compare"})
            frame = q.get("frame")
            checks.append({"code": "C4.frame", "subject": subj, "result": "NA",
                           "detail": ("frame only declared in manifest; legacy artifact "
                                      "carries no frame tag")})
            continue
        try:
            sig = dimension_signature(str(unit))
            checks.append({"code": "C2.unit_parse", "subject": subj, "result": "PASS",
                           "detail": f"unit={unit!r} -> {sig}"})
        except UnitError as exc:
            checks.append({"code": "C2.unit_parse", "subject": subj, "result": "FAIL",
                           "detail": str(exc)})
            sig = None
        ref = q.get("unit_ref")
        if ref is not None and sig is not None:
            try:
                ref_sig = dimension_signature(str(ref))
            except UnitError as exc:
                checks.append({"code": "C3.dimension", "subject": subj,
                               "result": "FAIL", "detail": f"ref unit: {exc}"})
                ref_sig = None
            if ref_sig is not None:
                ok = ref_sig == sig
                checks.append({"code": "C3.dimension", "subject": subj,
                               "result": "PASS" if ok else "FAIL",
                               "detail": f"declared {sig} vs reference {ref_sig} "
                                         f"(unit {unit!r} vs {ref!r})"})
        frame = q.get("frame")
        ok = frame in allowed_frames
        checks.append({"code": "C4.frame", "subject": subj,
                       "result": "PASS" if ok else "FAIL",
                       "detail": f"frame={frame!r} allowed={allowed_frames}"})
    return checks


def check_declarations(declared: list[dict[str, Any]],
                       allowed_frames: list[str]) -> list[dict[str, Any]]:
    """C0: manifest 声明的契约自洽 (单位可解析 + 参考系合法)。

    工件未落盘时这是唯一可查的一面：声明层就把跨仓单位/参考系钉死，
    生产者提交时按声明交付，检查在 C2/C3/C4 复核实测值。
    """
    checks: list[dict[str, Any]] = []
    for d in declared:
        subj = f"{d['node_ext_id']}:{d['name']}"
        try:
            sig = dimension_signature(str(d["unit"]))
            checks.append({"code": "C0.declaration_unit", "subject": subj,
                           "result": "PASS", "detail": f"declared unit={d['unit']!r} -> {sig}"})
        except UnitError as exc:
            checks.append({"code": "C0.declaration_unit", "subject": subj,
                           "result": "FAIL", "detail": str(exc)})
        ok = d.get("frame") in allowed_frames
        checks.append({"code": "C0.declaration_frame", "subject": subj,
                       "result": "PASS" if ok else "FAIL",
                       "detail": f"declared frame={d.get('frame')!r}"})
    return checks


def legacy_contract_checks(artifact: str, doc: Any) -> list[dict[str, Any]]:
    """C6: 无信封工件的历史元数据校验 (逐工件四类键: 格式/绑定/时间/单位)。"""
    checks: list[dict[str, Any]] = []
    if not isinstance(doc, dict):
        return [{"code": "C6.object", "subject": artifact, "result": "FAIL",
                 "detail": f"top-level JSON is {type(doc).__name__}, expected object"}]
    for kind, cands in LEGACY_METADATA_KEYS.items():
        hit = [k for k in cands if k in doc]
        checks.append({"code": f"C6.{kind}", "subject": artifact,
                       "result": "PASS" if hit else "FAIL",
                       "detail": (f"key {hit[0]!r} present" if hit else
                                  f"none of {list(cands)} present: artifact carries no "
                                  f"{kind} metadata")})
    return checks


def _canonical_unit(unit: str) -> str:
    """量纲 + SI 尺度 的规范串 (``%`` 与 ``1`` 量纲同但尺度差 100 → 不同串)。"""
    dims, scale = parse_unit(unit)
    sig = dimension_signature(unit)
    return f"{sig}@{scale:.6g}"


def cross_producer(quantities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """C5: 同一量名跨仓一致性 (量纲 + 参考系为硬契约)。

    单位前缀/尺度差异 (``kN`` vs ``N``、``%`` vs ``1``) 不判 FAIL: 同量纲
    换算即可，判 FAIL 会把合法交付打回；但差异必须显式记录 (``scale_variants``
    + ``scale_warning``)，因为跨仓**不经换算直接相加**就是 100 倍/1000 倍错误。
    量纲不同或参考系不同才是真违约。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for q in quantities:
        groups.setdefault(q["name"], []).append(q)
    out: list[dict[str, Any]] = []
    for name, entries in sorted(groups.items()):
        repos = sorted({e.get("repo", "?") for e in entries})
        dims: dict[str, list[str]] = {}
        scales: set[str] = set()
        frames: dict[str, list[str]] = {}
        unparsed: list[str] = []
        for e in entries:
            tag = f"{e.get('repo')}/{e.get('node_ext_id')}"
            try:
                sig = dimension_signature(str(e["unit"]))
                _, scale = parse_unit(str(e["unit"]))
                dims.setdefault(sig, []).append(f"{tag}:{e['unit']}")
                scales.add(f"{sig}@{scale:.6g}")
            except UnitError:
                unparsed.append(f"{tag}:{e['unit']}")
            frames.setdefault(str(e.get("frame")), []).append(tag)
        base = {"quantity": name, "producers": repos, "units": dims,
                "frames": frames, "scale_variants": sorted(scales),
                "scale_warning": len(scales) > 1,
                "unparsed_units": unparsed}
        if unparsed:
            out.append({**base, "result": "FAIL",
                        "detail": f"unparsable unit tags: {unparsed}"})
            continue
        if len(repos) < 2:
            out.append({**base, "result": "NA",
                        "detail": "single producer: cross-repo agreement not applicable"})
            continue
        ok = len(dims) == 1 and len(frames) == 1
        detail = ("all producers agree on dimension and frame"
                  if ok else
                  f"dimension variants={list(dims)} frame variants={list(frames)}")
        if ok and len(scales) > 1:
            detail += f"; scale variants {sorted(scales)} require explicit conversion"
        out.append({**base, "result": "PASS" if ok else "FAIL", "detail": detail})
    return out


def legacy_metadata(docs: dict[str, Any]) -> dict[str, Any]:
    """C6: 无信封工件的历史元数据键存在率 (跨仓漂移量化)。"""
    rows = []
    for rel, doc in sorted(docs.items()):
        if not isinstance(doc, dict):
            rows.append({"artifact": rel, "object": False, "keys": {}})
            continue
        keys = {kind: [k for k in cands if k in doc]
                for kind, cands in LEGACY_METADATA_KEYS.items()}
        rows.append({"artifact": rel, "object": True,
                     "keys": {k: v for k, v in keys.items()}})
    total = len(rows)
    rate = {}
    for kind in LEGACY_METADATA_KEYS:
        hit = sum(1 for r in rows if r["keys"].get(kind))
        rate[kind] = round(hit / total, 6) if total else None
    return {"artifacts": rows, "count": total, "presence_rate": rate, "docs": docs,
            "note": ("legacy artifacts without envelope: metadata key presence is a "
                     "measured contract-drift statistic")}


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """契约检查汇总: 各 result 计数 + 通过率 (NA 不计入分母)。"""
    counts = {"PASS": 0, "FAIL": 0, "NA": 0}
    by_code: dict[str, dict[str, int]] = {}
    for c in checks:
        counts[c["result"]] = counts.get(c["result"], 0) + 1
        slot = by_code.setdefault(c["code"], {"PASS": 0, "FAIL": 0, "NA": 0})
        slot[c["result"]] = slot.get(c["result"], 0) + 1
    denom = counts["PASS"] + counts["FAIL"]
    return {"counts": counts,
            "pass_rate": round(counts["PASS"] / denom, 6) if denom else None,
            "by_code": by_code,
            "violations": [c for c in checks if c["result"] == "FAIL"]}