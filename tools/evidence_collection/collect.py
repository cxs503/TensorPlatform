"""采集编排：面清单 → 在线探针 → 参考误差复算 → 绑定测试执行 → 定级。

一次 ``collect`` 产出::

    artifacts/evidence_collection/
        summary.json                  # 覆盖率/等级分布/逐能力一句话结论
        ledger_view.json              # 台账视图（能力 → 等级/理由码/工件摘要）
        <capability>/acceptance.json  # 单能力验收工件（含请求响应原文摘要、判据表、sha256）
        <capability>/criteria_table.md

运行期不可达时的降级：面清单与探针记 ``NOT_EXECUTED``（不是通过），
等级按 ``ec-grade-v1`` 落 WITHHELD，并在 summary 里显式写 ``runtime_available=false``。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import execute, reference, runtime
from .grade import grade
from .spec import spec_sha256


def _sha_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_capability(cap_id: str, entry: dict, ctx: dict) -> dict:
    thresholds = ctx["thresholds"]
    root = ctx["root"]
    signals: dict[str, Any] = {"capability": cap_id, "surface": None, "probes": [],
                               "reference": None, "tests": None}

    surface_patterns = entry.get("surface") or []
    if surface_patterns:
        if ctx.get("online_paths") is not None:
            signals["surface"] = runtime.surface_check(surface_patterns, ctx["online_paths"])
        else:
            signals["surface"] = {"declared": len(surface_patterns), "present": [], "absent": [],
                                  "status": runtime.NOT_EXECUTED,
                                  "detail": "runtime surface unavailable"}

    if ctx.get("base_url"):
        for probe in entry.get("probes") or []:
            signals["probes"].append(runtime.run_probe(ctx["base_url"], probe, ctx["timeout_s"]))

    if entry.get("reference"):
        signals["reference"] = reference.evaluate_reference(entry["reference"], root)

    if entry.get("tests") and ctx.get("run_tests", True):
        signals["tests"] = execute.run_tests(entry["tests"], root, ctx["test_timeout_s"])
    elif entry.get("tests"):
        signals["tests"] = {"modules": entry["tests"], "status": "SKIPPED",
                            "totals": {}, "detail": "test execution disabled in this run"}

    verdict = grade(signals, thresholds)
    signals["verdict"] = verdict
    return signals


def collect(spec: dict, root: str | Path = ".", base_url: str | None = None,
            run_tests: bool = True, timeout_s: float = 20.0, test_timeout_s: float = 600.0) -> dict:
    root = Path(root).resolve()
    spec_sha = spec_sha256(spec)
    runtime_meta: dict[str, Any] = {
        "base_url": base_url,
        "available": False,
        "openapi_sha256": None,
        "service_version": None,
        "detail": None,
    }
    online_paths = None
    if base_url:
        try:
            doc = runtime.fetch_openapi(base_url, spec["sources"]["runtime"].get("openapi_path", "/openapi.json"),
                                        timeout_s)
            online_paths = doc["paths"]
            runtime_meta.update({
                "available": True,
                "openapi_sha256": doc["sha256"],
                "service_version": doc.get("version"),
                "service_title": doc.get("title"),
                "n_paths": len(doc["paths"]),
            })
        except Exception as exc:  # noqa: BLE001 - 采集降级必须留痕
            runtime_meta["detail"] = f"{type(exc).__name__}: {exc}"

    ctx = {
        "thresholds": spec["thresholds"],
        "root": root,
        "base_url": base_url if runtime_meta["available"] else None,
        "online_paths": online_paths,
        "timeout_s": timeout_s,
        "test_timeout_s": test_timeout_s,
        "run_tests": run_tests,
    }
    caps = {}
    for cap_id, entry in spec["capabilities"].items():
        caps[cap_id] = collect_capability(cap_id, entry, ctx)

    ledger_path = root / spec["sources"]["ledger"]
    summary = {
        "schema": "evidence_collection.summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec_sha256": spec_sha,
        "ruleset": "ec-grade-v1",
        "thresholds": spec["thresholds"],
        "runtime": runtime_meta,
        "ledger": {
            "path": spec["sources"]["ledger"],
            "sha256_before": _sha_file(ledger_path),
        },
        "capabilities": {},
    }
    for cap_id, signals in caps.items():
        verdict = signals["verdict"]
        summary["capabilities"][cap_id] = {
            "level": verdict["level"],
            "reason_codes": verdict["reason_codes"],
            "executed_evidence_items": verdict["executed_evidence_items"],
            "counts": verdict["counts"],
            "signals_digest": hashlib.sha256(
                json.dumps(verdict["signals_digest_inputs"], sort_keys=True,
                           ensure_ascii=False).encode("utf-8")).hexdigest(),
        }
    levels = [v["level"] for v in summary["capabilities"].values()]
    covered = [k for k, v in summary["capabilities"].items() if v["executed_evidence_items"] > 0]
    summary["coverage"] = {
        "total": len(levels),
        "covered": len(covered),
        "covered_ids": sorted(covered),
        "uncovered_ids": sorted(set(summary["capabilities"]) - set(covered)),
        "runtime_probe_covered": len([k for k, v in caps.items()
                                      if any((s.get("status") or 0) in range(200, 300)
                                             for p in (v.get("probes") or []) for s in p["samples"])]),
        "reference_covered": len([k for k, v in caps.items() if (v.get("reference") or {}).get("sha256")]),
        "tests_covered": len([k for k, v in caps.items()
                              if (v.get("tests") or {}).get("status") not in (None, "MISSING", "SKIPPED")]),
    }
    from tools.capability_audit_gate.taxonomy import tally  # 局部导入，避免循环
    summary["level_distribution"] = tally(levels)
    # 采集期发现的运行期缺陷（5xx / 非预期状态码）：不参与定级，但必须显式成行，不许静默
    findings = []
    for cap_id, signals in caps.items():
        for probe in signals.get("probes") or []:
            for sample in probe["samples"]:
                status = sample.get("status")
                if status is None:
                    findings.append({"capability": cap_id, "probe": probe["id"], "kind": "unreachable",
                                     "detail": sample.get("error")})
                elif status >= 500:
                    findings.append({"capability": cap_id, "probe": probe["id"], "kind": "server_error",
                                     "status": status, "path": sample.get("path"),
                                     "response_sha256": sample.get("response_sha256")})
    tests_findings = [{"capability": cap_id, "kind": "tests_red",
                       "failed": (signals.get("tests") or {}).get("totals", {}).get("failures", 0),
                       "errors": (signals.get("tests") or {}).get("totals", {}).get("errors", 0)}
                      for cap_id, signals in caps.items()
                      if (signals.get("tests") or {}).get("status") in {"FAILED", "ERROR", "TIMEOUT"}]
    summary["findings"] = findings + tests_findings
    return {"summary": summary, "signals": caps, "runtime": runtime_meta, "spec_sha256": spec_sha}