"""机器验收：把「三道门是否真的建成了」写成可复算的自检清单（落 JSON 工件）。

每条判据都产出 ``value``/``detail``/``artifact``，报告直接引用这些数字，零手抄。
运行：``python3 -m tools.proposal_gate.acceptance``
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import GateConfig, load_config
from .contract import validate_proposal
from .gate import ProposalGate
from .index import ProposalIndex
from .ledger import LedgerStore
from .textsim import normalize, shingles, similarity

REPO = Path(__file__).resolve().parents[2]


def _cfg() -> GateConfig:
    return load_config()


def _v2(cfg: GateConfig, **over) -> dict[str, Any]:
    payload = {
        "schema_version": "2.0",
        "mission_id": 1,
        "plan_id": 1,
        "title": "foo 通道三道门",
        "repo": cfg.repo_whitelist[0],
        "goal": {
            "objective": "为 foo 通道建立产物契约、去重器与覆盖率账本三道机器门",
            "criteria": ["重复率 < 0.15", "覆盖率 ≥ 0.80", "误判率 ≤ 0.05"],
        },
        "dependencies_hint": ["门-a", "门-b"],
        "is_maturity": True,
    }
    payload.update(over)
    return payload


def check_schema_contract(cfg: GateConfig) -> dict[str, Any]:
    v2_path = cfg.resolve_path(str(cfg.need("schemas.v2")))
    v1_path = cfg.resolve_path(str(cfg.need("schemas.v1")))
    v1_payload = {
        "proposal_id": "t-1",
        "title": "widget 链路账本",
        "hypothesis": "widget 链路缺少覆盖率账本",
        "falsification_criteria": ["偏差 = 0", "检出率 ≥ 0.90", "误判率 ≤ 0.05"],
        "expected_artifacts": ["a.json"],
        "triggered_by": {"rule_id": "R1"},
    }
    v1_res = validate_proposal(v1_payload, cfg)
    bad = validate_proposal(_v2(cfg, title="  "), cfg)
    return {
        "id": "schema_contract",
        "value": {
            "v1_schema_exists": v1_path.exists(),
            "v2_schema_exists": v2_path.exists(),
            "v1_compat_mapping_ok": v1_res.ok,
            "v1_mapped_repo": v1_res.doc.repo if v1_res.doc else None,
            "v1_mapped_schema_version": v1_res.doc.schema_version if v1_res.doc else None,
            "empty_title_fail_closed": (not bad.ok) and bool(bad.errors),
        },
        "pass": bool(v1_path.exists() and v2_path.exists() and v1_res.ok and not bad.ok),
        "artifact": "tools/proposal_gate/schemas/proposal.v1.json + proposal.v2.json",
    }


def check_dedup(cfg: GateConfig, tmp: Path) -> dict[str, Any]:
    store = LedgerStore.for_sqlite(str(tmp / "dedup.sqlite"), cfg)
    gate = ProposalGate(cfg, store, quarantine_dir=tmp / "q", events_file=tmp / "e.jsonl")
    first = gate.evaluate(_v2(cfg), mission_id=1, persist_quarantine=False)
    light = gate.evaluate(
        _v2(cfg, goal={"objective": "为 foo 通道构建产物契约、去重器与覆盖率账本三道机器门",
                       "criteria": ["重复率 < 0.15", "覆盖率 ≥ 0.80", "误判率 ≤ 0.05"]}),
        mission_id=2,
        persist_quarantine=False,
    )
    other_repo = cfg.repo_whitelist[1]
    distinct = gate.evaluate(
        _v2(cfg, repo=other_repo, title="bar 报表延迟归因",
            goal={"objective": "把 bar 报表端到端延迟拆到各阶段定位阻滞点",
                  "criteria": ["P95 延迟 ≤ 2.5 秒", "归因率 ≥ 0.90", "误归因率 ≤ 0.10"]},
            dependencies_hint=["链-a"]),
        mission_id=3,
        persist_quarantine=False,
    )
    twin = gate.evaluate(
        _v2(cfg, title="widget 对账脚本", repo=cfg.repo_whitelist[0],
            goal={"objective": "为同一链路补对账脚本核对账本",
                  "criteria": ["失败率 ≤ 0.01", "覆盖 ≥ 5000", "耗时 ≤ 30 秒"]},
            dependencies_hint=["门-a", "门-b"]),
        mission_id=4,
        persist_quarantine=False,
    )
    nfkc = normalize("ＦＯＯ　ＢＡＲ") == "foo bar"
    grams = shingles("foo bar", int(cfg.need("dedup.shingle_size")))
    store.dispose()
    return {
        "id": "dedup_engine",
        "value": {
            "nfkc_normalisation": nfkc,
            "shingle_size": int(cfg.need("dedup.shingle_size")),
            "shingle_sample": sorted(grams)[:3],
            "first_verdict": first.verdict,
            "light_rewrite_verdict": light.verdict,
            "light_rewrite_score": round(light.score or 0.0, 6),
            "dup_threshold": cfg.need("dedup.dup_threshold"),
            "distinct_verdict": distinct.verdict,
            "distinct_score": round(distinct.score or 0.0, 6),
            "twin_verdict": twin.verdict,
            "twin_overlap": round(twin.score or 0.0, 6),
            "twin_threshold": cfg.need("dedup.twin_dependency_overlap"),
        },
        "pass": (
            nfkc
            and first.verdict == "accepted"
            and light.verdict == "duplicate"
            and distinct.verdict == "accepted"
            and twin.verdict == "twin"
        ),
        "artifact": "artifacts/proposal_gate/acceptance.json#dedup_engine（sqlite 临时库复算）",
    }


def check_ledger(cfg: GateConfig, tmp: Path) -> dict[str, Any]:
    store = LedgerStore.for_sqlite(str(tmp / "ledger.sqlite"), cfg)
    gate = ProposalGate(cfg, store, quarantine_dir=tmp / "q2", events_file=tmp / "e2.jsonl")
    res = gate.evaluate(_v2(cfg), mission_id=11, persist_quarantine=True)
    again = gate.evaluate(_v2(cfg), mission_id=11, persist_quarantine=False)
    stats = store.stats(adopted_covered=1)
    keys = [k["metric_key"] for k in stats.kpi]
    rows_after = len(store.rows(limit=100))
    store.dispose()
    return {
        "id": "ledger_four_state",
        "value": {
            "states": sorted(stats.counts),
            "idempotent_resubmission_created_row": again.created,
            "rows_after_resubmission": rows_after,
            "dup_of_points_to_first": again.dup_of == res.sha256,
            "kpi_keys": keys,
            "dup_kpi_required": cfg.need("kpi.dup_rejected_key") in keys,
        },
        "pass": (
            sorted(stats.counts) == ["adopted", "dedup_rejected", "proposal", "quarantined"]
            and again.created is False
            and rows_after == 1
            and cfg.need("kpi.dup_rejected_key") in keys
        ),
        "artifact": "artifacts/proposal_gate/acceptance.json#ledger_four_state（sqlite 临时库复算）",
    }


def check_sources() -> dict[str, Any]:
    strict = REPO / "artifacts/proposal_gate/strict/collect_latest.json"
    lenient = REPO / "artifacts/proposal_gate/lenient/collect_latest.json"
    out: dict[str, Any] = {"strict": None, "lenient": None}
    for name, path in (("strict", strict), ("lenient", lenient)):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        node_arm = next(a for a in data["arms"] if a["arm"] == "node_index")
        audit = next(a for a in data["arms"] if a["arm"] == "node_audit")
        cov = next(a for a in data["arms"] if a["arm"] == "coverage")
        passed = audit["verdicts"].get("accepted", 0) + audit["verdicts"].get("near_duplicate", 0) + audit["verdicts"].get("duplicate", 0)
        out[name] = {
            "nodes_indexed": node_arm["nodes_indexed"],
            "plans_covered": node_arm["plans_covered"],
            "index_kinds": node_arm["kinds"],
            "audited": audit["audited"],
            "verdicts": audit["verdicts"],
            "contract_pass_rate": round(passed / audit["audited"], 6) if audit["audited"] else None,
            "dup_share_of_contract_passed": round(
                audit["verdicts"].get("duplicate", 0) / passed, 6
            ) if passed else None,
            "near_share_of_contract_passed": round(
                audit["verdicts"].get("near_duplicate", 0) / passed, 6
            ) if passed else None,
            "mission_traceability_rate": cov.get("mission_traceability_rate"),
            "seed_point_rate": cov.get("seed_point_rate"),
            "ledger_stats": data["ledger_stats"],
            "sql_trace": data["sql_trace"],
        }
    kinds_ok = bool(
        out["strict"] and set(out["strict"]["index_kinds"]) == {"campaign_node"}
    )
    return {
        "id": "dual_index_real_corpus",
        "value": out,
        "pass": bool(out["strict"] and out["strict"]["nodes_indexed"] > 0 and kinds_ok),
        "artifact": "artifacts/proposal_gate/{strict,lenient}/collect_latest.json",
    }


def check_zero_domain() -> dict[str, Any]:
    targets = [
        "tools/proposal_gate",
        "backend/api/proposal_gate.py",
        "tests/proposal_gate_support.py",
    ] + [p.name for p in sorted((REPO / "tests").glob("test_proposal_gate_*.py"))]
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools/audit/domain_leak_scan.py"), "--paths", *targets],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
    )
    return {
        "id": "zero_domain_semantics",
        "value": {"exit_code": proc.returncode, "stdout": (proc.stdout or "").strip()[-400:]},
        "pass": proc.returncode == 0,
        "artifact": "tools/audit/domain_leak_scan.py",
    }


def check_calibration() -> dict[str, Any]:
    path = REPO / "artifacts/proposal_gate/calibration.json"
    if not path.exists():
        return {"id": "threshold_calibration", "value": None, "pass": False,
                "artifact": "artifacts/proposal_gate/calibration.json"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "id": "threshold_calibration",
        "value": {
            "positives_n": data["positives_n"],
            "negatives_n": data["negatives_n"],
            "positives_min": data["positives_min"],
            "negatives_max": data["negatives_max"],
            "separation_margin": round(data["positives_min"] - data["negatives_max"], 6),
            "operating_points": data["operating_points"],
            "real_corpus": {
                k: data["real_corpus"].get(k) for k in ("texts", "pairs", "median", "p95", "max", "plateau")
            },
        },
        "pass": data["positives_min"] > data["negatives_max"] > 0
        and data["operating_points"]["review"]["tpr"] == 1.0,
        "artifact": "artifacts/proposal_gate/calibration.json",
    }


def main() -> int:
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        checks = [
            check_schema_contract(cfg),
            check_dedup(cfg, tmp),
            check_ledger(cfg, tmp),
            check_sources(),
            check_calibration(),
            check_zero_domain(),
        ]
    result = {
        "schema": "proposal-gate-acceptance/1",
        "generated_for": "p128w3-n08-proposal-gate-maturity",
        "config_path": str(cfg.path),
        "all_pass": all(c["pass"] for c in checks),
        "checks": checks,
    }
    out = REPO / "artifacts/proposal_gate/acceptance.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all_pass": result["all_pass"],
                      "checks": {c["id"]: c["pass"] for c in checks}}, ensure_ascii=False, indent=2))
    print(f"written {out}")
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())