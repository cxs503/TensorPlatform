"""审计引擎：spec → 路径解析 → 信号提取（带增量缓存）→ 判定 → 汇总。

一次 ``run_audit`` 的输出就是完整证据链：每个能力的等级、理由码、实现/测试/基准
证据，以及覆盖率、一致率、多数类基线、耗时与缓存命中率——报告只需渲染，不再重算。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import rules, scan, taxonomy
from .cache import ScanCache
from .resolve import FileIndex, resolve_capability

SCHEMA = "capability_audit_gate.report.v1"


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spec_sha(spec: dict) -> str:
    return _sha_text(json.dumps(spec, ensure_ascii=False, sort_keys=True))


def load_contrast(path: str | Path, key: str = "reconciled_level") -> dict:
    """外部真值台账：``{id: level}``。用于把"自动判定 vs 人工/独立审计"落成数字。

    只认台账里**逐能力等级**字段；台账缺失该字段的能力不参与一致率（不猜）。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    caps = data.get("capabilities") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for cap in caps or []:
        if not isinstance(cap, dict):
            continue
        cid = cap.get("id")
        lv = cap.get(key)
        if cid and lv:
            out[str(cid)] = str(lv).upper()
    return out


def run_audit(spec: dict, root: str | Path = ".", cache_path: str | Path | None = None,
              contrast: dict | None = None) -> dict:
    root = Path(root).resolve()
    t0 = time.perf_counter()
    index = FileIndex(root)
    cache = ScanCache(cache_path)
    thresholds = spec["thresholds"]
    verdicts = []
    for cap in spec["capabilities"]:
        resolution = resolve_capability(cap, index, thresholds)
        impl = []
        for rel in resolution["code"]:
            sig = cache.get_or_compute(root / rel, rel, scan.scan_impl, namespace="impl")
            sig = dict(sig)
            sig["_sha256"] = _file_sha(root / rel)
            impl.append((rel, sig))
        tests = []
        for rel in resolution["tests"]:
            sig = dict(cache.get_or_compute(root / rel, rel, scan.scan_tests, namespace="tests"))
            sig["_sha256"] = _file_sha(root / rel)
            tests.append((rel, sig))
        benchmark = scan.scan_benchmark(cap.get("benchmark"), root)
        verdict = rules.judge(cap, resolution, impl, tests, benchmark, thresholds)
        verdict["resolution"] = {"source": resolution["source"], "missing": resolution["missing"],
                                 "patterns": resolution.get("patterns") or {}}
        verdict["expected_level"] = cap.get("expected_level")
        verdict["chain_sufficient"] = rules.chain_sufficient(verdict)
        verdicts.append(verdict)
    cache.save()
    runtime = time.perf_counter() - t0

    levels = [v["level"] for v in verdicts]
    audited = [v for v in verdicts if v["level"] in taxonomy.RANK]
    agreement = _agreement(verdicts)
    contrast_block = _contrast_block(verdicts, contrast) if contrast else None
    report = {
        "schema_version": SCHEMA,
        "ruleset_version": rules.RULESET_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputs": {
            "root": str(root),
            "spec_title": spec.get("title", ""),
            "spec_sha256": _spec_sha(spec),
            "capability_count": len(spec["capabilities"]),
            "thresholds": thresholds,
        },
        "capabilities": verdicts,
        "summary": {
            "capabilities": len(verdicts),
            "audited": len(audited),
            "coverage": round(len(audited) / len(verdicts), 4) if verdicts else 0.0,
            "level_tally": taxonomy.tally(levels),
            "chain_sufficient": sum(1 for v in verdicts if v["chain_sufficient"]),
            "chain_full": sum(1 for v in verdicts if rules.chain_full(v)),
            "reason_tally": _reason_tally(verdicts),
            "runtime_sec": round(runtime, 3),
            "cache": {"hits": cache.hits, "misses": cache.misses,
                      "hit_rate": round(cache.hit_rate, 4)},
        },
        "validation": agreement,
        "contrast": contrast_block,
    }
    return report


