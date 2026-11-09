"""CI 漂移门：判据工件/验证包的哈希钉桩 + 值级对照 + 篡改/假阳性计量。

机制
----
* **钉桩**：对每个受门工件取规范化 JSON（``sort_keys``、紧凑分隔符）的 sha256，
  连同关键标量值一起入 ``pins.json``；钉桩本身也是工件，可审计、可回滚。
* **检测**：重算哈希；不一致即告警（哈希级）；再对钉桩里记录的值做逐点对照
  （值级），二者任一触发即判"漂移"。
* **计量**：``run_gate`` 对 N 个程序化篡改变体统计检出率，对 N 个未改动副本统计
  假阳性率 —— 这两个数是成熟度评分里"漂移检出率/假阳性"的来源，不是自报。
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

FLOAT_TOL = 1e-12


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def flatten_numeric(payload: object, prefix: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], float]]:
    """按确定性顺序展开所有数值叶子（bool 视为非数值，避免脏路径）。"""
    out: List[Tuple[Tuple[str, ...], float]] = []
    if isinstance(payload, dict):
        for key in sorted(payload):
            out.extend(flatten_numeric(payload[key], prefix + (str(key),)))
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            out.extend(flatten_numeric(item, prefix + (str(idx),)))
    elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
        out.append((prefix, float(payload)))
    return out


def _get_path(payload: object, path: Sequence[str]) -> object:
    node = payload
    for part in path:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _set_path(payload: object, path: Sequence[str], value: float) -> None:
    node = payload
    for part in path[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = path[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def pin(payloads: Dict[str, object]) -> Dict[str, object]:
    """生成钉桩清单：每工件 sha256 + 全部数值叶子的值级快照。"""
    pins: Dict[str, object] = {"schema": "criteria-governance/drift-pins/v1", "artifacts": {}}
    for name in sorted(payloads):
        payload = payloads[name]
        pins["artifacts"][name] = {
            "sha256": canonical_hash(payload),
            "numeric_leaves": {"/".join(path): value for path, value in flatten_numeric(payload)},
        }
    return pins


@dataclass
class DriftFinding:
    artifact: str
    hash_match: bool
    value_mismatches: List[Dict[str, object]] = field(default_factory=list)

    @property
    def drifted(self) -> bool:
        return (not self.hash_match) or bool(self.value_mismatches)

    def to_dict(self) -> Dict[str, object]:
        return {"artifact": self.artifact, "hash_match": self.hash_match,
                "value_mismatches": self.value_mismatches, "drifted": self.drifted}


def check(payloads: Dict[str, object], pins: Dict[str, object]) -> List[DriftFinding]:
    findings: List[DriftFinding] = []
    for name in sorted(payloads):
        recorded = (pins.get("artifacts") or {}).get(name)
        if recorded is None:
            findings.append(DriftFinding(artifact=name, hash_match=False,
                                         value_mismatches=[{"path": "<whole>", "reason": "unpinned"}]))
            continue
        payload = payloads[name]
        hash_match = canonical_hash(payload) == recorded["sha256"]
        mismatches: List[Dict[str, object]] = []
        recorded_leaves: Dict[str, float] = recorded.get("numeric_leaves") or {}
        current = {"/".join(path): value for path, value in flatten_numeric(payload)}
        for path, old in recorded_leaves.items():
            new = current.get(path)
            if new is None:
                mismatches.append({"path": path, "recorded": old, "current": None,
                                   "reason": "removed"})
            elif abs(float(new) - float(old)) > FLOAT_TOL * max(1.0, abs(float(old))):
                mismatches.append({"path": path, "recorded": old, "current": new,
                                   "reason": "value_changed"})
        for path in current:
            if path not in recorded_leaves:
                mismatches.append({"path": path, "recorded": None,
                                   "current": current[path], "reason": "added"})
        findings.append(DriftFinding(artifact=name, hash_match=hash_match,
                                     value_mismatches=mismatches))
    return findings


def tamper_variants(payload: Dict[str, object], count: int, scale: float = 1.5,
                    offset: float = 1e-3,
                    prefer_substring: str = "threshold") -> List[Dict[str, object]]:
    """生成 ``count`` 个确定性篡改变体（每条改一个数值叶子）。

    叶子排序把 ``prefer_substring``（默认 ``threshold``）命中的路径排在前面，
    保证计量覆盖"阈值篡改"这一类，而不是只碰测量值。
    """
    leaves = [path for path, _ in flatten_numeric(payload) if path]
    leaves.sort(key=lambda path: (prefer_substring not in "/".join(path), path))
    variants: List[Dict[str, object]] = []
    for path in leaves[:count]:
        mutated = copy.deepcopy(payload)
        original = float(_get_path(mutated, path))
        _set_path(mutated, path, original * scale + offset)
        variants.append({"path": "/".join(path), "payload": mutated,
                         "targets_threshold": prefer_substring in "/".join(path)})
    return variants


def run_gate(payloads: Dict[str, object], trials_per_artifact: int = 10,
             clean_repeats: int = 10, mutation_scale: float = 1.5,
             tamper_scope: Optional[Sequence[str]] = None,
             clean_scope: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """跑完整漂移门：钉桩 → 篡改变体 → 未改动对照。

    ``tamper_scope``/``clean_scope`` 可把计量限定在指定工件子集（其余工件只钉桩、
    不计入检出率/假阳性率分子分母）。
    """
    pins = pin(payloads)
    tamper_names = [n for n in sorted(payloads)
                    if tamper_scope is None or n in set(tamper_scope)]
    clean_names = [n for n in sorted(payloads)
                   if clean_scope is None or n in set(clean_scope)]

    tampered: List[Dict[str, object]] = []
    for name in tamper_names:
        variants = tamper_variants(payloads[name], trials_per_artifact, scale=mutation_scale)
        for variant in variants:
            probe_payloads = dict(payloads)
            probe_payloads[name] = variant["payload"]
            findings = check(probe_payloads, pins)
            hit = [f.to_dict() for f in findings if f.artifact == name and f.drifted]
            tampered.append({
                "artifact": name,
                "mutation_path": variant["path"],
                "targets_threshold": variant["targets_threshold"],
                "detected": bool(hit),
                "hash_match": bool(hit and hit[0]["hash_match"]),
                "value_mismatch_count": len(hit[0]["value_mismatches"]) if hit else 0,
            })

    clean: List[Dict[str, object]] = []
    for name in clean_names:
        for repeat in range(clean_repeats):
            probe_payloads = dict(payloads)
            probe_payloads[name] = json.loads(canonical_json(payloads[name]))
            findings = check(probe_payloads, pins)
            alarmed = [f.to_dict() for f in findings if f.drifted]
            clean.append({"artifact": name, "repeat": repeat, "alarm": bool(alarmed)})

    detected = sum(1 for row in tampered if row["detected"])
    false_pos = sum(1 for row in clean if row["alarm"])
    return {
        "pins": pins,
        "tampered": tampered,
        "clean": clean,
        "tamper_total": len(tampered),
        "tamper_detected": detected,
        "detection_rate": detected / len(tampered) if tampered else 0.0,
        "clean_total": len(clean),
        "false_positive": false_pos,
        "false_positive_rate": false_pos / len(clean) if clean else 0.0,
        "threshold_targeted_trials": sum(1 for row in tampered if row["targets_threshold"]),
        "tamper_scope": tamper_names,
        "clean_scope": clean_names,
        "value_level_only_detected": sum(
            1 for row in tampered if row["detected"] and not row["hash_match"]),
    }