"""能力证据采集主管线（collect）：映射 → 执行 → 判级 → 落盘。

一次运行完成：
1. 从平台能力注册表读全部能力 id（``config/capabilities.json``，缺则退回夹具快照）；
2. 装载映射配置并静态校验（映射的测试必须真实存在）；
3. **一次** pytest 批量执行所有映射模块（junitxml 归属）；
4. 对每条能力跑运行时探针 + 静态标记扫描；
5. 判级（含人工复核上限），逐能力落 ``acceptance.json``；
6. 汇总覆盖率、C1~C4 机械结论、两条一致率口径。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .acceptance import (build_acceptance, build_coverage, check_no_downgrade,
                         content_digest, sha256_file, write_acceptance)
from .consistency import evaluate_labeled, evaluate_manual
from .grader import Grade, grade_capability, load_manual_ceiling, scan_markers
from .mapping import CapabilityBinding, EvidenceMap, load_mapping
from .probes import run_probe
from .runner import run_tests

DEFAULT_MAPPING = "config/capability_evidence_map.json"
DEFAULT_REGISTRY = "config/capabilities.json"
DEFAULT_SNAPSHOT = "tests/fixtures/capability_ledger/capabilities_snapshot.json"
DEFAULT_MANUAL_LEDGER = "artifacts/capability_ledger/capability_ledger_resolved.json"
DEFAULT_LABELED = "tests/fixtures/capability_evidence/labeled_cases.json"


def registry_capability_ids(root: Path, registry_path: str = DEFAULT_REGISTRY,
                            snapshot_path: str = DEFAULT_SNAPSHOT) -> List[str]:
    """从注册表（或快照）读能力 id 列表。"""
    reg = root / registry_path
    if reg.exists():
        data = json.loads(reg.read_text(encoding="utf-8"))
        caps = data.get("capabilities", [])
        if isinstance(caps, dict):
            return sorted(caps)
        return sorted(str(c.get("capability_id") or c.get("id")) for c in caps)
    snap = root / snapshot_path
    if snap.exists():
        data = json.loads(snap.read_text(encoding="utf-8"))
        return sorted(data.get("capabilities", {}))
    raise FileNotFoundError("capability registry and snapshot both missing")


def _collect_markers(root: Path, binding: CapabilityBinding) -> Dict[str, Any]:
    if not binding.code_globs:
        return {"files_scanned": 0, "stub_files": {}, "mock_files": {}}
    return scan_markers(root, binding.code_globs)


def _implementation_present(root: Path, binding: CapabilityBinding,
                            markers: Dict[str, Any]) -> bool:
    if markers.get("files_scanned", 0):
        return True
    for pattern in binding.code_globs:
        if any(root.glob(pattern)):
            return True
    return False


def collect(root: str | Path = ".", *,
            mapping_path: str = DEFAULT_MAPPING,
            registry_path: str = DEFAULT_REGISTRY,
            out_dir: str | Path | None = None,
            run_mapped_tests: bool = True,
            tests_payload: Optional[Dict[str, Any]] = None,
            manual_ledger: str = DEFAULT_MANUAL_LEDGER,
            labeled_path: str = DEFAULT_LABELED,
            target_covered: int = 15,
            write: bool = True) -> Dict[str, Any]:
    """执行完整采集管线，返回汇总（含产物路径与判据结论）。

    ``tests_payload`` 允许把上一次的测试执行结果注入（离线复算/单测用），
    格式为 ``{module: {passed, failed, errored, skipped, total}}``。
    """
    root = Path(root).resolve()
    t0 = time.time()
    mapping: EvidenceMap = load_mapping(mapping_path, root=root)
    cap_ids = registry_capability_ids(root, registry_path)
    out = Path(out_dir) if out_dir else (root / mapping.artifact_root)
    manual_levels = load_manual_ceiling(root / manual_ledger)
    mapping_digest = sha256_file(Path(mapping.source_path))

    if tests_payload is not None:
        module_stats = tests_payload
    elif run_mapped_tests:
        run = run_tests(root, mapping.test_modules())
        module_stats = {mod: res.to_dict() for mod, res in run.modules.items()}
    else:
        module_stats = {}

    acceptances: Dict[str, Dict[str, Any]] = {}
    grades: List[Grade] = []
    paths: Dict[str, Dict[str, str]] = {}
    for cap_id in cap_ids:
        binding = mapping.bindings.get(cap_id)
        if binding is None:
            continue
        criteria = mapping.acceptance_for(cap_id)
        aggregated = _aggregate_tests(module_stats, binding.test_modules, criteria)
        probes = [run_probe(name, root).to_dict() for name in binding.probes]
        markers = _collect_markers(root, binding)
        impl = _implementation_present(root, binding, markers)

        # 裸判级（无人上限），供一致率对照；再算带上限的最终判级
        raw = grade_capability(cap_id, tests=aggregated, probes=probes, markers=markers,
                              implementation_present=impl, mapped=True, ceiling=None)
        final = grade_capability(cap_id, tests=aggregated, probes=probes, markers=markers,
                                implementation_present=impl, mapped=True,
                                ceiling=manual_levels.get(cap_id))
        grades.append(final)
        grade_payload = final.to_dict()
        grade_payload["auto_level_raw"] = raw.level
        payload = build_acceptance(cap_id, criteria=criteria, tests=aggregated,
                                   probes=probes, grade=grade_payload,
                                   mapping_digest=mapping_digest,
                                   evidence_scope=binding.evidence_scope)
        acceptances[cap_id] = payload
        if write:
            paths[cap_id] = write_acceptance(out, cap_id, payload)

    coverage = build_coverage(capability_ids=cap_ids, acceptances=acceptances,
                              target_covered=target_covered)
    downgrade = check_no_downgrade(capability_ids=cap_ids, acceptances=acceptances,
                                   manual_levels=manual_levels)

    labeled_file = root / labeled_path
    labeled = evaluate_labeled(json.loads(labeled_file.read_text(encoding="utf-8"))
                               if labeled_file.exists() else [])
    manual = evaluate_manual(capability_ids=cap_ids, acceptances=acceptances,
                             manual_levels=manual_levels)

    summary: Dict[str, Any] = {
        "mapping_sha256": mapping_digest,
        "registry": registry_path,
        "capabilities_total": len(cap_ids),
        "capabilities_mapped": len(mapping.bindings),
        "unmapped": sorted(set(cap_ids) - set(mapping.bindings)),
        "coverage": coverage,
        "no_downgrade": downgrade,
        "consistency": {"labeled": labeled, "manual": manual},
        "grade_tally": coverage["grade_tally"],
        "acceptance_paths": paths,
        "run_metrics": {"elapsed_s": round(time.time() - t0, 2),
                        "tests_executed": bool(module_stats)},
    }
    summary["content_digest"] = content_digest(summary)
    if write:
        out.mkdir(parents=True, exist_ok=True)
        target = out / "coverage.json"
        target.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        (out / "coverage.json.sha256").write_text(
            f"{sha256_file(target)}  {target.name}\n", encoding="utf-8")
        summary["coverage_path"] = str(target)
    return summary


def _aggregate_tests(module_stats: Dict[str, Any], modules: Iterable[str],
                     criteria: Dict[str, Any]) -> Dict[str, Any]:
    """把若干模块的执行统计合并为一条能力的测试证据。"""
    acc = {"module": "+".join(modules), "total": 0, "passed": 0, "failed": 0,
           "errored": 0, "skipped": 0, "decisive": 0, "executed": False,
           "failures": []}
    for mod in modules:
        stats = module_stats.get(mod) or {}
        if not stats:
            continue
        acc["total"] += int(stats.get("total", 0))
        acc["passed"] += int(stats.get("passed", 0))
        acc["failed"] += int(stats.get("failed", 0))
        acc["errored"] += int(stats.get("errored", 0))
        acc["skipped"] += int(stats.get("skipped", 0))
        acc["failures"].extend(stats.get("failures", []) or [])
        acc["executed"] = acc["executed"] or bool(stats.get("executed"))
    acc["decisive"] = acc["passed"] + acc["failed"] + acc["errored"]
    acc["pass_rate"] = round(acc["passed"] / acc["decisive"], 4) if acc["decisive"] else 0.0
    acc["min_tests"] = int(criteria.get("min_tests", 1))
    acc["min_pass_rate"] = float(criteria.get("min_pass_rate", 0.8))
    acc["failures"] = acc["failures"][:5]
    return acc