def _contrast_block(verdicts: list[dict], contrast: dict) -> dict:
    pairs, matches = [], 0
    for v in verdicts:
        truth = contrast.get(v["capability"])
        if not truth:
            continue
        ok = truth == v["level"]
        matches += ok
        pairs.append({"id": v["capability"], "truth": truth, "detected": v["level"],
                      "match": ok, "reasons": v["reasons"]})
    compared = len(pairs)
    if not compared:
        return {"compared": 0, "agreement": None,
                "note": "no overlapping capabilities between audit and contrast ledger"}
    truth_levels = [p["truth"] for p in pairs]
    maj_level, maj_rate = taxonomy.majority_baseline(truth_levels)
    return {"compared": compared, "agreement": round(matches / compared, 4),
            "majority_baseline": round(maj_rate, 4), "majority_level": maj_level,
            "agreement_gain": round(matches / compared - maj_rate, 4),
            "disagreements": [p for p in pairs if not p["match"]],
            "source": "external contrast ledger (truth_key=reconciled_level)"}


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # pragma: no cover
        return ""


def _reason_tally(verdicts: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in verdicts:
        for r in v["reasons"]:
            out[r] = out.get(r, 0) + 1
    return dict(sorted(out.items()))


def _agreement(verdicts: list[dict]) -> dict:
    """与标注真值（``expected_level``）的一致率 + 多数类基线（对照）。"""
    labeled = [v for v in verdicts if v.get("expected_level")]
    if not labeled:
        return {"labeled": 0, "agreement": None, "majority_baseline": None,
                "majority_level": None, "agreement_gain": None, "disagreements": [],
                "note": "no labeled corpus provided (real ledger has no machine truth)"}
    matches = [v for v in labeled if v["level"] == v["expected_level"]]
    maj_level, maj_rate = taxonomy.majority_baseline([v["expected_level"] for v in labeled])
    agree = len(matches) / len(labeled)
    disagreements = [{"id": v["capability"], "expected": v["expected_level"],
                      "detected": v["level"], "reasons": v["reasons"]}
                     for v in labeled if v["level"] != v["expected_level"]]
    return {"labeled": len(labeled), "agreement": round(agree, 4),
            "majority_baseline": round(maj_rate, 4), "majority_level": maj_level,
            "agreement_gain": round(agree - maj_rate, 4),
            "disagreements": disagreements}


def acceptance(report: dict, min_labeled: int = 10, min_agreement: float = 0.9,
               min_chain_ratio: float = 1.0, max_runtime: float = 60.0) -> dict:
    """把战役验收判据机器化：C1 覆盖 / C2 一致率 / C4 证据链 / C5 时限。"""
    s = report["summary"]
    v = report["validation"] if (report["validation"].get("labeled") or 0) else (report.get("contrast") or report["validation"])
    checks = {
        "C1_coverage_100pct": {"value": s["coverage"], "threshold": 1.0,
                               "pass": s["coverage"] >= 1.0},
        "C2_agreement_ge_90pct": {
            "value": v["agreement"], "threshold": min_agreement,
            "labeled": v.get("labeled") or v.get("compared") or 0,
            "labeled_required": min_labeled,
            "majority_baseline": v.get("majority_baseline"),
            "pass": (v["agreement"] is not None and v["agreement"] >= min_agreement
                     and (v.get("labeled") or v.get("compared") or 0) >= min_labeled),
        },
        "C4_chain_sufficient": {"value": s["chain_sufficient"], "threshold": s["capabilities"],
                                "chain_full": s["chain_full"],
                                "pass": s["chain_sufficient"] >= s["capabilities"] * min_chain_ratio},
        "C5_runtime_lt_60s": {"value": s["runtime_sec"], "threshold": max_runtime,
                              "pass": s["runtime_sec"] < max_runtime},
    }
    checks["all_pass"] = all(c["pass"] for c in checks.values())
    return checks